"""
devtools/perf.py - finding what makes a Tk app feel slow.

High Performance Python (ch. 2) asks for measurements before changes, and for
a time-ordered view when the problem is a GUI that freezes.  cProfile is the
wrong tool for that here: from Python 3.12 it also records worker threads, so
its totals mix the Tk thread with background work.  These tools look only at
the Tk (main) thread:

    StallWatch(root)         reports every moment the event loop was blocked
                             longer than a threshold, with the code that blocked it
    MainThreadSampler()      a sampling profiler for the main thread only
    timed(name, budget_ms)   logs how long a block took; warns over budget on
                             the main thread

All three need no extra packages and no ptrace rights (py-spy may not attach).
"""
from __future__ import annotations

import collections
import logging
import sys
import threading
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

_log = logging.getLogger("perf")
_APP_MARK = ("/core/", "/tabs/", "/shell/", "/main.py")


def _app_frames(stack: traceback.StackSummary, depth: int) -> str:
    app = [f for f in stack if any(m in f.filename.replace("\\", "/") for m in _APP_MARK)]
    chosen = app[-depth:] if app else list(stack)[-1:]
    return " <- ".join(f"{_short(f.filename)}:{f.lineno}({f.name})" for f in reversed(chosen))


def _short(path: str) -> str:
    path = path.replace("\\", "/")
    for mark in ("/core/", "/tabs/", "/shell/"):
        if mark in path:
            return mark.strip("/") + "/" + path.split(mark, 1)[1]
    return path.rsplit("/", 1)[-1]


@dataclass
class Stall:
    label: str
    ms: float
    where: list[tuple[str, int]] = field(default_factory=list)


class StallWatch:
    """A Tk ``after()`` heartbeat stamps the time every *tick_ms*; a watchdog
    thread notices when the stamp is older than *threshold_ms* and samples the
    main thread's stack until the loop runs again."""

    def __init__(self, root, threshold_ms: float = 50, tick_ms: int = 10, sample_ms: float = 5) -> None:
        self.root = root
        self.threshold = threshold_ms / 1000
        self.tick = tick_ms
        self.sample = sample_ms / 1000
        self.stalls: list[Stall] = []
        self._label = ""
        self._last = time.perf_counter()
        self._running = True
        self._main = threading.main_thread().ident
        root.after(self.tick, self._beat)
        threading.Thread(target=self._watch, daemon=True, name="StallWatch").start()

    def label(self, text: str) -> None:
        """Name what the app is doing now, so a stall can be traced to it."""
        self._label = text

    def stop(self) -> None:
        self._running = False

    def _beat(self) -> None:
        self._last = time.perf_counter()
        if self._running:
            self.root.after(self.tick, self._beat)

    def _watch(self) -> None:
        current: tuple[str, float, collections.Counter[str]] | None = None
        while self._running:
            time.sleep(self.sample)
            stamp = self._last
            gap = time.perf_counter() - stamp
            if gap > self.threshold:
                if current is None:
                    current = (self._label, stamp, collections.Counter())
                frame = sys._current_frames().get(self._main or 0)
                if frame is not None:
                    current[2][_app_frames(traceback.extract_stack(frame), 4)] += 1
            elif current is not None:
                label, start, samples = current
                self.stalls.append(Stall(label, (self._last - start) * 1000, samples.most_common(2)))
                current = None

    def worst(self) -> list[Stall]:
        return sorted(self.stalls, key=lambda s: -s.ms)


class MainThreadSampler:
    """``with MainThreadSampler() as s: ...; print(s.report())``"""

    def __init__(self, interval: float = 0.001) -> None:
        self.interval = interval
        self.inclusive: collections.Counter[str] = collections.Counter()
        self.leaf: collections.Counter[str] = collections.Counter()
        self.samples = 0
        self._main = threading.main_thread().ident
        self._running = False
        self._thread: threading.Thread | None = None

    def __enter__(self) -> MainThreadSampler:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="MainThreadSampler")
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join()

    def _loop(self) -> None:
        while self._running:
            frame = sys._current_frames().get(self._main or 0)
            seen: set[str] = set()
            first = None
            while frame is not None:
                code = frame.f_code
                key = f"{_short(code.co_filename)}:{code.co_firstlineno}({code.co_name})"
                first = first or key
                if key not in seen:
                    self.inclusive[key] += 1
                    seen.add(key)
                frame = frame.f_back
            if first:
                self.leaf[first] += 1
            self.samples += 1
            time.sleep(self.interval)

    def report(self, top: int = 25) -> str:
        def ms(count: int) -> str:
            return f"{count * self.interval * 1000:7.1f} ms"
        lines = [f"{self.samples} samples (~{self.samples * self.interval * 1000:.0f} ms)"]
        for key, count in self.inclusive.most_common(top):
            lines.append(f"  incl {ms(count)}  {key}")
        lines.append(" self:")
        lines += [f"  self {ms(c)}  {k}" for k, c in self.leaf.most_common(8)]
        return "\n".join(lines)


@contextmanager
def timed(name: str, budget_ms: float | None = None) -> Iterator[None]:
    """Log how long the block took (DEBUG); a WARNING when it ran over
    *budget_ms* on the main thread, where it froze the window."""
    start = time.perf_counter()
    try:
        yield
    finally:
        ms = (time.perf_counter() - start) * 1000
        on_main = threading.current_thread() is threading.main_thread()
        if budget_ms is not None and on_main and ms > budget_ms:
            _log.warning("%s took %.0f ms on the UI thread (budget %.0f ms)", name, ms, budget_ms)
        else:
            _log.debug("%s took %.1f ms", name, ms)
