"""
tabs/kit/x11shape.py - cut an X11 window down to a set of rectangles.

The X SHAPE extension: the window is drawn only inside its *bounding*
rectangles, and receives the mouse only inside its *input* rectangles (none:
every click goes to the window below).  The Mystical theme's stars use it on
Linux for an overlay that shows nothing but the stars and is never in the
way.  Works under X11 and XWayland; raises OSError where it can't (no X
libraries, no SHAPE extension), so the caller can fall back.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from collections.abc import Sequence

_BOUNDING, _INPUT = 0, 2  # ShapeBounding, ShapeInput
_SET, _UNSORTED = 0, 0  # ShapeSet, Unsorted

Rect = tuple[int, int, int, int]  # x, y, width, height


class _XRectangle(ctypes.Structure):
    _fields_ = (
        ("x", ctypes.c_short),
        ("y", ctypes.c_short),
        ("width", ctypes.c_ushort),
        ("height", ctypes.c_ushort),
    )


def _library(name: str) -> ctypes.CDLL:
    path = ctypes.util.find_library(name)
    if not path:
        raise OSError(f"lib{name} not found")
    return ctypes.CDLL(path)


class Shaper:
    """The shape of one X window (by id), over a connection of its own."""

    def __init__(self, window: int) -> None:
        x11, ext = _library("X11"), _library("Xext")
        x11.XOpenDisplay.argtypes = (ctypes.c_char_p,)
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XFlush.argtypes = (ctypes.c_void_p,)
        x11.XCloseDisplay.argtypes = (ctypes.c_void_p,)
        x11.XFree.argtypes = (ctypes.c_void_p,)
        x11.XQueryTree.argtypes = (
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_uint),
        )
        ext.XShapeQueryExtension.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        )
        ext.XShapeCombineRectangles.argtypes = (
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(_XRectangle),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        )
        ext.XShapeGetRectangles.argtypes = (
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        )
        ext.XShapeGetRectangles.restype = ctypes.c_void_p
        display = x11.XOpenDisplay(None)
        if not display:
            raise OSError("no X display")
        event, error = ctypes.c_int(), ctypes.c_int()
        if not ext.XShapeQueryExtension(display, ctypes.byref(event), ctypes.byref(error)):
            x11.XCloseDisplay(display)
            raise OSError("the X server has no SHAPE extension")
        self._x11, self._ext, self._display = x11, ext, display
        self.window = self._top_level(window)

    def _top_level(self, window: int) -> int:
        """The window directly under the root that holds *window*: Tk's
        ``wm frame`` gives the window inside it, and only the outer one's
        shape decides what shows and what takes the mouse."""
        root, parent = ctypes.c_ulong(), ctypes.c_ulong()
        children, count = ctypes.c_void_p(), ctypes.c_uint()
        for _ in range(8):
            ok = self._x11.XQueryTree(
                self._display,
                window,
                ctypes.byref(root),
                ctypes.byref(parent),
                ctypes.byref(children),
                ctypes.byref(count),
            )
            if children.value:
                self._x11.XFree(children)
            if not ok or not parent.value or parent.value == root.value:
                return window
            window = parent.value
        return window

    def _combine(self, kind: int, rects: Sequence[Rect]) -> None:
        array = (_XRectangle * max(1, len(rects)))(
            *(_XRectangle(x, y, max(0, w), max(0, h)) for x, y, w, h in rects)
        )
        self._ext.XShapeCombineRectangles(
            self._display, self.window, kind, 0, 0, array, len(rects), _SET, _UNSORTED
        )
        self._x11.XFlush(self._display)

    def show_only(self, rects: Sequence[Rect]) -> None:
        """Draw the window only inside *rects* (none: not at all)."""
        self._combine(_BOUNDING, rects)

    def click_through(self) -> None:
        """Let every mouse event reach the windows below."""
        self._combine(_INPUT, ())

    def input_rectangles(self) -> int:
        """How many rectangles take the mouse (0 after click_through)."""
        count, ordering = ctypes.c_int(), ctypes.c_int()
        found = self._ext.XShapeGetRectangles(
            self._display, self.window, _INPUT, ctypes.byref(count), ctypes.byref(ordering)
        )
        if found:
            self._x11.XFree(found)
        return count.value

    def close(self) -> None:
        if self._display:
            self._x11.XCloseDisplay(self._display)
            self._display = None
