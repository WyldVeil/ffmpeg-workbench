"""
core/ops/scaler.py - command builders for tabs/transcoder/scaler.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from functools import partial

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl
from core import tasks, upscale
from core.ops.shared import final_copy_args, intermediate_args
from core.queue_manager import TaskContext

GPU_METHODS = {"gpu", *upscale.SHADERS}


Size = tuple[float, float]  # width, height


def _even(x: float) -> int:
    return max(2, round(x / 2) * 2)


def geometry(src: Size, box: Size, shape: str) -> tuple[tuple[int, int], tuple[int, int]]:
    """((scaled_w, scaled_h), (out_w, out_h)) for *shape*."""
    sw, sh = src
    bw, bh = box
    if shape == "stretch":
        return (_even(bw), _even(bh)), (_even(bw), _even(bh))
    ratio = (max if shape == "crop" else min)(bw / sw, bh / sh)
    scaled = (_even(sw * ratio), _even(sh * ratio))
    if shape == "fit":
        return scaled, scaled
    return scaled, (_even(bw), _even(bh))


def scale_chain(
    src: Size,
    box: Size,
    shape: str,
    method: str,
    *,
    sharpen: float = 0.0,
    gpu: bool = False,
    pix_fmt: str = "yuv420p",
    tags: Mapping[str, str] | None = None,
) -> str:
    """Filter graph scaling *src* into *box*.  GPU methods (libplacebo, with
    or without an upscaling shader) process the brightness plane only, see
    core/upscale.py; they need ``-init_hw_device vulkan``."""
    (w, h), (ow, oh) = geometry(src, box, shape)
    up = w * h > src[0] * src[1]
    if method == "auto":
        if up:
            method = "fsr" if gpu and upscale.shader_path("fsr") else "lanczos"
        else:
            method = "area" if w * 2 <= src[0] else "lanczos"
    if method in upscale.SHADERS and not up:
        method = "gpu"  # upscaling shaders do nothing when shrinking
    if method in GPU_METHODS:
        chain = [
            upscale.plane_graph(
                (w, h),
                shader=method if method in upscale.SHADERS else None,
                deep=pix_fmt.endswith("10le"),
                tags=tags,
            ),
            "setsar=1",
        ]
    else:
        chain = [f"scale={w}:{h}:flags={method}+accurate_rnd+full_chroma_int", "setsar=1"]
    if sharpen > 0 and up and method not in upscale.SHADERS:
        chain.append(f"cas=strength={min(1.0, sharpen):.2f}")
    if shape == "pad":
        chain.append(f"pad={ow}:{oh}:(ow-iw)/2:(oh-ih)/2:color=black")
    elif shape == "crop":
        chain.append(f"crop={ow}:{oh}")
    return ",".join(chain)


_NTSC = {
    "23.976": Fraction(24000, 1001),
    "29.97": Fraction(30000, 1001),
    "59.94": Fraction(60000, 1001),
    "119.88": Fraction(120000, 1001),
}


def rate_fraction(text: str) -> Fraction:
    text = text.strip()
    if text in _NTSC:
        return _NTSC[text]
    if "/" in text:
        a, b = text.split("/", 1)
        return Fraction(int(a), int(b))
    value = Fraction(text).limit_denominator(1001)
    if value <= 0 or value > 1000:
        raise ValueError("The frame rate must be between 1 and 1000.")
    return value


def fps_filter(method: str, target: Fraction) -> str:
    r = f"{target.numerator}/{target.denominator}"
    if method == "motion":
        return f"minterpolate=fps={r}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
    if method == "blend":
        return f"framerate=fps={r}"
    return f"fps={r}"


# ── Frame rate and slow motion ───────────────────────────────────────────────


def frame_rate_chain(method: str, target: Fraction, factor: Fraction) -> str:
    """Interpolate to *target* fps; for slow motion (*factor* > 1) make factor x
    more frames, then stretch time so they play back at the target rate."""
    chain = fps_filter(method, target * factor)
    if factor > 1:
        chain += f",setpts={float(factor):.6f}*PTS,fps={target.numerator}/{target.denominator}"
    return chain


def slow_audio(factor: Fraction) -> str:
    """atempo steps that stretch the sound *factor* times (keeping its pitch)."""
    tempo, steps = 1 / float(factor), list[str]()
    while tempo < 0.5:
        steps.append("atempo=0.5")
        tempo /= 0.5
    return ",".join([*steps, f"atempo={tempo:.6f}"])


def chunk_step(src: Fraction, dst: Fraction) -> Fraction:
    """Shortest time after which source and output frames line up again."""
    return Fraction(
        math.lcm(src.denominator, dst.denominator), math.gcd(src.numerator, dst.numerator)
    )


def chunks(duration: float, step: Fraction, parts: int) -> list[tuple[float, float]]:
    """[(start, length)] cut on multiples of *step*, about *parts* pieces."""
    if parts <= 1 or duration < float(step) * 2:
        return [(0.0, duration)]
    size = max(1, round(duration / parts / float(step))) * step
    out: list[tuple[float, float]] = []
    t0 = Fraction(0)
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


@dataclass(frozen=True)
class FrameRateJob:
    """A frame-rate change (or slow motion) in one go, or in pieces side by side."""

    src: str
    info: ff.MediaInfo
    venc: enc.VideoEncode
    chain: str
    pieces: list[tuple[float, float]]
    factor: Fraction
    audio_mode: str  # keep | slow | none
    out_dur: float
    container: str

    def audio_args(self, input_index: int) -> list[str]:
        if not self.info.has_audio or self.audio_mode == "none":
            return ["-an"]
        slow = ["-af", slow_audio(self.factor)] if self.audio_mode == "slow" else []
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
            done[i] = min(1.0, p.out_time / max(0.01, stretched))
            ctx.progress(0.92 * sum(done) / len(done))

        res = ff.run(cmd, duration=stretched, on_progress=prog, cancel=ctx.cancelled)
        if res.cancelled:
            raise ff.CancelledError("cancelled")
        if res.returncode != 0:
            raise RuntimeError(f"Piece {i + 1} failed:\n" + "\n".join(res.log_tail[-5:]))
