"""
tab_chaptermarkers.py  ─  YouTube Chapter Marker Generator
Build a list of chapter timestamps and titles, then export in
YouTube-ready format to paste into your video description.

Features:
  • Load a video and scrub the timeline to find chapter points
  • Click "Mark Here" to stamp the current position
  • Edit chapter names inline
  • Export as:
      - YouTube description text (MM:SS Title)
      - SRT chapter file  (for video players)
      - JSON  (for programmatic use)
      - VTT WebVTT chapters
  • Import from existing SRT or plain-text chapter files
  • Validates YouTube's requirements (≥3 chapters, first at 0:00)
"""

import json
import os
import re
import tkinter as tk
from tkinter import messagebox, ttk

from core.i18n import t
from core.scaling import px
from tabs import framestep
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit import dialogs
from tabs.widgets import scroll_body


def _fmt_yt(secs: float) -> str:
    """Format seconds as M:SS or H:MM:SS for YouTube."""
    secs = int(secs)
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_srt_tc(secs: float) -> str:
    """Format seconds as HH:MM:SS,000 for SRT."""
    h = int(secs) // 3600
    m = (int(secs) % 3600) // 60
    s = int(secs) % 60
    ms = min(999, round((secs - int(secs)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class Chapter:
    def __init__(self, time_secs: float, title: str, auto: bool = False):
        self.time = time_secs
        self.title = title
        self.auto = auto  # auto-numbered: renumbered after every change


def parse_chapter_text(content: str) -> list:
    """[(seconds, title)] from SRT/VTT cues or 'H:MM:SS Title' / 'MM:SS - Title' lines."""
    out = []
    cue = re.compile(r"(\d+:\d{2}:\d{2}(?:[.,]\d{1,3})?)\s*-->.*\n(.+)")
    found = cue.findall(content)
    if found:
        for tc, title in found:
            out.append((_parse_tc(tc), re.sub(r"<[^>]+>", "", title).strip()))
        return out
    line_re = re.compile(r"^\s*\(?((?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d+)?)\)?\s*[-–—:|]?\s*(.*)$")
    for line in content.splitlines():
        m = line_re.match(line)
        if m and m.group(2).strip():
            out.append((_parse_tc(m.group(1)), m.group(2).strip()))
    return out


def _parse_tc(tc: str) -> float:
    from core import ffmpeg as ff

    return ff.parse_time(tc.replace(",", "."))


def youtube_issues(chapters, duration: float) -> list:
    """Why YouTube would reject these chapters (empty list = fine)."""
    issues = []
    if len(chapters) < 3:
        issues.append(f"YouTube needs at least 3 chapters (you have {len(chapters)}).")
    if chapters and int(chapters[0].time) != 0:
        issues.append("The first chapter must start at 0:00.")
    secs = [int(c.time) for c in chapters]
    if len(set(secs)) != len(secs):
        issues.append("Two chapters share the same second.")
    for i, c in enumerate(chapters):
        end = chapters[i + 1].time if i + 1 < len(chapters) else (duration or c.time + 10)
        if end - c.time < 10:
            issues.append(f"'{c.title}' is shorter than 10 seconds.")
            break
    return issues


class ChapterMarkersTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_path = ""
        self._duration = 0.0
        self.chapters: list[Chapter] = []
        self._build_ui()

    def _build_ui(self):
        body = self._build_header()
        self._build_source_video(body)
        self._build_timeline_scrubber(body)
        self._build_mark_name(body)
        self._build_chapter_list(body)
        self._build_preview_pane(body)
        self._build_export(body)

    def _build_header(self):
        hdr = tk.Frame(self, bg=CLR["panel"])
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text="📑  " + t("tab.chapter_markers"),
            font=(UI_FONT, 16, "bold"),
            bg=CLR["panel"],
            fg=CLR["accent"],
        ).pack(side="left", padx=px(20), pady=px(12))
        tk.Label(hdr, text=t("chapter_markers.subtitle"), bg=CLR["panel"], fg=CLR["fgdim"]).pack(
            side="left"
        )

        body = scroll_body(self)
        return body

    def _build_source_video(self, body):
        """Source video."""
        # ── Source video ──────────────────────────────────────────────────
        sf = tk.Frame(body)
        sf.pack(pady=px(10))
        tk.Label(sf, text=t("common.source_video"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self.src_var = tk.StringVar()
        tk.Entry(sf, textvariable=self.src_var, width=58, relief="flat").pack(
            side="left", padx=px(8)
        )
        self.src_var.trace_add("write", lambda *_a: self.after(400, self._src_typed))
        tk.Button(
            sf, text=t("btn.browse"), command=self._browse, cursor="hand2", relief="flat"
        ).pack(side="left")
        self.dur_lbl = tk.Label(sf, text="", fg=CLR["fgdim"])
        self.dur_lbl.pack(side="left", padx=px(8))

    def _build_timeline_scrubber(self, body):
        """Timeline scrubber."""
        # ── Timeline scrubber ─────────────────────────────────────────────
        scrub_lf = tk.LabelFrame(
            body, text=f"  {t('chapter_markers.timeline_section')}  ", padx=px(14), pady=px(10)
        )
        scrub_lf.pack(fill="x", padx=px(16), pady=px(6))

        self.timeline = tk.Scale(
            scrub_lf,
            from_=0,
            to=100,
            orient="horizontal",
            length=px(700),
            resolution=0.001,
            command=self._on_scrub,
        )
        self.timeline.pack(fill="x")
        framestep.slider(self.timeline, lambda: framestep.fps_of(self.file_path))

        ts_row = tk.Frame(scrub_lf)
        ts_row.pack(fill="x", pady=px(4))
        tk.Label(ts_row, text=t("chapter_markers.current_position_label")).pack(side="left")
        self.pos_lbl = tk.Label(
            ts_row, text="0:00", fg=CLR["accent"], font=(UI_FONT, 14, "bold"), width=10
        )
        self.pos_lbl.pack(side="left", padx=px(8))

        tk.Label(ts_row, text=f"  {t('chapter_markers.jump_to_label')}").pack(side="left")
        self.jump_var = tk.StringVar()
        jump_e = tk.Entry(ts_row, textvariable=self.jump_var, width=7, relief="flat")
        jump_e.pack(side="left", padx=px(4))
        jump_e.bind("<Return>", lambda e: self._jump())
        tk.Button(
            ts_row, text="Go", command=self._jump, width=4, cursor="hand2", relief="flat"
        ).pack(side="left", padx=px(2))

        # Quick jump %
        for pct in [0, 10, 25, 50, 75, 90, 100]:
            tk.Button(
                ts_row,
                text=f"{pct}%",
                width=4,
                bg="#333",
                fg=CLR["fg"],
                font=(UI_FONT, 8),
                command=lambda p=pct: self._jump_pct(p),
            ).pack(side="left", padx=px(1))

    def _build_mark_name(self, body):
        """Mark + name."""
        # ── Mark + name ───────────────────────────────────────────────────
        mark_row = tk.Frame(body)
        mark_row.pack(fill="x", padx=px(16), pady=px(4))
        tk.Label(mark_row, text=t("chapter_markers.chapter_title_label")).pack(side="left")
        self.new_title_var = tk.StringVar(value="Chapter")
        title_entry = tk.Entry(mark_row, textvariable=self.new_title_var, width=30, relief="flat")
        title_entry.pack(side="left", padx=px(8))

        self.btn_mark = tk.Button(
            mark_row,
            text=f"📌  {t('chapter_markers.mark_here_button')}",
            bg=CLR["green"],
            fg="white",
            font=(UI_FONT, 11, "bold"),
            width=14,
            command=self._mark_here,
        )
        self.btn_mark.pack(side="left", padx=px(8))
        tk.Button(
            mark_row,
            text=f"➕ {t('chapter_markers.add_at_zero_button')}",
            bg=CLR["panel"],
            fg=CLR["fg"],
            command=lambda: self._add_chapter(0.0, "Intro"),
        ).pack(side="left", padx=px(4))

        # Auto-number
        auto_row = tk.Frame(body)
        auto_row.pack(anchor="w", padx=px(16), pady=px(2))
        self.autonumber_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            auto_row, text=t("chapter_markers.auto_number_checkbox"), variable=self.autonumber_var
        ).pack(side="left")
        self.prefix_var = tk.StringVar(value="Chapter")
        tk.Entry(auto_row, textvariable=self.prefix_var, width=12, relief="flat").pack(
            side="left", padx=px(6)
        )

    def _build_chapter_list(self, body):
        """Chapter list."""
        # ── Chapter list ──────────────────────────────────────────────────
        list_hdr = tk.Frame(body)
        list_hdr.pack(fill="x", padx=px(16), pady=px(8, 0))
        tk.Label(
            list_hdr,
            text=t("chapter_markers.chapter_list"),
            font=(UI_FONT, 9, "bold"),
            fg=CLR["fgdim"],
        ).pack(side="left")
        self.count_lbl = tk.Label(
            list_hdr, text=t("chapter_markers.0_chapters"), fg=CLR["fgdim"], font=(UI_FONT, 9)
        )
        self.count_lbl.pack(side="left", padx=px(10))

        # The list with its scrollbar and buttons is one row; everything else
        # goes below it at full width
        list_row = tk.Frame(body)
        list_row.pack(fill="x")
        cols = ("timestamp", "title")
        self.tree = ttk.Treeview(
            list_row, columns=cols, show="headings", selectmode="browse", height=8
        )
        self.tree.heading("timestamp", text=t("chapter_markers.timestamp_col"))
        self.tree.heading("title", text=t("chapter_markers.title_col"))
        self.tree.column("timestamp", width=px(120), anchor="center")
        self.tree.column("title", width=px(500))
        tree_sb = ttk.Scrollbar(list_row, command=self.tree.yview)
        self.tree.config(yscrollcommand=tree_sb.set)
        self.tree.pack(side="left", fill="both", expand=False, padx=px(16, 0), pady=px(4))
        tree_sb.pack(side="left", fill="y", pady=px(4))
        self.tree.bind("<Double-Button-1>", self._edit_selected)
        self.tree.bind("<Delete>", lambda e: self._remove_selected())

        # List action buttons
        list_btns = tk.Frame(list_row)
        list_btns.pack(side="left", padx=px(8), anchor="n", pady=px(4))
        for txt, cmd, bg in [
            ("✏  Edit Title", self._edit_selected, "#3A3A3A"),
            (f"🗑  {t('chapter_markers.remove_button')}", self._remove_selected, "#3A3A3A"),
            (f"⬆  {t('chapter_markers.move_up_button')}", self._move_up, "#3A3A3A"),
            (f"⬇  {t('chapter_markers.move_down_button')}", self._move_down, "#3A3A3A"),
            (f"🧹  {t('chapter_markers.clear_all_button')}", self._clear_all, "#3A3A3A"),
            ("📂  Import .srt", self._import_srt, "#3A3A3A"),
        ]:
            tk.Button(list_btns, text=txt, bg=bg, fg=CLR["fg"], width=16, command=cmd).pack(
                pady=px(2)
            )

    def _build_preview_pane(self, body):
        """Preview pane."""
        # ── Validation ────────────────────────────────────────────────────
        self.valid_lbl = tk.Label(
            body, text="", font=(UI_FONT, 9), fg=CLR["fgdim"], justify="left", anchor="w"
        )
        self.valid_lbl.pack(anchor="w", padx=px(16))

    def _build_export(self, body):
        """Export."""
        # ── Export ────────────────────────────────────────────────────────
        export_lf = tk.LabelFrame(
            body, text=f"  {t('chapter_markers.export_section')}  ", padx=px(14), pady=px(8)
        )
        export_lf.pack(fill="x", padx=px(16), pady=px(6))

        exp_row = tk.Frame(export_lf)
        exp_row.pack(fill="x")
        for fmt, cmd in [
            (f"📋  {t('chapter_markers.copy_youtube_button')}", self._copy_youtube),
            (f"📄  {t('chapter_markers.save_txt_button')}", self._save_txt),
            (f"📄  {t('chapter_markers.save_srt_button')}", self._save_srt),
            ("📄  Save .vtt", self._save_vtt),
            ("📄  Save .json", self._save_json),
            ("🎞  Embed into video…", self._embed),
        ]:
            tk.Button(
                exp_row,
                text=fmt,
                bg=CLR["accent"],
                fg="black",
                font=(UI_FONT, 9, "bold"),
                width=18,
                command=cmd,
            ).pack(side="left", padx=px(4))

        # Output preview
        preview_lf = tk.LabelFrame(
            body, text=f"  {t('chapter_markers.preview_section')}  ", padx=px(10), pady=px(6)
        )
        preview_lf.pack(fill="x", padx=px(16), pady=px(4))
        self.preview_text = tk.Text(
            preview_lf, height=8, bg=CLR["bg"], fg="#EEEEEE", font=(MONO_FONT, 10)
        )
        self.preview_text.pack(fill="x")

    # ─────────────────────────────────────────────────────────────────────
    def _browse(self):
        p = dialogs.ask_open("video", parent=self)
        if p:
            self.src_var.set(p)
            self._src_typed()

    def on_source_loaded(self, path):
        """Drag and drop / File > Open: the shared loader has already set the
        path, so load it here (the typed-path check would think it was done)."""
        self._src_typed(force=True)

    def _src_typed(self, force=False):
        p = self.src_var.get().strip()
        if p and os.path.isfile(p) and (force or p != getattr(self, "file_path", "")):
            from core import ffmpeg as ff

            self.file_path = p
            self._duration = ff.duration_of(p)
            m, sec = divmod(int(self._duration), 60)
            self.dur_lbl.config(text=f"{m}m {sec}s", fg=CLR["fgdim"])
            self.timeline.config(to=max(self._duration, 0.01))
            self._validate()

    def _on_scrub(self, val):
        self.pos_lbl.config(text=_fmt_yt(float(val)))

    def _jump(self):
        from core import ffmpeg as ff

        try:
            secs = ff.parse_time(self.jump_var.get())
            self.timeline.set(min(secs, self._duration))
        except ValueError:
            pass

    def _jump_pct(self, pct):
        self.timeline.set(self._duration * pct / 100)

    def mark_in(self):
        """i: a chapter starts at the position shown."""
        self._mark_here()

    def _mark_here(self):
        secs = float(self.timeline.get())
        title = self.new_title_var.get().strip()
        prefix = self.prefix_var.get().strip() or "Chapter"
        auto = self.autonumber_var.get() and (not title or title == prefix)
        self._add_chapter(secs, title or prefix, auto=auto)

    def _add_chapter(self, ts: float, title: str, auto: bool = False):
        # Prevent duplicate timestamps
        if any(abs(c.time - ts) < 0.5 for c in self.chapters):
            messagebox.showwarning(
                t("common.warning"), f"A chapter already exists near {_fmt_yt(ts)}."
            )
            return
        c = Chapter(ts, title, auto)
        self.chapters.append(c)
        self.chapters.sort(key=lambda x: x.time)
        self._refresh_tree()

    def _refresh_tree(self):
        prefix = self.prefix_var.get().strip() or "Chapter"
        for i, c in enumerate(self.chapters, 1):
            if c.auto:
                c.title = f"{prefix} {i}"
        self.tree.delete(*self.tree.get_children())
        for i, c in enumerate(self.chapters):
            self.tree.insert("", tk.END, iid=str(i), values=(_fmt_yt(c.time), c.title))
        n = len(self.chapters)
        self.count_lbl.config(text=f"{n} chapter{'s' if n != 1 else ''}")
        self._validate()
        self._update_preview()

    def _validate(self):
        issues = youtube_issues(self.chapters, self._duration)
        if not issues:
            self.valid_lbl.config(
                text=t("chapter_markers.valid_for_youtube_chapters"), fg=CLR["green"]
            )
        else:
            self.valid_lbl.config(text="\n".join("⚠  " + i for i in issues), fg=CLR["orange"])

    def _update_preview(self):
        self.preview_text.config(state="normal")
        self.preview_text.delete("1.0", tk.END)
        for c in self.chapters:
            self.preview_text.insert(tk.END, f"{_fmt_yt(c.time)} {c.title}\n")
        self.preview_text.config(state="disabled")

    def _edit_selected(self, _=None):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        c = self.chapters[idx]
        dlg = tk.Toplevel(self)
        dlg.title("Edit Chapter")
        dlg.geometry(f"{px(400)}x{px(150)}")
        dlg.grab_set()
        tk.Label(dlg, text=t("chapter_markers.timestamp_s")).pack(pady=px(14, 2))
        ts_var = tk.StringVar(value=str(c.time))
        tk.Entry(dlg, textvariable=ts_var, width=12, relief="flat").pack()
        tk.Label(dlg, text=t("chapter_markers.title")).pack(pady=px(8, 2))
        ti_var = tk.StringVar(value=c.title)
        tk.Entry(dlg, textvariable=ti_var, width=34).pack()

        def _save():
            from core import ffmpeg as ff

            try:
                c.time = ff.parse_time(ts_var.get())
                c.auto = False
                c.title = ti_var.get().strip()
                self.chapters.sort(key=lambda x: x.time)
                self._refresh_tree()
                dlg.destroy()
            except ValueError:
                messagebox.showerror(t("common.error"), "Enter a valid number for timestamp.")

        tk.Button(
            dlg,
            text="Save",
            command=_save,
            bg=CLR["green"],
            fg="white",
            cursor="hand2",
            relief="flat",
        ).pack(pady=px(10))

    def _remove_selected(self):
        sel = self.tree.selection()
        if sel:
            del self.chapters[int(sel[0])]
            self._refresh_tree()

    def _move_up(self):
        sel = self.tree.selection()
        if not sel or int(sel[0]) == 0:
            return
        i = int(sel[0])
        self.chapters[i], self.chapters[i - 1] = self.chapters[i - 1], self.chapters[i]
        self._refresh_tree()
        self.tree.selection_set(str(i - 1))

    def _move_down(self):
        sel = self.tree.selection()
        if not sel or int(sel[0]) >= len(self.chapters) - 1:
            return
        i = int(sel[0])
        self.chapters[i], self.chapters[i + 1] = self.chapters[i + 1], self.chapters[i]
        self._refresh_tree()
        self.tree.selection_set(str(i + 1))

    def _clear_all(self):
        if messagebox.askyesno(t("msg.clear_title"), t("msg.remove_all_chapters")):
            self.chapters.clear()
            self._refresh_tree()

    def _import_srt(self):
        p = dialogs.ask_open("subtitle", parent=self)
        if not p:
            return
        try:
            with open(p, encoding="utf-8-sig", errors="replace") as f:
                content = f.read()
        except OSError as exc:
            messagebox.showerror(t("common.error"), str(exc), parent=self)
            return
        found = parse_chapter_text(content)
        if not found:
            messagebox.showwarning(
                t("common.warning"), "No chapter times found in that file.", parent=self
            )
            return
        for secs, title in found:
            if not any(abs(c.time - secs) < 0.5 for c in self.chapters):
                self.chapters.append(Chapter(secs, title))
        self.chapters.sort(key=lambda x: x.time)
        self._refresh_tree()

    def _embed(self):
        """Write the chapters into a copy of the video (lossless remux)."""
        from core import ffmpeg as ff
        from core import filters as fl
        from core.ops.shared import chapters_cmd
        from core.paths import temp_dir

        src = self.src_var.get().strip()
        if not src or not os.path.isfile(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=self)
            return
        if not self.chapters:
            messagebox.showwarning(t("common.warning"), "Add some chapters first.", parent=self)
            return
        ext = os.path.splitext(src)[1].lower()
        if ext not in (".mp4", ".m4v", ".mov", ".mkv"):
            ext = ".mkv"
        out = self.confirm_output(self.suggest_output(src, "chapters", ext), [src], default_ext=ext)
        if not out:
            return
        dur = self._duration or ff.duration_of(src)
        chaps = []
        for i, c in enumerate(self.chapters):
            end = self.chapters[i + 1].time if i + 1 < len(self.chapters) else dur
            chaps.append((c.time, max(end, c.time + 0.001), c.title))
        meta = fl.write_ffmetadata(os.path.join(temp_dir("chapters_"), "meta.txt"), chapters=chaps)
        cmd = chapters_cmd(src, meta, out)
        self.run_ffmpeg(
            cmd,
            None,
            on_done=lambda rc: self.show_result(rc, out),
            duration=dur,
            output=out,
            inputs=[src],
            name="Embed chapters",
        )

    # ── Export methods ────────────────────────────────────────────────────
    def _yt_text(self):
        return "\n".join(f"{_fmt_yt(c.time)} {c.title}" for c in self.chapters)

    def _copy_youtube(self):
        txt = self._yt_text()
        self.clipboard_clear()
        self.clipboard_append(txt)
        messagebox.showinfo(t("msg.copied_title"), t("msg.chapters_copied"))

    def _save_txt(self):
        p = dialogs.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if p:
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(self._yt_text() + "\n")
            messagebox.showinfo("Saved", f"Saved to {os.path.basename(p)}")

    def _save_srt(self):
        p = dialogs.asksaveasfilename(defaultextension=".srt", filetypes=[("SRT", "*.srt")])
        if not p:
            return
        lines = []
        for i, c in enumerate(self.chapters):
            end_t = (
                self.chapters[i + 1].time - 0.001
                if i + 1 < len(self.chapters)
                else self._duration or c.time + 10
            )
            lines.append(str(i + 1))
            lines.append(f"{_fmt_srt_tc(c.time)} --> {_fmt_srt_tc(end_t)}")
            lines.append(c.title)
            lines.append("")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        messagebox.showinfo("Saved", f"SRT saved to {os.path.basename(p)}", parent=self)

    def _save_vtt(self):
        p = dialogs.asksaveasfilename(defaultextension=".vtt", filetypes=[("WebVTT", "*.vtt")])
        if not p:
            return
        lines = ["WEBVTT", ""]
        for i, c in enumerate(self.chapters):
            end_t = (
                self.chapters[i + 1].time - 0.001
                if i + 1 < len(self.chapters)
                else self._duration or c.time + 10
            )

            def vtt_tc(s):
                h = int(s) // 3600
                m = (int(s) % 3600) // 60
                sec = int(s) % 60
                ms = int((s - int(s)) * 1000)
                return f"{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"

            lines.append(f"{vtt_tc(c.time)} --> {vtt_tc(end_t)}")
            lines.append(c.title)
            lines.append("")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        messagebox.showinfo("Saved", f"VTT saved to {os.path.basename(p)}", parent=self)

    def _save_json(self):
        p = dialogs.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON", t("chapter_markers.json"))]
        )
        if not p:
            return
        data = [
            {"time": c.time, "timestamp": _fmt_yt(c.time), "title": c.title} for c in self.chapters
        ]
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        messagebox.showinfo("Saved", f"JSON saved to {os.path.basename(p)}")
