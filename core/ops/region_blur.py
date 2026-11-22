"""
core/ops/region_blur.py - command builders for tabs/visuals/region_blur.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import ffmpeg as ff

KINDS = {"blur": "Blur", "pixelate": "Pixelate", "box": "Solid black box"}


@dataclass
class Region:
    x: float
    y: float
    w: float
    h: float
    kind: str = "blur"
    strength: float = 0.6
    start: float = 0.0
    end: float | None = None

    def pixels(self, width: int, height: int) -> tuple:
        x = min(max(0, int(self.x * width) // 2 * 2), width - 4)
        y = min(max(0, int(self.y * height) // 2 * 2), height - 4)
        w = max(4, min(width - x, round(self.w * width / 2) * 2))
        h = max(4, min(height - y, round(self.h * height / 2) * 2))
        return x, y, w, h

    def enable(self) -> str:
        return (
            f"between(t,{self.start:.3f},{self.end:.3f})"
            if self.end is not None
            else f"gte(t,{self.start:.3f})"
        )

    def label(self) -> str:
        span = f"{ff.format_time(self.start)} → {ff.format_time(self.end) if self.end is not None else 'end'}"
        return (
            f"{KINDS[self.kind]:<16} {span}   at {self.x * 100:.0f}%,{self.y * 100:.0f}%  "
            f"{self.w * 100:.0f}×{self.h * 100:.0f}%"
        )


def blur_graph(regions, width: int, height: int, src: str = "[0:v]", out: str = "[v]") -> str:
    """filter_complex applying every region in order."""
    soft = [r for r in regions if r.kind != "box"]
    parts = []
    cur = src
    if soft:
        parts.append(
            f"{src}split={len(soft) + 1}[base]" + "".join(f"[c{i}]" for i in range(len(soft)))
        )
        cur = "[base]"
    for i, r in enumerate(soft):
        x, y, w, h = r.pixels(width, height)
        if r.kind == "pixelate":
            block = max(4, int(min(w, h) * (0.04 + r.strength * 0.16)))
            fx = f"scale={max(1, w // block)}:{max(1, h // block)}:flags=area,scale={w}:{h}:flags=neighbor"
        else:
            fx = f"gblur=sigma={max(2.0, r.strength * min(w, h) / 5):.1f}:steps=3"
        parts.append(f"[c{i}]crop={w}:{h}:{x}:{y},{fx}[e{i}]")
        parts.append(f"{cur}[e{i}]overlay={x}:{y}:enable='{r.enable()}'[o{i}]")
        cur = f"[o{i}]"
    for j, r in enumerate(x for x in regions if x.kind == "box"):
        x, y, w, h = r.pixels(width, height)
        parts.append(
            f"{cur}drawbox=x={x}:y={y}:w={w}:h={h}:color=black@1:t=fill:enable='{r.enable()}'[d{j}]"
        )
        cur = f"[d{j}]"
    parts.append(f"{cur}null{out}")
    return ";".join(parts)
