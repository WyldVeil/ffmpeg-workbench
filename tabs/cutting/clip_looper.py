"""
tabs/cutting/clip_looper.py  -  Clip Looper

Repeat a moment: plain loops, DJ-style stutters, loops that speed up each
time, or echoes that fade away - with the rest of the clip kept around it
if you like.  Each distinct loop is rendered once, then everything is
joined losslessly (audio encoded once at the end), so there is no drift or
quality loss however many repeats.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, FramePreview, scroll_body

MODES = [("simple", "Repeat", "Play the section several times in a row."),
         ("stutter", "Stutter", "Rapid repeats of a tiny slice (DJ / glitch style)."),
         ("accel", "Speed up each time", "Every repeat plays faster than the last."),
         ("echo", "Echo", "Every repeat is darker and quieter, fading away.")]
MAX_SPEED = 16.0


def loop_units(mode: str, count: int, factor: float) -> list:
    """(speed, level) for each repeat; level 1 = untouched picture/sound."""
    units = []
    for k in range(count):
        if mode == "accel":
            speed = min(factor ** k, MAX_SPEED)
            units.append((speed, 1.0))
        elif mode == "echo":
            units.append((1.0, max(0.15, 0.7 ** k)))
        else:
            units.append((1.0, 1.0))
    return units


def _tempo(speed: float) -> str:
    parts, rem = [], speed
    while rem > 2.0:
        parts.append("atempo=2.0")
        rem /= 2.0
    parts.append(f"atempo={rem:.5f}")
    return ",".join(parts)


class ClipLooperTab(BaseTab):
    # i / o (and the buttons): the section starts / ends at the frame shown
    def mark_in(self):
        self.start_var.set(ff.format_time(float(self.pos_var.get())))

    def mark_out(self):
        self.end_var.set(ff.format_time(float(self.pos_var.get())))

    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.clip_looper"), "Repeat, stutter or echo a moment.", icon="🔁")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), self.src_var,
                               on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        pv = tk.Frame(body)
        pv.pack(pady=px(4))
        self.preview = FramePreview(pv, 448, 252)
        self.preview.pack()
        self.pos_var = tk.DoubleVar(value=0)
        bar = tk.Frame(pv)
        bar.pack(fill="x")
        self.slider = ttk.Scale(bar, from_=0, to=1, variable=self.pos_var, length=px(300),
                                command=lambda _v: self.preview.show(float(self.pos_var.get())))
        self.slider.pack(side="left", fill="x", expand=True)
        framestep.slider(self.slider, self.preview)      # , and . step a frame
        tk.Button(bar, text="Set start", relief="flat", command=self.mark_in).pack(side="left")
        tk.Button(bar, text="Set end", relief="flat", command=self.mark_out).pack(side="left")

        opt = tk.LabelFrame(body, text="  Loop  ", padx=px(12), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.mode_var = tk.StringVar(value="simple")
        for key, label, desc in MODES:
            row = tk.Frame(opt)
            row.pack(fill="x")
            tk.Radiobutton(row, text=label, value=key, variable=self.mode_var, width=18, anchor="w",
                           command=self._mode_changed).pack(side="left")
            tk.Label(row, text=desc, fg=CLR["fgdim"], font=(UI_FONT, 9)).pack(side="left")
        r = tk.Frame(opt)
        r.pack(fill="x", pady=px(6, 2))
        tk.Label(r, text="Section from").pack(side="left")
        self.start_var = tk.StringVar(value="0")
        tk.Entry(r, textvariable=self.start_var, width=11).pack(side="left", padx=px(4))
        self.end_lbl = tk.Label(r, text="to")
        self.end_lbl.pack(side="left")
        self.end_var = tk.StringVar(value="2")
        self.end_entry = tk.Entry(r, textvariable=self.end_var, width=11)
        self.end_entry.pack(side="left", padx=px(4))
        tk.Label(r, text="Repeats:").pack(side="left", padx=px(10, 2))
        self.count_var = tk.StringVar(value="4")
        tk.Entry(r, textvariable=self.count_var, width=4).pack(side="left")
        r2 = tk.Frame(opt)
        r2.pack(fill="x", pady=px(2))
        tk.Label(r2, text="Stutter slice (s):").pack(side="left")
        self.slice_var = tk.StringVar(value="0.15")
        tk.Entry(r2, textvariable=self.slice_var, width=6).pack(side="left", padx=px(4))
        tk.Label(r2, text="Speed-up per repeat:").pack(side="left", padx=px(10, 2))
        self.factor_var = tk.StringVar(value="1.3")
        tk.Entry(r2, textvariable=self.factor_var, width=6).pack(side="left")
        self.context_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opt, text="Keep the rest of the clip before and after the loop",
                       variable=self.context_var).pack(anchor="w")

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.src_row.path, "looped"))
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(body, text="🔁  CREATE", font=(UI_FONT, 12, "bold"), bg=CLR["green"],
                                    fg="white", width=22, cursor="hand2", command=self._render)
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self._mode_changed()

    def _mode_changed(self):
        stutter = self.mode_var.get() == "stutter"
        self.end_entry.config(state="disabled" if stutter else "normal")

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        dur = self._info.duration if self._info else 0.0
        self.slider.configure(to=max(dur, 0.01))
        self.preview.load(path, dur, 0.0)
        self.end_var.set(ff.format_time(min(2.0, dur)))
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "looped"))

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        dur = info.duration
        mode = self.mode_var.get()
        fps = info.video.fps or 30.0
        try:
            start = ff.parse_time(self.start_var.get() or "0")
            if mode == "stutter":
                end = start + common.number(self.slice_var.get(), "Stutter slice", 1.0 / fps, 5)
            else:
                end = ff.parse_time(self.end_var.get())
            count = int(common.number(self.count_var.get(), "Repeats", 2, 200))
            factor = common.number(self.factor_var.get() or "1.3", "Speed-up", 1.01, 4)
            if not 0 <= start < end <= dur + 0.05:
                raise ValueError("The section must lie inside the clip, with 'from' before 'to'.")
            if end - start < 1.0 / fps:
                raise ValueError("The section is shorter than one frame.")
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        end = min(end, dur)
        out = common.output_for(self, self.out_row, src, "looped")
        if not out:
            return
        units = loop_units(mode, count, factor)
        if mode == "accel":                        # stop once a repeat is shorter than a frame
            units = [u for u in units if (end - start) / u[0] >= 1.0 / fps] or units[:1]
        keep = self.context_var.get()
        audio = info.has_audio
        w, h = common.even_size(info)
        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        fps_str = common.frame_rate(info)
        base = f"scale={w}:{h},setsar=1,fps={fps_str},format=yuv420p"
        pieces = ([("plain", 0.0, start, 1.0, 1.0)] if keep and start > 0.01 else [])
        pieces += [("loop", start, end, sp, lv) for sp, lv in units]
        if keep and end < dur - 0.01:
            pieces.append(("plain", end, dur, 1.0, 1.0))
        total = sum((b - a) / sp for _k, a, b, sp, _lv in pieces)

        def work(_p, _c, ctx):
            tmp = ctx.tempdir()
            made, files, done = {}, [], 0.0
            for i, (kind, a, b, sp, lv) in enumerate(pieces):
                key = (a, b, sp, lv)
                length = (b - a) / sp
                if key not in made:
                    seg = os.path.join(tmp, f"u{len(made):04d}.mkv")
                    vf = base + (f",setpts=PTS/{sp:.5f}" if sp != 1 else "") + \
                        (f",lutyuv=y=val*{lv:.3f}" if lv < 1 else "")
                    af = "anull" + (f",{_tempo(sp)}" if sp != 1 else "") + \
                        (f",volume={lv:.3f}" if lv < 1 else "")
                    # -t before -i limits the SOURCE section (speed changes come after)
                    cmd = [ff.ffmpeg_path(), "-y", *venc.pre_input, "-ss", ff.ts(a), "-t", ff.ts(b - a),
                           "-i", src, "-map", "0:v:0", *venc.vf_args(vf)]
                    cmd += (["-map", "0:a:0", "-af", af, "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"]
                            if audio else ["-an"])
                    cmd += [*common.intermediate_args(venc), seg]
                    ctx.span(0.9 * done / total, 0.9 * (done + length) / total).run(cmd, duration=length)
                    made[key] = seg
                done += length
                files.append(made[key])
            lst = fl.write_concat_list(files, os.path.join(tmp, "list.txt"))
            final = [ff.ffmpeg_path(), "-y", "-f", "concat", "-safe", "0", "-i", lst,
                     "-map", "0:v:0", "-c:v", "copy"]
            final += (["-map", "0:a:0", *enc.audio(enc.default_audio_for(container), container=container)]
                      if audio else ["-an"])
            ctx.span(0.9, 1.0).run(final + common.final_copy_args(venc, container) + [ctx.output()],
                                   duration=total)
            return 0

        self.log(self.console, f"{len(units)} repeats of {ff.format_time(end - start)} → "
                               f"{ff.format_time(total)} total")
        common.queue_job(self, name="Clip Looper", out=out, work=work, btn=self.btn_render,
                         btn_label="🔁  CREATE", console=self.console, duration=total, inputs=[src])
