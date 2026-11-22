"""
core/ops/zoom.py - Animated Zoom (tabs/visuals/zoom.py): punch-ins, pulses, Ken
Burns moves and keyframed pans as expressions of time, drawn with zoompan (or
libplacebo on the GPU).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _progress(a: float, b: float) -> str:
    return f"clip((T-{a:.3f})/{max(b - a, 0.001):.3f},0,1)"


def _ease(p: str, kind: str) -> str:
    if kind == "linear":
        return p
    if kind == "snappy":
        return f"(1-pow(1-{p},3))"
    return f"({p}*{p}*(3-2*{p}))"


def zoom_exprs(mode: str, p: Mapping[str, Any], duration: float) -> tuple[str, str, str]:
    """(zoom, focus_x, focus_y) expressions in T (seconds); focus as 0..1 of the frame."""
    ease = p.get("ease", "smooth")
    if mode == "punch":
        ramp = 0.18 if ease == "snappy" else 0.5
        a, b = p["start"], max(p["end"], p["start"] + 2 * ramp)
        e = f"({_ease(_progress(a, a + ramp), ease)}-{_ease(_progress(b - ramp, b), ease)})"
        return f"1+{p['zoom'] - 1:.4f}*{e}", f"{p['x'] / 100:.4f}", f"{p['y'] / 100:.4f}"
    if mode == "pulse":
        e = f"sin(PI*{_progress(p['time'], p['time'] + p['dur'])})"
        return f"1+{p['zoom'] - 1:.4f}*{e}", f"{p['x'] / 100:.4f}", f"{p['y'] / 100:.4f}"
    if mode == "kenburns":
        e = _ease(_progress(0, duration), ease)
        z = p["zoom"]
        d = p["dir"]
        if d == "in":
            return f"1+{z - 1:.4f}*{e}", "0.5", "0.5"
        if d == "out":
            return f"{z:.4f}-{z - 1:.4f}*{e}", "0.5", "0.5"
        if d in ("lr", "rl"):
            return f"{z:.4f}", (f"({e})" if d == "lr" else f"(1-{e})"), "0.5"
        return f"{z:.4f}", "0.5", (f"({e})" if d == "tb" else f"(1-{e})")
    # keyframes
    e = _ease(_progress(p["t0"], p["t1"]), ease)
    (x0, y0, z0), (x1, y1, z1) = p["from"], p["to"]
    return (
        f"{z0:.4f}+{z1 - z0:.4f}*{e}",
        f"{x0 / 100:.4f}+{(x1 - x0) / 100:.4f}*{e}",
        f"{y0 / 100:.4f}+{(y1 - y0) / 100:.4f}*{e}",
    )


def zoom_filter(
    exprs: tuple[str, str, str],
    size: tuple[int, int],
    fps: str,
    *,
    gpu: bool,
    pix_fmt: str = "yuv420p",
    supersample: bool = True,
) -> str:
    z, fx, fy = exprs
    w, h = size
    if gpu:
        z, fx, fy = (e.replace("T", "t") for e in (z, fx, fy))
        return (
            f"libplacebo=w={w}:h={h}:crop_w='iw/max(1,{z})':crop_h='ih/max(1,{z})':"
            f"crop_x='clip(({fx})*iw-crop_w/2,0,iw-crop_w)':crop_y='clip(({fy})*ih-crop_h/2,0,ih-crop_h)':"
            f"format={pix_fmt}"
        )
    z, fx, fy = (e.replace("T", "it") for e in (z, fx, fy))
    pre = "scale=iw*2:ih*2:flags=bicubic," if supersample else ""
    return (
        f"{pre}zoompan=z='max(1,{z})':x='clip(({fx})*iw-iw/zoom/2,0,iw-iw/zoom)':"
        f"y='clip(({fy})*ih-ih/zoom/2,0,ih-ih/zoom)':d=1:s={w}x{h}:fps={fps},setsar=1"
    )
