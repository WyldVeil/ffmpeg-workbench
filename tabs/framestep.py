"""
tabs/framestep.py - Frame-by-frame stepping with the , and . keys.

Anything that picks a moment in a video (a position slider under a preview,
a timeline's playhead or its in / out handles) registers here.  Then, on the
open tool:

    ,  /  .              one frame back / forward
    Shift + ,  /  .      one second back / forward

The keys go to the control used last (clicked or dragged), otherwise to the
first one on the page.  They are left alone while the cursor is in a text
box, so typing a comma still works.  Times are snapped to whole frames of the
video, so stepping always lands exactly on a frame.

    from tabs import framestep
    framestep.slider(self.pos_scale, self.preview)          # fps from the preview's file
    framestep.register(timeline, timeline.step_frames, lambda: timeline.fps)   # its own logic
"""

from __future__ import annotations

import logging
import tkinter as tk
import weakref
from collections.abc import Callable
from typing import TYPE_CHECKING

from core import ffmpeg as ff
from tabs.kit.contracts import Shell
from tabs.kit.tktyped import set_scale

if TYPE_CHECKING:
    from tkinter.ttk import Scale


logger = logging.getLogger("crossfader.framestep")

DEFAULT_FPS = 30.0
HINT = "Drag, or press , and . to step one frame (with Shift, one second)"
TYPING_CLASSES = {"Entry", "TEntry", "Text", "Spinbox", "TSpinbox", "TCombobox", "Listbox"}

_Entry = tuple[Callable[[int], None], Callable[[], float]]
_steppers: weakref.WeakKeyDictionary[tk.Misc, _Entry] = weakref.WeakKeyDictionary()
_order: list[weakref.ref[tk.Misc]] = []  # in registration order
_last: weakref.ref[tk.Misc] | None = None


def fps_of(path: str | None) -> float:
    """The frame rate of *path* (probe results are cached), 30 if unknown."""
    if not path:
        return DEFAULT_FPS
    info = ff.try_probe(path)
    fps = info.fps if info is not None else 0.0
    return fps if 1.0 <= fps <= 1000.0 else DEFAULT_FPS


def snap(t: float, fps: float, frames: int = 0) -> float:
    """*t* moved to the nearest frame start, then *frames* frames on."""
    return (round(t * fps) + frames) / fps


def register(widget: tk.Misc, step: Callable[[int], None], fps: FpsSource = DEFAULT_FPS) -> None:
    """*step(n)* moves the control *n* frames (negative = back); *fps* (as for
    :func:`slider`) turns Shift's one second into frames."""
    entry = (step, _fps_getter(fps))
    if widget in _steppers:
        _steppers[widget] = entry
        return
    _steppers[widget] = entry
    _order.append(weakref.ref(widget))
    for seq in ("<ButtonPress>", "<FocusIn>"):
        widget.bind(seq, lambda _e, w=widget: touch(w), add=True)


def touch(widget: tk.Misc) -> None:
    """Make *widget* the one the keys move (it was just used)."""
    global _last
    _last = weakref.ref(widget)


FpsSource = float | Callable[[], float] | object


def _fps_getter(source: FpsSource) -> Callable[[], float]:
    if isinstance(source, (int, float)):
        return lambda: float(source)
    if callable(source):
        return source  # type: ignore[return-value]
    return lambda: fps_of(getattr(source, "path", None))  # a FramePreview / CompareView


def slider(scale: tk.Scale | Scale, fps: FpsSource) -> None:
    """A position slider in seconds (tk or ttk ``Scale``).  *fps*: a number, a
    callable, or a preview widget whose loaded file gives the frame rate.
    Setting the slider runs its command, so the preview follows."""
    get_fps = _fps_getter(fps)

    def step(n: int) -> None:
        rate = get_fps()
        lo, hi = sorted((float(scale.cget("from")), float(scale.cget("to"))))
        last = max(lo, hi - 1.0 / rate)  # the last frame, not the end of the video
        set_scale(scale, min(last, max(lo, snap(float(scale.get()), rate, n))))

    register(scale, step, get_fps)
    from tabs.base_tab import add_tooltip

    add_tooltip(scale, HINT)


def _inside(widget: tk.Misc, ancestor: tk.Misc) -> bool:
    w: tk.Misc | None = widget
    while w is not None:
        if w is ancestor:
            return True
        w = getattr(w, "master", None)
    return False


def _usable(widget: tk.Misc, page: tk.Misc) -> bool:
    try:
        return bool(widget.winfo_exists() and widget.winfo_viewable() and _inside(widget, page))
    except Exception:
        logger.debug("_usable: best-effort step failed", exc_info=True)
        return False


def target(page: tk.Misc) -> tk.Misc | None:
    """The control the keys would move on *page* (None if there is none)."""
    last = _last() if _last is not None else None
    if last is not None and _usable(last, page):
        return last
    for ref in list(_order):
        w = ref()
        if w is None:
            _order.remove(ref)
            continue
        if _usable(w, page):
            return w
    return None


def on_key(app: tk.Misc, event: object, direction: int, second: bool = False) -> str | None:
    """Key handler for the main window: , and . (*second*: with Shift)."""
    try:
        focus = app.focus_get()
    except Exception:
        logger.debug("on_key: best-effort step failed", exc_info=True)
        focus = None
    if focus is not None and focus.winfo_class() in TYPING_CLASSES:
        return None
    if focus is not None and focus.winfo_toplevel() is not app:
        return None  # a dialog has the keyboard
    page = app.current() if isinstance(app, Shell) else None
    widget = target(page) if page is not None else None
    if widget is None:
        return None
    entry = _steppers.get(widget)
    if entry is None:
        return None
    step, get_fps = entry
    step(direction * (max(1, round(get_fps())) if second else 1))
    return "break"


def step(widget: tk.Misc, frames: int = 0, seconds: int = 0) -> None:
    """Move a registered control (for on-screen ◀ ▶ buttons): *frames* frames
    plus *seconds* seconds, landing on a frame."""
    entry = _steppers.get(widget)
    if entry is None:
        return
    do, get_fps = entry
    touch(widget)
    do(frames + seconds * max(1, round(get_fps())))
