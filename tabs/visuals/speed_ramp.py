"""
tabs/visuals/speed_ramp.py  -  Speed Ramper

* Constant speed: 0.1x - 16x with pitch-preserving audio (or natural
  chipmunk / slow-voice pitch, or muted), and a choice of how slow-motion
  frames are made (duplicate, blend, or optical flow).
* Speed ramp: a smooth speed curve through any number of points, done in a
  single pass.  Video timestamps follow the exact integral of 1/speed and
  audio tempo follows the curve ten times per second, so picture and sound
  stay in sync.  Before the first point and after the last one the speed
  holds.
"""
from __future__ import annotations

import math
import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import encoders as enc
from core import ffmpeg as ff
from core.i18n import t
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs import common
from tabs.widgets import EncoderPanel, FileRow, scroll_body

MIN_SPEED, MAX_SPEED = 0.1, 16.0
SLOWMO = {
    "dup": "Duplicate frames (fast)",
    "blend": "Blend frames (smoother)",
    "flow": "Optical flow (smoothest, very slow)",
}
AUDIO = {
    "tempo": "Keep pitch",
    "pitch": "Natural pitch (chipmunk / deep)",
    "mute": "Mute",
}
PRESETS = {
    "Speed up in the middle": [(0.0, 1.0), (0.35, 1.0), (0.45, 4.0), (0.55, 4.0), (0.65, 1.0)],
    "Slow-mo in the middle": [(0.0, 1.0), (0.4, 1.0), (0.45, 0.3), (0.6, 0.3), (0.65, 1.0)],
    "Ramp up (1× → 4×)": [(0.0, 1.0), (0.8, 4.0)],
    "Ramp down (4× → 1×)": [(0.0, 4.0), (0.8, 1.0)],
    "Hit the beat (fast, slow, fast)": [(0.0, 3.0), (0.3, 3.0), (0.35, 0.4), (0.55, 0.4),
                                        (0.6, 3.0)],
}


# ── Speed curve maths (pure, tested) ─────────────────────────────────────────

def normalise_points(points, duration):
    """Sorted (time, speed) points clipped to [0, duration], with the speed
    held before the first and after the last point."""
    pts = sorted((max(0.0, min(float(tm), duration)), float(sp)) for tm, sp in points)
    merged = []
    for tm, sp in pts:
        if merged and abs(merged[-1][0] - tm) < 1e-6:
            merged[-1] = (tm, sp)
        else:
            merged.append((tm, sp))
    if not merged:
        return [(0.0, 1.0), (duration, 1.0)]
    if merged[0][0] > 0:
        merged.insert(0, (0.0, merged[0][1]))
    if merged[-1][0] < duration:
        merged.append((duration, merged[-1][1]))
    return merged


def speed_at(points, at):
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t0 <= at <= t1:
            return v0 if t1 - t0 < 1e-9 else v0 + (v1 - v0) * (at - t0) / (t1 - t0)
    return points[-1][1]


def segments(points):
    """[(t0, t1, v0, v1, out_start, out_len)] for consecutive points."""
    out, acc = [], 0.0
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t1 - t0 < 1e-9:
            continue
        if abs(v1 - v0) < 1e-9:
            seg = (t1 - t0) / v0
        else:
            k = (v1 - v0) / (t1 - t0)
            seg = math.log(v1 / v0) / k
        out.append((t0, t1, v0, v1, acc, seg))
        acc += seg
    return out


def output_duration(points) -> float:
    segs = segments(points)
    return segs[-1][4] + segs[-1][5] if segs else 0.0


def setpts_expr(points) -> str:
    """Output time (seconds) as an FFmpeg expression of the source time T."""
    pieces = []
    for t0, t1, v0, v1, start, _seg in segments(points):
        if abs(v1 - v0) < 1e-9:
            e = f"({start:.6f}+(T-{t0:.6f})/{v0:.6f})"
        else:
            k = (v1 - v0) / (t1 - t0)
            e = f"({start:.6f}+log(({v0:.6f}+{k:.6f}*(T-{t0:.6f}))/{v0:.6f})/{k:.6f})"
        pieces.append((t1, e))
    expr = pieces[-1][1]
    for t1, e in reversed(pieces[:-1]):
        expr = f"if(lt(T,{t1:.6f}),{e},{expr})"
    return expr


