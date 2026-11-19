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
    ui.run_in_thread(work, on_done=show)  # run work() in a thread, show(result) on Tk
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable, Optional

_log = logging.getLogger("crossfader.ui")

_q: "queue.SimpleQueue[tuple]" = queue.SimpleQueue()
_root = None
_main_ident: Optional[int] = None
_POLL_MS = 15
_BATCH = 400


def install(root, poll_ms: int = 15) -> None:
    """Start draining the queue on *root*'s event loop (call on the Tk thread)."""
    global _root, _main_ident, _POLL_MS
    _root = root
    _main_ident = threading.get_ident()
    _POLL_MS = max(5, int(poll_ms))
    root.after(_POLL_MS, _drain)


def installed() -> bool:
    return _root is not None


def on_main_thread() -> bool:
    if _main_ident is None:
        return threading.current_thread() is threading.main_thread()
    return threading.get_ident() == _main_ident


def _drain() -> None:
    try:
        for _ in range(_BATCH):
            try:
                fn, args, kwargs = _q.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args, **kwargs)
            except Exception:  # never let one bad callback stop the pump
                _log.exception("UI callback %r failed", getattr(fn, "__name__", fn))
    finally:
        root = _root
        if root is not None:
            try:
                root.after(_POLL_MS, _drain)
            except Exception:
                pass  # root destroyed


def call_soon(fn: Callable, *args: Any, **kwargs: Any) -> None:
    """Run ``fn(*args, **kwargs)`` on the Tk thread as soon as possible.
    Safe to call from any thread."""
    if _root is None:
        # Not installed (e.g. a tab built in isolation): best effort.
        if on_main_thread():
            fn(*args, **kwargs)
            return
        try:
            import tkinter
            r = tkinter._default_root  # type: ignore[attr-defined]
            if r is not None:
                r.after(0, lambda: fn(*args, **kwargs))
                return
        except Exception:
            pass
        _log.warning("ui.call_soon before install(); dropping %r", fn)
        return
    _q.put((fn, args, kwargs))


def run_in_thread(fn: Callable, *args: Any,
                  on_done: Optional[Callable[[Any], None]] = None,
                  on_error: Optional[Callable[[BaseException], None]] = None,
                  name: Optional[str] = None, **kwargs: Any) -> threading.Thread:
    """Run ``fn(*args, **kwargs)`` in a daemon thread; deliver the result to
    *on_done* (or the exception to *on_error*) on the Tk thread."""

    def _worker():
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - forwarded to the UI
            _log.exception("background task %s failed", name or getattr(fn, "__name__", fn))
            if on_error is not None:
                call_soon(on_error, exc)
            return
        if on_done is not None:
            call_soon(on_done, result)

    t = threading.Thread(target=_worker, name=name or f"bg-{getattr(fn, '__name__', 'task')}",
                         daemon=True)
    t.start()
    return t


def debounce(widget, delay_ms: int, fn: Callable[[], None]) -> Callable[[], None]:
    """Return a callable that runs *fn* once, *delay_ms* after the last call
    (main thread only) - e.g. for regenerating a preview while dragging."""
    state = {"after": None}

    def trigger(*_a):
        if state["after"] is not None:
            try:
                widget.after_cancel(state["after"])
            except Exception:
                pass
        state["after"] = widget.after(delay_ms, _fire)

    def _fire():
        state["after"] = None
        fn()

    return trigger
