"""
tabs/cutting/reverser.py  -  Video Reverser

Play a clip backwards, reverse just a section, or make boomerang / ping-pong
loops.  Works on clips of any length: FFmpeg's reverse filter has to hold
every frame in memory, so long clips are reversed in chunks sized to stay
within a memory budget, and the chunks are joined losslessly in reverse
order (audio is reversed the same way and encoded once at the end).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from core import ffmpeg as ff
from core.i18n import t
from core.ops import reverse as rv
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.kit.time_fields import RangePicker
from tabs.widgets import EncoderPanel, FileRow, SaveRow, scroll_body

MODES = [
    ("full", "Reverse the whole clip", "The entire video plays backwards."),
    (
        "range",
        "Reverse one section",
        "Only the chosen part plays backwards; the rest is untouched.",
    ),
    ("boomerang", "Boomerang", "Forwards, then backwards (Instagram style)."),
    ("pingpong", "Ping-pong loop", "Forwards and backwards, repeated."),
]


class ReverserTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self,
            t("tab.video_reverser"),
            "Backwards playback, boomerangs and ping-pong loops - for clips of any length.",
            icon="⏪",
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.info_lbl = tk.Label(body, text="", fg=CLR["fgdim"])
        self.info_lbl.pack(anchor="w", padx=px(24))

        mode_lf = tk.LabelFrame(body, text="  Effect  ", padx=px(12), pady=px(6))
        mode_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.mode_var = tk.StringVar(value="full")
        for key, label, desc in MODES:
            row = tk.Frame(mode_lf)
            row.pack(fill="x", pady=px(1))
            tk.Radiobutton(
                row,
                text=label,
                value=key,
                variable=self.mode_var,
                width=22,
                anchor="w",
                command=self._mode_changed,
            ).pack(side="left")
            tk.Label(row, text=desc, fg=CLR["fgdim"], font=(UI_FONT, 9)).pack(side="left")
        self.range_f = tk.Frame(mode_lf)
        tk.Label(self.range_f, text="Section from").pack(side="left")
        self.section = RangePicker(
            self.range_f, end_hint="(empty = end)", source=lambda: self.src_row.path
        )
        self.section.pack(side="left", padx=px(4))
        tk.Label(self.range_f, text="   Repeats:").pack(side="left")
        self.repeat_var = tk.StringVar(value="3")
        tk.Entry(self.range_f, textvariable=self.repeat_var, width=4).pack(side="left", padx=px(4))
        self.audio_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            mode_lf,
            text="Reverse the sound too (otherwise the output is silent)",
            variable=self.audio_var,
        ).pack(anchor="w", pady=px(4, 0))

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1"))
        self.encoder.pack(anchor="w")
        self.out_row = SaveRow(
            out_lf,
            t("common.output_file"),
            suggest=lambda: self.suggest_output(self.src_row.path, "reversed"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = self.make_render_btn(body, "⏪  CREATE", self._render, width=22)
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=6)
        self._mode_changed()

    def _mode_changed(self):
        if self.mode_var.get() == "full":
            self.range_f.pack_forget()
        else:
            self.range_f.pack(fill="x", pady=px(4, 0))

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        if self._info is not None and self._info.has_video:
            w, h = self._info.require_video().display_size
            self.info_lbl.config(text=f"{w}×{h}, {ff.format_time(self._info.duration)}")
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "reversed"))

    def _section(self, info: ff.MediaInfo) -> tuple[float, float, int] | None:
        """(start, end, repeats) from the fields; None after saying what's wrong."""
        dur, mode = info.duration, self.mode_var.get()
        try:
            start, end_or_none = self.section.span() if mode != "full" else (0.0, None)
            end = dur if end_or_none is None else end_or_none
            repeats = int(common.number(self.repeat_var.get() or "1", "Repeats", 1, 50))
            if not 0 <= start < end <= dur + 0.05:
                raise ValueError("The section must lie inside the clip, with 'from' before 'to'.")
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return None
        return start, min(end, dur), repeats

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        section = self._section(info) if info is not None else None
        if info is None or section is None:
            return
        start, end, repeats = section
        mode = self.mode_var.get()
        out = common.output_for(self, self.out_row, src, "reversed")
        if not out:
            return
        w, h = common.even_size(info)
        chunk = rv.chunk_seconds(w, h, info.require_video().fps or 30.0)
        pieces = rv.plan(mode, info.duration, start, end, repeats)
        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        total = sum(b - a for _k, a, b in pieces)
        job = rv.ReverseJob(
            src=src,
            pieces=pieces,
            chunk=chunk,
            norm=rv.normalise(w, h, venc),
            venc=venc,
            container=container,
            audio=self.audio_var.get() and info.has_audio,
            total=total,
        )

        n_chunks = sum(len(rv.reverse_chunks(a, b, chunk)) for k, a, b in pieces if k == "rev")
        self.log(
            self.console,
            f"{dict((k, lbl) for k, lbl, _d in MODES)[mode]} · "
            f"{ff.format_time(total)} · reversed in {n_chunks} chunk(s) of up to {chunk:.0f}s",
        )
        common.queue_job(
            self,
            common.Job(name="Reverser", out=out, work=job.run, duration=total, inputs=[src]),
            btn=self.btn_render,
            console=self.console,
        )