_ATEMPO_STAGES = ("a", "b", "c", "d")     # 4 chained atempo: 0.0625x - 10^8x


def tempo_commands(points, engine: str = "rubberband", step: float = 0.1) -> str:
    """asendcmd script that makes the audio tempo follow the speed curve.

    *engine* ``rubberband`` drives one rubberband filter (0.01x - 100x);
    ``atempo`` drives four named atempo filters with the 4th root of the
    speed each (atempo alone only accepts 0.5x - 100x)."""
    def line(at, speed):
        if engine == "rubberband":
            return f"{at:.3f} rubberband tempo {speed:.6f};"
        r = speed ** 0.25
        return f"{at:.3f} " + ", ".join(f"atempo@{n} tempo {r:.6f}" for n in _ATEMPO_STAGES) + ";"

    lines = []
    for t0, t1, v0, v1, _s, _l in segments(points):
        if abs(v1 - v0) < 1e-9:
            lines.append(line(t0, v0))
            continue
        k = (v1 - v0) / (t1 - t0)
        tm = t0
        while tm < t1 - 1e-9:
            end = min(tm + step, t1)
            va, vb = v0 + k * (tm - t0), v0 + k * (end - t0)
            # constant speed with the same output length as this slice of the curve
            lines.append(line(tm, (end - tm) * k / math.log(vb / va)))
            tm = end
    return "\n".join(lines) + "\n"


def ramp_audio_chain(points, engine: str = "rubberband") -> str:
    """Audio filters (after asendcmd) for a speed curve starting at points[0]."""
    v0 = points[0][1]
    if engine == "rubberband":
        return f"asendcmd=f=tempo.cmd,rubberband=tempo={v0:.6f}"
    r = v0 ** 0.25
    return "asendcmd=f=tempo.cmd," + ",".join(f"atempo@{n}={r:.6f}" for n in _ATEMPO_STAGES)


def tempo_engine() -> str:
    return "rubberband" if ff.has_filter("rubberband") else "atempo"


def atempo_chain(speed: float, engine: str = "atempo") -> str:
    """Pitch-preserving constant tempo for any speed."""
    if engine == "rubberband" and 0.01 <= speed <= 100:
        return f"rubberband=tempo={speed:.6f}"
    parts, rem = [], speed
    while rem < 0.5:
        parts.append("atempo=0.5")
        rem /= 0.5
    while rem > 100.0:
        parts.append("atempo=100")
        rem /= 100.0
    parts.append(f"atempo={rem:.6f}")
    return ",".join(parts)


def frame_filter(mode: str, fps: str) -> str:
    if mode == "blend":
        return f"framerate=fps={fps}"
    if mode == "flow":
        return f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
    return f"fps={fps}"


# ── Tab ──────────────────────────────────────────────────────────────────────

class SpeedRamperTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self.duration = 0.0
        self.ramp_points: list = []
        self._job = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.speed_ramper"), t("speed_ramp.subtitle"), icon="⚡")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), self.src_var,
                               on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=20, pady=(10, 4))

        mode_lf = tk.LabelFrame(body, text=f"  {t('speed_ramp.speed_mode_section')}  ", padx=12, pady=6)
        mode_lf.pack(fill="x", padx=20, pady=4)
        self.mode_var = tk.StringVar(value="constant")
        tk.Radiobutton(mode_lf, text=t("speed_ramp.constant_option"), value="constant",
                       variable=self.mode_var, command=self._on_mode).pack(side="left", padx=12)
        tk.Radiobutton(mode_lf, text="Speed ramp (smooth curve)", value="ramp",
                       variable=self.mode_var, command=self._on_mode).pack(side="left", padx=12)

        # constant
        self.const_f = tk.Frame(body)
        tk.Label(self.const_f, text=t("speed_ramp.speed_multiplier_label"),
                 font=(UI_FONT, 11, "bold")).pack(side="left")
        self.speed_var = tk.DoubleVar(value=2.0)
        tk.Scale(self.const_f, variable=self.speed_var, from_=MIN_SPEED, to=MAX_SPEED,
                 resolution=0.05, orient="horizontal", length=300,
                 command=lambda _v: self._schedule()).pack(side="left", padx=8)
        quick = tk.Frame(self.const_f)
        quick.pack(side="left", padx=10)
        for val, lbl in [(0.25, "¼×"), (0.5, "½×"), (0.75, "¾×"), (1.5, "1.5×"), (2.0, "2×"),
                         (4.0, "4×"), (8.0, "8×")]:
            tk.Button(quick, text=lbl, width=4, relief="flat",
                      command=lambda v=val: (self.speed_var.set(v), self._schedule())
                      ).pack(side="left", padx=1)

        # ramp
        self.ramp_f = tk.LabelFrame(body, text=f"  {t('speed_ramp.ramp_points_section')}  ",
                                    padx=10, pady=8)
        top = tk.Frame(self.ramp_f)
        top.pack(fill="x")
        tk.Label(top, text="Preset:").pack(side="left")
        self.preset_var = tk.StringVar(value="")
        pb = ttk.Combobox(top, textvariable=self.preset_var, values=list(PRESETS),
                          state="readonly", width=32)
        pb.pack(side="left", padx=6)
        pb.bind("<<ComboboxSelected>>", lambda _e: self._apply_preset())
        tk.Button(top, text=t("speed_ramp.add_point_button"), relief="flat",
                  command=lambda: self._add_point()).pack(side="left", padx=8)
        self.graph = tk.Canvas(self.ramp_f, height=130, bg=CLR["console_bg"], highlightthickness=0)
        self.graph.pack(fill="x", pady=6)
        self.graph.bind("<Configure>", lambda _e: self._draw())
        hdr = tk.Frame(self.ramp_f)
        hdr.pack(fill="x")
        for text, w in (("#", 3), ("Time (s or m:ss)", 16), ("Speed (×)", 10)):
            tk.Label(hdr, text=text, width=w, anchor="w", font=(UI_FONT, 9, "bold")).pack(side="left", padx=2)
        self.rows_f = tk.Frame(self.ramp_f)
        self.rows_f.pack(fill="x")

        opt = tk.LabelFrame(body, text="  Frames & sound  ", padx=12, pady=6)
        opt.pack(fill="x", padx=20, pady=4)
        self.opt_frame = opt
        r0 = tk.Frame(opt)
        r0.pack(fill="x", pady=2)
        tk.Label(r0, text="Slow-motion frames:", width=18, anchor="w").pack(side="left")
        self.slowmo_var = tk.StringVar(value=SLOWMO["dup"])
        ttk.Combobox(r0, textvariable=self.slowmo_var, values=list(SLOWMO.values()),
                     state="readonly", width=36).pack(side="left", padx=6)
        r1 = tk.Frame(opt)
        r1.pack(fill="x", pady=2)
        tk.Label(r1, text="Audio:", width=18, anchor="w").pack(side="left")
        self.audio_var = tk.StringVar(value=AUDIO["tempo"])
        self.audio_box = ttk.Combobox(r1, textvariable=self.audio_var, values=list(AUDIO.values()),
                                      state="readonly", width=36)
        self.audio_box.pack(side="left", padx=6)
        self.result_lbl = tk.Label(opt, text="", fg=CLR["accent"], font=(MONO_FONT, 10))
        self.result_lbl.pack(anchor="w", pady=(4, 0))

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=12, pady=8)
        out_lf.pack(fill="x", padx=20, pady=6)
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9", "prores", "dnxhr"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.src_row.path, "speed"))
        self.out_row.pack(fill="x", pady=(8, 0))
        self.btn_render = tk.Button(body, text=t("speed_ramp.apply_button"), font=(UI_FONT, 12, "bold"),
                                    bg=CLR["green"], fg="white", width=28, cursor="hand2",
                                    command=self._render)
        self.btn_render.pack(pady=8)
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self.console, csb = self.make_console(cf, height=6)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

        self._mode_widgets = (self.const_f, self.ramp_f)
        self._apply_preset("Speed up in the middle")
        self._on_mode()

    # ── Points ───────────────────────────────────────────────────────────────

    def _add_point(self, at=None, speed=1.0):
        if at is None:
            last = self.ramp_points[-1]["time"].get() if self.ramp_points else "0"
            try:
                at = ff.parse_time(last) + 1.0
            except ValueError:
                at = 0.0
        row = tk.Frame(self.rows_f)
        row.pack(fill="x", pady=1)
        idx = tk.Label(row, text=str(len(self.ramp_points) + 1), width=3)
        idx.pack(side="left")
        tv = tk.StringVar(value=f"{at:.2f}")
        sv = tk.StringVar(value=f"{speed:g}")
        for var, w in ((tv, 16), (sv, 10)):
            e = tk.Entry(row, textvariable=var, width=w, relief="flat")
            e.pack(side="left", padx=2)
            var.trace_add("write", lambda *_a: self._schedule())
        point = {"time": tv, "speed": sv, "row": row, "idx": idx}
        tk.Button(row, text="✕", relief="flat", width=2, cursor="hand2",
                  command=lambda p=point: self._remove_point(p)).pack(side="left", padx=4)
        self.ramp_points.append(point)
        self._schedule()

    def _remove_point(self, point):
        if point in self.ramp_points:
            self.ramp_points.remove(point)
            point["row"].destroy()
            for i, p in enumerate(self.ramp_points, 1):
                p["idx"].config(text=str(i))
            self._schedule()

    def _apply_preset(self, name=None):
        name = name or self.preset_var.get()
        if name not in PRESETS:
            return
        for p in list(self.ramp_points):
            p["row"].destroy()
        self.ramp_points.clear()
        dur = self.duration or 10.0
        for frac, speed in PRESETS[name]:
            self._add_point(frac * dur, speed)

    def parsed_points(self):
        """[(time, speed)] from the rows; raises ValueError with a clear message."""
        pts = []
        for i, p in enumerate(self.ramp_points, 1):
            try:
                tm = ff.parse_time(p["time"].get())
            except ValueError:
                raise ValueError(f"Point {i}: '{p['time'].get()}' is not a time.") from None
            sp = common.number(p["speed"].get(), f"Point {i} speed", MIN_SPEED, MAX_SPEED)
            pts.append((tm, sp))
        if not pts:
            raise ValueError("Add at least one point.")
        return pts

    # ── Display ──────────────────────────────────────────────────────────────

    def _on_mode(self):
        for w in self._mode_widgets:
            w.pack_forget()
        target = self.const_f if self.mode_var.get() == "constant" else self.ramp_f
        target.pack(fill="x", padx=20, pady=4, before=self.opt_frame)
        ramp = self.mode_var.get() == "ramp"
        self.audio_box.config(values=[AUDIO["tempo"], AUDIO["mute"]] if ramp else list(AUDIO.values()))
        if ramp and self.audio_var.get() == AUDIO["pitch"]:
            self.audio_var.set(AUDIO["tempo"])
        self._schedule()

    def _source_changed(self, path):
        info = ff.try_probe(path)
        old = self.duration
        self.duration = info.duration if info else 0.0
        if self.duration and old != self.duration and self.preset_var.get() in PRESETS:
            self._apply_preset()
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "speed"))
        self._schedule()

    def _schedule(self):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(120, self._refresh)

    def _refresh(self):
        self._job = None
        self._draw()
        dur = self.duration
        if not dur:
            self.result_lbl.config(text="")
            return
        try:
            if self.mode_var.get() == "constant":
                new = dur / max(self.speed_var.get(), MIN_SPEED)
            else:
                new = output_duration(normalise_points(self.parsed_points(), dur))
        except ValueError as exc:
            self.result_lbl.config(text=f"⚠ {exc}")
            return
        self.result_lbl.config(text=f"Result: {ff.format_time(new)}   (source {ff.format_time(dur)})")

    def _draw(self):
        c = self.graph
        c.delete("all")
        w, h = max(c.winfo_width(), 200), int(c.cget("height"))
        dur = self.duration or 10.0
        try:
            pts = normalise_points(self.parsed_points(), dur)
        except ValueError:
            return
        lo, hi = math.log2(MIN_SPEED), math.log2(MAX_SPEED)

        def y_of(speed):
            return h - 8 - (math.log2(speed) - lo) / (hi - lo) * (h - 16)

        for ref in (0.25, 0.5, 1, 2, 4, 8):
            y = y_of(ref)
            c.create_line(30, y, w - 4, y, fill="#333333" if ref != 1 else "#555555",
                          dash=() if ref == 1 else (2, 3))
            c.create_text(4, y, text=f"{ref:g}×", anchor="w", fill="#777777", font=(MONO_FONT, 7))
        xy = []
        steps = 200
        for i in range(steps + 1):
            tm = dur * i / steps
            xy += [30 + (w - 34) * i / steps, y_of(speed_at(pts, tm))]
        c.create_line(*xy, fill=CLR["accent"], width=2, smooth=False)
        for tm, sp in pts:
            x = 30 + (w - 34) * tm / dur
            c.create_oval(x - 3, y_of(sp) - 3, x + 3, y_of(sp) + 3, fill=CLR["accent"], outline="")

    # ── Render ───────────────────────────────────────────────────────────────

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        dur = info.duration
        fps = common.frame_rate(info)
        slowmo = next((k for k, v in SLOWMO.items() if v == self.slowmo_var.get()), "dup")
        audio_mode = next((k for k, v in AUDIO.items() if v == self.audio_var.get()), "tempo")
        speed = 1.0
        try:
            if self.mode_var.get() == "constant":
                speed = float(self.speed_var.get())
                if not MIN_SPEED <= speed <= MAX_SPEED:
                    raise ValueError(f"Speed must be between {MIN_SPEED}× and {MAX_SPEED}×.")
                points = None
                new_dur = dur / speed
            else:
                points = normalise_points(self.parsed_points(), dur)
                new_dur = output_duration(points)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "speed")
        if not out:
            return
        container = common.container_of(out)
        frames = frame_filter(slowmo, fps)
        has_audio = info.has_audio and audio_mode != "mute"
        sr = int(info.audio.sample_rate or 48000) if info.has_audio else 48000

        if points is None:
            graph = f"[0:v]setpts=(PTS-STARTPTS)/{speed:.6f},{frames}[v]"
            if has_audio:
                if audio_mode == "pitch":
                    af = f"asetrate={sr * speed:.0f},aresample={sr}"
                else:
                    af = atempo_chain(speed, tempo_engine())
                graph += f";[0:a:0]asetpts=PTS-STARTPTS,{af}[a]"
            self.log(self.console, f"Speed {speed:g}× → {ff.format_time(new_dur)}")
            self._queue(src, info, out, container, graph, has_audio, new_dur, None)
            return

        expr = setpts_expr(points)
        graph = f"[0:v]setpts=PTS-STARTPTS,setpts='({expr})/TB',{frames}[v]"
        engine = tempo_engine()
        if has_audio:
            graph += f";[0:a:0]asetpts=PTS-STARTPTS,{ramp_audio_chain(points, engine)}[a]"
        self.log(self.console, f"Speed ramp through {len(points)} points → {ff.format_time(new_dur)}")
        self._queue(src, info, out, container, graph, has_audio, new_dur,
                    tempo_commands(points, engine) if has_audio else None)

    def _queue(self, src, info, out, container, graph, has_audio, new_dur, commands):
        venc = self.encoder.video_encode(container)
        if venc.filter_suffix:
            graph += f";[v]{venc.filter_suffix}[venc]"
        vmap = "[venc]" if venc.filter_suffix else "[v]"
        audio = (["-map", "[a]", *enc.audio(enc.default_audio_for(container), container=container)]
                 if has_audio else ["-an"])
        base = [ff.ffmpeg_path(), "-y", *venc.pre_input, "-i", src, "-filter_complex", graph,
                "-map", vmap, *audio, *venc.args]

        def work(_progress, _cancelled, ctx):
            workdir = ctx.tempdir()
            if commands:
                with open(os.path.join(workdir, "tempo.cmd"), "w", encoding="ascii") as fh:
                    fh.write(commands)
            ctx.run([*base, ctx.output()], duration=new_dur, cwd=workdir)
            return 0

        common.queue_job(self, name="Speed Ramper", out=out, work=work, btn=self.btn_render,
                         btn_label=t("speed_ramp.apply_button"), console=self.console,
                         duration=new_dur, inputs=[src])
