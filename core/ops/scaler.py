"""
core/ops/scaler.py - command builders for tabs/transcoder/scaler.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

from fractions import Fraction

from core import upscale

GPU_METHODS = {"gpu", *upscale.SHADERS}


def _even(x: float) -> int:
    return max(2, round(x / 2) * 2)


def geometry(src: tuple, box: tuple, shape: str) -> tuple:
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
    src: tuple,
    box: tuple,
    shape: str,
    method: str,
    *,
    sharpen: float = 0.0,
    gpu: bool = False,
    pix_fmt: str = "yuv420p",
    tags: dict | None = None,
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
