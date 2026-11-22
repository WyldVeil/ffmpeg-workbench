"""
core/ops/denoise.py - command builders for tabs/visuals/denoise.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations


def denoise_chain(method: str, strength: float, *, gpu: bool = False, ten_bit: bool = False) -> str:
    """Filter chain for *strength* 1..10 (CPU chain unless *gpu*; *ten_bit* keeps
    10-bit precision through the GPU round trip)."""
    s = max(0.5, min(10.0, strength))
    if method == "nlm":
        sigma = 1.0 + s * 0.8
        if gpu:
            fmt = "yuv420p10le" if ten_bit else "yuv420p"
            return f"format={fmt},hwupload,nlmeans_vulkan=s={sigma:.2f}:p=7:r=15,hwdownload,format={fmt}"
        return f"nlmeans=s={sigma:.2f}:p=7:r=15"
    if method == "temporal":
        return (
            f"atadenoise=0a={0.01 + s * 0.006:.3f}:0b={0.02 + s * 0.012:.3f}:"
            f"1a={0.01 + s * 0.006:.3f}:1b={0.02 + s * 0.012:.3f}:s={min(99, 5 + int(s) * 2) | 1}"
        )
    if method == "grain":
        return f"fftdnoiz=sigma={s * 2.5:.1f}:amount=1:overlap=0.5"
    return f"hqdn3d={s * 0.8:.2f}:{s * 0.6:.2f}:{s * 1.2:.2f}:{s * 0.9:.2f}"
