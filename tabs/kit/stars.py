"""
tabs/kit/stars.py - the Mystical skin's falling stars over the window.

They are drawn on a borderless click-through window kept over the app, so
only the stars show and every click reaches the app below:

* Windows: the window's background colour is keyed out (-transparentcolor).
* Linux (X11 and XWayland): the window is cut to the stars themselves with
  the X SHAPE extension (tabs/kit/x11shape.py); each star is a sparkle made
  of a few rectangles, so the cut is exact.  It hides while another program
  has the focus, since such a window would otherwise float over it.
* Elsewhere, or if that fails: a canvas behind the content (seen only where
  nothing covers it).
"""

from __future__ import annotations

import contextlib
import logging
import random
import sys
import tkinter as tk
from dataclasses import dataclass

from core import settings
from core.scaling import px
from core.skins import UI_FONT, Skin
from tabs.kit.x11shape import Rect, Shaper

logger = logging.getLogger("crossfader.stars")

_CHARS = ("✦", "✧", "⋆", "·", "˚", "∘", "*")
_COLOURS = ("#E1BEE7", "#CE93D8", "#BA68C8", "#AB47BC", "#F3E5F5", "#D1C4E9", "#B39DDB", "#EDE7F6")
_CHROMA = "#010203"  # the keyed-out background: never used in any real skin
_DOTS = ("·", "˚", "∘")  # drawn as a dot; the others as a four-pointed sparkle


