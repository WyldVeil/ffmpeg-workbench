"""
tab_multisplitter.py  ─  Manual Multi-Splitter
Extract multiple different segments from a single video into separate files.
"""
import tkinter as tk
from tkinter import messagebox
from core import dialogs
import os

from tabs.base_tab import BaseTab, VideoTimeline, CLR, UI_FONT, MONO_FONT
from core.hardware import get_video_duration
from core.i18n import t
from core import encoders as enc
from core import ffmpeg as ff
from core import outputs


def _tr(key, fallback):
    text = t(key)
    return fallback if text == key else text


def _fmt(seconds):
    if seconds < 0:
        seconds = 0
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{int(h):02d}:{int(m):02d}:{s:05.2f}"
    return f"{int(m):02d}:{s:05.2f}"

class MultiSplitterTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_path = ""
        self.duration = 0.0
        self.split_rows = []
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.manual_multi_splitter"),
                         t("splitter.subtitle"),
                         icon="✂️")

        # Source File
        sf = tk.Frame(self, bg=CLR["bg"])
        sf.pack(fill="x", padx=20, pady=10)
        tk.Label(sf, text=t("common.source_video"), bg=CLR["bg"], fg=CLR["fg"], font=(UI_FONT, 10)).pack(side="left")
        self.src_var = tk.StringVar()
        tk.Entry(sf, textvariable=self.src_var, width=50, bg=CLR["input_bg"], fg=CLR["input_fg"], font=(UI_FONT, 10)).pack(side="left", padx=10)
        tk.Button(sf, text=t("btn.browse"), command=self._browse_file, bg=CLR["panel"], fg=CLR["fg"]).pack(side="left")

        # Timeline
        tl_frame = tk.LabelFrame(self, text=t("splitter.timeline_section") if t("splitter.timeline_section") != "splitter.timeline_section" else "Timeline",
                                 bg=CLR["bg"], fg=CLR["fgdim"], padx=10, pady=6)
        tl_frame.pack(fill="x", padx=20, pady=(6, 2))
        self._timeline = VideoTimeline(tl_frame, on_change=self._on_timeline_change,
                                       height=90, show_handles=True)
        self._timeline.pack(fill="x", padx=4, pady=4)
        self._tl_info = tk.Label(tl_frame, text="No file loaded", bg=CLR["bg"],
                                 fg=CLR["fgdim"], font=(MONO_FONT, 9))
        self._tl_info.pack(anchor="w", padx=4)

        # Options
        opt_f = tk.Frame(self, bg=CLR["bg"])
        opt_f.pack(fill="x", padx=20, pady=5)
        self.copy_mode = tk.BooleanVar(value=True)
        tk.Checkbutton(opt_f, text=t("splitter.stream_copy_checkbox"), variable=self.copy_mode, bg=CLR["bg"], fg=CLR["fg"], selectcolor=CLR["panel"]).pack(side="left")

        # Splits Container
        self.splits_container = tk.LabelFrame(self, text=t("splitter.split_regions_section"), bg=CLR["bg"], fg=CLR["fgdim"], padx=10, pady=10)
        self.splits_container.pack(fill="both", expand=True, padx=20, pady=10)
        
        btn_f = tk.Frame(self.splits_container, bg=CLR["bg"])
        btn_f.pack(fill="x", pady=(0, 10))
        tk.Button(btn_f, text=t("splitter.add_split_button"), command=self._add_row, bg=CLR["panel"], fg=CLR["fg"]).pack(side="left")

        self.rows_frame = tk.Frame(self.splits_container, bg=CLR["bg"])
        self.rows_frame.pack(fill="both", expand=True)

        # Add first row by default
        self._add_row()

        # Render Button & Console
        self.btn_render = tk.Button(self, text=t("splitter.batch_extract_button"), font=(UI_FONT, 11, "bold"), bg=CLR["green"], fg="white", command=self._render_all)
        self.btn_render.pack(pady=10)
        cf = tk.Frame(self, bg=CLR["bg"])
        cf.pack(fill="both", expand=True, padx=20, pady=4)
        self.console, csb = self.make_console(cf)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def _browse_file(self):
        p = dialogs.askopenfilename(filetypes=[(t("silence.video_files"), "*.mp4 *.mov *.mkv *.avi *.webm")])
        if p:
            self.file_path = p
            self.src_var.set(p)
            self.duration = get_video_duration(p)
            self._timeline.set_duration(self.duration)
            self._timeline.set_range(0, self.duration)
            self._tl_info.config(
                text=f"{os.path.basename(p)}  ·  {_fmt(self.duration)} total")

    def _on_timeline_change(self, start, end, playhead):
        self._tl_info.config(
            text=f"Start {_fmt(start)}  ·  End {_fmt(end)}  ·  Playhead {_fmt(playhead)}")

    def _add_row(self):
        if len(self.split_rows) >= 10:
            messagebox.showwarning(t("splitter.limit_reached_title"), t("splitter.limit_reached_message"))
            return

        idx = len(self.split_rows) + 1
        row = tk.Frame(self.rows_frame, bg=CLR["bg"])
        row.pack(fill="x", pady=4)

        tk.Label(row, text=f"Clip {idx}:", bg=CLR["bg"], fg=CLR["accent"], width=6).pack(side="left")
        
        tk.Label(row, text=t("splitter.start_label"), bg=CLR["bg"], fg=CLR["fg"]).pack(side="left", padx=(10,2))
        start_var = tk.StringVar(value="00:00:00")
        tk.Entry(row, textvariable=start_var, width=10, bg=CLR["input_bg"], fg=CLR["input_fg"]).pack(side="left")

        tk.Label(row, text=t("splitter.end_label"), bg=CLR["bg"], fg=CLR["fg"]).pack(side="left", padx=(10,2))
        end_var = tk.StringVar(value="00:00:10")
        tk.Entry(row, textvariable=end_var, width=10, bg=CLR["input_bg"], fg=CLR["input_fg"]).pack(side="left")

        tk.Label(row, text=t("splitter.suffix_label"), bg=CLR["bg"], fg=CLR["fg"]).pack(side="left", padx=(10,2))
        suffix_var = tk.StringVar(value=f"_clip{idx}")
        tk.Entry(row, textvariable=suffix_var, width=15, bg=CLR["input_bg"], fg=CLR["input_fg"]).pack(side="left")

        btn_rm = tk.Button(row, text="❌", bg=CLR["bg"], fg=CLR["red"], command=lambda r=row: self._remove_row(r))
        btn_rm.pack(side="left", padx=10)

        self.split_rows.append({"frame": row, "start": start_var, "end": end_var, "suffix": suffix_var})

    def _remove_row(self, row_frame):
        row_frame.destroy()
        self.split_rows = [r for r in self.split_rows if r["frame"] != row_frame]

    def _render_all(self):
        if not self.file_path:
            messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=self)
            return
        if not self.split_rows:
            return
        info = ff.try_probe(self.file_path)
        total = info.duration if info else 0.0
        base, src_ext = os.path.splitext(self.file_path)
        copy = bool(self.copy_mode.get())
        ext = src_ext if copy else ".mp4"
        jobs, seen = [], set()
        for i, data in enumerate(self.split_rows, start=1):
            try:
                ss = ff.parse_time(data["start"].get())
                ee = ff.parse_time(data["end"].get())
            except ValueError:
                messagebox.showwarning(t("common.warning"),
                                       _tr("splitter.bad_time", "Clip {n}: times must look like 12.5, 01:23.4 or 1:02:03.").format(n=i),
                                       parent=self)
                return
            if total:
                ee = min(ee, total)
            if ee <= ss:
                messagebox.showwarning(t("common.warning"),
                                       _tr("splitter.empty_range", "Clip {n} ends before it starts.").format(n=i), parent=self)
                return
            out = f"{base}{data['suffix'].get().strip() or f'_clip{i}'}{ext}"
            key = os.path.normcase(os.path.abspath(out))
            if key in seen:
                messagebox.showwarning(t("common.warning"),
                                       _tr("splitter.duplicate_name", "Two clips would both be saved as\n{p}\nGive them different suffixes.").format(p=out),
                                       parent=self)
                return
            seen.add(key)
            jobs.append((ss, ee, out))
        existing = [o for _, _, o in jobs if os.path.exists(o)]
        if existing:
            ans = messagebox.askyesnocancel(
                _tr("splitter.exists_title", "Files already exist"),
                _tr("splitter.exists_msg", "{n} of the output files already exist.\n\nYes = overwrite them\nNo = save with new names\nCancel = stop").format(n=len(existing)),
                parent=self)
            if ans is None:
                return
            if ans is False:
                jobs = [(a, b, outputs.unique_path(o)) for a, b, o in jobs]
        src = self.file_path
        results = []

        def work(progress_cb, cancel_fn, ctx):
            from tabs.cutting.trimmer import keyframe_at_or_before
            n = len(jobs)
            for i, (ss, ee, out) in enumerate(jobs):
                if cancel_fn():
                    return -1
                part = os.path.splitext(out)[0] + ".part" + os.path.splitext(out)[1]
                if copy:
                    k = keyframe_at_or_before(src, ss)
                    k = ss if k is None else k
                    cmd = [ff.ffmpeg_path(), "-y", "-ss", ff.ts(k), "-i", src, "-t", ff.ts(ee - k),
                           "-map", "0:v:0?", "-map", "0:a?", "-c", "copy", "-sn", "-dn"]
                    seconds = ee - k
                else:
                    cmd = [ff.ffmpeg_path(), "-y", "-ss", ff.ts(ss), "-i", src, "-t", ff.ts(ee - ss),
                           "-map", "0:v:0?", "-map", "0:a?", "-sn", "-dn",
                           *enc.video("h264", "very_high", hw="off", container="mp4").args,
                           *enc.audio("aac", "192k", container="mp4")]
                    seconds = ee - ss
                if ext.lower() in (".mp4", ".mov", ".m4v") and "-movflags" not in cmd:
                    cmd += ["-movflags", "+faststart"]
                cmd.append(part)
                progress_cb(f"\u25b6 {i + 1}/{n}: {ff.format_time(ss)} \u2013 {ff.format_time(ee)} \u2192 {os.path.basename(out)}")
                res = ctx.span(i / n, (i + 1) / n).run(cmd, duration=seconds, check=False)
                if res.cancelled:
                    if os.path.exists(part):
                        os.remove(part)
                    return -1
                if res.ok and os.path.exists(part) and os.path.getsize(part) > 0:
                    os.replace(part, out)
                    results.append((out, None))
                else:
                    if os.path.exists(part):
                        os.remove(part)
                    results.append((out, res.error_summary))
            return 0 if all(err is None for _, err in results) else 1

        label = self.btn_render.cget("text")
        self.btn_render.config(state="disabled")

        def done(task_id, rc):
            self.btn_render.config(state="normal", text=label)
            if rc == -1:
                return
            ok = [o for o, e in results if e is None]
            bad = [(o, e) for o, e in results if e is not None]
            if not bad:
                messagebox.showinfo(t("msg.done_title"), t("msg.batch_split_done") + "\n\n" +
                                    "\n".join(os.path.basename(o) for o in ok), parent=self)
            else:
                messagebox.showwarning(t("msg.done_title"),
                                       _tr("splitter.partial", "{ok} clip(s) saved, {bad} failed:").format(ok=len(ok), bad=len(bad))
                                       + "\n\n" + "\n".join(f"{os.path.basename(o)}: {e.splitlines()[-1]}" for o, e in bad),
                                       parent=self)

        self.enqueue_render("Multi-Splitter", "", worker_fn=work, inputs=[src],
                            on_progress=lambda tid, line: self.log(self.console, line),
                            on_complete=done)