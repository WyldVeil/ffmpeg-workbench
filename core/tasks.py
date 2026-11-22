"""
core/tasks.py - background work, done one way.

Effective Python items 72 and 74: a new Thread per job costs a thread start,
puts no bound on how many run at once, and loses its exceptions.  A pool
bounds the threads, and its Future carries the result *or* the exception back
to the caller.  Fluent Python ch. 20: let ``submit`` make the futures.

    future = tasks.submit(partial(ff.probe, path), on_done=self._show, on_error=self._failed)

* ``fn`` runs on a worker thread.  It must not touch Tk widgets or Tk
  variables: read what it needs on the UI thread first (into a frozen
  dataclass) and pass that in.
* ``on_done(result)`` / ``on_error(exc)`` run on the UI thread (through
  :func:`core.ui.call_soon`), so they may update widgets.
* An error with no ``on_error`` is logged with its traceback - never lost.
* Previews have their own small pool, so a slow analysis can't hold up
  scrubbing (pool="preview").

Long-running loops that live as long as the app (the render queue's workers,
a recorder's reader) are not jobs; start those with :func:`start_service`.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from typing import Literal, TypeVar

logger = logging.getLogger("crossfader.tasks")

T = TypeVar("T")
Pool = Literal["background", "preview"]

_SIZES: dict[Pool, int] = {"background": 8, "preview": 2}
_pools: dict[Pool, ThreadPoolExecutor] = {}  # threads: created on first use, under _LOCK
_LOCK = threading.Lock()
Service = threading.Thread  # what start_service returns (join(), is_alive())


def _pool(kind: Pool) -> ThreadPoolExecutor:
    with _LOCK:
        pool = _pools.get(kind)
        if pool is None:
            pool = _pools[kind] = ThreadPoolExecutor(
                max_workers=_SIZES[kind], thread_name_prefix=kind
            )
        return pool


def _post(fn: Callable[..., object], arg: object) -> None:
    """To the UI thread when there is a window.  Without Tk at all (the command
    line, tests of the engine) straight away, on this thread."""
    from core import ui

    tkinter = sys.modules.get("tkinter")
    if ui.installed() or getattr(tkinter, "_default_root", None) is not None:
        ui.call_soon(fn, arg)
    else:
        fn(arg)


def _deliver(
    future: Future[T],
    name: str,
    on_done: Callable[[T], object] | None,
    on_error: Callable[[BaseException], object] | None,
) -> None:
    if future.cancelled():
        return
    exc = future.exception()
    if exc is None:
        if on_done is not None:
            _post(on_done, future.result())
        return
    if isinstance(exc, CancelledError):
        return
    if on_error is not None:
        from core.errors import AppError

        if not isinstance(exc, AppError):  # a bug: keep the traceback in the log
            logger.error("background task %s failed", name, exc_info=exc)
        _post(on_error, exc)
    else:
        logger.error("background task %s failed", name, exc_info=exc)


def submit(
    fn: Callable[[], T],
    *,
    on_done: Callable[[T], object] | None = None,
    on_error: Callable[[BaseException], object] | None = None,
    pool: Pool = "background",
    name: str | None = None,
) -> Future[T]:
    """Run ``fn()`` on a worker thread; deliver its result or error on the UI thread."""
    label = name or getattr(fn, "__name__", None) or repr(fn)
    future = _pool(pool).submit(fn)
    future.add_done_callback(lambda f: _deliver(f, label, on_done, on_error))
    return future


def run_all(jobs: Sequence[Callable[[], T]], *, name: str) -> list[T]:
    """Run *jobs* side by side and wait for every one (from a worker thread, e.g.
    a render made in pieces).  They get their own threads, so a busy pool can't
    leave them waiting on each other; the first error is raised once all end."""
    with ThreadPoolExecutor(max_workers=max(1, len(jobs)), thread_name_prefix=name) as pool:
        futures = [pool.submit(job) for job in jobs]
    return [f.result() for f in futures]


def start_service(target: Callable[[], object], *, name: str) -> Service:
    """Start a long-lived background loop (a daemon thread: it never keeps the
    app from closing).  For one-off jobs use :func:`submit`."""

    def _run() -> None:
        try:
            target()
        except Exception:  # a crashed service is a bug: log it
            logger.exception("service thread %s crashed", name)

    thread = threading.Thread(target=_run, name=name, daemon=True)
    thread.start()
    return thread


def shutdown() -> None:
    """At exit: drop queued jobs; running ones end when their processes are
    stopped (core.proc.kill_all)."""
    with _LOCK:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        pool.shutdown(wait=False, cancel_futures=True)