def sparkle(x: float, y: float, char: str, size: int) -> list[Rect]:
    """The rectangles of one star drawn at (*x*, *y*): a dot, or a cross of two
    thin bars with a brighter centre (the shape the overlay is cut to)."""
    cx, cy, s = int(x), int(y), px(size)
    if char in _DOTS:
        d = max(2, s // 5)
        return [(cx - d // 2, cy - d // 2, d, d)]
    arm, bar = max(2, s // 2), max(1, px(1))
    core = bar + 2
    return [
        (cx - bar // 2, cy - arm, bar, 2 * arm),
        (cx - arm, cy - bar // 2, 2 * arm, bar),
        (cx - core // 2, cy - core // 2, core, core),
    ]


@dataclass(slots=True)
class _Star:
    """One falling star: where it is, how it moves, and its canvas item."""

    x: float = 0.0
    y: float = 0.0
    speed: float = 1.0
    drift: float = 0.0
    char: str = "*"
    colour: str = "#FFFFFF"
    size: int = 12
    item: int | None = None

    def respawn(self, x: float, y: float) -> None:
        self.x, self.y = x, y
        self.speed = random.uniform(0.5, 1.8)
        self.drift = random.uniform(-0.2, 0.2)
        self.char = random.choice(_CHARS)
        self.colour = random.choice(_COLOURS)
        self.size = random.randint(8, 16)

    def fall(self, speed: float, width: int, height: int) -> None:
        self.y += px(self.speed * speed)
        self.x += px(self.drift * speed)
        if self.y > height + px(20):  # re-enter from a random x across the full width
            self.respawn(random.uniform(0, width), random.uniform(-px(30), -px(5)))
        if self.x < 0:
            self.x = width
        if self.x > width:
            self.x = 0


class StarField:
    """Stars falling over *app* until :meth:`stop`; *speed* can change live."""

    def __init__(self, app: tk.Tk, background: str, count: int, speed: float) -> None:
        self.app, self.count, self.speed = app, count, speed
        self.stars: list[_Star] = []
        self._after: str | None = None
        self._overlay: tk.Toplevel | None = None
        self._shaper: Shaper | None = None
        if sys.platform == "win32":
            self.canvas = self._windows_overlay()
        else:
            self.canvas = self._x11_overlay() or self._behind(background)
        self._after = self.canvas.after(50, self._tick)

    def _x11_overlay(self) -> tk.Canvas | None:
        """A window over the app cut to the stars (None where X SHAPE isn't available)."""
        if not sys.platform.startswith(("linux", "freebsd", "openbsd", "netbsd")):
            return None
        top = tk.Toplevel(self.app)
        try:
            top.overrideredirect(True)
            canvas = tk.Canvas(top, bg=_COLOURS[0], highlightthickness=0, bd=0)
            canvas.pack(fill="both", expand=True)
            top.update_idletasks()
            self._shaper = Shaper(int(top.wm_frame(), 16))
            self._shaper.show_only(())
            self._shaper.click_through()
        except (OSError, ValueError, tk.TclError):
            logger.info("falling stars: no X SHAPE here, drawing them behind the window")
            top.destroy()
            self._shaper = None
            return None
        self._overlay = top
        self._cover_window()
        self.app.bind("<Configure>", lambda _e: self._cover_window(), add=True)
        self.app.after(50, self._follow_window)
        return canvas

    def _behind(self, background: str) -> tk.Canvas:
        canvas = tk.Canvas(self.app, bg=background, highlightthickness=0, bd=0)
        canvas.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        # Canvas.lower() reorders the canvas's *items*; the window-stacking
        # lower is Misc.lower - without it the canvas covered the whole UI.
        with contextlib.suppress(tk.TclError):
            tk.Misc.lower(canvas)  # pyright: ignore[reportUnknownMemberType] - Misc.lower's stub is untyped
        return canvas

    def _windows_overlay(self) -> tk.Canvas:
        if sys.platform != "win32":
            raise NotImplementedError("the click-through overlay is Windows-only")
        top = self._overlay = tk.Toplevel(self.app)
        top.overrideredirect(True)
        top.configure(bg=_CHROMA)
        top.attributes("-transparentcolor", _CHROMA)
        # Not -topmost (that drew stars over every other program): owned by the
        # app window and kept just above it.
        top.transient(self.app)
        top.lift(self.app)
        self.app.after(80, self._click_through)
        canvas = tk.Canvas(top, bg=_CHROMA, highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        self.app.bind("<Configure>", lambda _e: self._cover_window(), add=True)
        self.app.after(50, self._follow_window)
        return canvas

    def _click_through(self) -> None:
        """WS_EX_TRANSPARENT | WS_EX_LAYERED: every mouse event reaches the app below."""
        if sys.platform != "win32" or self._overlay is None:
            return
        import ctypes

        try:
            self._overlay.update_idletasks()
            raw = self._overlay.winfo_id()
            hwnd = ctypes.windll.user32.GetParent(raw) or raw
            gwl_exstyle, ws_ex_layered, ws_ex_transparent = -20, 0x00080000, 0x00000020
            ex = ctypes.windll.user32.GetWindowLongW(hwnd, gwl_exstyle)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, gwl_exstyle, ex | ws_ex_layered | ws_ex_transparent
            )
        except Exception:
            logger.debug("_click_through: best-effort step failed", exc_info=True)

    def _cover_window(self) -> None:
        top = self._overlay
        try:
            if top is not None and top.winfo_exists():
                app = self.app
                top.geometry(
                    f"{app.winfo_width()}x{app.winfo_height()}+{app.winfo_rootx()}+{app.winfo_rooty()}"
                )
        except Exception:
            logger.debug("_cover_window: best-effort step failed", exc_info=True)

    def _app_hidden(self) -> bool:
        """Minimised - or, on X11, another program has the focus (the overlay
        would float over that program's window otherwise)."""
        if self.app.state() == "iconic":
            return True
        if self._shaper is None:
            return False
        try:
            return self.app.focus_displayof() is None
        except (KeyError, tk.TclError):  # focus in a widget Tkinter doesn't know (a popdown)
            return False

    def _follow_window(self) -> None:
        """Every half second: hide with the app, else sit over it."""
        top = self._overlay
        if top is None or not top.winfo_exists():
            return
        try:
            if self._app_hidden():
                top.withdraw()
            else:
                top.deiconify()
                top.lift(self.app)  # pyright: ignore[reportUnknownMemberType] - lift's stub is untyped
                self._cover_window()
        except Exception:
            logger.debug("_follow_window: best-effort step failed", exc_info=True)
        top.after(500, self._follow_window)

    def _size(self) -> tuple[int, int]:
        """The app window's size (the overlay lags behind it until laid out)."""
        width, height = self.app.winfo_width(), self.app.winfo_height()
        return (width if width >= 100 else 1440), (height if height >= 100 else 900)

    def _tick(self) -> None:
        canvas = self.canvas
        if not canvas.winfo_exists():
            return
        width, height = self._size()
        if not self.stars:  # first tick: spread them over the window
            slot = width / self.count
            for i in range(self.count):
                star = _Star()
                star.respawn(
                    slot * i + random.uniform(0, slot), random.uniform(-px(120), height + px(200))
                )
                self.stars.append(star)
        if self._shaper is not None:
            self._draw_sparkles(canvas, width, height)
        else:
            self._draw_glyphs(canvas, width, height)
        self._after = canvas.after(50, self._tick)

    def _draw_glyphs(self, canvas: tk.Canvas, width: int, height: int) -> None:
        for star in self.stars:
            if star.item is not None:
                with contextlib.suppress(tk.TclError):
                    canvas.delete(star.item)
            star.fall(self.speed, width, height)
            star.item = canvas.create_text(
                int(star.x),
                int(star.y),
                text=star.char,
                fill=star.colour,
                font=(UI_FONT, star.size),
            )

    def _draw_sparkles(self, canvas: tk.Canvas, width: int, height: int) -> None:
        """Each star as rectangles, and the overlay cut to exactly those."""
        canvas.delete("star")
        shape: list[Rect] = []
        for star in self.stars:
            star.fall(self.speed, width, height)
            for x, y, w, h in sparkle(star.x, star.y, star.char, star.size):
                canvas.create_rectangle(
                    x, y, x + w, y + h, fill=star.colour, width=0, tags=("star",)
                )
                shape.append((x, y, w, h))
        if self._shaper is not None:
            try:
                self._shaper.show_only(shape)
            except OSError:
                logger.debug("_draw_sparkles: reshaping failed", exc_info=True)

    def stop(self) -> None:
        with contextlib.suppress(tk.TclError):
            if self._after is not None and self.canvas.winfo_exists():
                self.canvas.after_cancel(self._after)
        self._after = None
        with contextlib.suppress(tk.TclError):  # destroying the overlay destroys its canvas too
            widget: tk.Misc = self._overlay if self._overlay is not None else self.canvas
            if widget.winfo_exists():
                widget.destroy()
        self._overlay = None
        if self._shaper is not None:
            self._shaper.close()
            self._shaper = None
        self.stars.clear()


_field: StarField | None = None
_speed = 1.0  # 1.0 = default, <1 slower, >1 faster
_count = 55  # how many stars


def get_star_speed() -> float:
    return _speed


def set_star_speed(mult: float) -> None:
    """Change the stars' speed, live."""
    global _speed
    _speed = float(mult)
    if _field is not None:
        _field.speed = _speed


def get_star_count() -> int:
    return _count


def set_star_count(count: int) -> None:
    """How many stars there are (from the next time the skin is applied)."""
    global _count
    _count = max(10, int(count))


def running() -> StarField | None:
    """The stars on screen now, if any."""
    return _field


def start_stars(app: tk.Tk, skin: Skin) -> None:
    """Start the falling stars over *app* (any running ones stop first)."""
    global _field
    stop_stars()
    set_star_speed(settings.STAR_SPEED.get())
    set_star_count(settings.STAR_COUNT.get())
    _field = StarField(app, skin["sb_bg"], _count, _speed)


def stop_stars() -> None:
    """Stop the falling stars and remove their overlay."""
    global _field
    if _field is not None:
        _field.stop()
        _field = None
