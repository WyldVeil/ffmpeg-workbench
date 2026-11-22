"""
core/ops/crop.py - command builders for tabs/cutting/crop.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations


def fit_aspect(vw: int, vh: int, rw: int, rh: int) -> tuple[int, int, int, int]:
    """Largest centred rectangle of aspect rw:rh inside vw x vh (even sizes)."""
    if vw / vh > rw / rh:
        h = vh
        w = int(h * rw / rh)
    else:
        w = vw
        h = int(w * rh / rw)
    w, h = min(w, vw) // 2 * 2, min(h, vh) // 2 * 2
    return (vw - w) // 4 * 2, (vh - h) // 4 * 2, w, h


def crop_chain(x: int, y: int, w: int, h: int, scale_to: tuple[int, int] | None = None) -> str:
    """Cut out w x h at (x, y); then, if asked, fit it (letterboxed) into *scale_to*."""
    chain = f"crop={w}:{h}:{x}:{y}"
    if scale_to is not None:
        sw, sh = scale_to
        chain += f",scale={sw}:{sh}:force_original_aspect_ratio=decrease:flags=lanczos,pad={sw}:{sh}:(ow-iw)/2:(oh-ih)/2"
    return chain + ",setsar=1"
