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

import math
import os
import tkinter as tk
from dataclasses import dataclass
from fractions import Fraction
from functools import partial
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import filters as fl
from core import tasks, upscale
from core.encoders import VideoEncode
from core.i18n import t
from core.ops.scaler import GPU_METHODS, fps_filter, geometry, rate_fraction, scale_chain
from core.ops.shared import RenderJob, final_copy_args, frame_rate, intermediate_args
from core.queue_manager import TaskContext
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, SaveRow, scroll_body

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


def _slow_audio(factor: Fraction) -> str:
    """atempo steps that stretch the sound *factor* times (keeping its pitch)."""
    tempo, steps = 1 / float(factor), []
    while tempo < 0.5:
        steps.append("atempo=0.5")
        tempo /= 0.5
    return ",".join([*steps, f"atempo={tempo:.6f}"])


@dataclass(frozen=True)
class _FrameRateJob:
    """A frame-rate change (or slow motion) in one go, or in pieces side by side."""

    src: str
    info: ff.MediaInfo
    venc: VideoEncode
    chain: str
    pieces: list[tuple[float, float]]
    factor: Fraction
    audio_mode: str  # keep | slow | none
    out_dur: float
    container: str

    def audio_args(self, input_index: int) -> list[str]:
        if not self.info.has_audio or self.audio_mode == "none":
            return ["-an"]
        slow = ["-af", _slow_audio(self.factor)] if self.audio_mode == "slow" else []
        return ["-map", f"{input_index}:a:0", *slow, "-c:a", "aac", "-b:a", "192k"]

    def run(self, _p: object, _c: object, ctx: TaskContext) -> int:
        final = ctx.output()
        if len(self.pieces) == 1:
            v = self.venc
            cmd = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y", *v.pre_input, "-i", self.src]
            cmd += [
                "-map",
                "0:v:0",
                *v.vf_args(self.chain),
                *v.args,
                *self.audio_args(0),
                "-map_metadata",
                "0",
                final,
            ]
            ctx.run(cmd, duration=self.out_dur)
            return 0
        tmp = ctx.tempdir()
        names = [os.path.join(tmp, f"piece{i:03d}.mkv") for i in range(len(self.pieces))]
        done = [0.0] * len(self.pieces)
        ctx.log(f"Interpolating {len(self.pieces)} pieces in parallel…")
        tasks.run_all(
            [partial(self._piece, ctx, i, names[i], done) for i in range(len(self.pieces))],
            name="FpsPiece",
        )
        listing = fl.write_concat_list(names, os.path.join(tmp, "pieces.txt"))
        ctx.log("Joining the pieces…")
        cmd = [
            ff.ffmpeg_path(),
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            listing,
        ]
        cmd += [
            "-i",
            self.src,
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            *final_copy_args(self.venc, self.container),
        ]
        cmd += [*self.audio_args(1), "-t", f"{self.out_dur:.3f}", "-map_metadata", "1", final]
        ctx.span(0.92, 1.0).run(cmd, duration=self.out_dur)
        return 0

    def _piece(self, ctx: TaskContext, i: int, name: str, done: list[float]) -> None:
        start, length = self.pieces[i]
        v = self.venc
        cmd = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y", *v.pre_input]
        cmd += ["-ss", f"{start:.6f}", "-t", f"{length:.6f}", "-i", self.src, "-map", "0:v:0"]
        cmd += [*v.vf_args(self.chain), *intermediate_args(v), "-an", name]
        stretched = length * float(self.factor)

        def prog(p: ff.Progress) -> None:
            if p.out_time is not None:
                done[i] = min(1.0, p.out_time / max(0.01, stretched))
                ctx.progress(0.92 * sum(done) / len(done))

        res = ff.run(cmd, duration=stretched, on_progress=prog, cancel=ctx.cancelled)
        if res.cancelled:
            raise ff.CancelledError("cancelled")
        if res.returncode != 0:
            raise RuntimeError(f"Piece {i + 1} failed:\n" + "\n".join(res.log_tail[-5:]))


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
        opt = tk.LabelFrame(
            body, text=f"  {t('scaler.scale_options_section')}  ", padx=px(12), pady=px(8)
        )
        opt.pack(fill="x", padx=px(20), pady=px(6))

        def row(r, label, *widgets):
            # each row lays out on its own, so a wide widget in one row can't push another row's apart
            tk.Label(opt, text=label, width=10, anchor="w").grid(
                row=r, column=0, sticky="w", pady=px(3)
            )
            holder = tk.Frame(opt)
            holder.grid(row=r, column=1, sticky="w", pady=px(3))
            for w in widgets:
                w.pack(in_=holder, side="left", padx=px(0, 6))
                w.lift(holder)

        self.size_var = tk.StringVar(value=SIZES[3][0])
        sb = ttk.Combobox(
            opt,
            textvariable=self.size_var,
            values=[s[0] for s in SIZES],
            state="readonly",
            width=24,
        )
        self.w_var, self.h_var = tk.StringVar(value="1920"), tk.StringVar(value="1080")
        row(
            0,
            "Size:",
            sb,
            tk.Entry(opt, textvariable=self.w_var, width=6),
            tk.Label(opt, text="×"),
            tk.Entry(opt, textvariable=self.h_var, width=6),
        )
        self.shape_var = tk.StringVar(value=SHAPES["fit"])
        row(
            1,
            "Shape:",
            ttk.Combobox(
                opt,
                textvariable=self.shape_var,
                values=list(SHAPES.values()),
                state="readonly",
                width=40,
            ),
        )
        self.method_var = tk.StringVar(value=METHODS["auto"])
        have = set(upscale.available()) if fl.vulkan_ok() else set()
        methods = [
            v
            for k, v in METHODS.items()
            if k not in GPU_METHODS or (k == "gpu" and fl.vulkan_ok()) or k in have
        ]
        row(
            2,
            "Method:",
            ttk.Combobox(
                opt, textvariable=self.method_var, values=methods, state="readonly", width=40
            ),
        )
        self.sharpen_var = tk.DoubleVar(value=0.3)
        row(
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
        )
        self.result_lbl = tk.Label(opt, text="", fg=CLR["accent"])
        self.result_lbl.grid(row=4, column=0, columnspan=5, sticky="w", pady=px(4, 0))
        for v in (self.size_var, self.shape_var, self.w_var, self.h_var):
            v.trace_add("write", lambda *_a: self._update())

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9", "prores", "ffv1"))
        self.encoder.pack(anchor="w")
        self.out_row = SaveRow(
            out_lf,
            t("common.output_file"),
            suggest=lambda: self.suggest_output(self.src_row.path, self._suffix()),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.out_var = self.out_row.var
        self.btn_render = tk.Button(
            body,
            text="📐  SCALE VIDEO",
            font=(UI_FONT, 12, "bold"),
            bg=CLR["green"],
            fg="white",
            width=26,
            command=self._render,
        )
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=6)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

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
        extra_pre = ["-init_hw_device", "vulkan"] if "libplacebo" in chain else []
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


