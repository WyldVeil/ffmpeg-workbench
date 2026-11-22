"""
tabs/visuals/deshaker.py  -  Deshaker

Two-pass stabilisation with vid.stab (vidstabdetect -> vidstabtransform),
run as one queued job with progress and cancel.  Falls back to FFmpeg's
single-pass ``deshake`` filter when the FFmpeg build has no vid.stab.
Optionally renders a side-by-side comparison (original | stabilised).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from core.i18n import t
from core.ops import deshake as ds
from core.ops.shared import keep_audio_args
from core.queue_manager import Hooks
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, SaveRow, scroll_body

ZOOM_MODES = {
    "Auto zoom to hide borders (recommended)": 1,
    "Adaptive zoom (zooms only when needed)": 2,
    "No automatic zoom": 0,
}
EDGE_MODES = {"Keep previous frame at edges": "keep", "Black edges": "black"}


class DeshakerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._build_ui()

    def _build_ui(self):
        body, opts = self._build_header()
        self._build_output(body, opts)

    def _build_header(self):
        self.make_header(self, t("tab.deshaker"), t("deshaker.subtitle"), icon="🎥")
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))

        opts = tk.LabelFrame(
            body, text=f"  {t('deshaker.options_section')}  ", padx=px(12), pady=px(8)
        )
        opts.pack(fill="x", padx=px(20), pady=px(6))

        def scale(row, label, var, lo, hi, hint=""):
            self.form_row(
                opts,
                row,
                label,
                tk.Scale(opts, variable=var, from_=lo, to=hi, orient="horizontal", length=px(220)),
                label_width=26,
            )
            if hint:
                tk.Label(
                    opts,
                    text=hint,
                    fg=CLR["fgdim"],
                    font=(UI_FONT, 8),
                    wraplength=px(360),
                    justify="left",
                ).grid(row=row, column=2, sticky="w", padx=px(8))

        self.smooth_var = tk.IntVar(value=15)
        scale(
            0,
            t("deshaker.smoothing_label"),
            self.smooth_var,
            1,
            60,
            "Frames averaged each way: higher = steadier, floatier camera.",
        )
        self.shake_var = tk.IntVar(value=5)
        scale(
            1,
            t("deshaker.shake_detect_label"),
            self.shake_var,
            1,
            10,
            "How shaky the footage is (1 = a little, 10 = very).",
        )
        self.accuracy_var = tk.IntVar(value=15)
        scale(
            2,
            t("deshaker.accuracy_label"),
            self.accuracy_var,
            1,
            15,
            "Detection accuracy (15 = best, slower analysis).",
        )

        self.zoom_mode = tk.StringVar(value=next(iter(ZOOM_MODES)))
        self.form_row(
            opts,
            3,
            "Zoom:",
            ttk.Combobox(
                opts,
                textvariable=self.zoom_mode,
                values=list(ZOOM_MODES),
                state="readonly",
                width=40,
            ),
            label_width=26,
            span=2,
        )
        return body, opts

    def _build_output(self, body, opts):
        self.zoom_var = tk.StringVar(value="0")
        self.form_row(
            opts,
            4,
            "Extra zoom (%):",
            tk.Entry(opts, textvariable=self.zoom_var, width=6),
            label_width=26,
        )
        self.edge_var = tk.StringVar(value=next(iter(EDGE_MODES)))
        self.form_row(
            opts,
            5,
            "Edges:",
            ttk.Combobox(
                opts,
                textvariable=self.edge_var,
                values=list(EDGE_MODES),
                state="readonly",
                width=40,
            ),
            label_width=26,
            span=2,
        )

        self.tripod_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            opts,
            text="Tripod mode (lock the frame completely - for shots meant to be static)",
            variable=self.tripod_var,
        ).grid(row=6, column=0, columnspan=3, sticky="w")
        self.sharpen_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            opts, text="Light sharpening after stabilising (recommended)", variable=self.sharpen_var
        ).grid(row=7, column=0, columnspan=3, sticky="w")
        self.compare_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            opts,
            text="Comparison video: original | stabilised side by side",
            variable=self.compare_var,
        ).grid(row=8, column=0, columnspan=3, sticky="w")

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(
            out_lf, codecs=("h264", "hevc", "av1", "vp9", "prores", "dnxhr")
        )
        self.encoder.pack(anchor="w")
        self.out_row = SaveRow(
            out_lf,
            t("common.output_file"),
            suggest=lambda: self.suggest_output(self.src_row.path, "stabilised"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))

        self.btn_render = self.make_render_btn(
            body, t("deshaker.stabilise_button"), self._render, width=28
        )
        self.btn_render.pack(pady=px(8))
        self.status_lbl = tk.Label(body, text="", fg=CLR["fgdim"])
        self.status_lbl.pack()
        self.console = self.make_console_block(body, height=7)

    def _source_changed(self, path):
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "stabilised"))

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        try:
            zoom = common.number(self.zoom_var.get() or "0", "Extra zoom", -50, 100)
        except ValueError as exc:
            from tkinter import messagebox

            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "stabilised")
        if not out:
            return
        settings = ds.Stabilise(
            shakiness=self.shake_var.get(),
            accuracy=self.accuracy_var.get(),
            smoothing=self.smooth_var.get(),
            zoom_mode=ZOOM_MODES.get(self.zoom_mode.get(), 1),
            zoom=zoom,
            edges=EDGE_MODES.get(self.edge_var.get(), "keep"),
            tripod=self.tripod_var.get(),
            sharpen=self.sharpen_var.get(),
            compare=self.compare_var.get(),
        )
        two_pass = ds.vidstab_available()
        if not two_pass:
            self.log_tagged(
                self.console,
                "This FFmpeg build has no vid.stab - using the "
                "single-pass 'deshake' filter instead.",
                "warn",
            )
        container = common.container_of(out)
        duration = info.duration or None
        job = ds.DeshakeJob(
            src=src,
            settings=settings,
            two_pass=two_pass,
            venc=self.encoder.video_encode(container),
            audio=keep_audio_args(info, container),
            duration=duration,
        )

        def on_start(_tid):
            self.status_lbl.config(
                text="Stabilising… (analysis pass first)" if two_pass else "Stabilising…"
            )

        common.queue_job(
            self,
            common.Job(name="Deshaker", out=out, work=job.run, duration=duration, inputs=[src]),
            btn=self.btn_render,
            console=self.console,
            hooks=Hooks(on_start=on_start),
        )
