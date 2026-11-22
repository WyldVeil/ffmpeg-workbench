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
from tkinter import messagebox

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, scroll_body

MODES = [("full", "Reverse the whole clip", "The entire video plays backwards."),
         ("range", "Reverse one section", "Only the chosen part plays backwards; the rest is untouched."),
         ("boomerang", "Boomerang", "Forwards, then backwards (Instagram style)."),
         ("pingpong", "Ping-pong loop", "Forwards and backwards, repeated.")]
MEMORY_BUDGET = 1.2e9          # bytes of decoded frames the reverse filter may hold


def chunk_seconds(width: int, height: int, fps: float, budget: float = MEMORY_BUDGET) -> float:
    frame_bytes = max(1, width * height) * 1.5 * 1.3        # yuv420p + filter overhead
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


class ReverserTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.video_reverser"), "Backwards playback, boomerangs and "
                         "ping-pong loops - for clips of any length.", icon="⏪")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), self.src_var,
                               on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.info_lbl = tk.Label(body, text="", fg=CLR["fgdim"])
        self.info_lbl.pack(anchor="w", padx=px(24))

        mode_lf = tk.LabelFrame(body, text="  Effect  ", padx=px(12), pady=px(6))
        mode_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.mode_var = tk.StringVar(value="full")
        for key, label, desc in MODES:
            row = tk.Frame(mode_lf)
            row.pack(fill="x", pady=px(1))
            tk.Radiobutton(row, text=label, value=key, variable=self.mode_var, width=22, anchor="w",
                           command=self._mode_changed).pack(side="left")
            tk.Label(row, text=desc, fg=CLR["fgdim"], font=(UI_FONT, 9)).pack(side="left")
        self.range_f = tk.Frame(mode_lf)
        tk.Label(self.range_f, text="Section from").pack(side="left")
        self.start_var = tk.StringVar(value="0")
        tk.Entry(self.range_f, textvariable=self.start_var, width=10).pack(side="left", padx=px(4))
        tk.Label(self.range_f, text="to").pack(side="left")
        self.end_var = tk.StringVar(value="")
        tk.Entry(self.range_f, textvariable=self.end_var, width=10).pack(side="left", padx=px(4))
        tk.Label(self.range_f, text="(empty = end)   Repeats:").pack(side="left")
        self.repeat_var = tk.StringVar(value="3")
        tk.Entry(self.range_f, textvariable=self.repeat_var, width=4).pack(side="left", padx=px(4))
        self.audio_var = tk.BooleanVar(value=True)
        tk.Checkbutton(mode_lf, text="Reverse the sound too (otherwise the output is silent)",
                       variable=self.audio_var).pack(anchor="w", pady=px(4, 0))

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.src_row.path, "reversed"))
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(body, text="⏪  CREATE", font=(UI_FONT, 12, "bold"),
                                    bg=CLR["green"], fg="white", width=22, cursor="hand2",
                                    command=self._render)
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
            w, h = self._info.video.display_size
            self.info_lbl.config(text=f"{w}×{h}, {ff.format_time(self._info.duration)}")
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "reversed"))

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        dur = info.duration
        mode = self.mode_var.get()
        try:
            start = ff.parse_time(self.start_var.get() or "0") if mode != "full" else 0.0
            end = ff.parse_time(self.end_var.get()) if mode != "full" and self.end_var.get().strip() else dur
            repeats = int(common.number(self.repeat_var.get() or "1", "Repeats", 1, 50))
            if not 0 <= start < end <= dur + 0.05:
                raise ValueError("The section must lie inside the clip, with 'from' before 'to'.")
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        end = min(end, dur)
        out = common.output_for(self, self.out_row, src, "reversed")
        if not out:
            return
        w, h = common.even_size(info)
        fps = info.video.fps or 30.0
        chunk = chunk_seconds(w, h, fps)
        pieces = plan(mode, dur, start, end, repeats)
        audio = self.audio_var.get() and info.has_audio
        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        norm = f"scale={w}:{h},setsar=1,format=yuv420p" if not venc.pix_fmt.endswith("10le") else \
            f"scale={w}:{h},setsar=1"
        total = sum(b - a for _k, a, b in pieces)

        def work(_p, _c, ctx):
            tmp = ctx.tempdir()
            files, cache, done, n = [], {}, 0.0, 0
            for kind, a, b in pieces:
                key = (kind, a, b)
                if key in cache:                       # ping-pong reuses rendered pieces
                    files += cache[key]
                    done += b - a
                    continue
                slices = reverse_chunks(a, b, chunk) if kind == "rev" else [(a, b)]
                made = []
                for sa, sb in slices:
                    n += 1
                    seg = os.path.join(tmp, f"piece{n:05d}.mkv")
                    vf = ("reverse," if kind == "rev" else "") + norm
                    cmd = [ff.ffmpeg_path(), "-y", *venc.pre_input, "-ss", ff.ts(sa), "-i", src,
                           "-t", ff.ts(sb - sa), "-map", "0:v:0", *venc.vf_args(vf)]
                    cmd += (["-map", "0:a:0", "-af", "areverse" if kind == "rev" else "anull",
                             "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"] if audio else ["-an"])
                    cmd += [*common.intermediate_args(venc), seg]
                    ctx.span(0.95 * done / total, 0.95 * (done + sb - sa) / total).run(cmd, duration=sb - sa)
                    done += sb - sa
                    made.append(seg)
                cache[key] = made
                files += made
            lst = fl.write_concat_list(files, os.path.join(tmp, "list.txt"))
            final = [ff.ffmpeg_path(), "-y", "-f", "concat", "-safe", "0", "-i", lst, "-map", "0:v:0",
                     "-c:v", "copy"]
            final += (["-map", "0:a:0", *enc.audio(enc.default_audio_for(container), container=container)]
                      if audio else ["-an"])
            final += common.final_copy_args(venc, container)
            ctx.span(0.95, 1.0).run(final + [ctx.output()], duration=total)
            return 0

        n_chunks = sum(len(reverse_chunks(a, b, chunk)) for k, a, b in pieces if k == "rev")
        self.log(self.console, f"{dict((k, lbl) for k, lbl, _d in MODES)[mode]} · "
                               f"{ff.format_time(total)} · reversed in {n_chunks} chunk(s) of up to {chunk:.0f}s")
        common.queue_job(self, name="Reverser", out=out, work=work, btn=self.btn_render,
                         btn_label="⏪  CREATE", console=self.console, duration=total, inputs=[src])
