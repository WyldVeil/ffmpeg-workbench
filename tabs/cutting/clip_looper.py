"""
tabs/cutting/clip_looper.py  -  Clip Looper

Repeat a moment: plain loops, DJ-style stutters, loops that speed up each
time, or echoes that fade away - with the rest of the clip kept around it
if you like.  Each distinct loop is rendered once, then everything is
joined losslessly (audio encoded once at the end), so there is no drift or
quality loss however many repeats.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops import looper
from core.ops.shared import frame_rate
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.kit.time_fields import RangePicker
from tabs.widgets import FileRow, FramePreview, output_section, scroll_body

MODES = [
    ("simple", "Repeat", "Play the section several times in a row."),
    ("stutter", "Stutter", "Rapid repeats of a tiny slice (DJ / glitch style)."),
    ("accel", "Speed up each time", "Every repeat plays faster than the last."),
    ("echo", "Echo", "Every repeat is darker and quieter, fading away."),
]


class ClipLooperTab(BaseTab):
    # i / o (and the buttons): the section starts / ends at the frame shown
    def mark_in(self) -> None:
        self.section.start.use_here()

    def mark_out(self) -> None:
        self.section.end.use_here()

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info: ff.MediaInfo | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        body, opt = self._build_header()
        self._build_output(body, opt)
        self._build_console(body)

    def _build_header(self) -> tuple[tk.Frame, tk.LabelFrame]:
        self.make_header(self, t("tab.clip_looper"), t("toolinfo.clip_looper"), icon="🔁")
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        pv = tk.Frame(body)
        pv.pack(pady=px(4))
        self.preview = FramePreview(pv, 448, 252)
        self.preview.pack()
        self.pos_var = tk.DoubleVar(value=0)
        bar = tk.Frame(pv)
        bar.pack(fill="x")
        self.slider = ttk.Scale(
            bar,
            from_=0,
            to=1,
            variable=self.pos_var,
            length=px(300),
            command=lambda _v: self.preview.show(float(self.pos_var.get())),
        )
        self.slider.pack(side="left", fill="x", expand=True)
        framestep.slider(self.slider, self.preview)  # , and . step a frame
        tk.Button(bar, text=t("clip_looper.set_start"), relief="flat", command=self.mark_in).pack(
            side="left"
        )
        tk.Button(bar, text=t("clip_looper.set_end"), relief="flat", command=self.mark_out).pack(
            side="left"
        )

        opt = tk.LabelFrame(body, text=t("clip_looper.loop"), padx=px(12), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.mode_var = tk.StringVar(value="simple")
        for key, label, desc in MODES:
            row = tk.Frame(opt)
            row.pack(fill="x")
            tk.Radiobutton(
                row,
                text=label,
                value=key,
                variable=self.mode_var,
                width=18,
                anchor="w",
                command=self._mode_changed,
            ).pack(side="left")
            tk.Label(row, text=desc, fg=CLR["fgdim"], font=(UI_FONT, 9)).pack(side="left")
        return body, opt

    def _build_output(self, body: tk.Frame, opt: tk.LabelFrame) -> None:
        r = tk.Frame(opt)
        r.pack(fill="x", pady=px(6, 2))
        tk.Label(r, text=t("reverser.section_from")).pack(side="left")
        self.section = RangePicker(r, end="2", here=lambda: float(self.pos_var.get()))
        self.section.pack(side="left", padx=px(4))
        tk.Label(r, text=t("clip_looper.repeats")).pack(side="left", padx=px(10, 2))
        self.count_var = tk.StringVar(value="4")
        tk.Entry(r, textvariable=self.count_var, width=4).pack(side="left")
        r2 = tk.Frame(opt)
        r2.pack(fill="x", pady=px(2))
        tk.Label(r2, text=t("clip_looper.stutter_slice_s")).pack(side="left")
        self.slice_var = tk.StringVar(value="0.15")
        tk.Entry(r2, textvariable=self.slice_var, width=6).pack(side="left", padx=px(4))
        tk.Label(r2, text=t("clip_looper.speed_up_per_repeat")).pack(side="left", padx=px(10, 2))
        self.factor_var = tk.StringVar(value="1.3")
        tk.Entry(r2, textvariable=self.factor_var, width=6).pack(side="left")
        self.context_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            opt,
            text=t("clip_looper.keep_the_rest_of_the_clip_before"),
            variable=self.context_var,
        ).pack(anchor="w")

        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1"),
            suggest=lambda: self.suggest_output(self.src_row.path, "looped"),
        )
        self.btn_render = self.make_render_btn(
            body, t("clip_looper.create"), self._render, width=22
        )

    def _build_console(self, body: tk.Frame) -> None:
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)
        self._mode_changed()

    def _mode_changed(self) -> None:
        stutter = self.mode_var.get() == "stutter"
        self.section.end.entry.config(state="disabled" if stutter else "normal")

    def _source_changed(self, path: str) -> None:
        self._info = ff.try_probe(path)
        dur = self._info.duration if self._info else 0.0
        self.slider.configure(to=max(dur, 0.01))
        self.preview.load(path, dur, 0.0)
        self.section.end.set(min(2.0, dur))
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "looped"))

    def _section(self, info: ff.MediaInfo) -> tuple[float, float, int, float] | None:
        """(start, end, repeats, speed-up) from the fields; None after saying what's wrong."""
        mode = self.mode_var.get()
        fps = info.require_video().fps or 30.0
        try:
            start = self.section.start.seconds(0.0, t("muter.from")) or 0.0
            if mode == "stutter":
                end = start + common.number(
                    self.slice_var.get(), t("clip_looper.stutter_slice"), 1.0 / fps, 5
                )
            else:
                end = self.section.end.seconds(None, "To")
                if end is None:
                    raise ValueError("Say where the section ends.")
            count = int(common.number(self.count_var.get(), t("reverser.repeats_2"), 2, 200))
            factor = common.number(
                self.factor_var.get() or "1.3", t("clip_looper.speed_up"), 1.01, 4
            )
            if not 0 <= start < end <= info.duration + 0.05:
                raise ValueError("The section must lie inside the clip, with 'from' before 'to'.")
            if end - start < 1.0 / fps:
                raise ValueError("The section is shorter than one frame.")
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return None
        return start, min(end, info.duration), count, factor

    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        section = self._section(info) if info is not None else None
        if info is None or section is None:
            return
        start, end, count, factor = section
        dur, mode, fps = info.duration, self.mode_var.get(), info.require_video().fps or 30.0
        out = common.output_for(self, self.out_row, src, "looped")
        if not out:
            return
        units = looper.loop_units(mode, count, factor)
        if mode == "accel":  # stop once a repeat is shorter than a frame
            units = [u for u in units if (end - start) / u[0] >= 1.0 / fps] or units[:1]
        w, h = common.even_size(info)
        container = common.container_of(out)
        pieces = looper.plan(start, end, dur, units, keep_context=self.context_var.get())
        total = looper.output_length(pieces)
        job = looper.LoopJob(
            src=src,
            pieces=pieces,
            base_vf=looper.base_filter(w, h, frame_rate(info)),
            venc=self.encoder.video_encode(container),
            container=container,
            audio=info.has_audio,
            total=total,
        )
        self.log(
            self.console,
            t(
                "clip_looper.units_repeats_of_end_total_total",
                units=len(units),
                end=ff.format_time(end - start),
                total=ff.format_time(total),
            ),
        )
        common.queue_job(
            self,
            common.Job(name="Clip Looper", out=out, work=job.run, duration=total, inputs=[src]),
            btn=self.btn_render,
            console=self.console,
        )
