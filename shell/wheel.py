"""
shell/wheel.py - the mouse wheel scrolls whatever is under the pointer.

Tk sends the wheel to the widget with focus, so without this the sidebar
would scroll while the pointer is over a page (or not at all).  Text boxes,
lists and tables that have more to show keep their own scrolling.
"""
from __future__ import annotations

import sys
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

UNITS_PER_NOTCH = 3                # Windows' default is 3 lines
_IS_MAC = sys.platform == "darwin"


def can_scroll(widget: tk.Misc) -> bool:
    """True when *widget* has more content than it shows vertically."""
    try:
        first, last = widget.yview()  # type: ignore[attr-defined]
    except (tk.TclError, ValueError, AttributeError):
        return False
    return first > 0.0 or last < 1.0


def units(num: int, delta: int, carry: int) -> tuple[int, int]:
    """(units to scroll - positive is down -, the carry for the next event).

    X11 with Tk 8.6 reports the wheel as button 4 (up) and 5 (down).  Windows
    and Tk 9 send a delta, positive for up, 120 per notch; touchpads and
    smooth wheels send smaller steps, so the remainder is carried over.  Tk 8.6
    on macOS sends small deltas, one per line."""
    if num == 4:
        return -UNITS_PER_NOTCH, carry
    if num == 5:
        return UNITS_PER_NOTCH, carry
    if not delta:
        return 0, carry
    if _IS_MAC and abs(delta) < 120:
        return -delta * UNITS_PER_NOTCH, carry
    carry = (carry if (carry > 0) == (delta > 0) else 0) + delta
    notches = int(carry / 120)                          # rounds towards zero
    return -notches * UNITS_PER_NOTCH, carry - notches * 120


class WheelRouter:
    """Installs the bindings on *window*.  *sidebar* is the sidebar frame and
    *sidebar_canvas* what scrolls when the pointer is anywhere over it."""

    def __init__(self, window: tk.Tk, sidebar: Callable[[], tuple[tk.Misc, tk.Canvas]]) -> None:
        self._window = window
        self._sidebar = sidebar
        self._carry = 0
        window.bind_all("<MouseWheel>", self._on_wheel)
        if tk.TkVersion < 9:      # Tk 9 reports X11's wheel as <MouseWheel> too, and uses buttons
            window.bind_all("<Button-4>", self._on_wheel)       # 4/5 for the mouse's side buttons
            window.bind_all("<Button-5>", self._on_wheel)
        # The wheel changes a drop-down's or number box's value even when the pointer
        # merely passes over it while scrolling the page: only let it when the box has
        # keyboard focus (as Windows does); otherwise the wheel scrolls the page.
        for cls in ("TCombobox", "TSpinbox"):
            for seq in window.bind_class(cls):
                if "Wheel" in seq or seq.endswith(("Button-4>", "Button-5>")):
                    script = window.bind_class(cls, seq)
                    window.bind_class(cls, seq, f'if {{[focus] eq "%W"}} {{{script}; break}}')

    def _on_wheel(self, event: tk.Event) -> None:
        steps, self._carry = units(event.num if isinstance(event.num, int) else 0, event.delta or 0, self._carry)
        if steps:
            target = self.target(event.x_root, event.y_root)
            if target is not None:
                target.yview_scroll(steps, "units")

    def target(self, x: int, y: int) -> tk.Canvas | None:
        """The canvas the wheel should scroll at screen point (x, y), or None.

        Goes by what is under the pointer rather than the event's widget, so a
        dialog in front of the sidebar scrolls itself, not the sidebar."""
        try:
            widget: tk.Misc | None = self._window.winfo_containing(x, y)
        except (KeyError, tk.TclError):
            return None             # one of Tk's own windows, such as an open drop-down
        sidebar, canvas = self._sidebar()
        while widget is not None:
            if widget is sidebar:
                return canvas
            if isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview)):
                if can_scroll(widget):
                    return None
            elif isinstance(widget, tk.Canvas) and widget.cget("yscrollcommand") and can_scroll(widget):
                return widget
            widget = widget.master
        return None
