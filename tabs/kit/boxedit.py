"""
tabs/kit/boxedit.py - Dragging a box on a picture: move it, or resize it from
a corner or from any edge.

The pages that edit a box on the video (Manual Crop, the regions in Privacy
Blur) share this, so they behave alike: square handles on the four corners
and in the middle of the four edges, an edge can be grabbed anywhere along
its length (so cropping just the top is a straight up-and-down drag that
can't wander sideways), and the cursor shows what a drag will do.

Everything here works in one coordinate system (canvas pixels, or video
pixels - the caller decides); nothing knows about Tk except ``draw`` and
``set_cursor``.
"""

from __future__ import annotations

import tkinter as tk

Rect = tuple[float, float, float, float]  # x, y, width, height

#: What a drag from each spot does, and the cursor that says so.
CURSORS = {
    "tl": "top_left_corner",
    "tr": "top_right_corner",
    "bl": "bottom_left_corner",
    "br": "bottom_right_corner",
    "t": "sb_v_double_arrow",
    "b": "sb_v_double_arrow",
    "l": "sb_h_double_arrow",
    "r": "sb_h_double_arrow",
    "move": "fleur",
}


def hit(rect: Rect, x: float, y: float, grab: float) -> str | None:
    """Which part of *rect* the point (x, y) is on: a corner ("tl", "tr",
    "bl", "br"), an edge ("t", "b", "l", "r"), "move" for the inside, or None.
    *grab* is how close counts as "on" a corner or edge."""
    x1, y1, w, h = rect
    x2, y2 = x1 + w, y1 + h
    if not (x1 - grab <= x <= x2 + grab and y1 - grab <= y <= y2 + grab):
        return None
    on_l, on_r = abs(x - x1) <= grab, abs(x - x2) <= grab
    on_t, on_b = abs(y - y1) <= grab, abs(y - y2) <= grab
    # A box smaller than two grabs: prefer the nearer side
    if on_l and on_r:
        on_l, on_r = abs(x - x1) <= abs(x - x2), abs(x - x2) < abs(x - x1)
    if on_t and on_b:
        on_t, on_b = abs(y - y1) <= abs(y - y2), abs(y - y2) < abs(y - y1)
    vertical = "t" if on_t else ("b" if on_b else "")
    horizontal = "l" if on_l else ("r" if on_r else "")
    if vertical or horizontal:
        return vertical + horizontal
    return "move"


def drag(
    kind: str,
    start: Rect,
    dx: float,
    dy: float,
    bounds: tuple[float, float],
    ratio: tuple[float, float] | None = None,
    minimum: float = 16,
) -> Rect:
    """The box after dragging part *kind* of *start* by (dx, dy), kept inside
    (0, 0)-*bounds* and at least *minimum* each way.  With *ratio* (w, h) the
    shape stays locked: an edge drag changes the other side about the middle."""
    bw, bh = bounds
    x, y, w, h = start
    if kind == "move":
        return (min(max(0.0, x + dx), bw - w), min(max(0.0, y + dy), bh - h), w, h)
    left, top, right, bottom = x, y, x + w, y + h
    if "l" in kind:
        left = min(max(0.0, left + dx), right - minimum)
    if "r" in kind:
        right = max(min(bw, right + dx), left + minimum)
    if "t" in kind:
        top = min(max(0.0, top + dy), bottom - minimum)
    if "b" in kind:
        bottom = max(min(bh, bottom + dy), top + minimum)
    nw, nh = right - left, bottom - top
    if not ratio:
        return (left, top, nw, nh)
    rw, rh = ratio
    if kind in ("t", "b"):  # height leads; width follows about the middle
        nw = nh * rw / rh
        if nw > bw:
            nw, nh = bw, bw * rh / rw
        cx = x + w / 2
        left = min(max(0.0, cx - nw / 2), bw - nw)
        if kind == "t":
            top = bottom - nh
    elif kind in ("l", "r"):  # width leads; height follows about the middle
        nh = nw * rh / rw
        if nh > bh:
            nh, nw = bh, bh * rw / rh
        cy = y + h / 2
        top = min(max(0.0, cy - nh / 2), bh - nh)
        if kind == "l":
            left = right - nw
    else:  # a corner: width leads, the opposite corner stays put
        nh = nw * rh / rw
        if "t" in kind:
            top = bottom - nh
            if top < 0:
                top, nh = 0.0, bottom
                nw = nh * rw / rh
        elif top + nh > bh:
            nh = bh - top
            nw = nh * rw / rh
        if "l" in kind:
            left = right - nw
    return (left, top, nw, nh)


def handle_points(x1: float, y1: float, x2: float, y2: float) -> list[tuple[float, float]]:
    """Where the eight handles go: the corners and the middle of each edge."""
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    return [(x1, y1), (mx, y1), (x2, y1), (x2, my), (x2, y2), (mx, y2), (x1, y2), (x1, my)]


def draw(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    colour: str,
    size: float,
    tag: str = "",
) -> None:
    """The eight square handles of the box (x1, y1)-(x2, y2)."""
    for hx, hy in handle_points(x1, y1, x2, y2):
        canvas.create_rectangle(
            hx - size,
            hy - size,
            hx + size,
            hy + size,
            fill=colour,
            outline="white",
            width=1,
            tags=(tag,) if tag else (),
        )


def set_cursor(widget: tk.Misc, kind: str | None, default: str = "crosshair") -> None:
    """Show what a drag from here would do."""
    try:
        widget.configure(cursor=CURSORS.get(kind or "", default))  # pyright: ignore[reportCallIssue]
    except tk.TclError:  # a cursor this system doesn't have
        pass
