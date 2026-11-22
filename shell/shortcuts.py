"""
shell/shortcuts.py - the window's keyboard shortcuts.

Each key asks the open page for one ability from tabs/kit/contracts.py
(a playhead, in/out marks, a main action) and does nothing when the page
doesn't have it.  Keys never fire while a person types into a box or has
focus on a button (Space presses a button).
"""
from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from core.hardware import launch_preview
from tabs import framestep
from tabs.kit.contracts import HasTransport, MarksIn, MarksOut, Previewing, Renderable, Transport

#: Focus on one of these means the person is typing or pressing: leave the keys alone.
_TYPING = (tk.Entry, tk.Text, tk.Spinbox, tk.Listbox, ttk.Entry, ttk.Combobox, ttk.Spinbox, tk.Button,
           ttk.Button, tk.Checkbutton, ttk.Checkbutton, tk.Radiobutton, ttk.Radiobutton)   # a slider is fine
NUDGE_SECONDS = 1.0

SHORTCUTS_HELP = (
    "PLAYBACK\n"
    "  Space          Play / stop a preview\n"
    "  Escape         Stop preview\n\n"
    "NAVIGATION\n"
    "  Home / End     Go to the start / end\n"
    "  Left / Right   Nudge the playhead ±1 sec\n"
    "  , / .          One frame back / forward (Shift: one second)\n\n"
    "EDITING\n"
    "  I / O          Set the IN / OUT point at the playhead\n"
    "  Ctrl+S         Start the render / export\n\n"
    "FILE\n"
    "  Ctrl+O         Open file\n"
    "  Drag & Drop    Drop files onto the window\n")


class Shortcuts:
    """Binds the keys on *window*; *current()* is the page on show."""

    def __init__(self, window: tk.Tk, current: Callable[[], tk.Misc | None],
                 stop_previews: Callable[[], object]) -> None:
        self._window = window
        self._current = current
        self._stop_previews = stop_previews
        keys: dict[str, Callable[[], object]] = {
            "<space>": self.play_pause, "i": self.mark_in, "o": self.mark_out, "<Home>": self.go_start,
            "<End>": self.go_end, "<Left>": lambda: self.nudge(-NUDGE_SECONDS),
            "<Right>": lambda: self.nudge(NUDGE_SECONDS), "<Escape>": stop_previews,
        }
        for key, action in keys.items():
            window.bind(key, lambda _e, act=action: None if self._typing() else act())
        window.bind("<Control-s>", lambda _e: self.render())
        # , and . step one frame on whatever picks a moment in the open tool (tabs/framestep.py)
        for names, direction, second in ((("<comma>",), -1, False), (("<period>",), 1, False),
                                         (("<less>", "<semicolon>"), -1, True),
                                         (("<greater>", "<colon>"), 1, True)):
            for name in names:
                window.bind(name, lambda e, d=direction, s=second: framestep.on_key(window, e, d, s))

    def _typing(self) -> bool:
        try:
            return isinstance(self._window.focus_get(), _TYPING)
        except (KeyError, tk.TclError):         # focus is in one of Tk's own windows
            return False

    def _transport(self) -> Transport | None:
        page = self._current()
        return page.transport() if isinstance(page, HasTransport) else None

    # ── Actions (public so tests and menus can use them) ──────────────────────
    def play_pause(self) -> None:
        """Space: open a preview player at the playhead, or close the one playing."""
        page = self._current()
        if page is None:
            return
        if isinstance(page, Previewing) and getattr(page, "preview_proc", None) is not None:
            page.stop_preview()
            return
        path = getattr(page, "file_path", "")
        if not isinstance(path, str) or not os.path.isfile(path):
            return
        transport = self._transport()
        player = launch_preview(path, start_time=transport.position if transport else 0.0)
        if isinstance(page, Previewing):
            page.preview_proc = player  # type: ignore[attr-defined]  - BaseTab declares it

    def mark_in(self) -> None:
        page = self._current()
        if isinstance(page, MarksIn):
            page.mark_in()

    def mark_out(self) -> None:
        page = self._current()
        if isinstance(page, MarksOut):
            page.mark_out()

    def go_start(self) -> None:
        if (t := self._transport()) is not None:
            t.seek(0.0)

    def go_end(self) -> None:
        if (t := self._transport()) is not None:
            t.seek(t.duration)

    def nudge(self, seconds: float) -> None:
        if (t := self._transport()) is not None:
            t.seek(min(max(0.0, t.position + seconds), t.duration or t.position + seconds))

    def render(self) -> str | None:
        page = self._current()
        if isinstance(page, Renderable):
            page.render()
            return "break"
        return None
