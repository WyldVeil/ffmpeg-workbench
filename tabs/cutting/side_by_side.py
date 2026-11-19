"""
tabs/cutting/side_by_side.py  -  Side-by-Side

Comparison and reaction layouts from 2-4 clips: side by side, stacked,
2x2 grid, or a before/after wipe (fixed or sweeping).  Each clip is fitted
(or cropped) into its cell, optional labels and divider, audio from either
clip or a mix, and the length follows the shortest or longest clip - with
shorter clips looping or holding their last frame.
"""
from __future__ import annotations

import os
import shutil
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk

from core import ass
from core import encoders as enc
from core import ffmpeg as ff
from core.i18n import t
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, scroll_body

LAYOUTS = {"side": "Side by side", "stack": "Stacked (top / bottom)", "grid": "2×2 grid",
           "wipe": "Before / after wipe"}
SIZES = {"1920×1080": (1920, 1080), "3840×2160": (3840, 2160), "1280×720": (1280, 720),
         "1080×1920 (vertical)": (1080, 1920), "1080×1080": (1080, 1080)}
AUDIO = {"0": "Audio from clip 1", "1": "Audio from clip 2", "mix": "Mix all clips",
         "none": "No audio"}
LENGTH = {"shortest": "Stop when the shortest clip ends",
          "loop": "Run to the longest - loop shorter clips",
          "hold": "Run to the longest - hold shorter clips' last frame"}


@dataclass
class Clip:
    path: str
    duration: float
    has_audio: bool
    label: str = ""
    speed: float = 1.0


def cells(layout: str, n: int, w: int, h: int) -> list:
    """(x, y, cw, ch) of every cell."""
    if layout == "side":
        cw = w // 2 // 2 * 2
        return [(0, 0, cw, h), (cw, 0, w - cw, h)]
    if layout == "stack":
        ch = h // 2 // 2 * 2
        return [(0, 0, w, ch), (0, ch, w, h - ch)]
    if layout == "grid":
        cw, ch = w // 2 // 2 * 2, h // 2 // 2 * 2
        return [(0, 0, cw, ch), (cw, 0, w - cw, ch), (0, ch, cw, h - ch), (cw, ch, w - cw, h - ch)]
    return [(0, 0, w, h), (0, 0, w, h)]


def build_graph(clips, layout, w, h, fps, *, fill=False, audio="0", length="shortest",
                wipe_pos=0.5, wipe_period=0.0, divider=True, labels_ass=None) -> tuple:
    """(filter_complex, total seconds, has_audio_out)."""
    n = len(clips)
    durs = [c.duration / c.speed for c in clips]
    total = min(durs) if length == "shortest" else max(durs)
    parts = []
    boxes = cells(layout, n, w, h)
    for i, c in enumerate(clips):
        _x, _y, cw, chh = boxes[min(i, len(boxes) - 1)]
        fit = (f"scale={cw}:{chh}:force_original_aspect_ratio=increase,crop={cw}:{chh}" if fill else
               f"scale={cw}:{chh}:force_original_aspect_ratio=decrease,"
               f"pad={cw}:{chh}:(ow-iw)/2:(oh-ih)/2:color=black")
        speed = f"setpts=(PTS-STARTPTS)/{c.speed:.4f}" if abs(c.speed - 1) > 1e-6 else "setpts=PTS-STARTPTS"
        hold = ",tpad=stop_mode=clone:stop=-1" if length == "hold" else ""
        parts.append(f"[{i}:v]{speed},{fit},setsar=1,fps={fps},format=yuv420p{hold}[v{i}]")
    if layout == "wipe":
        p = min(max(wipe_pos, 0.02), 0.98)
        line = f",drawbox=x={w - 2}:y=0:w=4:h={h}:color=white@0.9:t=fill" if divider else ""
        if wipe_period > 0:
            cx = f"{w}*({p:.3f}+min({p:.3f},{1 - p:.3f})*0.9*sin(2*PI*t/{wipe_period:.3f}))"
        else:
            cx = f"{round(w * p)}"
        parts.append(f"[v1]format=yuva420p,pad={2 * w}:{h}:{w}:0:color=black@0{line},"
                     f"crop={w}:{h}:x='{w}-({cx})':y=0[bw]")
        parts.append("[v0][bw]overlay=0:0:shortest=1,format=yuv420p[st]")
    else:
        if layout == "grid":
            ins = [f"[v{i}]" for i in range(n)]
            while len(ins) < 4:
                k = len(ins)
                _x, _y, cw, chh = boxes[k]
                parts.append(f"color=c=black:s={cw}x{chh}:r={fps}[blank{k}]")
                ins.append(f"[blank{k}]")
            parts.append("".join(ins) + "xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0:shortest=1[st0]")
        else:
            stack = "hstack" if layout == "side" else "vstack"
            parts.append(f"[v0][v1]{stack}=inputs=2:shortest=1[st0]")
        chain = []
        if divider:
            if layout in ("side", "grid"):
                chain.append(f"drawbox=x={boxes[0][2] - 2}:y=0:w=4:h={h}:color=white@0.8:t=fill")
            if layout in ("stack", "grid"):
                chain.append(f"drawbox=x=0:y={boxes[0][3] - 2}:w={w}:h=4:color=white@0.8:t=fill")
        parts.append(f"[st0]{','.join(chain) if chain else 'null'}[st]")
    vlast = "[st]"
    if labels_ass:
        parts.append(f"{vlast}{ass.subtitles_filter(labels_ass)}[lab]")
        vlast = "[lab]"
    parts.append(f"{vlast}trim=duration={total:.3f}[v]")

    srcs = [i for i, c in enumerate(clips) if c.has_audio]
    want = srcs if audio == "mix" else [int(audio)] if audio.isdigit() and int(audio) in srcs else []
    for i in want:
        c = clips[i]
        tempo = "" if abs(c.speed - 1) < 1e-6 else f",{_atempo(c.speed)}"
        pad = ",apad" if length != "shortest" else ""
        parts.append(f"[{i}:a:0]asetpts=PTS-STARTPTS{tempo},aresample=48000{pad}[a{i}]")
    if len(want) > 1:
        parts.append("".join(f"[a{i}]" for i in want) +
                     f"amix=inputs={len(want)}:duration=longest:normalize=0,"
                     f"atrim=duration={total:.3f}[a]")
    elif want:
        parts.append(f"[a{want[0]}]atrim=duration={total:.3f}[a]")
    return ";".join(parts), total, bool(want)


