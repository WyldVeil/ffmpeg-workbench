"""
tab_encodequeue.py  ─  Global Render Queue Dashboard
======================================================
Live view of every task in the RenderQueueManager - whether submitted from
this tab or any other tab in the app.

Left panel  - queue treeview (all tasks, live-refreshed)
Right panel - job settings editor for queueing new encode jobs from here
"""

import contextlib
import os
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops import encode_queue as eq
from core.queue_manager import Hooks
from core.scaling import px
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab, make_header
from tabs.kit import dialogs
from tabs.widgets import SaveRow, scroll_body

RESOLUTIONS = ["Source", "3840x2160", "2560x1440", "1920x1080", "1280x720", "854x480", "640x360"]


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def progress_text(task) -> str:
    """What the Progress column shows: a bar and percentage while it runs (the
    raw FFmpeg log line it used to show meant nothing to most people)."""
    status = task.status
    if status == "pending":
        return "Waiting"
    if status == "done":
        return "▰" * 10 + "  Finished"
    if status == "cancelled":
        return "Cancelled"
    if status == "failed":
        return "Failed - " + (task.error.splitlines()[0][:60] if task.error else "see the log")
    if task.fraction is None:
        return "Working…"
    filled = round(task.fraction * 10)
    text = "▰" * filled + "▱" * (10 - filled) + f"  {task.fraction * 100:.0f}%"
    if task.eta is not None and task.fraction < 1:
        text += f" · {_fmt_elapsed(task.eta)} left"
    return text


# ─────────────────────────────────────────────────────────────────────────────
#  EncodeQueueTab
# ─────────────────────────────────────────────────────────────────────────────


class EncodeQueueTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.editor: dict[str, tk.StringVar] = {}  # the job editor's fields, by name
        self._editor_job: eq.EncodeJob | None = None
        self._selected_task_id: str | None = None
        self._build_ui()

        # Wire to global queue
        from core.queue_manager import RenderQueueManager

        self._qmgr = RenderQueueManager.get_instance()
        self._qmgr.register_update_callback(self._refresh_queue_tree)
        self._refresh_queue_tree()

    # ═══════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        # Header
        make_header(self, t("tab.encode_queue"), t("encode_queue.subtitle"), icon="📋")

        # Paned layout
        paned = tk.PanedWindow(self, orient="horizontal", sashwidth=px(6), bg="#AAAAAA")
        paned.pack(fill="both", expand=True, padx=px(10), pady=px(8))

        left = tk.Frame(paned, width=px(540))
        paned.add(left, minsize=px(340))
        right = tk.Frame(paned, width=px(480))
        paned.add(right, minsize=px(320))

        self._build_queue_panel(left)
        self._build_editor_panel(right)

    # ── Queue list panel ──────────────────────────────────────────────────
    def _build_queue_panel(self, parent):
        tk.Label(
            parent, text=t("encode_queue.queue_section"), font=(UI_FONT, 9, "bold"), fg=CLR["fgdim"]
        ).pack(anchor="w", padx=px(8), pady=px(6, 2))

        cols = ("status", "name", "output", "elapsed", "progress")
        self.tree = ttk.Treeview(
            parent, columns=cols, show="headings", selectmode="browse", height=18
        )
        for col, txt, w, stretch in [
            ("status", t("encode_queue.status_col"), 72, False),
            ("name", t("encode_queue.task_col"), 160, True),
            ("output", t("encode_queue.output_col"), 140, True),
            ("elapsed", t("encode_queue.elapsed_col"), 70, False),
            ("progress", t("encode_queue.progress_col"), 220, True),
        ]:
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=px(w), stretch=stretch)

        self.tree.tag_configure("done", foreground="#4CAF50")
        self.tree.tag_configure("failed", foreground="#F44336")
        self.tree.tag_configure("cancelled", foreground="#888888")
        self.tree.tag_configure("active", foreground="#4FC3F7")
        self.tree.tag_configure("pending", foreground="#EEEEEE")

        tsb = ttk.Scrollbar(parent, command=self.tree.yview)
        self.tree.config(yscrollcommand=tsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # Control buttons
        btn_f = tk.Frame(parent)
        btn_f.pack(fill="x", padx=px(4), pady=px(6))
        self._btn_cancel = tk.Button(
            btn_f,
            text=t("encode_queue.cancel_button"),
            bg=CLR["red"],
            fg="white",
            font=(UI_FONT, 9, "bold"),
            command=self._cancel_selected,
        )
        self._btn_cancel.pack(side="left", padx=px(4))
        tk.Button(
            btn_f,
            text=t("encode_queue.clear_finished_button"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            font=(UI_FONT, 9),
            command=self._clear_finished,
        ).pack(side="left", padx=px(4))

        # Stats label
        self._stats_lbl = tk.Label(parent, text="", fg=CLR["fgdim"], font=(UI_FONT, 9))
        self._stats_lbl.pack(anchor="w", padx=px(8))

        # Console for selected task's last progress
        tk.Label(
            parent,
            text=t("encode_queue.task_log_section"),
            font=(UI_FONT, 9, "bold"),
            fg=CLR["fgdim"],
        ).pack(anchor="w", padx=px(8), pady=px(6, 2))
        self.console = scrolledtext.ScrolledText(
            parent, height=7, bg=CLR["console_bg"], fg="#00FF88", font=(MONO_FONT, 8)
        )
        self.console.pack(fill="both", expand=True, padx=px(4), pady=px(4))

    # ── eq.EncodeJob editor panel ─────────────────────────────────────────────────
    def _build_editor_panel(self, parent):
        tk.Label(
            parent,
            text=t("encode_queue.editor_section"),
            font=(UI_FONT, 9, "bold"),
            fg=CLR["fgdim"],
        ).pack(anchor="w", padx=px(8), pady=px(6, 2))

        self.editor_frame = scroll_body(parent)

        ef = self.editor_frame

        # Source / output
        self.ed_src_var = tk.StringVar()
        f_src = tk.Frame(ef)
        f_src.pack(fill="x", padx=px(10), pady=px(4))
        tk.Label(f_src, text=t("encode_queue.source_label"), width=14, anchor="e").pack(side="left")
        tk.Entry(f_src, textvariable=self.ed_src_var, width=32, relief="flat").pack(
            side="left", padx=px(4)
        )
        tk.Button(f_src, text="…", width=2, command=self._ed_browse_src).pack(side="left")

        self.ed_out_var = tk.StringVar()
        SaveRow(ef, t("encode_queue.output_label"), self.ed_out_var).pack(
            fill="x", padx=px(10), pady=px(4)
        )

        # Codec / quality fields
        fields = [
            (t("encode_queue.video_codec"), "ed_vcodec_var", eq.VIDEO_CODECS, "libx264"),
            (t("common.crf"), "ed_crf_var", None, "18"),
            (t("common.preset"), "ed_preset_var", eq.PRESETS, "fast"),
            (t("common.resolution"), "ed_res_var", RESOLUTIONS, "Source"),
            (
                t("batch_joiner.lbl_fps"),
                "ed_fps_var",
                ["Source", "23.976", "24", "25", "29.97", "30", "50", "60"],
                "Source",
            ),
            ("Audio codec:", "ed_acodec_var", eq.AUDIO_CODECS, "aac"),
            (
                "Audio bitrate:",
                "ed_abitrate_var",
                ["64k", "96k", "128k", "192k", "256k", "320k"],
                "192k",
            ),
        ]
        for label, attr, choices, default in fields:
            var = tk.StringVar(value=default)
            self.editor[attr] = var
            f = tk.Frame(ef)
            f.pack(fill="x", padx=px(10), pady=px(4))
            tk.Label(f, text=label, width=14, anchor="e").pack(side="left")
            if choices:
                ttk.Combobox(f, textvariable=var, values=choices, state="readonly", width=18).pack(
                    side="left", padx=px(4)
                )
            else:
                tk.Entry(f, textvariable=var, width=8, relief="flat").pack(side="left", padx=px(4))

        for label, attr in [("Extra -vf:", "ed_evf_var"), ("Extra args:", "ed_eargs_var")]:
            var = tk.StringVar()
            self.editor[attr] = var
            f = tk.Frame(ef)
            f.pack(fill="x", padx=px(10), pady=px(4))
            tk.Label(f, text=label, width=14, anchor="e").pack(side="left")
            tk.Entry(f, textvariable=var, width=34, relief="flat").pack(side="left", padx=px(4))

        # Submit button
        tk.Button(
            ef,
            text=t("encode_queue.add_to_queue_button"),
            bg=CLR["green"],
            fg="black",
            font=(UI_FONT, 10, "bold"),
            command=self._add_to_queue,
        ).pack(pady=px(10), padx=px(10), fill="x")

        # Add folder button
        tk.Button(
            ef,
            text=t("encode_queue.add_folder_button"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            font=(UI_FONT, 9),
            command=self._add_folder,
        ).pack(pady=px(0, 6), padx=px(10), fill="x")

    # ═══════════════════════════════════════════════════════════════════════
    #  Queue tree refresh  (called by RenderQueueManager update callback)
    # ═══════════════════════════════════════════════════════════════════════
    def _refresh_queue_tree(self):
        tasks = self._qmgr.get_all_tasks()

        # Preserve selection
        sel_id = self._selected_task_id

        self.tree.delete(*self.tree.get_children())
        for task in tasks:
            elapsed = ""
            if task.started_at:
                end = task.finished_at or time.time()
                elapsed = _fmt_elapsed(end - task.started_at)

            progress = progress_text(task)
            out_name = os.path.basename(task.output_path) if task.output_path else "-"

            self.tree.insert(
                "",
                tk.END,
                iid=task.id,
                values=(task.status.upper(), task.name, out_name, elapsed, progress),
                tags=(task.status,),
            )

        # Re-select
        if sel_id:
            with contextlib.suppress(tk.TclError):
                self.tree.selection_set(sel_id)

        # Stats
        active, pending, done, failed = self._qmgr.get_stats()
        total = len(tasks)
        self._stats_lbl.config(
            text=f"{total} tasks  ·  {active} active  ·  {pending} pending  "
            f"·  {done} done  ·  {failed} failed"
        )

    def _on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        self._selected_task_id = sel[0]
        # Show latest progress in console
        tasks = self._qmgr.get_all_tasks()
        for task in tasks:
            if task.id == self._selected_task_id:
                if task.progress:
                    self.console.delete("1.0", tk.END)
                    self.console.insert(tk.END, f"[{task.name}]\n{task.progress}\n")
                break

    def _cancel_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(
                t("encode_queue.nothing_selected_title"), t("encode_queue.nothing_selected_message")
            )
            return
        self._qmgr.cancel(sel[0])

    def _clear_finished(self):
        self._qmgr.clear_finished()

    # ═══════════════════════════════════════════════════════════════════════
    #  Editor: add new encode jobs
    # ═══════════════════════════════════════════════════════════════════════
    def _ed_browse_src(self):
        p = dialogs.askopenfilename(
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All", t("ducker.item_2"))]
        )
        if p:
            self.ed_src_var.set(p)
            if not self.ed_out_var.get():
                self.ed_out_var.set(os.path.splitext(p)[0] + "_encoded.mp4")

    def _queue_job(self, src, out) -> bool:
        j = self._read_editor()
        j.src, j.out = src, out
        try:
            cmd = j.build_cmd()
        except ValueError as exc:
            messagebox.showwarning(
                t("common.warning"), f"{os.path.basename(src)}:\n{exc}", parent=self
            )
            return False
        self.enqueue_render(
            os.path.basename(src),
            output_path=out,
            work=[cmd],
            inputs=[src],
            expected_duration=ff.duration_of(src) or None,
            hooks=Hooks(
                on_progress=lambda tid, line: self._on_task_progress(tid, line),
                on_complete=lambda tid, rc, out=out: self._on_task_complete(tid, rc, out),
            ),
        )
        return True

    def _default_ext(self):
        return eq.DEFAULT_EXT.get(self.editor["ed_vcodec_var"].get(), ".mp4")

    def _add_to_queue(self):
        src = self.ed_src_var.get().strip()
        if not src or not os.path.isfile(src):
            messagebox.showwarning(
                t("encode_queue.no_source_title"), t("encode_queue.no_source_message"), parent=self
            )
            return
        out = self.ed_out_var.get().strip() or self.suggest_output(
            src, "encoded", self._default_ext()
        )
        out = self.confirm_output(out, [src], default_ext=self._default_ext())
        if not out:
            return
        self.ed_out_var.set(out)
        self._queue_job(src, out)

    def _add_folder(self):
        from core.outputs import free_name

        folder = dialogs.askdirectory()
        if not folder:
            return
        exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v", ".mts", ".m2ts", ".mxf"}
        ext = self._default_ext()
        count, taken = 0, set()
        for fn in sorted(os.listdir(folder)):
            stem, e = os.path.splitext(fn)
            src = os.path.join(folder, fn)
            if (
                e.lower() not in exts
                or stem.endswith("_encoded")
                or ".part" in stem
                or not os.path.isfile(src)
                or os.path.getsize(src) == 0
            ):
                continue  # not a video, or one of this tool's own outputs
            out = free_name(os.path.join(folder, stem + "_encoded" + ext), taken)
            taken.add(os.path.normcase(out))
            if self._queue_job(src, out):
                count += 1
            else:
                break
        if count:
            messagebox.showinfo(
                "Queued", f"{count} file(s) added to the render queue.", parent=self
            )
        else:
            messagebox.showwarning(
                t("common.warning"), "No video files to add were found.", parent=self
            )

    def _read_editor(self) -> eq.EncodeJob:
        j = eq.EncodeJob()
        j.vcodec = self.editor["ed_vcodec_var"].get()
        j.crf = self.editor["ed_crf_var"].get()
        j.preset = self.editor["ed_preset_var"].get()
        j.resolution = self.editor["ed_res_var"].get()
        j.fps = self.editor["ed_fps_var"].get()
        j.acodec = self.editor["ed_acodec_var"].get()
        j.abitrate = self.editor["ed_abitrate_var"].get()
        j.extra_vf = self.editor["ed_evf_var"].get()
        j.extra_args = self.editor["ed_eargs_var"].get()
        return j

    def _on_task_progress(self, task_id, line):
        if self._selected_task_id == task_id:
            try:
                self.console.insert(tk.END, line + "\n")
                self.console.see(tk.END)
            except tk.TclError:
                pass

    def _on_task_complete(self, task_id, returncode, out_path):
        if returncode != 0:
            messagebox.showerror(
                "Encode Failed",
                f"Task finished with error code {returncode}.\n"
                f"Check the queue log for details.\nOutput: {out_path}",
            )
