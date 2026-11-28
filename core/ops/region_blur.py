"""
core/ops/region_blur.py - command builders for tabs/visuals/region_blur.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

from collections.abc import Sequence
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

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
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


def blur_graph(
    regions: Sequence[Region], width: int, height: int, src: str = "[0:v]", out: str = "[v]"
) -> str:
    """filter_complex applying every region in order."""
    soft = [r for r in regions if r.kind != "box"]
    parts: list[str] = []
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


@dataclass(frozen=True)
class Cover:
    """Something of the person's own laid over every hidden box: an *image*
    (a logo) or a line of *text*, at most *fill* of the box, at *opacity*."""

    image: str = ""
    text: str = ""
    fill: float = 0.85
    opacity: float = 1.0

    @property
    def wanted(self) -> bool:
        return bool(self.image or self.text.strip())


def cover_graph(
    regions: Sequence[Region], width: int, height: int, cover: Cover, src: str, out: str
) -> str:
    """filter_complex laying *cover* centred over each region while it shows
    (after :func:`blur_graph`, so it sits on top of the blur)."""
    from core import filters as fl

    if not regions or not cover.wanted:
        return f"{src}null{out}"
    parts: list[str] = []
    cur = src
    fill = min(max(cover.fill, 0.2), 1.0)
    if cover.image:
        fade = f"colorchannelmixer=aa={cover.opacity:.2f}," if cover.opacity < 1 else ""
        labels = "".join(f"[k{i}]" for i in range(len(regions)))
        parts.append(
            f"{fl.preview_movie(cover.image)}format=rgba,{fade}split={len(regions)}{labels}"
        )
    for i, r in enumerate(regions):
        x, y, w, h = r.pixels(width, height)
        if cover.image:
            bw, bh = max(2, int(w * fill)), max(2, int(h * fill))
            parts.append(f"[k{i}]scale={bw}:{bh}:force_original_aspect_ratio=decrease[s{i}]")
            parts.append(
                f"{cur}[s{i}]overlay=x={x}+({w}-overlay_w)/2:y={y}+({h}-overlay_h)/2"
                f":shortest=1:enable='{r.enable()}'[w{i}]"
            )
        else:
            text = cover.text.strip()
            size = max(8, int(min(h * 0.7, w / (0.6 * max(1, len(text)))) * fill))
            draw = fl.DrawText(
                text=text, font="sans", fontsize=size, x=f"{x}+({w}-text_w)/2",
                y=f"{y}+({h}-text_h)/2", alpha=f"{cover.opacity:.2f}", enable=r.enable(),
                shadowx=max(1, size // 16), shadowy=max(1, size // 16),
            )  # fmt: skip
            parts.append(f"{cur}{draw.filter()}[w{i}]")
        cur = f"[w{i}]"
    parts.append(f"{cur}null{out}")
    return ";".join(parts)
