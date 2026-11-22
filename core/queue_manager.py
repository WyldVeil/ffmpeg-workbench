"""
core/queue_manager.py - Global background render queue.

Every long-running job (FFmpeg renders, downloads, multi-step pipelines) goes
through this singleton so the app can show progress, queue work, cancel it
reliably and stop everything on exit.

Highlights
----------
* Jobs run in worker threads (``max_parallel_jobs`` setting, default 1).
* FFmpeg commands run through :func:`core.ffmpeg.run`: real percentage and
  ETA (from ``-progress``), ``-nostdin``, UTF-8-safe log decoding, and a
  cancel that kills the whole process tree within a fraction of a second.
* Atomic outputs: a command whose last argument is the task's
  ``output_path`` writes to ``<name>.part<ext>`` and is renamed on success;
  the partial file is deleted on failure or cancel, so a broken render never
  replaces a good file.
* ``on_complete(task_id, returncode)`` ALWAYS fires exactly once - including
  for jobs cancelled while still waiting.  ``returncode`` is 0 on success,
  ``-1`` when cancelled; look up :meth:`get_task` for ``status`` and
  ``error`` (a human-readable summary of what went wrong).
* All UI callbacks run on the Tk thread via :mod:`core.ui`.

``worker_fn(progress_cb, cancel_fn)`` jobs remain supported; a worker that
accepts a third parameter receives a :class:`TaskContext` with ``run()``
(an FFmpeg runner wired to this task's progress and cancel), ``progress()``,
``log()`` and a private ``tempdir()`` that is cleaned up afterwards.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from core import ffmpeg as ff
from core import settings, tasks

logger = logging.getLogger("crossfader.queue")


class TaskStatus(StrEnum):
    """Where a job is in its life.  A StrEnum, so it still compares equal to
    the plain strings older code (and the saved queue) use."""

    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        return self in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)


PENDING, ACTIVE, DONE, FAILED, CANCELLED = (
    TaskStatus.PENDING,
    TaskStatus.ACTIVE,
    TaskStatus.DONE,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
)


#: worker_fn(progress_cb, cancel_fn[, ctx]) -> return code (None means 0)
WorkerFn = Callable[..., int | None]
OnStart = Callable[[str], object]  # (task_id)
OnProgress = Callable[[str, str], object]  # (task_id, log line)
OnComplete = Callable[[str, int], object]  # (task_id, returncode)
#: A job's work: commands run in order, or a worker function
Work = Sequence[Sequence[object]] | WorkerFn


@dataclass(frozen=True, slots=True)
class Hooks:
    """What a job tells the page that queued it - always on the Tk thread."""

    on_start: OnStart | None = None
    on_progress: OnProgress | None = None
    on_complete: OnComplete | None = None  # fires exactly once, cancelled jobs included


@dataclass(eq=False, kw_only=True)
class RenderTask:
    """One unit of work in the queue (read-only from the UI's point of view)."""

    id: str
    name: str
    output_path: str = ""
    cmds: list[list[str]] | None = None
    worker_fn: WorkerFn | None = None
    hooks: Hooks = field(default_factory=Hooks)
    expected_duration: float | None = None
    inputs: list[str] = field(default_factory=list[str])
    atomic_output: bool = True
    # ── state, updated by the queue ──
    status: TaskStatus = field(default=TaskStatus.PENDING, init=False)
    progress: str = field(default="", init=False)  # latest log line
    fraction: float | None = field(default=None, init=False)
    eta: float | None = field(default=None, init=False)
    speed: float | None = field(default=None, init=False)
    error: str = field(default="", init=False)
    log_tail: list[str] = field(default_factory=list[str], init=False)
    created_at: float = field(default_factory=time.time, init=False)
    started_at: float | None = field(default=None, init=False)
    finished_at: float | None = field(default=None, init=False)
    returncode: int = field(default=0, init=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, init=False)
    completed_fired: bool = field(default=False, init=False)
    tempdirs: list[str] = field(default_factory=list[str], init=False)
    parts: list[tuple[str, str]] = field(
        default_factory=list[tuple[str, str]], init=False
    )  # (part, final)

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    @property
    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        return (self.finished_at or time.time()) - self.started_at

    @property
    def command_text(self) -> str:
        if not self.cmds:
            return ""
        return "\n".join(ff.format_cmd(c) for c in self.cmds)


class TaskContext:
    """Helper handed to 3-argument ``worker_fn(progress_cb, cancel_fn, ctx)``."""

    def __init__(
        self, manager: RenderQueueManager, task: RenderTask, progress_cb: Callable[[str], None]
    ):
        self._m = manager
        self.task = task
        self._progress_cb = progress_cb
        self._span = (0.0, 1.0)

    def log(self, line: str) -> None:
        self._progress_cb(line)

    def cancelled(self) -> bool:
        return self.task.cancelled

    def progress(self, fraction: float | None) -> None:
        """Report overall progress 0..1 for this task."""
        self._m.set_fraction(self.task, fraction)

    def span(self, start: float, end: float) -> TaskContext:
        """Map the next :meth:`run`'s 0..1 progress onto *start*..*end*."""
        self._span = (max(0.0, start), min(1.0, end))
        return self

    def tempdir(self) -> str:
        from core.paths import temp_dir

        d = temp_dir(prefix=f"task_{self.task.id}_")
        self.task.tempdirs.append(d)
        return d

    def output(self, path: str | None = None) -> str:
        """A temporary ``.part`` file to write a final output to.  It replaces
        *path* (default: the task's output) only if the job succeeds, so a
        failed or cancelled job never leaves a broken file behind."""
        final = path or self.task.output_path
        base, ext = os.path.splitext(final)
        part = f"{base}.part{ext}"
        self.task.parts.append((part, final))
        return part

    def run(
        self,
        cmd: Sequence[str],
        *,
        duration: float | None = None,
        check: bool = True,
        cwd: str | None = None,
        tail: int = 200,
    ) -> ff.RunResult:
        """Run one command for this task.  Raises :class:`ff.CancelledError`
        or :class:`ff.CommandError` (when *check*) so multi-step jobs can
        simply call ``ctx.run`` repeatedly."""
        lo, hi = self._span

        def on_prog(p: ff.Progress) -> None:
            if p.fraction is not None:
                self._m.set_fraction(self.task, lo + (hi - lo) * p.fraction, p)

        self._progress_cb("$ " + ff.format_cmd(cmd))
        res = ff.run(
            cmd,
            duration=duration,
            on_line=self._progress_cb,
            on_progress=on_prog,
            cancel=self.cancelled,
            cwd=cwd,
            tail=tail,
        )
        self.task.log_tail = res.log_tail[-60:]
        self._span = (hi, hi)
        if check:
            if res.cancelled:
                raise ff.CancelledError("cancelled")
            if res.returncode != 0:
                raise ff.CommandError(res, cmd)
        return res


class RenderQueueManager:
    _instance: RenderQueueManager | None = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> RenderQueueManager:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._tasks: list[RenderTask] = []
        self._lock = threading.RLock()
        self._work = threading.Condition(self._lock)
        self._update_callbacks: list[Callable[[], object]] = []
        self._workers: list[tasks.Service] = []
        self._last_ui_update = 0.0
        self._ensure_workers()

    # ── configuration ────────────────────────────────────────────────────────

    def _parallel(self) -> int:
        return settings.MAX_PARALLEL_JOBS.get()

    def _ensure_workers(self) -> None:
        with self._lock:
            self._workers = [w for w in self._workers if w.is_alive()]
            while len(self._workers) < self._parallel():
                self._workers.append(
                    tasks.start_service(
                        self._run_worker, name=f"RenderQueueWorker-{len(self._workers) + 1}"
                    )
                )

    def register_update_callback(self, cb: Callable[[], object]) -> None:
        self._update_callbacks.append(cb)

    # ── public API ───────────────────────────────────────────────────────────

    def enqueue(
        self,
        name: str,
        output_path: str = "",
        *,
        work: Work,
        hooks: Hooks | None = None,
        expected_duration: float | None = None,
        inputs: Sequence[str] = (),
        atomic_output: bool = True,
    ) -> str:
        """Queue *work* (commands run in order, or a worker function) and return its id."""
        if callable(work):
            cmds, worker = None, work
        else:
            cmds, worker = [[str(c) for c in cmd] for cmd in work], None
            if not cmds:
                raise ValueError("enqueue() needs at least one command")
        task = RenderTask(
            id=uuid.uuid4().hex[:8],
            name=name,
            output_path=output_path,
            cmds=cmds,
            worker_fn=worker,
            hooks=hooks or Hooks(),
            expected_duration=expected_duration,
            inputs=[i for i in inputs if i],
            atomic_output=atomic_output,
        )
        with self._work:
            self._tasks.append(task)
            self._work.notify()
        self._ensure_workers()
        self._fire_update(force=True)
        logger.info("queued %s [%s] -> %s", task.name, task.id, task.output_path or "-")
        return task.id

    def get_task(self, task_id: str) -> RenderTask | None:
        with self._lock:
            return next((t for t in self._tasks if t.id == task_id), None)

    def cancel(self, task_id: str) -> None:
        fire = None
        with self._lock:
            task = next((t for t in self._tasks if t.id == task_id), None)
            if task is None or task.status in (DONE, FAILED, CANCELLED):
                return
            task.cancel_event.set()
            if task.status == PENDING:
                task.status = CANCELLED
                task.returncode = -1
                task.error = "Cancelled before it started."
                task.finished_at = time.time()
                fire = task
        if fire is not None:
            self._complete(fire)
        self._fire_update(force=True)

    def cancel_all(self) -> int:
        with self._lock:
            ids = [t.id for t in self._tasks if t.status in (PENDING, ACTIVE)]
        for tid in ids:
            self.cancel(tid)
        return len(ids)

    def retry(self, task_id: str) -> str | None:
        task = self.get_task(task_id)
        if task is None or task.status not in (FAILED, CANCELLED):
            return None
        return self.enqueue(
            task.name,
            task.output_path,
            work=task.worker_fn or task.cmds or [],
            hooks=task.hooks,
            expected_duration=task.expected_duration,
            inputs=task.inputs,
            atomic_output=task.atomic_output,
        )

    def clear_finished(self) -> None:
        with self._lock:
            self._tasks = [t for t in self._tasks if t.status in (PENDING, ACTIVE)]
        self._fire_update(force=True)

    def get_all_tasks(self) -> list[RenderTask]:
        with self._lock:
            return list(self._tasks)

    def active_tasks(self) -> list[RenderTask]:
        with self._lock:
            return [t for t in self._tasks if t.status in (PENDING, ACTIVE)]

    def get_stats(self):
        """(active, pending, done, failed_or_cancelled)."""
        with self._lock:
            active = sum(1 for t in self._tasks if t.status == ACTIVE)
            pending = sum(1 for t in self._tasks if t.status == PENDING)
            done = sum(1 for t in self._tasks if t.status == DONE)
            failed = sum(1 for t in self._tasks if t.status in (FAILED, CANCELLED))
        return active, pending, done, failed

    def overall_progress(self) -> float | None:
        """Mean fraction of active tasks (None when nothing reports progress)."""
        with self._lock:
            fr = [t.fraction for t in self._tasks if t.status == ACTIVE and t.fraction is not None]
        return sum(fr) / len(fr) if fr else None

    # ── internals ────────────────────────────────────────────────────────────

    def _fire_update(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_ui_update < 0.25:
            return
        self._last_ui_update = now
        from core import ui

        for cb in list(self._update_callbacks):
            ui.call_soon(_safe_call, cb)

    def _dispatch(self, fn: Callable[..., object] | None, *args: object) -> None:
        if fn is None:
            return
        from core import ui

        ui.call_soon(_safe_call, fn, *args)

    def set_fraction(
        self, task: RenderTask, fraction: float | None, prog: ff.Progress | None = None
    ) -> None:
        if fraction is not None:
            task.fraction = max(task.fraction or 0.0, min(1.0, fraction))
        if prog is not None:
            task.speed = prog.speed
            if task.fraction and task.fraction > 0.01 and task.started_at:
                elapsed = time.time() - task.started_at
                task.eta = max(0.0, elapsed / task.fraction - elapsed)
        self._fire_update()

    def _run_worker(self) -> None:
        while True:
            with self._work:
                task = None
                while task is None:
                    task = next((t for t in self._tasks if t.status == PENDING), None)
                    if task is None:
                        self._work.wait(timeout=1.0)
                        if threading.current_thread() not in self._workers[: self._parallel()]:
                            return
                task.status = ACTIVE
            try:
                self._execute(task)
            except Exception:  # pragma: no cover - last-resort guard
                logger.exception("queue worker crashed on %s", task.name)
                task.status = FAILED
                task.error = "Internal error - see the log file."
                self._complete(task)

    def _execute(self, task: RenderTask) -> None:
        task.started_at = time.time()
        self._fire_update(force=True)
        self._dispatch(task.hooks.on_start, task.id)

        def progress_cb(line: str) -> None:
            task.progress = line
            self._dispatch(task.hooks.on_progress, task.id, line)

        rc = 0
        try:
            if task.worker_fn is not None:
                rc = self._run_worker_fn(task, progress_cb)
            else:
                rc = self._run_cmds(task, progress_cb)
        except ff.CancelledError:
            rc = -1
        except ff.CommandError as exc:
            rc = exc.result.returncode or 1
            task.error = exc.result.error_summary
            task.log_tail = exc.result.log_tail[-60:]
        except Exception as exc:
            logger.exception("task %s failed", task.name)
            progress_cb(f"⚠ {type(exc).__name__}: {exc}")
            task.error = f"{type(exc).__name__}: {exc}"
            rc = 1
        finally:
            for d in task.tempdirs:
                shutil.rmtree(d, ignore_errors=True)
        rc = self._promote_parts(task, rc)

        task.finished_at = time.time()
        if task.cancelled:
            task.status, task.returncode = CANCELLED, -1
            task.error = task.error or "Cancelled."
        elif rc == 0:
            task.status, task.returncode = DONE, 0
            task.fraction = 1.0
        else:
            task.status, task.returncode = FAILED, rc
            if not task.error:
                task.error = ff.summarize_error(task.log_tail, rc)
        logger.info(
            "%s %s [%s] in %.1fs%s",
            task.status,
            task.name,
            task.id,
            task.elapsed,
            f": {task.error.splitlines()[0]}" if task.status == FAILED and task.error else "",
        )
        self._complete(task)

    @staticmethod
    def _promote_parts(task: RenderTask, rc: int) -> int:
        """Rename a worker's ``.part`` outputs on success, delete them otherwise."""
        ok = rc == 0 and not task.cancelled
        for part, final in task.parts:
            if ok and (not os.path.exists(part) or os.path.getsize(part) == 0):
                task.error = "The job finished but wrote no output."
                ok, rc = False, 1
            if ok:
                try:
                    os.replace(part, final)
                    continue
                except OSError as exc:
                    task.error = f"Could not save {final}: {exc}"
                    ok, rc = False, 1
            try:
                if os.path.exists(part):
                    os.remove(part)
            except OSError:
                pass
        return rc

    def _run_worker_fn(self, task: RenderTask, progress_cb: Callable[[str], None]) -> int:
        fn = task.worker_fn
        assert fn is not None, "only called for worker tasks"
        wants_ctx = False
        try:
            params = [
                p
                for p in inspect.signature(fn).parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
            wants_ctx = len(params) >= 3
        except (TypeError, ValueError):
            pass
        if wants_ctx:
            result = fn(progress_cb, lambda: task.cancelled, TaskContext(self, task, progress_cb))
        else:
            result = fn(progress_cb, lambda: task.cancelled)
        return result if isinstance(result, int) else 0

    def _run_cmds(self, task: RenderTask, progress_cb: Callable[[str], None]) -> int:
        n = len(task.cmds or [])
        for i, cmd in enumerate(task.cmds or []):
            if task.cancelled:
                return -1
            part = None
            argv = list(cmd)
            if (
                task.atomic_output
                and task.output_path
                and argv
                and argv[-1] == task.output_path
                and ff.is_ffmpeg(argv[0])
            ):
                base, ext = os.path.splitext(task.output_path)
                part = f"{base}.part{ext}"
                argv[-1] = part
                if "-y" not in argv:
                    argv.insert(1, "-y")
            lo, hi = i / n, (i + 1) / n

            def on_prog(p: ff.Progress, lo: float = lo, hi: float = hi) -> None:
                if p.fraction is not None:
                    self.set_fraction(task, lo + (hi - lo) * p.fraction, p)

            progress_cb("$ " + ff.format_cmd(cmd))
            res = ff.run(
                argv,
                duration=task.expected_duration,
                on_line=progress_cb,
                on_progress=on_prog,
                cancel=lambda: task.cancelled,
            )
            task.log_tail = res.log_tail[-60:]
            if res.cancelled or res.returncode != 0:
                if part and os.path.exists(part):
                    with contextlib.suppress(OSError):
                        os.remove(part)
                if res.cancelled:
                    return -1
                task.error = res.error_summary
                return res.returncode or 1
            if part:
                if not os.path.exists(part) or os.path.getsize(part) == 0:
                    task.error = "FFmpeg finished but wrote no output."
                    return 1
                os.replace(part, task.output_path)
        return 0

    def _complete(self, task: RenderTask) -> None:
        with self._lock:
            if task.completed_fired:
                return
            task.completed_fired = True
        self._fire_update(force=True)
        self._dispatch(task.hooks.on_complete, task.id, task.returncode)
        try:
            from core.notify import job_finished

            job_finished(task)
        except Exception:
            logger.debug("_complete: best-effort step failed", exc_info=True)


def _safe_call(fn: Callable[..., object], *args: object) -> None:
    try:
        fn(*args)
    except Exception:
        logger.exception("queue callback %r failed", getattr(fn, "__name__", fn))
