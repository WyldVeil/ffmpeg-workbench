"""
core/ops/rotate_flip.py - command builders for tabs/cutting/rotate_flip.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations


def reencode_chain(rot_cw: int, hflip: bool, vflip: bool) -> str:
    """Pixel filters: rotate clockwise by *rot_cw*, then mirror (as displayed)."""
    parts = {90: ["transpose=clock"], 180: ["hflip", "vflip"], 270: ["transpose=cclock"]}.get(
        rot_cw % 360, []
    )
    if hflip:
        parts.append("hflip")
    if vflip:
        parts.append("vflip")
    return ",".join(parts)


def lossless_args(existing_cw: int, rot_cw: int, hflip: bool, vflip: bool) -> list:
    """Input options that rewrite the orientation flag (display matrix): the
    total rotation, then mirroring as displayed (verified against the pixel
    filters for all 16 combinations, including already-rotated sources)."""
    total_cw = (existing_cw + rot_cw) % 360
    args = ["-display_rotation:v:0", str((-total_cw) % 360)]
    if hflip:
        args.append("-display_hflip:v:0")
    if vflip:
        args.append("-display_vflip:v:0")
    return args
