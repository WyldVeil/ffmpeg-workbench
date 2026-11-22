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
from core import tasks
from core.i18n import t
from core.scaling import px
from tabs import framestep
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit import dialogs
from tabs.widgets import FramePreview


def _tr(key: str, fallback: str) -> str:
    text = t(key)
    return fallback if text == key else text


def _human_bytes(n: int) -> str:
    if not n:
        return "—"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return str(n)


def _human_rate(bps: int) -> str:
    if not bps:
        return "—"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Mb/s"
    return f"{bps / 1000:.0f} kb/s"


def _fps_text(fps: float) -> str:
    if not fps:
        return "—"
    return f"{fps:.3f}".rstrip("0").rstrip(".")


def diagnose(info: ff.MediaInfo) -> list[tuple[str, str]]:
    """Return ``[(level, message)]`` notes about things worth knowing."""
    notes: list[tuple[str, str]] = []
    v, a = info.video, info.audio
    if v:
        if (
            v.r_fps
            and v.fps
            and abs(v.r_fps - v.fps) / max(v.fps, 1e-6) > 0.01
            and not info.is_image
        ):
            notes.append(
                (
                    "warn",
                    _tr(
                        "inspector.vfr",
                        "Variable frame rate likely (average {avg} fps vs nominal {nom} fps). "
                        "Editing tools may drift; convert to constant frame rate first.",
                    ).format(avg=_fps_text(v.fps), nom=_fps_text(v.r_fps)),
                )
            )
        if v.field_order and v.field_order not in ("progressive", "unknown"):
            notes.append(
                (
                    "warn",
                    _tr(
                        "inspector.interlaced",
                        "Interlaced video ({order}). Use the Deinterlace tool before scaling or "
                        "slow motion.",
                    ).format(order=v.field_order),
                )
            )
        if v.is_hdr:
            notes.append(
                (
                    "info",
                    _tr(
                        "inspector.hdr",
                        "HDR video ({tr}). Converting to SDR needs tone-mapping or colours "
                        "will look washed out.",
                    ).format(tr=v.color_transfer),
                )
            )
        if v.rotation:
            notes.append(
                (
                    "info",
                    _tr(
                        "inspector.rotation",
                        "Rotation metadata: {deg}°. Players rotate it on display; FFmpeg "
                        "applies it automatically when re-encoding.",
                    ).format(deg=v.rotation),
                )
            )
        if v.width % 2 or v.height % 2:
            notes.append(
                (
                    "warn",
                    _tr(
                        "inspector.odd_size",
                        "Odd frame size ({w}×{h}): H.264/H.265 in 4:2:0 need even "
                        "dimensions, so tools will round them.",
                    ).format(w=v.width, h=v.height),
                )
            )
        if (
            v.pix_fmt
            and not v.pix_fmt.startswith(("yuv420p", "yuvj420p", "nv12", "p010"))
            and not info.is_image
        ):
            notes.append(
                (
                    "info",
                    _tr(
                        "inspector.pixfmt",
                        "Pixel format {pf} is not 4:2:0; some players and web sites will "
                        "not play it until converted.",
                    ).format(pf=v.pix_fmt),
                )
            )
        if v.sar and v.sar not in ("1:1", "0:1", ""):
            notes.append(
                ("info", _tr("inspector.sar", "Non-square pixels (SAR {sar}).").format(sar=v.sar))
            )
    if not a and v and not info.is_image:
        notes.append(("info", _tr("inspector.no_audio", "No audio stream.")))
    if len(info.audio_streams) > 1:
        notes.append(
            (
                "info",
                _tr(
                    "inspector.multi_audio", "{n} audio tracks - most tools use the first one."
                ).format(n=len(info.audio_streams)),
            )
        )
    if not notes:
        notes.append(("ok", _tr("inspector.all_good", "Nothing unusual detected.")))
    return notes


