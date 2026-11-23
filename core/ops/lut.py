"""
core/ops/lut.py - command builders for tabs/visuals/lut.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable

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


# ── Built-in looks (generated .cube files, no licensing strings attached) ────


def _clip(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _luma(r: float, g: float, b: float) -> float:
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _smooth(e0: float, e1: float, x: float) -> float:
    x = _clip((x - e0) / (e1 - e0))
    return x * x * (3 - 2 * x)


def _teal_orange(r: float, g: float, b: float) -> tuple[float, float, float]:
    w = _smooth(0.15, 0.85, _luma(r, g, b))
    return r * (0.86 + 0.24 * w), g * (0.98 + 0.02 * w), b * (1.14 - 0.30 * w)


def _bleach(r: float, g: float, b: float) -> tuple[float, float, float]:
    y = _luma(r, g, b)

    def look(x: float) -> float:
        return 0.5 + (x + (y - x) * 0.6 - 0.5) * 1.3

    return look(r), look(g), look(b)


def _warm_film(r: float, g: float, b: float) -> tuple[float, float, float]:
    return 0.035 + 0.94 * (r * 1.07), 0.035 + 0.94 * (g * 1.01), 0.035 + 0.94 * (b * 0.9)


def _cool_night(r: float, g: float, b: float) -> tuple[float, float, float]:
    return r * 0.78, g * 0.9, b * 1.08


def _sepia(r: float, g: float, b: float) -> tuple[float, float, float]:
    return (
        0.393 * r + 0.769 * g + 0.189 * b,
        0.349 * r + 0.686 * g + 0.168 * b,
        0.272 * r + 0.534 * g + 0.131 * b,
    )


def _cross_process(r: float, g: float, b: float) -> tuple[float, float, float]:
    def s(x: float) -> float:
        return 0.5 - 0.5 * math.cos(math.pi * _clip(x))

    return s(r) * 1.05, g * 1.06 + 0.02, 0.12 + 0.76 * b


def _vintage(r: float, g: float, b: float) -> tuple[float, float, float]:
    y = _luma(r, g, b)

    def look(x: float) -> float:
        return 0.08 + 0.82 * (x + (y - x) * 0.25)

    return look(r * 1.04), look(g), look(b * 0.92)


def _mono_contrast(r: float, g: float, b: float) -> tuple[float, float, float]:
    y = _luma(r, g, b)
    y = 0.5 + (y - 0.5) * 1.25
    return y, y, y


LookFn = Callable[[float, float, float], tuple[float, float, float]]

#: the built-in looks: name -> colour mapping (0..1 RGB in and out)
BUILTIN: dict[str, LookFn] = {
    "Teal & orange": _teal_orange,
    "Bleach bypass": _bleach,
    "Warm film": _warm_film,
    "Cool night": _cool_night,
    "Sepia": _sepia,
    "Cross process": _cross_process,
    "Vintage fade": _vintage,
    "Contrasty black & white": _mono_contrast,
}


def builtin_lut_path(name: str, size: int = 33) -> str:
    """Path of the generated .cube for built-in look *name* (created once)."""
    from core.paths import cache_dir

    folder = os.path.join(cache_dir(), "luts")
    os.makedirs(folder, exist_ok=True)
    slug = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
    path = os.path.join(folder, f"{slug}_{size}.cube")
    if os.path.isfile(path):
        return path
    fn = BUILTIN[name]
    step = 1.0 / (size - 1)
    lines = [f'TITLE "{name}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    for bi in range(size):  # .cube order: red varies fastest
        for gi in range(size):
            for ri in range(size):
                r, g, b = fn(ri * step, gi * step, bi * step)
                r, g, b = _clip(r), _clip(g), _clip(b)
                lines.append(f"{r:.6f} {g:.6f} {b:.6f}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="ascii", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return path
