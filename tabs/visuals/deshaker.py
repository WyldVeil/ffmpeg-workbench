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

from core import ffmpeg as ff
from core.i18n import t
from core.ops.shared import keep_audio_args
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, scroll_body

ZOOM_MODES = {
    "Auto zoom to hide borders (recommended)": 1,
    "Adaptive zoom (zooms only when needed)": 2,
    "No automatic zoom": 0,
}
EDGE_MODES = {"Keep previous frame at edges": "keep", "Black edges": "black"}
TRF = "transforms.trf"  # relative: pass 1 and 2 run inside the job's temp dir


def detect_filter(shakiness: int, accuracy: int, tripod: bool) -> str:
    f = f"vidstabdetect=shakiness={shakiness}:accuracy={accuracy}:result={TRF}"
    return f + (":tripod=1" if tripod else "")


def transform_filter(
    smoothing: int, zoom_mode: int, zoom: float, edges: str, tripod: bool, sharpen: bool
) -> str:
    f = (
        f"vidstabtransform=input={TRF}:smoothing={smoothing}:optzoom={zoom_mode}"
        f":zoom={zoom:g}:crop={edges}:interpol=bicubic"
    )
    if tripod:
        f += ":tripod=1"
    if sharpen:
        f += ",unsharp=5:5:0.8:3:3:0.4"
    return f


def fallback_filter(edges: str) -> str:
    return f"deshake=rx=32:ry=32:edge={'blank' if edges == 'black' else 'clamp'}"


class DeshakerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._build_ui()

    def _build_ui(self):
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
            tk.Label(opts, text=label, anchor="w", width=26).grid(row=row, column=0, sticky="w")
            tk.Scale(opts, variable=var, from_=lo, to=hi, orient="horizontal", length=px(220)).grid(
                row=row, column=1, sticky="w"
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

        tk.Label(opts, text="Zoom:", anchor="w", width=26).grid(
            row=3, column=0, sticky="w", pady=px(3)
        )
        self.zoom_mode = tk.StringVar(value=next(iter(ZOOM_MODES)))
        ttk.Combobox(
            opts, textvariable=self.zoom_mode, values=list(ZOOM_MODES), state="readonly", width=40
        ).grid(row=3, column=1, columnspan=2, sticky="w")
        tk.Label(opts, text="Extra zoom (%):", anchor="w", width=26).grid(
            row=4, column=0, sticky="w"
        )
        self.zoom_var = tk.StringVar(value="0")
        tk.Entry(opts, textvariable=self.zoom_var, width=6).grid(row=4, column=1, sticky="w")
        tk.Label(opts, text="Edges:", anchor="w", width=26).grid(
            row=5, column=0, sticky="w", pady=px(3)
        )
        self.edge_var = tk.StringVar(value=next(iter(EDGE_MODES)))
        ttk.Combobox(
            opts, textvariable=self.edge_var, values=list(EDGE_MODES), state="readonly", width=40
        ).grid(row=5, column=1, columnspan=2, sticky="w")

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
        self.out_row = FileRow(
            out_lf,
            t("common.output_file"),
            save=True,
            suggest=lambda: self.suggest_output(self.src_row.path, "stabilised"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))

        self.btn_render = tk.Button(
            body,
            text=t("deshaker.stabilise_button"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["green"],
            fg="white",
            width=28,
            cursor="hand2",
            command=self._render,
        )
        self.btn_render.pack(pady=px(8))
        self.status_lbl = tk.Label(body, text="", fg=CLR["fgdim"])
        self.status_lbl.pack()
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=7)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

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
        edges = EDGE_MODES.get(self.edge_var.get(), "keep")
        tripod = self.tripod_var.get()
        two_pass = ff.has_filter("vidstabdetect") and ff.has_filter("vidstabtransform")
        if two_pass:
            stab = transform_filter(
                self.smooth_var.get(),
                ZOOM_MODES.get(self.zoom_mode.get(), 1),
                zoom,
                edges,
                tripod,
                self.sharpen_var.get(),
            )
            detect = detect_filter(self.shake_var.get(), self.accuracy_var.get(), tripod)
        else:
            self.log_tagged(
                self.console,
                "This FFmpeg build has no vid.stab - using the "
                "single-pass 'deshake' filter instead.",
                "warn",
            )
            stab, detect = fallback_filter(edges), None
        if self.compare_var.get():
            graph = f"[0:v]setsar=1,split[a][b];[b]{stab},setsar=1[s];[a][s]hstack=inputs=2[v]"
        else:
            graph = f"[0:v]{stab}[v]"

        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        if venc.filter_suffix:
            graph += f";[v]{venc.filter_suffix}[venc]"
        vlabel = "[venc]" if venc.filter_suffix else "[v]"
        duration = info.duration or None
        encode = [
            ff.ffmpeg_path(),
            "-y",
            *venc.pre_input,
            "-i",
            src,
            "-filter_complex",
            graph,
            "-map",
            vlabel,
            *keep_audio_args(info, container),
            *venc.args,
        ]

        def work(_progress, _cancelled, ctx):
            workdir = ctx.tempdir()
            if detect:
                ctx.log("Pass 1 of 2 - analysing camera motion…")
                ctx.span(0.0, 0.4).run(
                    [
                        ff.ffmpeg_path(),
                        "-y",
                        "-i",
                        src,
                        "-an",
                        "-sn",
                        "-vf",
                        detect,
                        "-f",
                        "null",
                        "-",
                    ],
                    duration=duration,
                    cwd=workdir,
                )
                ctx.log("Pass 2 of 2 - stabilising and encoding…")
                ctx.span(0.4, 1.0)
            ctx.run([*encode, ctx.output()], duration=duration, cwd=workdir)
            return 0

        def on_start(_tid):
            self.status_lbl.config(
                text="Stabilising… (analysis pass first)" if detect else "Stabilising…"
            )

        common.queue_job(
            self,
            name="Deshaker",
            out=out,
            work=work,
            btn=self.btn_render,
            console=self.console,
            duration=duration,
            inputs=[src],
            on_start=on_start,
        )
