"""
core/ops/text_watermark.py - the text watermark for tabs/social/watermark.py.

Pure functions: values in, a libass subtitle script out (core/ass.py) - no
widgets, no Tk.  Places, time ranges, the bouncing (DVD style) path and the
jumping spots.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from core import ass
from core import ffmpeg as ff

Segment = tuple[float, float, float, float, float, float]  # t0, t1, x0, y0, x1, y1

PLACES = {
    "tl": "Top left",
    "tc": "Top centre",
    "tr": "Top right",
    "ml": "Middle left",
    "center": "Centre",
    "mr": "Middle right",
    "bl": "Bottom left",
    "bc": "Bottom centre",
    "br": "Bottom right",
    "custom": "Custom spot (%)",
    "bounce": "Bouncing (DVD style)",
    "jump": "Jumping around",
}
_ANCHOR = {
    "tl": (7, 0, 0),
    "tc": (8, 0.5, 0),
    "tr": (9, 1, 0),
    "ml": (4, 0, 0.5),
    "center": (5, 0.5, 0.5),
    "mr": (6, 1, 0.5),
    "bl": (1, 0, 1),
    "bc": (2, 0.5, 1),
    "br": (3, 1, 1),
}


def parse_ranges(text: str) -> list[tuple[float, float]]:
    """'0-5, 20-25.5, 1:00-1:10' -> [(0, 5), (20, 25.5), (60, 70)]."""
    out: list[tuple[float, float]] = []
    for part in (text or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        a, sep, b = part.partition("-")
        if not sep:
            raise ValueError(f"'{part}' is not a range like 5-10.")
        start, end = ff.parse_time(a.strip()), ff.parse_time(b.strip())
        if end <= start:
            raise ValueError(f"In '{part}' the end must be after the start.")
        out.append((start, end))
    return sorted(out)


def anchor_xy(key: str, w: float, h: float, margin: float) -> tuple[int, float, float]:
    an, fx, fy = _ANCHOR[key]
    x = margin + (w - 2 * margin) * fx
    y = margin + (h - 2 * margin) * fy
    return an, x, y


def bounce_path(
    duration: float, w: float, h: float, tw: float, th: float, speed: float, seed: int = 3
) -> list[Segment]:
    """[(t0, t1, x0, y0, x1, y1)] straight segments of a box bouncing off the edges (top-left coords)."""
    rng = random.Random(seed)
    tw, th = min(tw, w * 0.9), min(th, h * 0.9)
    x, y = rng.uniform(0, max(w - tw, 1)), rng.uniform(0, max(h - th, 1))
    vx, vy = speed * 0.8, speed * 0.6
    tm = 0.0
    segs: list[Segment] = []
    while tm < duration and len(segs) < 5000:
        tx = ((w - tw - x) / vx if vx > 0 else -x / vx) if vx else float("inf")
        ty = ((h - th - y) / vy if vy > 0 else -y / vy) if vy else float("inf")
        dt = max(min(tx, ty, duration - tm), 0.001)
        nx, ny = x + vx * dt, y + vy * dt
        segs.append((tm, tm + dt, x, y, nx, ny))
        x, y, tm = nx, ny, tm + dt
        if abs(dt - tx) < 1e-6:
            vx = -vx
        if abs(dt - ty) < 1e-6:
            vy = -vy
    return segs


@dataclass(frozen=True, slots=True, kw_only=True)
class TextMark:
    """A text watermark: what it says, how it looks and where it goes.

    *place*: a key of PLACES (*custom* is an x/y % spot; "jump" moves every
    *every* seconds, or a random time in *random_every*; "bounce" moves at
    *speed* px/s at 1080p).  *ranges*: when it shows ((start, end) seconds;
    None = always), with soft fades when *fade*."""

    text: str
    place: str = "br"
    font: str = "Montserrat"
    size: int = 64
    colour: str = "#FFFFFF"
    opacity: float = 1.0
    bold: bool = True
    italic: bool = False
    shadow: bool = True
    outline: bool = True
    angle: float = 0.0
    custom: tuple[float, float] = (50.0, 50.0)
    every: float = 3.0
    random_every: tuple[float, float] | None = None
    ranges: Sequence[tuple[float, float]] | None = None
    fade: bool = True
    speed: float = 180.0
    seed: int = 7


def build_script(mark: TextMark, w: int, h: int, duration: float) -> ass.Script:
    """The watermark as a subtitle script for a *w* x *h* video of *duration* s."""
    sc = ass.Script(w, h)
    u = min(w, h) / 1080
    px = max(8, round(mark.size * u))
    sc.style(
        ass.Style(
            "WM",
            font=mark.font,
            size=px,
            colour=mark.colour,
            bold=mark.bold,
            italic=mark.italic,
            outline=(2.5 * u if mark.outline else 0),
            shadow=(2 * u if mark.shadow else 0),
            back_alpha=0.5,
        )
    )
    body = ass.escape(mark.text)
    alpha = f"\\alpha{ass.alpha_tag(1 - mark.opacity)}"
    windows = mark.ranges or [(0.0, duration)]
    fade_tag = "\\fad(300,300)" if mark.fade else ""
    margin = 30 * u
    tw, th = ass.text_width(mark.text, px, bold=mark.bold), px * 1.2

    def add(a: float, b: float, tags: str) -> None:
        for ra, rb in windows:
            s, e = max(a, ra), min(b, rb)
            if e - s > 0.04:
                fades = fade_tag if (s == ra or e == rb) and mark.fade else ""
                sc.add(s, e, f"{{{tags}{alpha}{fades}}}{body}", "WM")

    rot = f"\\frz{-mark.angle:g}" if mark.angle else ""
    if mark.place == "bounce":
        for a, b, x0, y0, x1, y1 in bounce_path(duration, w, h, tw, th, mark.speed * u, mark.seed):
            add(a, b, f"\\an7\\move({x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f})")
    elif mark.place == "jump":
        rng = random.Random(mark.seed)
        spots = [k for k in _ANCHOR if k != "center"]
        tm, last = 0.0, None
        while tm < duration:
            step = rng.uniform(*mark.random_every) if mark.random_every else mark.every
            key = rng.choice([k for k in spots if k != last])
            last = key
            an, x, y = anchor_xy(key, w, h, margin)
            add(tm, min(tm + step, duration), f"\\an{an}\\pos({x:.0f},{y:.0f}){rot}")
            tm += step
    elif mark.place == "custom":
        x, y = w * mark.custom[0] / 100, h * mark.custom[1] / 100
        add(0, duration, f"\\an5\\pos({x:.0f},{y:.0f}){rot}")
    else:
        an, x, y = anchor_xy(mark.place, w, h, margin)
        add(0, duration, f"\\an{an}\\pos({x:.0f},{y:.0f}){rot}")
    return sc
