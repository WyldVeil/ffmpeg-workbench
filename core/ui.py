"""
core/ui.py - Hand work to the Tk main thread safely.

Tk is single-threaded.  Calling ``widget.after()`` (or any widget method)
from a worker thread works only by accident: it raises "main thread is not in
main loop" before the loop starts and can dead-lock when the main thread is
waiting on that worker.  Instead, workers put callables on a queue which the
main thread drains every few milliseconds.

    from core import ui
    ui.install(root)                      # once, in App.__init__
    ui.call_soon(label.config, text="Done")   # from any thread

Background work itself goes through core.tasks.submit, which delivers its
result here.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import sys
import threading
from collections.abc import Callable, Generator
from typing import Any, Protocol


class EventLoop(Protocol):
    """What the engine needs from a window: Tk's ``after`` and ``after_cancel``."""

    def after(self, ms: int, func: Callable[[], object], /) -> str: ...
    def after_cancel(self, id: str, /) -> None: ...


_Call = tuple[Callable[..., object], tuple[Any, ...], dict[str, Any]]

logger = logging.getLogger("crossfader.ui")

_q: queue.SimpleQueue[_Call] = queue.SimpleQueue()
_root: EventLoop | None = None
_main_ident: int | None = None
_poll_ms = 15
_IDLE_MS = 100
_quiet_ms = 0
_BATCH = 400


def install(root: EventLoop, poll_ms: int = 15) -> None:
    """Start draining the queue on *root*'s event loop (call on the Tk thread)."""
    global _root, _main_ident, _poll_ms
    _root = root
    _main_ident = threading.get_ident()
    _poll_ms = max(5, int(poll_ms))
    root.after(_poll_ms, _drain)


def tk_error() -> type[Exception]:
    """tkinter.TclError, without importing Tk here."""
    tkinter = sys.modules.get("tkinter")
    return tkinter.TclError if tkinter is not None else RuntimeError


def tk_default_root() -> Any:
    """Tk's default root window, if Tk is loaded and has one (without loading Tk)."""
    tkinter = sys.modules.get("tkinter")
    return getattr(
        tkinter, "_default_root", None
    )  # Tk keeps it private; this is the one place we read it


def installed() -> bool:
    return _root is not None


@contextlib.contextmanager
def hosted_by(root: EventLoop, poll_ms: int = 15) -> Generator[None]:
    """Hand UI calls to *root* for a while (a throwaway window in a test); the
    window that had them before gets them back afterwards."""
    global _root, _main_ident, _poll_ms
    previous: tuple[EventLoop | None, int | None, int] = (_root, _main_ident, _poll_ms)
    install(root, poll_ms)
    try:
        yield
    finally:
        _root, _main_ident, _poll_ms = previous
        if _root is not None:
            _root.after(_poll_ms, _drain)  # its drain loop moved to *root*: restart it


def on_main_thread() -> bool:
    if _main_ident is None:
        return threading.current_thread() is threading.main_thread()
    return threading.get_ident() == _main_ident


def _drain() -> None:
    did_work = False
    try:
        for _ in range(_BATCH):
            try:
                fn, args, kwargs = _q.get_nowait()
            except queue.Empty:
                break
            did_work = True
            try:
                fn(*args, **kwargs)
            except Exception:  # never let one bad callback stop the pump
                logger.exception("UI callback %r failed", getattr(fn, "__name__", fn))
    finally:
        root = _root
        if root is not None:
            # Every _poll_ms while there is work; when idle, back off gradually to
            # _IDLE_MS so an idle app doesn't wake the CPU 66 times a second.
            global _quiet_ms
            _quiet_ms = 0 if did_work else min(_IDLE_MS, _quiet_ms + _poll_ms)
            try:
                root.after(max(_poll_ms, _quiet_ms), _drain)
            except tk_error():
                logger.debug("_drain: the window is gone", exc_info=True)


def call_soon(fn: Callable[..., object], *args: Any, **kwargs: Any) -> None:
    """Run ``fn(*args, **kwargs)`` on the Tk thread as soon as possible.
    Safe to call from any thread."""
    if _root is None:
        # Not installed (e.g. a tab built in isolation): best effort.
        if on_main_thread():
            fn(*args, **kwargs)
            return
        r = tk_default_root()
        if r is not None:
            r.after(0, lambda: fn(*args, **kwargs))
            return
        logger.warning("ui.call_soon before install(); dropping %r", fn)
        return
    _q.put((fn, args, kwargs))


def debounce(widget: EventLoop, delay_ms: int, fn: Callable[[], None]) -> Callable[..., None]:
    """Return a callable that runs *fn* once, *delay_ms* after the last call
    (main thread only) - e.g. for regenerating a preview while dragging."""
    state: dict[str, str | None] = {"after": None}

    def trigger(*_a: object) -> None:
        pending = state["after"]
        if pending is not None:
            try:
                widget.after_cancel(pending)
            except Exception:
                logger.debug("trigger: best-effort step failed", exc_info=True)
        state["after"] = widget.after(delay_ms, _fire)

    def _fire() -> None:
        state["after"] = None
        fn()

    return trigger
