"""
tabs/kit/contracts.py - what the window may ask of a tool page.

Fluent Python ch. 13 and Robust Python ch. 13: the shell used to find out
what a page could do by poking at its private attributes (``_timeline``,
``_scrub``, ``_btn_trim``...) - forty-odd ``hasattr`` checks that broke
silently whenever a page renamed something.  Now each ability is a small
:class:`typing.Protocol`.  A page has the ability by having the methods;
nothing needs registering or inheriting, and pyright checks both sides.

The shell asks with ``isinstance(page, MarksIn)`` (the protocols are
runtime-checkable) and then calls the methods - never private names.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from core.tools import ToolId


@runtime_checkable
class Transport(Protocol):
    """A playhead: Home, End, the arrow keys and Space drive it."""

    @property
    def position(self) -> float: ...
    @property
    def duration(self) -> float: ...
    def seek(self, seconds: float) -> None: ...


@runtime_checkable
class HasTransport(Protocol):
    def transport(self) -> Transport | None: ...


@runtime_checkable
class MarksIn(Protocol):
    """A start point (or a single moment) at the playhead: the I key."""

    def mark_in(self) -> None: ...


@runtime_checkable
class MarksOut(Protocol):
    """An end point at the playhead: the O key."""

    def mark_out(self) -> None: ...


@runtime_checkable
class Renderable(Protocol):
    """The page's main action: Ctrl+S."""

    def render(self) -> None: ...


@runtime_checkable
class AcceptsFiles(Protocol):
    """Files dropped on the window, opened from the menu or Recent Files."""

    def load_file(self, path: str) -> bool: ...


@runtime_checkable
class Previewing(Protocol):
    """Runs a preview player that must stop when the page is left."""

    def stop_preview(self) -> None: ...


@runtime_checkable
class ClosingAware(Protocol):
    """Has something to finish (a recording, a temporary file) before the app quits."""

    def on_app_closing(self) -> None: ...


@runtime_checkable
class Showable(Protocol):
    """Refreshes itself whenever it is shown."""

    def on_show(self) -> None: ...


@runtime_checkable
class Navigating(Protocol):
    """Opens other tools (the Start and Home pages)."""

    def set_navigator(self, navigate: Callable[[ToolId], object]) -> None: ...


@runtime_checkable
class Shell(Protocol):
    """What a page may ask of the window it lives in (shell/app.py)."""

    def set_status(self, message: str, color: str | None = None) -> None: ...
    def show_page(self, tool: ToolId | str) -> tk.Misc | None: ...
    def current(self) -> tk.Misc | None: ...
    def log_debug(self, message: str) -> None: ...
    def apply_theme(self, name: str) -> None: ...
    def recent_tools(self) -> list[ToolId]: ...


# ── Adapter for pages whose playhead is a scrubber (VideoTimeline is a Transport itself) ──


class ScrubTransport:
    """A scrubber - a :class:`tk.Scale` or the Tk variable behind one - and the
    clip's length."""

    def __init__(self, scale: tk.Scale | tk.DoubleVar, duration: Callable[[], float]) -> None:
        self._scale = scale
        self._duration = duration

    @property
    def position(self) -> float:
        try:
            return float(self._scale.get())
        except (ValueError, tk.TclError):
            return 0.0

    @property
    def duration(self) -> float:
        return max(0.0, float(self._duration() or 0.0))

    def seek(self, seconds: float) -> None:
        self._scale.set(max(0.0, min(seconds, self.duration or seconds)))  # pyright: ignore[reportUnknownMemberType] - stub gap