def _atempo(speed: float) -> str:
    parts, rem = [], speed
    while rem < 0.5:
        parts.append("atempo=0.5")
        rem /= 0.5
    parts.append(f"atempo={rem:.4f}")
    return ",".join(parts)


def labels_script(layout, labels, w, h, total, font="Montserrat") -> ass.Script:
    sc = ass.Script(w, h)
    u = min(w, h) / 1080
    sc.style(ass.Style("Lab", font=font, size=round(40 * u), bold=True, outline=0, shadow=0,
                       border_style=3, back_colour="#000000", back_alpha=0.45, alignment=7))
    boxes = cells(layout, len(labels), w, h)
    for i, text in enumerate(labels):
        if not text.strip():
            continue
        if layout == "wipe":
            x = 30 * u if i == 0 else w - 30 * u
            an = 7 if i == 0 else 9
            y = 30 * u
        else:
            bx, by, _cw, _ch = boxes[min(i, len(boxes) - 1)]
            x, y, an = bx + 24 * u, by + 24 * u, 7
        sc.add(0, total + 1, f"{{\\an{an}\\pos({x:.0f},{y:.0f})}} {ass.escape(text)} ", "Lab")
    return sc


class SideBySideTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.side_by_side"), "Comparison, reaction and before/after "
                         "layouts from two to four clips.", icon="◫")
        body = scroll_body(self)
        clips = tk.LabelFrame(body, text="  Clips  ", padx=12, pady=6)
        clips.pack(fill="x", padx=20, pady=(10, 4))
        self.rows, self.label_vars, self.speed_vars = [], [], []
        for i in range(4):
            line = tk.Frame(clips)
            line.pack(fill="x", pady=2)
            fr = FileRow(line, f"Clip {i + 1}{'' if i < 2 else ' (grid)'}:", self.src_var if i == 0 else None,
                         label_width=12, width=40)
            fr.pack(side="left", fill="x", expand=True)
            lv = tk.StringVar(value=("Before", "After", "", "")[i])
            tk.Label(line, text="Label:").pack(side="left", padx=(6, 2))
            tk.Entry(line, textvariable=lv, width=12).pack(side="left")
            sv = tk.StringVar(value="1.0")
            tk.Label(line, text="Speed:").pack(side="left", padx=(6, 2))
            ttk.Combobox(line, textvariable=sv, values=["0.25", "0.5", "0.75", "1.0", "1.25", "1.5", "2.0"],
                         width=5).pack(side="left")
            self.rows.append(fr)
            self.label_vars.append(lv)
            self.speed_vars.append(sv)

        lay = tk.LabelFrame(body, text="  Layout  ", padx=12, pady=6)
        lay.pack(fill="x", padx=20, pady=4)

        def row(r, label, *widgets):
            tk.Label(lay, text=label, width=14, anchor="w").grid(row=r, column=0, sticky="w", pady=2)
            for i, w in enumerate(widgets):
                w.grid(row=r, column=1 + i, sticky="w", padx=(0, 8), pady=2)

        self.layout_var = tk.StringVar(value=LAYOUTS["side"])
        self.size_var = tk.StringVar(value="1920×1080")
        row(0, "Layout:", ttk.Combobox(lay, textvariable=self.layout_var, values=list(LAYOUTS.values()),
                                       state="readonly", width=24),
            ttk.Combobox(lay, textvariable=self.size_var, values=list(SIZES), state="readonly", width=18))
        self.fill_var = tk.BooleanVar(value=False)
        self.divider_var = tk.BooleanVar(value=True)
        self.labels_on = tk.BooleanVar(value=True)
        row(1, "Framing:", tk.Checkbutton(lay, text="Fill cells (crop instead of black bars)",
                                          variable=self.fill_var),
            tk.Checkbutton(lay, text="Divider line", variable=self.divider_var),
            tk.Checkbutton(lay, text="Labels", variable=self.labels_on))
        self.wipe_pos = tk.DoubleVar(value=0.5)
        self.wipe_period = tk.StringVar(value="0")
        row(2, "Wipe:", tk.Scale(lay, variable=self.wipe_pos, from_=0.05, to=0.95, resolution=0.05,
                                 orient="horizontal", length=160),
            tk.Label(lay, text="Sweep every (s, 0 = fixed):"), tk.Entry(lay, textvariable=self.wipe_period,
                                                                        width=5))
        self.audio_var = tk.StringVar(value=AUDIO["0"])
        self.length_var = tk.StringVar(value=LENGTH["shortest"])
        row(3, "Audio:", ttk.Combobox(lay, textvariable=self.audio_var, values=list(AUDIO.values()),
                                      state="readonly", width=24))
        row(4, "Length:", ttk.Combobox(lay, textvariable=self.length_var, values=list(LENGTH.values()),
                                       state="readonly", width=44))

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=12, pady=8)
        out_lf.pack(fill="x", padx=20, pady=6)
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.rows[0].path, "side_by_side"))
        self.out_row.pack(fill="x", pady=(8, 0))
        self.btn_render = tk.Button(body, text="◫  CREATE", font=(UI_FONT, 12, "bold"), bg=CLR["green"],
                                    fg="white", width=22, cursor="hand2", command=self._render)
        self.btn_render.pack(pady=8)
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def _key(self, table, var, default):
        return next((k for k, v in table.items() if v == var.get()), default)

    def _render(self):
        layout = self._key(LAYOUTS, self.layout_var, "side")
        want = 4 if layout == "grid" else 2
        clips, labels = [], []
        for i, fr in enumerate(self.rows[:want]):
            path = fr.path
            if not path and layout == "grid" and i >= 2:
                continue
            info = common.check_source(self, path, what=f"Clip {i + 1}")
            if info is None:
                return
            try:
                speed = common.number(self.speed_vars[i].get(), f"Clip {i + 1} speed", 0.1, 4)
            except ValueError as exc:
                messagebox.showwarning(t("common.warning"), str(exc), parent=self)
                return
            clips.append(Clip(path, info.duration, info.has_audio, self.label_vars[i].get(), speed))
            labels.append(self.label_vars[i].get())
        try:
            period = common.number(self.wipe_period.get() or "0", "Sweep", 0, 600)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h = SIZES.get(self.size_var.get(), (1920, 1080))
        first = ff.probe(clips[0].path)
        fps = common.frame_rate(first)
        out = common.output_for(self, self.out_row, clips[0].path, "side_by_side",
                                inputs=[c.path for c in clips[1:]])
        if not out:
            return
        length = self._key(LENGTH, self.length_var, "shortest")
        longest = max(c.duration / c.speed for c in clips)
        from core.paths import temp_dir
        work = temp_dir("sbs_")
        labels_path = None
        if self.labels_on.get() and any(lbl.strip() for lbl in labels):
            labels_path = labels_script(layout, labels, w, h, longest).write(
                os.path.join(work, "labels.ass"))
        graph, total, has_audio = build_graph(
            clips, layout, w, h, fps, fill=self.fill_var.get(),
            audio=self._key(AUDIO, self.audio_var, "0"), length=length,
            wipe_pos=float(self.wipe_pos.get()), wipe_period=period,
            divider=self.divider_var.get(), labels_ass=labels_path)
        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        cmd = [ff.ffmpeg_path(), "-y", *venc.pre_input]
        for c in clips:
            if length == "loop" and c.duration / c.speed < longest - 0.05:
                cmd += ["-stream_loop", "-1"]
            cmd += ["-i", c.path]
        vmap = "[v]"
        if venc.filter_suffix:
            graph += f";[v]{venc.filter_suffix}[venc]"
            vmap = "[venc]"
        amap = (["-map", "[a]", *enc.audio(enc.default_audio_for(container), container=container)]
                if has_audio else ["-an"])
        cmd += ["-filter_complex", graph, "-map", vmap, *amap, *venc.args, "-t", f"{total:.3f}", out]

        def done(rc):
            shutil.rmtree(work, ignore_errors=True)
            self.show_result(rc, out)

        self.log(self.console, f"{LAYOUTS[layout]} · {w}×{h} · {ff.format_time(total)}")
        self.run_ffmpeg(cmd, self.console, on_done=done, btn=self.btn_render, btn_label="◫  CREATE",
                        duration=total, output=out, inputs=[c.path for c in clips], name="Side-by-Side")
