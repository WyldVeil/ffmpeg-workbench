"""
tabs/audio/loudness.py  -  Loudness Normalizer

Measure and normalise loudness to a standard (EBU R128 / streaming
targets) with a proper two-pass loudnorm: one exact gain for the whole
file, true-peak limited, back at the source sample rate.  Works on audio
files and videos (picture and other tracks kept).  "Analyse" just reports
the current loudness.
"""

from __future__ import annotations

import tkinter as tk
from functools import partial
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import loudness as ln
from core import tasks
from core.i18n import t
from core.ops.shared import audio_filter_cmd
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.widgets import FileRow, SaveRow, scroll_body


class LoudnessNormTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.loudness_normalizer"), t("loudness.subtitle"), icon="📢")
        body = scroll_body(self)
        self.src_row = FileRow(
            body,
            t("common.source_video"),
            self.src_var,
            kind="media",
            on_change=self._source_changed,
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        opt = tk.LabelFrame(body, text="  Target  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.preset_var = tk.StringVar(value=ln.TARGETS["youtube"][0])
        pb = ttk.Combobox(
            opt,
            textvariable=self.preset_var,
            values=[v[0] for v in ln.TARGETS.values()],
            state="readonly",
            width=56,
        )
        pb.grid(row=0, column=0, columnspan=6, sticky="w")
        pb.bind("<<ComboboxSelected>>", lambda _e: self._preset())
        self.i_var, self.tp_var, self.lra_var = (
            tk.StringVar(value="-16"),
            tk.StringVar(value="-1.5"),
            tk.StringVar(value="11"),
        )
        for col, (label, var) in enumerate(
            (
                ("Loudness (LUFS)", self.i_var),
                ("True peak (dBTP)", self.tp_var),
                ("Range (LU)", self.lra_var),
            )
        ):
            tk.Label(opt, text=label).grid(row=1, column=col * 2, sticky="w", pady=px(4))
            tk.Entry(opt, textvariable=var, width=7).grid(
                row=1, column=col * 2 + 1, sticky="w", padx=px(4, 12)
            )
        self.track_var = tk.StringVar()
        self.track_box = ttk.Combobox(opt, textvariable=self.track_var, state="readonly", width=40)
        tk.Label(opt, text="Audio track:").grid(row=2, column=0, sticky="w")
        self.track_box.grid(row=2, column=1, columnspan=5, sticky="w", pady=px(4))
        self.report = tk.Label(
            body, text="", fg=CLR["accent"], font=(MONO_FONT, 10), justify="left"
        )
        self.report.pack(anchor="w", padx=px(24), pady=px(4))
        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.out_row = SaveRow(
            out_lf,
            t("common.output_file"),
            kind="media",
            suggest=lambda: self.suggest_output(self.src_row.path, "normalised"),
        )
        self.out_row.pack(fill="x")
        row = tk.Frame(body)
        row.pack(pady=px(8))
        self.btn_analyse = tk.Button(
            row, text="📏  ANALYSE", font=(UI_FONT, 11, "bold"), width=14, command=self._analyse
        )
        self.btn_analyse.pack(side="left", padx=px(6))
        self.btn_render = self.make_render_btn(row, "📢  NORMALISE", self._render, width=18)
        self.btn_render.pack(side="left", padx=px(6))
        self.console = self.make_console_block(body, height=6)

    def _preset(self):
        key = next((k for k, v in ln.TARGETS.items() if v[0] == self.preset_var.get()), "youtube")
        _l, i, tp, lra = ln.TARGETS[key]
        self.i_var.set(f"{i:g}")
        self.tp_var.set(f"{tp:g}")
        self.lra_var.set(f"{lra:g}")

    def _source_changed(self, path):
        info = ff.try_probe(path)
        tracks = (
            [
                f"Track {i + 1}: {a.codec}, {a.channels} ch, {a.sample_rate} Hz"
                + (f" [{a.language}]" if a.language else "")
                for i, a in enumerate(info.audio_streams)
            ]
            if info
            else []
        )
        self.track_box.config(values=tracks or ["(no audio)"])
        self.track_var.set(tracks[0] if tracks else "(no audio)")
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "normalised"))

    def _targets(self):
        return (
            common.number(self.i_var.get(), "Loudness", -70, -5),
            common.number(self.tp_var.get(), "True peak", -9, 0),
            common.number(self.lra_var.get(), "Range", 1, 50),
        )

    def _stream(self):
        values = list(self.track_box.cget("values"))
        return values.index(self.track_var.get()) if self.track_var.get() in values else 0

    def _analyse(self):
        src = self.src_row.path
        info = common.check_source(self, src, need_video=False)
        if info is None:
            return
        stream = self._stream()
        self.btn_analyse.config(state="disabled", text="Measuring…")

        def done(m):
            self.btn_analyse.config(state="normal", text="📏  ANALYSE")
            self.report.config(
                text=f"Integrated {m.input_i:.1f} LUFS   ·   true peak {m.input_tp:.1f} dBTP   ·   "
                f"range {m.input_lra:.1f} LU"
            )

        def failed(exc):
            self.btn_analyse.config(state="normal", text="📏  ANALYSE")
            messagebox.showerror(t("common.error"), f"Measurement failed:\n{exc}", parent=self)

        tasks.submit(
            partial(ln.measure, src, -16, -1.5, 11, stream=stream),
            on_done=done,
            on_error=failed,
            name="LoudnessMeasure",
        )

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src, need_video=False)
        if info is None:
            return
        if not info.has_audio:
            messagebox.showwarning(t("common.warning"), "This file has no audio.", parent=self)
            return
        try:
            i, tp, lra = self._targets()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "normalised")
        if not out:
            return
        stream = self._stream()
        sr = info.audio_streams[stream].sample_rate if info.audio_streams else 48000
        dur = info.duration or None
        result = {}

        def work(_p, _c, ctx):
            ctx.log("Pass 1 of 2: measuring…")
            m = ln.measure(src, i, tp, lra, stream=stream, ctx=ctx.span(0.0, 0.45))
            result["before"] = m
            ctx.log(f"Measured {m.input_i:.1f} LUFS, peak {m.input_tp:.1f} dBTP")
            ctx.log("Pass 2 of 2: normalising…")
            af = ln.normalise_filter(i, tp, lra, m, sr)
            cmd = audio_filter_cmd(src, af, ctx.output(), info, stream=stream)
            ctx.span(0.45, 1.0).run(cmd, duration=dur)
            return 0

        common.queue_job(
            self,
            common.Job(name="Loudness", out=out, work=work, duration=dur, inputs=[src]),
            btn=self.btn_render,
            console=self.console,
        )
