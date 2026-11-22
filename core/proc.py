"""
core/proc.py - the only place the app starts other programs.

Effective Python item 67 (use subprocess; always give a timeout) and Robust
Python ch. 11 (make the right thing the easy thing).  Every child process
goes through :func:`spawn`, which

* closes stdin unless asked, never opens a console window on Windows, and
  puts the child in its own process group so its whole tree can be stopped;
* registers it, so :func:`kill_all` (app exit, the Stop button) reaches it;
* raises :class:`~core.errors.ProgramNotFound` rather than a bare OSError.

:func:`run` is the blocking form.  Its ``timeout`` has no default on purpose:
every caller decides how long is too long (``None`` means "no limit" and has
to be written out).  It can also be cancelled, and returns a frozen
:class:`ProcResult` instead of raising for a non-zero exit code.

    res = proc.run([ffprobe, "-version"], timeout=10)
    if res.ok: ...
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import IO, Any, AnyStr, Generic, Literal, overload

from core.errors import Cancelled, ProcessError, ProcessTimeout, ProgramNotFound, StartError
from core.procenv import child_env

__all__ = [
    "DEVNULL",
    "PIPE",
    "STDOUT",
    "Cancelled",
    "Popen",
    "ProcResult",
    "ProcessError",
    "ProcessTimeout",
    "ProgramNotFound",
    "StartError",
    "TimeoutExpired",
    "active_count",
    "forget",
    "kill_all",
    "kill_tree",
    "lines",
    "list2cmdline",
    "run",
    "spawn",
]

logger = logging.getLogger("crossfader.proc")

IS_WINDOWS = os.name == "nt"
_CREATE_NEW_PROCESS_GROUP = 0x00000200 if IS_WINDOWS else 0
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # no console window flashing up

DEVNULL = subprocess.DEVNULL
PIPE = subprocess.PIPE
STDOUT = subprocess.STDOUT
Popen = subprocess.Popen
TimeoutExpired = subprocess.TimeoutExpired
list2cmdline = subprocess.list2cmdline

_ACTIVE: set[subprocess.Popen[Any]] = set()  # threads: any thread adds/removes, under _LOCK
_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class ProcResult(Generic[AnyStr]):
    """What a finished program left behind (text or bytes, as asked for)."""

    argv: tuple[str, ...]
    returncode: int
    stdout: AnyStr
    stderr: AnyStr
    seconds: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def last_error_line(self) -> str:
        """The last non-empty line of stderr (FFmpeg puts the reason there)."""
        text = (
            self.stderr.decode("utf-8", "replace")
            if isinstance(self.stderr, bytes)
            else self.stderr
        )
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return lines[-1] if lines else f"exited with code {self.returncode}"


def _argv(argv: Sequence[object]) -> list[str]:
    return [os.fsdecode(a) if isinstance(a, os.PathLike) else str(a) for a in argv]  # pyright: ignore[reportUnknownArgumentType]


def spawn(
    argv: Sequence[object],
    *,
    stdin: int | IO[Any] | None = DEVNULL,
    stdout: int | IO[Any] | None = None,
    stderr: int | IO[Any] | None = None,
    text: bool = False,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    new_group: bool = True,
    bufsize: int = -1,
) -> subprocess.Popen[Any]:
    """Start a program (not waiting for it) and register it for :func:`kill_all`."""
    args = _argv(argv)
    kwargs: dict[str, Any] = {
        "stdin": stdin,
        "stdout": stdout,
        "stderr": stderr,
        "cwd": cwd,
        "env": dict(env) if env is not None else child_env(),
        "bufsize": bufsize,
    }
    if text:
        kwargs.update(text=True, encoding="utf-8", errors="replace")
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW | (_CREATE_NEW_PROCESS_GROUP if new_group else 0)
    elif new_group:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(args, **kwargs)
    except FileNotFoundError as exc:
        raise ProgramNotFound(f"{os.path.basename(args[0])} was not found") from exc
    except OSError as exc:
        raise StartError(
            f"couldn't start {os.path.basename(args[0])}: {exc.strerror or exc}"
        ) from exc
    with _LOCK:
        _ACTIVE.add(proc)
    return proc


@overload
def run(
    argv: Sequence[object],
    *,
    timeout: float | None,
    text: Literal[True] = ...,
    input: str | None = ...,
    cwd: str | None = ...,
    env: Mapping[str, str] | None = ...,
    cancel: Callable[[], bool] | None = ...,
    merge_stderr: bool = ...,
) -> ProcResult[str]: ...
@overload
def run(
    argv: Sequence[object],
    *,
    timeout: float | None,
    text: Literal[False],
    input: bytes | None = ...,
    cwd: str | None = ...,
    env: Mapping[str, str] | None = ...,
    cancel: Callable[[], bool] | None = ...,
    merge_stderr: bool = ...,
) -> ProcResult[bytes]: ...


def run(
    argv: Sequence[object],
    *,
    timeout: float | None,
    text: bool = True,
    input: str | bytes | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    cancel: Callable[[], bool] | None = None,
    merge_stderr: bool = False,
) -> ProcResult[Any]:
    """Run a program to the end and collect its output.

    Raises :class:`ProgramNotFound`, :class:`ProcessTimeout` (after stopping
    the program) or :class:`Cancelled` (when *cancel* returns True); a
    non-zero exit code is *not* an exception - check ``result.ok``."""
    start = time.monotonic()
    args = _argv(argv)
    proc = spawn(
        args,
        stdin=PIPE if input is not None else DEVNULL,
        stdout=PIPE,
        stderr=STDOUT if merge_stderr else PIPE,
        text=text,
        cwd=cwd,
        env=env,
    )
    deadline = None if timeout is None else start + timeout
    pending: str | bytes | None = input
    try:
        while True:
            step: float | None = None
            if cancel is not None:
                step = 0.1
            if deadline is not None:
                left = max(0.0, deadline - time.monotonic())
                step = left if step is None else min(step, left)
            try:
                out, err = proc.communicate(pending, timeout=step)
                break
            except subprocess.TimeoutExpired:
                pending = None  # input is sent once; retrying keeps all output
                if cancel is not None and cancel():
                    kill_tree(proc)
                    raise Cancelled("stopped") from None
                if deadline is not None and time.monotonic() >= deadline:
                    kill_tree(proc)
                    raise ProcessTimeout(
                        f"{os.path.basename(args[0])} took longer than "
                        f"{timeout:g} s and was stopped"
                    ) from None
    finally:
        _forget(proc)
    if text:
        return ProcResult[str](
            tuple(args), proc.returncode, out or "", err or "", time.monotonic() - start
        )
    return ProcResult[bytes](
        tuple(args), proc.returncode, out or b"", err or b"", time.monotonic() - start
    )


def lines(child: subprocess.Popen[str]) -> Iterator[str]:
    """Each line *child* writes (started with ``stdout=PIPE, text=True``), until it
    closes its output; then the pipe is closed and the program waited for."""
    out = child.stdout
    if out is None:
        raise ValueError("lines() needs a program started with stdout=PIPE")
    try:
        yield from iter(out.readline, "")
    finally:
        out.close()
        child.wait()
        _forget(child)


def _forget(proc: subprocess.Popen[Any] | None) -> None:
    if proc is None:
        return
    with _LOCK:
        _ACTIVE.discard(proc)


def forget(proc: subprocess.Popen[Any]) -> None:
    """Stop tracking *proc* (it has been waited for)."""
    _forget(proc)


def kill_tree(proc: subprocess.Popen[Any] | None, grace: float = 3.0) -> None:
    """Stop *proc* and every child it started: politely first, then by force."""
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
        if IS_WINDOWS:
            # A targeted tree kill of this PID only (never "/IM ffmpeg.exe").
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except OSError:
                proc.terminate()
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            if IS_WINDOWS:
                proc.kill()
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except OSError:
                    proc.kill()
            proc.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        logger.warning("could not stop process %s cleanly", proc.pid, exc_info=True)
        with contextlib.suppress(OSError):
            proc.kill()
    finally:
        _forget(proc)


def kill_all() -> int:
    """Stop every program started through :func:`spawn`.  Returns how many."""
    with _LOCK:
        procs = list(_ACTIVE)
    stopped = 0
    for p in procs:
        if p.poll() is None:
            kill_tree(p, grace=2.0)
            stopped += 1
        _forget(p)
    return stopped


def active_count() -> int:
    """How many started programs are still running."""
    with _LOCK:
        return sum(1 for p in _ACTIVE if p.poll() is None)
