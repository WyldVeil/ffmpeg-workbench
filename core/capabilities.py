"""
core/capabilities.py - what the installed FFmpeg can do, learned once.

Some facts cost a program run to learn: which filters this FFmpeg has, whether
its Vulkan (GPU) filters work on this computer, whether ``anlmdn`` is one of
the builds where it crashes.  They only change when the program changes, so
each one is

* learned on a worker thread - never on the UI thread (High Performance
  Python ch. 2: the stalls the profiler found were all one-off probes like
  these, run while a page was being built);
* kept on disk for a week, keyed by the program's identity (path, size,
  modification time), so the next start knows at once;
* read in one of three ways:

    capabilities.get("vulkan")              # blocks until known (workers, CLI)
    capabilities.peek("vulkan", False)      # never blocks (building a page)
    capabilities.on_ready("vulkan", cb)     # cb(value) on the UI thread, once known

:func:`warm` (called at start-up) learns everything in the background.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from functools import partial
from typing import Any, Final, Literal, cast

from core import paths, proc, tasks, ui

logger = logging.getLogger("crossfader.capabilities")

Name = Literal["filters", "vulkan", "anlmdn"]
NAMES: Final[tuple[Name, ...]] = ("filters", "vulkan", "anlmdn")
_MAX_AGE: Final = 7 * 24 * 3600  # GPU drivers change without FFmpeg changing: re-check weekly
_MISSING: Final = object()

_values: dict[str, Any] = {}  # threads: written under the name's lock, read freely
_locks: dict[str, threading.Lock] = {n: threading.Lock() for n in NAMES}
_disk_lock = threading.Lock()


# ── What each fact is, and how to learn it ─────────────────────────────────────


def _ffmpeg() -> str:
    from core import ffmpeg as ff

    return ff.ffmpeg_path()


def _learn_filters() -> list[str]:
    out = proc.run([_ffmpeg(), "-hide_banner", "-filters"], timeout=20).stdout
    names: set[str] = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and len(parts[0]) <= 4 and "->" in parts[2]:
            names.add(parts[1])
    return sorted(names)


def _learn_vulkan() -> bool:
    from core import ffmpeg as ff

    if "libplacebo" not in get("filters"):
        return False
    r = proc.run(
        [
            _ffmpeg(),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            *ff.VULKAN_DEVICE,
            "-f",
            "lavfi",
            "-i",
            "color=s=64x64:d=0.1",
            "-vf",
            "libplacebo=w=128:h=128:format=yuv420p",
            "-f",
            "null",
            "-",
        ],
        timeout=30,
    )
    return r.ok


def _learn_anlmdn() -> bool:
    """anlmdn corrupts the heap in some FFmpeg builds: run it once on noise."""
    r = proc.run(
        [
            _ffmpeg(),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=d=1:c=pink",
            "-af",
            "anlmdn",
            "-f",
            "null",
            "-",
        ],
        timeout=30,
    )
    return r.ok


_LEARN: Final[dict[Name, tuple[Callable[[], Any], Any]]] = {
    # name: (how to learn it, what to assume when the program can't be run)
    "filters": (_learn_filters, []),
    "vulkan": (_learn_vulkan, False),
    "anlmdn": (_learn_anlmdn, False),
}


def _shape(name: Name, value: Any) -> Any:
    """In memory, the filter list is a frozenset (it is asked "is X in it?" a lot)."""
    return frozenset(value) if name == "filters" else value


# ── The disk cache ─────────────────────────────────────────────────────────────


def _cache_file() -> str:
    return os.path.join(paths.cache_dir(), "capabilities.json")


def _identity(binary: str) -> str | None:
    try:
        st = os.stat(binary)
    except OSError:
        return None
    return f"{os.path.abspath(binary)}|{st.st_size}|{st.st_mtime_ns}"


def _read_disk() -> dict[str, Any]:
    try:
        with open(_cache_file(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return cast("dict[str, Any]", data) if isinstance(data, dict) else {}


def _entry(raw: object) -> dict[str, Any] | None:
    """A cached fact that is still fresh, or None."""
    if not isinstance(raw, dict):
        return None
    entry = cast("dict[str, Any]", raw)
    try:
        fresh = time.time() - float(entry.get("at", 0)) < _MAX_AGE
    except (TypeError, ValueError):
        return None
    return entry if fresh else None


def _from_disk(name: Name, ident: str) -> Any:
    entry = _entry(_read_disk().get(f"{name}|{ident}"))
    return entry.get("value", _MISSING) if entry is not None else _MISSING


def _to_disk(name: Name, ident: str, value: Any) -> None:
    with _disk_lock:
        data: dict[str, Any] = {k: v for k, v in _read_disk().items() if _entry(v) is not None}
        if isinstance(value, (set, frozenset)):
            value = sorted(cast("set[str]", value))
        data[f"{name}|{ident}"] = {"value": value, "at": time.time()}
        tmp = _cache_file() + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, _cache_file())
        except OSError:
            logger.debug("could not save %s", _cache_file(), exc_info=True)


# ── Reading ───────────────────────────────────────────────────────────────────


def get(name: Name) -> Any:
    """The fact, learning it first if needed.  On the UI thread the learning
    still happens on a worker (this waits for it): no program ever starts there."""
    value = _values.get(name, _MISSING)
    if value is not _MISSING:
        return value
    if ui.installed() and ui.on_main_thread():
        logger.debug("UI thread waits for capability %r - call warm() earlier or use peek()", name)
        return tasks.submit(partial(get, name), name=f"capability {name}").result()
    with _locks[name]:
        value = _values.get(name, _MISSING)
        if value is not _MISSING:
            return value
        learn, fallback = _LEARN[name]
        ident = _identity(_ffmpeg())
        value = _from_disk(name, ident) if ident else _MISSING
        if value is _MISSING:
            try:
                value = learn()
            except proc.ProcessError:
                logger.info(
                    "capability %s: FFmpeg could not be run; assuming %r",
                    name,
                    fallback,
                    exc_info=True,
                )
                value, ident = fallback, None  # don't remember a guess
            if ident:
                _to_disk(name, ident, value)
        value = _shape(name, value)
        _values[name] = value
        return value


def peek(name: Name, default: Any = None) -> Any:
    """The fact if it is known already (from this run or the disk cache), else
    *default*.  Never runs a program."""
    value = _values.get(name, _MISSING)
    if value is not _MISSING:
        return value
    ident = _identity(_ffmpeg())
    if ident:
        value = _from_disk(name, ident)
        if value is not _MISSING:
            return _values.setdefault(name, _shape(name, value))
    return default


def on_ready(name: Name, callback: Callable[[Any], object]) -> Future[Any]:
    """Call *callback(value)* on the UI thread once the fact is known."""
    return tasks.submit(partial(get, name), on_done=callback, name=f"capability {name}")


def warm() -> None:
    """Learn everything in the background (at start-up)."""
    for name in NAMES:
        tasks.submit(partial(get, name), name=f"capability {name}")


def forget() -> None:
    """After the FFmpeg in use changes (Advanced Settings): learn again."""
    for name in NAMES:
        with _locks[name]:
            _values.pop(name, None)
