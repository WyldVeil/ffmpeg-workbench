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
import tkinter as tk
from dataclasses import dataclass
from functools import partial
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops import speed_ramp as sr_ops
from core.ops.shared import frame_rate
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit.tktyped import on_write
from tabs.widgets import FileRow, output_section, scroll_body

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
    "Hit the beat (fast, slow, fast)": [
        (0.0, 3.0),
        (0.3, 3.0),
        (0.35, 0.4),
        (0.55, 0.4),
        (0.6, 3.0),
    ],
}


# ── Tab ──────────────────────────────────────────────────────────────────────


@dataclass(eq=False)
class _Point:
    """A speed change on the ramp: when, how fast, and its row on the page."""

    time: tk.StringVar
    speed: tk.StringVar
    row: tk.Frame
    idx: tk.Label


class SpeedRamperTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self.duration = 0.0
        self.ramp_points: list[_Point] = []
        self._job: str | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        body = self._build_header()
        self._build_ramp_points(body)
        self._build_out(body)

    def _preset(self, value: float) -> None:
        self.speed_var.set(value)
        self._schedule()

    def _build_header(self) -> tk.Frame:
        self.make_header(self, t("tab.speed_ramper"), t("speed_ramp.subtitle"), icon="⚡")
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))

        mode_lf = tk.LabelFrame(
            body, text=f"  {t('speed_ramp.speed_mode_section')}  ", padx=px(12), pady=px(6)
        )
        mode_lf.pack(fill="x", padx=px(20), pady=px(4))
        self.mode_var = tk.StringVar(value="constant")
        tk.Radiobutton(
            mode_lf,
            text=t("speed_ramp.constant_option"),
            value="constant",
            variable=self.mode_var,
            command=self._on_mode,
        ).pack(side="left", padx=px(12))
        tk.Radiobutton(
            mode_lf,
            text="Speed ramp (smooth curve)",
            value="ramp",
            variable=self.mode_var,
            command=self._on_mode,
        ).pack(side="left", padx=px(12))

        # constant
        self.const_f = tk.Frame(body)
        tk.Label(
            self.const_f, text=t("speed_ramp.speed_multiplier_label"), font=(UI_FONT, 11, "bold")
        ).pack(side="left")
        self.speed_var = tk.DoubleVar(value=2.0)
        tk.Scale(
            self.const_f,
            variable=self.speed_var,
            from_=MIN_SPEED,
            to=MAX_SPEED,
            resolution=0.05,
            orient="horizontal",
            length=px(300),
            command=lambda _v: self._schedule(),
        ).pack(side="left", padx=px(8))
        quick = tk.Frame(self.const_f)
        quick.pack(side="left", padx=px(10))
        for val, lbl in [
            (0.25, "¼×"),
            (0.5, "½×"),
            (0.75, "¾×"),
            (1.5, "1.5×"),
            (2.0, "2×"),
            (4.0, "4×"),
            (8.0, "8×"),
        ]:
            tk.Button(
                quick,
                text=lbl,
                width=4,
                relief="flat",
                command=partial(self._preset, val),
            ).pack(side="left", padx=px(1))
        return body

    def _build_ramp_points(self, body: tk.Frame) -> None:
        # ramp
        self.ramp_f = tk.LabelFrame(
            body, text=f"  {t('speed_ramp.ramp_points_section')}  ", padx=px(10), pady=px(8)
        )
        top = tk.Frame(self.ramp_f)
        top.pack(fill="x")
        tk.Label(top, text="Preset:").pack(side="left")
        self.preset_var = tk.StringVar(value="")
        pb = ttk.Combobox(
            top, textvariable=self.preset_var, values=list(PRESETS), state="readonly", width=32
        )
        pb.pack(side="left", padx=px(6))
        pb.bind("<<ComboboxSelected>>", lambda _e: self._apply_preset())
        tk.Button(
            top,
            text=t("speed_ramp.add_point_button"),
            relief="flat",
            command=lambda: self._add_point(),
        ).pack(side="left", padx=px(8))
        self.graph = tk.Canvas(
            self.ramp_f, height=px(130), bg=CLR["console_bg"], highlightthickness=0
        )
        self.graph.pack(fill="x", pady=px(6))
        self.graph.bind("<Configure>", lambda _e: self._draw())
        hdr = tk.Frame(self.ramp_f)
        hdr.pack(fill="x")
        for text, w in (("#", 3), ("Time (s or m:ss)", 16), ("Speed (×)", 10)):
            tk.Label(hdr, text=text, width=w, anchor="w", font=(UI_FONT, 9, "bold")).pack(
                side="left", padx=px(2)
            )
        self.rows_f = tk.Frame(self.ramp_f)
        self.rows_f.pack(fill="x")

        opt = tk.LabelFrame(body, text="  Frames & sound  ", padx=px(12), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.opt_frame = opt
        r0 = tk.Frame(opt)
        r0.pack(fill="x", pady=px(2))
        tk.Label(r0, text="Slow-motion frames:", width=18, anchor="w").pack(side="left")
        self.slowmo_var = tk.StringVar(value=SLOWMO["dup"])
        ttk.Combobox(
            r0,
            textvariable=self.slowmo_var,
            values=list(SLOWMO.values()),
            state="readonly",
            width=36,
        ).pack(side="left", padx=px(6))
        r1 = tk.Frame(opt)
        r1.pack(fill="x", pady=px(2))
        tk.Label(r1, text="Audio:", width=18, anchor="w").pack(side="left")
        self.audio_var = tk.StringVar(value=AUDIO["tempo"])
        self.audio_box = ttk.Combobox(
            r1, textvariable=self.audio_var, values=list(AUDIO.values()), state="readonly", width=36
        )
        self.audio_box.pack(side="left", padx=px(6))
        self.result_lbl = tk.Label(opt, text="", fg=CLR["accent"], font=(MONO_FONT, 10))
        self.result_lbl.pack(anchor="w", pady=px(4, 0))

    def _build_out(self, body: tk.Frame) -> None:
        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9", "prores", "dnxhr"),
            suggest=lambda: self.suggest_output(self.src_row.path, "speed"),
        )
        self.btn_render = self.make_render_btn(
            body, t("speed_ramp.apply_button"), self._render, width=28
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=6)

        self._mode_widgets = (self.const_f, self.ramp_f)
        self._apply_preset("Speed up in the middle")
        self._on_mode()

    # ── Points ───────────────────────────────────────────────────────────────

    def _add_point(self, at: float | None = None, speed: float = 1.0) -> None:
        if at is None:
            last = self.ramp_points[-1].time.get() if self.ramp_points else "0"
            try:
                at = ff.parse_time(last) + 1.0
            except ValueError:
                at = 0.0
        row = tk.Frame(self.rows_f)
        row.pack(fill="x", pady=px(1))
        idx = tk.Label(row, text=str(len(self.ramp_points) + 1), width=3)
        idx.pack(side="left")
        tv = tk.StringVar(value=f"{at:.2f}")
        sv = tk.StringVar(value=f"{speed:g}")
        for var, w in ((tv, 16), (sv, 10)):
            e = tk.Entry(row, textvariable=var, width=w, relief="flat")
            e.pack(side="left", padx=px(2))
            on_write(var, lambda: self._schedule())
        point = _Point(tv, sv, row, idx)
        tk.Button(
            row,
            text="✕",
            relief="flat",
            width=2,
            cursor="hand2",
            command=partial(self._remove_point, point),
        ).pack(side="left", padx=px(4))
        self.ramp_points.append(point)
        self._schedule()

    def _remove_point(self, point: _Point) -> None:
        if point in self.ramp_points:
            self.ramp_points.remove(point)
            point.row.destroy()
            for i, p in enumerate(self.ramp_points, 1):
                p.idx.config(text=str(i))
            self._schedule()

    def _apply_preset(self, name: str | None = None) -> None:
        name = name or self.preset_var.get()
        if name not in PRESETS:
            return
        for p in list(self.ramp_points):
            p.row.destroy()
        self.ramp_points.clear()
        dur = self.duration or 10.0
        for frac, speed in PRESETS[name]:
            self._add_point(frac * dur, speed)

    def parsed_points(self) -> list[tuple[float, float]]:
        """[(time, speed)] from the rows; raises ValueError with a clear message."""
        pts: list[tuple[float, float]] = []
        for i, p in enumerate(self.ramp_points, 1):
            try:
                tm = ff.parse_time(p.time.get())
            except ValueError:
                raise ValueError(f"Point {i}: '{p.time.get()}' is not a time.") from None
            sp = common.number(p.speed.get(), f"Point {i} speed", MIN_SPEED, MAX_SPEED)
            pts.append((tm, sp))
        if not pts:
            raise ValueError("Add at least one point.")
        return pts

    # ── Display ──────────────────────────────────────────────────────────────

    def _on_mode(self) -> None:
        for w in self._mode_widgets:
            w.pack_forget()
        target = self.const_f if self.mode_var.get() == "constant" else self.ramp_f
        target.pack(fill="x", padx=px(20), pady=px(4), before=self.opt_frame)
        ramp = self.mode_var.get() == "ramp"
        self.audio_box.config(
            values=[AUDIO["tempo"], AUDIO["mute"]] if ramp else list(AUDIO.values())
        )
        if ramp and self.audio_var.get() == AUDIO["pitch"]:
            self.audio_var.set(AUDIO["tempo"])
        self._schedule()

    def _source_changed(self, path: str) -> None:
        info = ff.try_probe(path)
        old = self.duration
        self.duration = info.duration if info else 0.0
        if self.duration and old != self.duration and self.preset_var.get() in PRESETS:
            self._apply_preset()
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "speed"))
        self._schedule()

    def _schedule(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(120, self._refresh)

    def _refresh(self) -> None:
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
                new = sr_ops.output_duration(sr_ops.normalise_points(self.parsed_points(), dur))
        except ValueError as exc:
            self.result_lbl.config(text=f"⚠ {exc}")
            return
        self.result_lbl.config(
            text=f"Result: {ff.format_time(new)}   (source {ff.format_time(dur)})"
        )

    def _draw(self) -> None:
        c = self.graph
        c.delete("all")
        w, h = max(c.winfo_width(), px(200)), int(c.cget("height"))
        left, right, edge = px(30), px(4), px(8)
        dur = self.duration or 10.0
        try:
            pts = sr_ops.normalise_points(self.parsed_points(), dur)
        except ValueError:
            return
        lo, hi = math.log2(MIN_SPEED), math.log2(MAX_SPEED)

        def y_of(speed: float) -> float:
            return h - edge - (math.log2(speed) - lo) / (hi - lo) * (h - 2 * edge)

        for ref in (0.25, 0.5, 1, 2, 4, 8):
            y = y_of(ref)
            c.create_line(
                left,
                y,
                w - right,
                y,
                fill="#333333" if ref != 1 else "#555555",
                dash=() if ref == 1 else px(2, 3),
                width=px(1),
            )
            c.create_text(
                px(4), y, text=f"{ref:g}×", anchor="w", fill="#777777", font=(MONO_FONT, 7)
            )
        xy: list[float] = []
        steps = 200
        for i in range(steps + 1):
            tm = dur * i / steps
            xy += [left + (w - left - right) * i / steps, y_of(sr_ops.speed_at(pts, tm))]
        c.create_line(*xy, fill=CLR["accent"], width=px(2), smooth=False)
        for tm, sp in pts:
            x = left + (w - left - right) * tm / dur
            r = px(3)
            c.create_oval(x - r, y_of(sp) - r, x + r, y_of(sp) + r, fill=CLR["accent"], outline="")

    # ── Render ───────────────────────────────────────────────────────────────

    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        dur = info.duration
        fps = frame_rate(info)
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
                points = sr_ops.normalise_points(self.parsed_points(), dur)
                new_dur = sr_ops.output_duration(points)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "speed")
        if not out:
            return
        container = common.container_of(out)
        has_audio = info.has_audio and audio_mode != "mute"
        sr = int(info.require_audio().sample_rate or 48000) if info.has_audio else 48000
        spec = sr_ops.Speed(
            points=points,
            speed=speed,
            slowmo=slowmo,
            rate=fps,
            keep_pitch=audio_mode != "pitch",
            audio=has_audio,
            sample_rate=sr,
        )
        if points is None:
            self.log(self.console, f"Speed {speed:g}× → {ff.format_time(new_dur)}")
        else:
            self.log(
                self.console, f"Speed ramp through {len(points)} points → {ff.format_time(new_dur)}"
            )
        job = sr_ops.SpeedJob(
            src=src,
            speed=spec,
            engine=sr_ops.tempo_engine(),
            venc=self.encoder.video_encode(container),
            container=container,
            duration=new_dur,
        )
        common.queue_job(
            self,
            common.Job(name="Speed Ramper", out=out, work=job.run, duration=new_dur, inputs=[src]),
            btn=self.btn_render,
            console=self.console,
        )
