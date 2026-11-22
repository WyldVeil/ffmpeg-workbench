"""
core/ops/lut.py - command builders for tabs/visuals/lut.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

import os

from core import filters as fl

INTERPOLATIONS = ["tetrahedral", "trilinear", "pyramid", "prism", "nearest"]


_HALD_EXTS = (".png", ".tif", ".tiff")


def lut_kind(path: str) -> str:
    """'3d', '1d' or 'hald' for a LUT file."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _HALD_EXTS:
        return "hald"
    if ext == ".cube":
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for _ in range(200):
                    line = fh.readline()
                    if not line:
                        break
                    if line.strip().upper().startswith("LUT_1D_SIZE"):
                        return "1d"
                    if line.strip().upper().startswith("LUT_3D_SIZE"):
                        return "3d"
        except OSError:
            pass
    return "3d"


def build_chain(lut_path: str, *, intensity: float = 1.0, interp: str = "tetrahedral") -> str:
    """A single-input filter chain applying *lut_path* at *intensity* (0..1)."""
    kind = lut_kind(lut_path)
    if kind == "1d":
        one_d = {"nearest": "nearest", "trilinear": "linear"}.get(interp, "cubic")
        look = f"lut1d=file={fl.filter_path(lut_path)}:interp={one_d}"
    elif kind == "hald":
        look = ""
    else:
        look = fl.lut3d_filter(lut_path, interp if interp in INTERPOLATIONS else "tetrahedral")
    intensity = max(0.0, min(1.0, intensity))
    if kind == "hald":
        clut = f"movie=filename={fl.filter_path(lut_path)}[clut]"
        if intensity >= 0.999:
            return f"{clut};[in][clut]haldclut"
        return (
            f"{clut};[in]split[orig][grade];[grade][clut]haldclut[look];"
            f"[look][orig]blend=all_mode=normal:all_opacity={intensity:.3f}"
        )
    if intensity >= 0.999:
        return look
    return (
        f"split[orig][grade];[grade]{look}[look];"
        f"[look][orig]blend=all_mode=normal:all_opacity={intensity:.3f}"
    )