def _seconds(value: object) -> float:
    """A time from ffprobe's JSON (a string or a number), or 0."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


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
            _tr("tab.media_inspector", "Media Inspector"),
            _tr(
                "inspector.subtitle",
                "Everything inside a media file - streams, codecs, colour, chapters, tags",
            ),
            icon="\U0001f50d",
        )
        row = tk.Frame(self, bg=CLR["bg"])
        row.pack(fill="x", padx=px(20), pady=px(12, 6))
        self.src_var = tk.StringVar()
        self.make_file_row(
            row, _tr("common.source_file", "Source file"), self.src_var, self._browse
        ).pack(side="left")
        tk.Button(
            row,
            text=_tr("inspector.copy_report", "Copy report"),
            relief="flat",
            bg=CLR["panel"],
            fg=CLR["fg"],
            cursor="hand2",
            command=self._copy_report,
        ).pack(side="left", padx=px(8, 0))
        tk.Button(
            row,
            text=_tr("inspector.export_json", "Export JSON…"),
            relief="flat",
            bg=CLR["panel"],
            fg=CLR["fg"],
            cursor="hand2",
            command=self._export_json,
        ).pack(side="left", padx=px(6, 0))

        top = tk.Frame(self, bg=CLR["bg"])
        top.pack(fill="x", padx=px(20), pady=px(4))
        self.preview = FramePreview(top, width=384, height=216)
        self.preview.pack(side="left", anchor="n")
        side = tk.Frame(top, bg=CLR["bg"])
        side.pack(side="left", fill="both", expand=True, padx=px(16, 0))
        self.summary_lbl = tk.Label(
            side,
            text=_tr("inspector.drop_hint", "Open or drop a video, audio or image file."),
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
            text=_tr("inspector.scrub", "Preview position"),
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

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=px(20), pady=px(8, 12))

        streams = tk.Frame(self.nb, bg=CLR["bg"])
        cols = ("idx", "type", "codec", "detail", "rate", "lang", "flags")
        heads = (
            "#",
            _tr("inspector.col_type", "Type"),
            _tr("inspector.col_codec", "Codec"),
            _tr("inspector.col_detail", "Details"),
            _tr("inspector.col_bitrate", "Bitrate"),
            _tr("inspector.col_lang", "Language / title"),
            _tr("inspector.col_flags", "Flags"),
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
        self.nb.add(streams, text=_tr("inspector.tab_streams", "Streams"))

        self.format_txt = self._text_page(_tr("inspector.tab_format", "Container"))
        self.chapters_txt = self._text_page(_tr("inspector.tab_chapters", "Chapters"))
        self.tags_txt = self._text_page(_tr("inspector.tab_tags", "Tags"))
        self.json_txt = self._text_page(_tr("inspector.tab_json", "Raw JSON"))

    def _text_page(self, title: str) -> tk.Text:
        frame = tk.Frame(self.nb, bg=CLR["bg"])
        txt = tk.Text(
            frame,
            wrap="none",
            bg=CLR["console_bg"],
            fg=CLR["console_fg"],
            font=(MONO_FONT, 9),
            relief="flat",
            padx=px(10),
            pady=px(8),
        )
        sb = ttk.Scrollbar(frame, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.nb.add(frame, text=title)
        return txt

    # ── actions ──────────────────────────────────────────────────────────────

    def _browse(self):
        path = dialogs.ask_open("any", title=_tr("inspector.open_title", "Inspect a media file"))
        if path:
            self.load_file(path)

    def load_file(self, path: str) -> bool:
        """Public entry point (also used by drag-and-drop and Open Recent).
        ffprobe runs in the background; the page fills in when it answers."""
        self.file_path = path
        self.src_var.set(path)
        self.info = None
        self.summary_lbl.config(text=_tr("inspector.reading", "Reading..."), fg=CLR["fgdim"])
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
            text=_tr("inspector.unreadable", "Not a readable media file:") + f" {exc}",
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
        self._fill_notes(diagnose(info))

        self.tree.delete(*self.tree.get_children())
        for s in info.streams:
            if s.type == "video":
                w, h = s.display_size
                detail = f"{w}×{h}  {_fps_text(s.fps)} fps  {s.pix_fmt or ''}  {s.bit_depth}-bit"
                if s.color_transfer or s.color_primaries:
                    detail += f"  {s.color_primaries or '?'}/{s.color_transfer or '?'}"
                if s.rotation:
                    detail += f"  rot {s.rotation}°"
                if s.attached_pic:
                    detail = f"cover art {w}×{h}"
            elif s.type == "audio":
                detail = (
                    f"{s.sample_rate} Hz  {s.channels} ch "
                    f"({s.channel_layout or '?'})  {s.sample_fmt}"
                )
            else:
                detail = s.codec_long
            flags = ", ".join(
                f
                for f, on in (
                    ("default", s.default),
                    ("forced", s.forced),
                    ("cover", s.attached_pic),
                )
                if on
            )
            lang = " / ".join(x for x in (s.language if s.language != "und" else "", s.title) if x)
            codec = s.codec + (f" ({s.profile})" if s.profile else "")
            self.tree.insert(
                "",
                "end",
                values=(s.index, s.type, codec, detail, _human_rate(s.bit_rate), lang, flags),
            )

        fmt_lines = [
            f"File:        {info.path}",
            f"Container:   {info.format_long} ({info.format_name})",
            f"Size:        {_human_bytes(info.size)}",
            f"Duration:    {ff.format_time(info.duration)}  ({info.duration:.3f} s)",
            f"Bitrate:     {_human_rate(info.bit_rate)}",
            f"Start time:  {info.start_time:.3f} s",
            f"Streams:     {len(info.video_streams)} video, {len(info.audio_streams)} audio, "
            f"{len(info.subtitle_streams)} subtitle, {len(info.streams)} total",
        ]
        self._set_text(self.format_txt, "\n".join(fmt_lines))

        if info.chapters:
            chap = []
            for i, c in enumerate(info.chapters, 1):
                start = _seconds(c.get("start_time"))
                end = _seconds(c.get("end_time"))
                title = (c.get("tags") or {}).get("title", "")
                chap.append(f"{i:>3}. {ff.format_time(start)} → {ff.format_time(end)}  {title}")
            self._set_text(self.chapters_txt, "\n".join(chap))
        else:
            self._set_text(self.chapters_txt, _tr("inspector.no_chapters", "No chapters."))

        tag_lines = [f"{k}: {v}" for k, v in sorted(info.tags.items())]
        for s in info.streams:
            for k, v in sorted(s.tags.items()):
                tag_lines.append(f"[stream {s.index}] {k}: {v}")
        self._set_text(self.tags_txt, "\n".join(tag_lines) or _tr("inspector.no_tags", "No tags."))
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
            title=_tr("inspector.export_json", "Export JSON…"),
        )
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(self.info.raw, fh, indent=2, ensure_ascii=False)
