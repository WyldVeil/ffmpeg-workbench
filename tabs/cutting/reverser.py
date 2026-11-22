"""
tabs/cutting/reverser.py  -  Video Reverser

Play a clip backwards, reverse just a section, or make boomerang / ping-pong
loops.  Works on clips of any length: FFmpeg's reverse filter has to hold
every frame in memory, so long clips are reversed in chunks sized to stay
within a memory budget, and the chunks are joined losslessly in reverse
order (audio is reversed the same way and encoded once at the end).
"""

from __future__ import annotations

import os
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl
from core.encoders import VideoEncode
from core.i18n import t
from core.queue_manager import TaskContext
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
MEMORY_BUDGET = 1.2e9  # bytes of decoded frames the reverse filter may hold


def chunk_seconds(width: int, height: int, fps: float, budget: float = MEMORY_BUDGET) -> float:
    frame_bytes = max(1, width * height) * 1.5 * 1.3  # yuv420p + filter overhead
    return max(1.0, budget / frame_bytes / max(fps, 1.0))


def reverse_chunks(start: float, end: float, chunk: float) -> list:
    """[(a, b)] slices of [start, end] in the order they must be played once
    each slice is reversed (latest slice first)."""
    out, b = [], end
    while b - start > 1e-6:
        a = max(start, b - chunk)
        out.append((a, b))
        b = a
    return out


def plan(mode: str, dur: float, start: float, end: float, repeats: int) -> list:
    """[('fwd'|'rev', a, b)] pieces of the output in order."""
    if mode == "full":
        return [("rev", 0.0, dur)]
    if mode == "range":
        pieces = []
        if start > 0.01:
            pieces.append(("fwd", 0.0, start))
        pieces.append(("rev", start, end))
        if end < dur - 0.01:
            pieces.append(("fwd", end, dur))
        return pieces
    if mode == "boomerang":
        return [("fwd", start, end), ("rev", start, end)]
    return [("fwd", start, end), ("rev", start, end)] * max(1, repeats)


@dataclass(frozen=True, kw_only=True)
class _ReverseJob:
    """Reversed parts are made in chunks small enough to reverse in memory
    (each chunk reversed, the chunks in reverse order); then all are joined."""

    src: str
    pieces: list[tuple[str, float, float]]  # kind ("fwd"/"rev"), from, to
    chunk: float
    norm: str
    venc: VideoEncode
    container: str
    audio: bool
    total: float

    def _slice_cmd(self, kind: str, sa: float, sb: float, seg: str) -> list[str]:
        vf = ("reverse," if kind == "rev" else "") + self.norm
        cmd = [
            ff.ffmpeg_path(),
            "-y",
            *self.venc.pre_input,
            "-ss",
            ff.ts(sa),
            "-i",
            self.src,
            "-t",
            ff.ts(sb - sa),
        ]
        cmd += ["-map", "0:v:0", *self.venc.vf_args(vf)]
        af = "areverse" if kind == "rev" else "anull"
        audio = ["-map", "0:a:0", "-af", af, "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"]
        return [
            *cmd,
            *(audio if self.audio else ["-an"]),
            *common.intermediate_args(self.venc),
            seg,
        ]

    def run(self, _p: object, _c: object, ctx: TaskContext) -> int:
        tmp = ctx.tempdir()
        files: list[str] = []
        cache: dict[tuple[str, float, float], list[str]] = {}
        done, n = 0.0, 0
        for kind, a, b in self.pieces:
            key = (kind, a, b)
            if key in cache:  # ping-pong reuses rendered pieces
                files += cache[key]
                done += b - a
                continue
            made: list[str] = []
            for sa, sb in reverse_chunks(a, b, self.chunk) if kind == "rev" else [(a, b)]:
                n += 1
                seg = os.path.join(tmp, f"piece{n:05d}.mkv")
                ctx.span(0.95 * done / self.total, 0.95 * (done + sb - sa) / self.total).run(
                    self._slice_cmd(kind, sa, sb, seg), duration=sb - sa
                )
                done += sb - sa
                made.append(seg)
            cache[key] = made
            files += made
        lst = fl.write_concat_list(files, os.path.join(tmp, "list.txt"))
        final = [
            ff.ffmpeg_path(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            lst,
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
        ]
        audio = [
            "-map",
            "0:a:0",
            *enc.audio(enc.default_audio_for(self.container), container=self.container),
        ]
        final += [
            *(audio if self.audio else ["-an"]),
            *common.final_copy_args(self.venc, self.container),
        ]
        ctx.span(0.95, 1.0).run([*final, ctx.output()], duration=self.total)
        return 0


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
        self.btn_render = tk.Button(
            body,
            text="⏪  CREATE",
            font=(UI_FONT, 12, "bold"),
            bg=CLR["green"],
            fg="white",
            width=22,
            cursor="hand2",
            command=self._render,
        )
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=6)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
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
        chunk = chunk_seconds(w, h, info.require_video().fps or 30.0)
        pieces = plan(mode, info.duration, start, end, repeats)
        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        norm = f"scale={w}:{h},setsar=1" + (
            "" if venc.pix_fmt.endswith("10le") else ",format=yuv420p"
        )
        total = sum(b - a for _k, a, b in pieces)
        job = _ReverseJob(
            src=src,
            pieces=pieces,
            chunk=chunk,
            norm=norm,
            venc=venc,
            container=container,
            audio=self.audio_var.get() and info.has_audio,
            total=total,
        )

        n_chunks = sum(len(reverse_chunks(a, b, chunk)) for k, a, b in pieces if k == "rev")
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
