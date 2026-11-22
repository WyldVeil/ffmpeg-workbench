"""
tabs/transcoder/scaler.py  -  Resolution Scaler and Framerate Interpolator

Resolution Scaler: any size (presets turn with portrait videos, custom sizes
and x2/x3/x4/half), keep the shape / add bars / crop / stretch, with the
right scaler for the direction - GPU upscaling through libplacebo (Vulkan)
when available, Lanczos + gentle sharpening on the CPU otherwise, area
averaging for big reductions.

Framerate Interpolator: change the frame rate by motion interpolation,
frame blending or plain drop/repeat, plus true slow motion.  Motion
interpolation (single-threaded in FFmpeg) is split into chunks that run in
parallel; chunk edges fall where source and output frames line up, so the
joined video stays in sync with its audio.
"""

from __future__ import annotations

import os
import tkinter as tk
from fractions import Fraction
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import filters as fl
from core import upscale
from core.i18n import t
from core.ops import scaler as scaler_ops
from core.ops.scaler import GPU_METHODS, geometry, rate_fraction, scale_chain
from core.ops.shared import RenderJob, frame_rate
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, BaseTab
from tabs.widgets import FileRow, output_section, scroll_body

SIZES = [
    ("8K (7680×4320)", 7680, 4320),
    ("4K UHD (3840×2160)", 3840, 2160),
    ("1440p (2560×1440)", 2560, 1440),
    ("1080p (1920×1080)", 1920, 1080),
    ("720p (1280×720)", 1280, 720),
    ("480p (854×480)", 854, 480),
    ("360p (640×360)", 640, 360),
    ("Twice the size (×2)", 2, 0),
    ("Three times (×3)", 3, 0),
    ("Four times (×4)", 4, 0),
    ("Half the size (½)", -2, 0),
    ("Custom size", 0, 0),
]
SHAPES = {
    "fit": "Keep the shape (fit inside the size)",
    "pad": "Keep the shape, add bars to fill the size",
    "crop": "Fill the size, crop the edges",
    "stretch": "Stretch to the size exactly",
}
METHODS = {
    "auto": "Automatic (best for the direction)",
    "gpu": "GPU - libplacebo EWA Lanczos sharp",
    "lanczos": "Lanczos (sharp)",
    "bicubic": "Bicubic (soft)",
    "area": "Area (smooth downscale)",
    "neighbor": "Nearest (pixel art)",
}
METHODS.update({key: "Upscale: " + spec[0] for key, spec in upscale.SHADERS.items()})


class ResolutionScalerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self._info = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.resolution_scaler"), t("scaler.subtitle"), icon="📐")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.src_var = self.src_row.var
        self.info_lbl = tk.Label(body, text="", fg=CLR["fgdim"], anchor="w")
        self.info_lbl.pack(fill="x", padx=px(20))
        self._build_size_options(body)
        self._build_output(body)

    def _build_size_options(self, body):
        """Size, shape, method and sharpening."""
        opt = tk.LabelFrame(
            body, text=f"  {t('scaler.scale_options_section')}  ", padx=px(12), pady=px(8)
        )
        opt.pack(fill="x", padx=px(20), pady=px(6))

        self.size_var = tk.StringVar(value=SIZES[3][0])
        sb = ttk.Combobox(
            opt,
            textvariable=self.size_var,
            values=[s[0] for s in SIZES],
            state="readonly",
            width=24,
        )
        self.w_var, self.h_var = tk.StringVar(value="1920"), tk.StringVar(value="1080")
        self.form_row(
            opt,
            0,
            "Size:",
            sb,
            tk.Entry(opt, textvariable=self.w_var, width=6),
            tk.Label(opt, text="×"),
            tk.Entry(opt, textvariable=self.h_var, width=6),
            label_width=10,
        )
        self.shape_var = tk.StringVar(value=SHAPES["fit"])
        self.form_row(
            opt,
            1,
            "Shape:",
            ttk.Combobox(
                opt,
                textvariable=self.shape_var,
                values=list(SHAPES.values()),
                state="readonly",
                width=40,
            ),
            label_width=10,
        )
        self.method_var = tk.StringVar(value=METHODS["auto"])
        have = set(upscale.available()) if fl.vulkan_ok() else set()
        methods = [
            v
            for k, v in METHODS.items()
            if k not in GPU_METHODS or (k == "gpu" and fl.vulkan_ok()) or k in have
        ]
        self.form_row(
            opt,
            2,
            "Method:",
            ttk.Combobox(
                opt, textvariable=self.method_var, values=methods, state="readonly", width=40
            ),
            label_width=10,
        )
        self.sharpen_var = tk.DoubleVar(value=0.3)
        self.form_row(
            opt,
            3,
            "Sharpen:",
            tk.Scale(
                opt,
                variable=self.sharpen_var,
                from_=0,
                to=1,
                resolution=0.05,
                orient="horizontal",
                length=px(200),
            ),
            tk.Label(opt, text="(when upscaling with Lanczos, Bicubic or GPU)", fg=CLR["fgdim"]),
            label_width=10,
        )
        self.result_lbl = tk.Label(opt, text="", fg=CLR["accent"])
        self.result_lbl.grid(row=4, column=0, columnspan=5, sticky="w", pady=px(4, 0))
        for v in (self.size_var, self.shape_var, self.w_var, self.h_var):
            v.trace_add("write", lambda *_a: self._update())

    def _build_output(self, body):
        """Encoder, where the result goes, the button and the log."""
        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9", "prores", "ffv1"),
            suggest=lambda: self.suggest_output(self.src_row.path, self._suffix()),
        )
        self.out_var = self.out_row.var
        self.btn_render = self.make_render_btn(body, "📐  SCALE VIDEO", self._render, width=26)
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=6)

    def _box(self, info) -> tuple:
        label = self.size_var.get()
        _l, a, b = next((s for s in SIZES if s[0] == label), SIZES[3])
        sw, sh = common.even_size(info)
        if a == 0:
            return (
                int(common.number(self.w_var.get(), "Width", 16, 16384)),
                int(common.number(self.h_var.get(), "Height", 16, 16384)),
            )
        if b == 0:
            f = 0.5 if a < 0 else a
            return sw * f, sh * f
        return (b, a) if sh > sw else (a, b)  # portrait videos turn the preset round

    def _shape(self):
        return next((k for k, v in SHAPES.items() if v == self.shape_var.get()), "fit")

    def _method(self):
        return next((k for k, v in METHODS.items() if v == self.method_var.get()), "auto")

    def _suffix(self):
        info = self._info
        if info is None or not info.has_video:
            return "scaled"
        try:
            _s, (ow, oh) = geometry(common.even_size(info), self._box(info), self._shape())
            return f"{ow}x{oh}"
        except ValueError:
            return "scaled"

    def _source_changed(self, path):
        self._info = info = ff.try_probe(path)
        if info is not None and info.has_video:
            w, h = common.even_size(info)
            self.info_lbl.config(
                text=f"Now {w}×{h} · {info.require_video().fps:.3g} fps · {ff.format_time(info.duration)}"
            )
        self.out_row.set(self.suggest_output(path, self._suffix()))
        self._update()

    def _update(self):
        info = self._info
        if info is None or not info.has_video:
            return
        try:
            (w, h), (ow, oh) = geometry(common.even_size(info), self._box(info), self._shape())
        except ValueError:
            self.result_lbl.config(text="")
            return
        sw, sh = common.even_size(info)
        verb = "Upscale" if w * h > sw * sh else "Downscale" if w * h < sw * sh else "Same size"
        self.result_lbl.config(text=f"{verb}: {sw}×{sh} → {ow}×{oh}")

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        self._info = info
        try:
            box = self._box(info)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        method = self._method()
        gpu = fl.vulkan_ok()
        try:
            chain = scale_chain(
                common.even_size(info),
                box,
                self._shape(),
                method,
                sharpen=float(self.sharpen_var.get()),
                gpu=gpu,
                pix_fmt="yuv420p10le" if self.encoder.ten_bit else "yuv420p",
                tags=upscale.tags_of(info.require_video()),
            )
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        if "libplacebo" in chain and not gpu:
            messagebox.showwarning(
                t("common.warning"), "GPU scaling isn't available on this computer.", parent=self
            )
            return
        out = common.output_for(self, self.out_row, src, self._suffix())
        if not out:
            return
        extra_pre = list(ff.VULKAN_DEVICE) if "libplacebo" in chain else []
        self.log(self.console, chain)
        common.render(
            self,
            RenderJob(
                src=src,
                info=info,
                out=out,
                chain=chain,
                name="Resolution Scaler",
                pre_input=extra_pre,
            ),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
        )


# ── Framerate Interpolator ───────────────────────────────────────────────────

RATES = ["23.976", "24", "25", "29.97", "30", "48", "50", "59.94", "60", "90", "120", "144"]
FPS_METHODS = {
    "motion": "Smooth motion (motion interpolation - slowest, best)",
    "blend": "Blend frames (fast, slight ghosting)",
    "dup": "Drop / repeat frames (fastest, no smoothing)",
}


class FPSInterpolatorTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self._info = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self,
            t("tab.framerate_interpolator"),
            "Change the frame rate smoothly, or make true slow motion.",
            icon="🎞",
        )
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.src_var = self.src_row.var
        self.info_lbl = tk.Label(body, text="", fg=CLR["fgdim"], anchor="w")
        self.info_lbl.pack(fill="x", padx=px(20))
        self._build_rate_options(body)
        self._build_output(body)

    def _build_rate_options(self, body):
        """The new rate, how frames are made, and slow motion."""
        opt = tk.LabelFrame(body, text="  Frame rate  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(6))
        r0 = tk.Frame(opt)
        r0.pack(anchor="w", pady=px(2))
        tk.Label(r0, text="New frame rate:").pack(side="left")
        self.fps_var = tk.StringVar(value="60")
        ttk.Combobox(r0, textvariable=self.fps_var, values=RATES, width=8).pack(
            side="left", padx=px(6)
        )
        tk.Label(r0, text="fps").pack(side="left")
        self.method_var = tk.StringVar(value=FPS_METHODS["motion"])
        ttk.Combobox(
            opt,
            textvariable=self.method_var,
            values=list(FPS_METHODS.values()),
            state="readonly",
            width=52,
        ).pack(anchor="w", pady=px(4))
        r1 = tk.Frame(opt)
        r1.pack(anchor="w", pady=px(2))
        self.slowmo_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r1, text="Slow motion: play", variable=self.slowmo_var).pack(side="left")
        self.factor_var = tk.StringVar(value="2")
        ttk.Combobox(
            r1, textvariable=self.factor_var, values=["1.5", "2", "3", "4", "8"], width=4
        ).pack(side="left")
        tk.Label(r1, text="× slower (new frames are made in between)").pack(side="left", padx=px(4))
        r2 = tk.Frame(opt)
        r2.pack(anchor="w", pady=px(2))
        self.audio_var = tk.StringVar(value="slow")
        tk.Label(r2, text="Slow-motion audio:").pack(side="left")
        tk.Radiobutton(
            r2, text="slow it down too (same pitch)", variable=self.audio_var, value="slow"
        ).pack(side="left")
        tk.Radiobutton(r2, text="remove it", variable=self.audio_var, value="none").pack(
            side="left"
        )
        self.parallel_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            opt,
            text=f"Use all processor cores for motion interpolation ({os.cpu_count() or 1} "
            "available)",
            variable=self.parallel_var,
        ).pack(anchor="w")

    def _build_output(self, body):
        """Encoder, where the result goes, the button and the log."""
        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "prores"),
            suggest=lambda: self.suggest_output(self.src_row.path, "interp"),
        )
        self.out_var = self.out_row.var
        self.btn_render = self.make_render_btn(body, "🎞  CHANGE FRAME RATE", self._render, width=26)
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=6)

    def _source_changed(self, path):
        self._info = info = ff.try_probe(path)
        if info is not None and info.has_video:
            self.info_lbl.config(
                text=f"Now {info.require_video().fps:.3f} fps · {ff.format_time(info.duration)}"
            )
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "interp"))

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        try:
            target = rate_fraction(self.fps_var.get())
            factor = (
                Fraction(self.factor_var.get()).limit_denominator(100)
                if self.slowmo_var.get()
                else Fraction(1)
            )
            if not 1 <= factor <= 16:
                raise ValueError("The slow-motion factor must be between 1 and 16.")
            src_rate = rate_fraction(frame_rate(info))
        except (ValueError, ZeroDivisionError) as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        method = next((k for k, v in FPS_METHODS.items() if v == self.method_var.get()), "motion")
        out = common.output_for(
            self,
            self.out_row,
            src,
            "slowmo" if factor > 1 else f"{float(target):.3g}fps".replace(".", "_"),
        )
        if not out:
            return
        container = os.path.splitext(out)[1].lstrip(".").lower() or "mp4"
        venc = self.encoder.video_encode(container)
        dur = info.duration
        out_dur = dur * float(factor)
        # slow motion: make factor x more frames, then stretch time so they play at the target rate
        work_rate = target * factor
        chain = scaler_ops.frame_rate_chain(method, target, factor)
        audio_mode = self.audio_var.get() if factor > 1 else "keep"
        parts = 1
        if method == "motion" and self.parallel_var.get():
            limit = 4 if venc.hw else max(1, (os.cpu_count() or 2) - 1)
            parts = max(1, min(limit, int(dur // 4)))
        pieces = scaler_ops.chunks(dur, scaler_ops.chunk_step(src_rate, work_rate), parts)
        self.log(
            self.console,
            f"{float(src_rate):.3f} → {float(target):.3f} fps"
            + (f", {float(factor):g}× slow motion" if factor > 1 else "")
            + (f", {len(pieces)} parallel pieces" if len(pieces) > 1 else ""),
        )

        job = scaler_ops.FrameRateJob(
            src, info, venc, chain, pieces, factor, audio_mode, out_dur, container
        )

        common.queue_job(
            self,
            common.Job(
                name="Framerate Interpolator", out=out, work=job.run, duration=out_dur, inputs=[src]
            ),
            btn=self.btn_render,
            console=self.console,
        )
