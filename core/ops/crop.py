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