def chunk_step(src: Fraction, dst: Fraction) -> Fraction:
    """Shortest time after which source and output frames line up again."""
    return Fraction(
        math.lcm(src.denominator, dst.denominator), math.gcd(src.numerator, dst.numerator)
    )


def chunks(duration: float, step: Fraction, parts: int) -> list:
    """[(start, length)] cut on multiples of *step*, about *parts* pieces."""
    if parts <= 1 or duration < float(step) * 2:
        return [(0.0, duration)]
    size = max(1, round(duration / parts / float(step))) * step
    out, t0 = [], Fraction(0)
    while float(t0) < duration - 1e-6:
        length = min(float(size), duration - float(t0))
        out.append((float(t0), length))
        t0 += size
    if (
        len(out) > 1 and out[-1][1] < float(size) / 2
    ):  # fold a short leftover into the previous piece
        a, la = out[-2]
        out[-2:] = [(a, la + out[-1][1])]
    return out


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

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "prores"))
        self.encoder.pack(anchor="w")
        self.out_row = SaveRow(
            out_lf,
            t("common.output_file"),
            suggest=lambda: self.suggest_output(self.src_row.path, "interp"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.out_var = self.out_row.var
        self.btn_render = tk.Button(
            body,
            text="🎞  CHANGE FRAME RATE",
            font=(UI_FONT, 12, "bold"),
            bg=CLR["green"],
            fg="white",
            width=26,
            command=self._render,
        )
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=6)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

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
        chain = fps_filter(method, work_rate)
        if factor > 1:
            r = f"{target.numerator}/{target.denominator}"
            chain += f",setpts={float(factor):.6f}*PTS,fps={r}"
        audio_mode = self.audio_var.get() if factor > 1 else "keep"
        parts = 1
        if method == "motion" and self.parallel_var.get():
            limit = 4 if venc.hw else max(1, (os.cpu_count() or 2) - 1)
            parts = max(1, min(limit, int(dur // 4)))
        pieces = chunks(dur, chunk_step(src_rate, work_rate), parts)
        self.log(
            self.console,
            f"{float(src_rate):.3f} → {float(target):.3f} fps"
            + (f", {float(factor):g}× slow motion" if factor > 1 else "")
            + (f", {len(pieces)} parallel pieces" if len(pieces) > 1 else ""),
        )

        job = _FrameRateJob(src, info, venc, chain, pieces, factor, audio_mode, out_dur, container)

        common.queue_job(
            self,
            common.Job(
                name="Framerate Interpolator", out=out, work=job.run, duration=out_dur, inputs=[src]
            ),
            btn=self.btn_render,
            console=self.console,
        )
