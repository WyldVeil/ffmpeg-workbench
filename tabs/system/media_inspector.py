"""
tabs/system/media_inspector.py - Media Inspector

A MediaInfo-style view of any media file: container, every stream (codec,
profile, resolution, frame rate, colour/HDR, bit depth, rotation, audio
layout, language, default/forced flags), chapters and tags, plus a frame
preview, the raw ffprobe JSON, and quick diagnostics (variable frame rate,
interlacing, HDR, rotation metadata, missing audio, odd dimensions).
"""

from __future__ import annotations

import json
import os
import tkinter as tk
from functools import partial
from tkinter import ttk

from core import ffmpeg as ff
from core import media_notes, tasks
from core.i18n import t_or
from core.scaling import px
from tabs import framestep
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.kit import dialogs
from tabs.widgets import FramePreview


class MediaInspectorTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_path = ""
        self.info: ff.MediaInfo | None = None
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.make_header(
            self,
            t_or("tab.media_inspector", "Media Inspector"),
            t_or(
                "inspector.subtitle",
                "Everything inside a media file - streams, codecs, colour, chapters, tags",
            ),
            icon="\U0001f50d",
        )
        row = tk.Frame(self, bg=CLR["bg"])
        row.pack(fill="x", padx=px(20), pady=px(12, 6))
        self.src_var = tk.StringVar()
        self.make_file_row(
            row, t_or("common.source_file", "Source file"), self.src_var, self._browse
        ).pack(side="left")
        tk.Button(
            row,
            text=t_or("inspector.copy_report", "Copy report"),
            relief="flat",
            bg=CLR["panel"],
            fg=CLR["fg"],
            cursor="hand2",
            command=self._copy_report,
        ).pack(side="left", padx=px(8, 0))
        tk.Button(
            row,
            text=t_or("inspector.export_json", "Export JSON…"),
            relief="flat",
            bg=CLR["panel"],
            fg=CLR["fg"],
            cursor="hand2",
            command=self._export_json,
        ).pack(side="left", padx=px(6, 0))
        self._build_overview()
        self._build_pages()

    def _build_overview(self):
        """The frame preview beside the summary, the notes and a scrubber."""
        top = tk.Frame(self, bg=CLR["bg"])
        top.pack(fill="x", padx=px(20), pady=px(4))
        self.preview = FramePreview(top, width=384, height=216)
        self.preview.pack(side="left", anchor="n")
        side = tk.Frame(top, bg=CLR["bg"])
        side.pack(side="left", fill="both", expand=True, padx=px(16, 0))
        self.summary_lbl = tk.Label(
            side,
            text=t_or("inspector.drop_hint", "Open or drop a video, audio or image file."),
            bg=CLR["bg"],
            fg=CLR["accent"],
            font=(UI_FONT, 11, "bold"),
            justify="left",
            anchor="w",
            wraplength=px(620),
        )
        self.summary_lbl.pack(fill="x")
        self.notes = tk.Text(
            side,
            height=8,
            wrap="word",
            bg=CLR["bg"],
            fg=CLR["fg"],
            relief="flat",
            font=(UI_FONT, 9),
            highlightthickness=0,
        )
        self.notes.tag_configure("warn", foreground=CLR["orange"])
        self.notes.tag_configure("info", foreground=CLR["fgdim"])
        self.notes.tag_configure("ok", foreground=CLR["green"])
        self.notes.pack(fill="both", expand=True, pady=px(6, 0))
        scrub_row = tk.Frame(side, bg=CLR["bg"])
        scrub_row.pack(fill="x", pady=px(4, 0))
        tk.Label(
            scrub_row,
            text=t_or("inspector.scrub", "Preview position"),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(side="left")
        self.scrub = ttk.Scale(
            scrub_row,
            from_=0,
            to=1,
            orient="horizontal",
            command=lambda v: self.preview.show(float(v)),
        )
        self.scrub.pack(side="left", fill="x", expand=True, padx=px(6))
        framestep.slider(self.scrub, self.preview)  # , and . step a frame

    def _build_pages(self):
        """Streams, Container, Chapters, Tags and Raw JSON, in tabs."""
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=px(20), pady=px(8, 12))

        streams = tk.Frame(self.nb, bg=CLR["bg"])
        cols = ("idx", "type", "codec", "detail", "rate", "lang", "flags")
        heads = (
            "#",
            t_or("inspector.col_type", "Type"),
            t_or("inspector.col_codec", "Codec"),
            t_or("inspector.col_detail", "Details"),
            t_or("inspector.col_bitrate", "Bitrate"),
            t_or("inspector.col_lang", "Language / title"),
            t_or("inspector.col_flags", "Flags"),
        )
        self.tree = ttk.Treeview(streams, columns=cols, show="headings", height=7)
        widths = (40, 80, 150, 420, 100, 180, 120)
        for c, h, w in zip(cols, heads, widths, strict=False):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=px(w), anchor="w", stretch=c == "detail")
        ysb = ttk.Scrollbar(streams, command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        self.nb.add(streams, text=t_or("inspector.tab_streams", "Streams"))

        self.format_txt = self._text_page(t_or("inspector.tab_format", "Container"))
        self.chapters_txt = self._text_page(t_or("inspector.tab_chapters", "Chapters"))
        self.tags_txt = self._text_page(t_or("inspector.tab_tags", "Tags"))
        self.json_txt = self._text_page(t_or("inspector.tab_json", "Raw JSON"))

    def _text_page(self, title: str) -> tk.Text:
        frame = tk.Frame(self.nb, bg=CLR["bg"])
        txt = self.make_console_block(frame, height=20, padx=0, pady=0)
        self.nb.add(frame, text=title)
        return txt

    # ── actions ──────────────────────────────────────────────────────────────

    def _browse(self):
        path = dialogs.ask_open("any", title=t_or("inspector.open_title", "Inspect a media file"))
        if path:
            self.load_file(path)

    def load_file(self, path: str) -> bool:
        """Public entry point (also used by drag-and-drop and Open Recent).
        ffprobe runs in the background; the page fills in when it answers."""
        self.file_path = path
        self.src_var.set(path)
        self.info = None
        self.summary_lbl.config(text=t_or("inspector.reading", "Reading..."), fg=CLR["fgdim"])
        tasks.submit(
            partial(ff.probe, path, use_cache=False),
            on_done=partial(self._probed, path),
            on_error=partial(self._unreadable, path),
            name="Inspect",
        )
        return True

    def _probed(self, path: str, info: ff.MediaInfo) -> None:
        if path != self.file_path:  # another file was opened meanwhile
            return
        self.info = info
        self._populate(info)

    def _unreadable(self, path: str, exc: BaseException) -> None:
        if path != self.file_path:
            return
        self.info = None
        self.summary_lbl.config(
            text=t_or("inspector.unreadable", "Not a readable media file:") + f" {exc}",
            fg=CLR["red"],
        )
        self._fill_notes([])
        for txt in (self.format_txt, self.chapters_txt, self.tags_txt, self.json_txt):
            self._set_text(txt, "")
        self.tree.delete(*self.tree.get_children())
        self.preview.clear()

    def _set_text(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _fill_notes(self, notes):
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        for level, msg in notes:
            mark = {"warn": "⚠ ", "info": "ℹ ", "ok": "✔ "}.get(level, "")
            self.notes.insert("end", mark + msg + "\n", level)
        self.notes.configure(state="disabled")

    def _populate(self, info: ff.MediaInfo) -> None:
        name = os.path.basename(info.path)
        self.summary_lbl.config(text=f"{name}\n{info.summary()}", fg=CLR["accent"])
        self._fill_notes(media_notes.diagnose(info))

        self.tree.delete(*self.tree.get_children())
        for stream in info.streams:
            self.tree.insert("", "end", values=media_notes.stream_row(stream))
        self._set_text(self.format_txt, media_notes.container_text(info))
        self._set_text(self.chapters_txt, media_notes.chapters_text(info))
        self._set_text(self.tags_txt, media_notes.tags_text(info))
        self._set_text(self.json_txt, json.dumps(info.raw, indent=2, ensure_ascii=False))

        if info.has_video:
            self.scrub.configure(to=max(info.duration, 0.01))
            mid = 0.0 if info.is_image else min(info.duration * 0.25, 10.0)
            self.scrub.set(mid)
            self.preview.load(info.path, duration=info.duration, at=mid)
        else:
            self.preview.load(info.path, duration=info.duration)

    def report_text(self) -> str:
        if not self.info:
            return ""
        parts = [
            self.summary_lbl.cget("text"),
            "",
            self.format_txt.get("1.0", "end-1c"),
            "",
            "Streams:",
        ]
        for iid in self.tree.get_children():
            vals = self.tree.item(iid, "values")
            parts.append("  #" + "  |  ".join(str(v) for v in vals if str(v)))
        parts += ["", "Notes:", self.notes.get("1.0", "end-1c")]
        return "\n".join(parts)

    def _copy_report(self):
        text = self.report_text()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.log_debug("Media report copied")

    def _export_json(self):
        if not self.info:
            return
        default = os.path.splitext(os.path.basename(self.info.path))[0] + "_probe.json"
        out = dialogs.ask_save(
            default,
            kind="preset",
            defaultextension=".json",
            title=t_or("inspector.export_json", "Export JSON…"),
        )
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(self.info.raw, fh, indent=2, ensure_ascii=False)
