"""
core/ops/chain_run.py - runs an All-in-One Builder plan (core/ops/chain.py)
as one render-queue job.

Each pass runs in turn.  Before a pass that joins several steps, FFmpeg tries
it for a moment; if the joined command doesn't work (a mix the joiner didn't
foresee), those steps are planned again one command each, so the render still
succeeds - just with a pass or two more.  A step whose job is Python code runs
with a context that points its output at the plan's file, and swaps the
encoding of its final command: lossless when another step follows, the
builder's own settings when it is the last one.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import os
from collections.abc import Callable, Sequence
from typing import Any

from core import ffmpeg as ff
from core.ops import chain
from core.queue_manager import RenderTask, TaskContext

logger = logging.getLogger("crossfader.chain")

Replan = Callable[[set[int]], list[chain.Pass]]


def works(cmd: Sequence[str]) -> bool:
    """True if *cmd* runs for a moment without an error."""
    try:
        res = ff.run(chain.dry_run_cmd(cmd), timeout=90)
    except Exception:
        logger.debug("dry run failed to start", exc_info=True)
        return False
    if res.returncode != 0:
        logger.info("joined command failed its check: %s", res.error_summary)
    return res.returncode == 0


def execute(
    replan: Replan,
    ctx: Any,
    *,
    src: str,
    final: chain.Encode,
    duration: float | None = None,
    check: bool = True,
) -> int:
    """Run the plan *replan(set())* on *src* - re-planned with steps on their
    own when a joined command fails its check.  *ctx* is the render queue's
    TaskContext (or a :class:`LocalContext`).  Returns 0, or raises like ``ctx.run``."""
    alone: set[int] = set()
    passes = replan(alone)
    k = 0
    while k < len(passes):
        if ctx.cancelled():
            return -1
        p = passes[k]
        if os.path.abspath(p.writes) == os.path.abspath(src):
            raise RuntimeError("a step would write over the source video")
        if check and len(p.steps) > 1 and p.cmds and not works(p.cmds[0]):
            ctx.log(f"(steps {p.steps[0] + 1}-{p.steps[-1] + 1} run one at a time)")
            alone |= set(p.steps)
            passes = replan(alone)
            k = next(i for i, q in enumerate(passes) if q.steps[0] == p.steps[0])
            continue
        share = (k / len(passes), (k + 1) / len(passes))
        if not p.final and os.path.exists(p.writes):
            os.remove(p.writes)  # never written through: it may be a link to a preview
        if p.run is not None:
            _run_python(p, ctx, share, final)
        else:
            for n, cmd in enumerate(p.cmds):
                lo = share[0] + (share[1] - share[0]) * n / len(p.cmds)
                hi = share[0] + (share[1] - share[0]) * (n + 1) / len(p.cmds)
                ctx.span(lo, hi).run(cmd, duration=duration)
        k += 1
    ctx.progress(1.0)
    return 0


class StepContext:
    """What a Python step's job sees as its task: its output is the plan's
    file *writes*; the command that writes it gets *encode*'s encoding."""

    def __init__(self, parent: Any, writes: str, encode: chain.Encode | None) -> None:
        self._parent = parent
        self._encode = encode
        self.task = RenderTask(
            id=parent.task.id + "-step", name=parent.task.name, output_path=writes
        )
        self.task.cancel_event = parent.task.cancel_event
        self._parts: set[str] = set()

    def log(self, line: str) -> None:
        self._parent.log(line)

    def cancelled(self) -> bool:
        return self._parent.cancelled()

    def progress(self, fraction: float | None) -> None:
        self._parent.progress(fraction)

    def span(self, start: float, end: float) -> StepContext:
        self._parent.span(start, end)
        return self

    def tempdir(self) -> str:
        return self._parent.tempdir()

    def output(self, path: str | None = None) -> str:
        # the first output is the step's result, wherever the job meant to put it
        final = self.task.output_path if not self.task.parts else (path or self.task.output_path)
        base, ext = os.path.splitext(final)
        part = f"{base}.part{ext}"
        self.task.parts.append((part, final))
        self._parts.add(part)
        return part

    def run(self, cmd: Sequence[str], **kwargs: Any) -> ff.RunResult:
        argv = list(cmd)
        if self._encode is not None and argv and argv[-1] in self._parts and ff.is_ffmpeg(argv[0]):
            argv = chain.reencode(argv, self._encode)
        return self._parent.run(argv, **kwargs)

    def finish(self) -> None:
        for part, final in self.task.parts:
            if os.path.exists(part):
                os.replace(part, final)

    def discard(self) -> None:
        for part, _final in self.task.parts:
            with contextlib.suppress(OSError):
                os.remove(part)


def _run_python(
    p: chain.Pass, ctx: TaskContext, share: tuple[float, float], final: chain.Encode
) -> None:
    encode = None if p.own_format else (final if p.final else chain.LOSSLESS_ENCODE)
    step_ctx = StepContext(ctx, p.writes, encode)
    ctx.span(*share)
    fn = p.run
    try:
        params = [
            q
            for q in inspect.signature(fn).parameters.values()
            if q.kind in (q.POSITIONAL_ONLY, q.POSITIONAL_OR_KEYWORD)
        ]
    except (TypeError, ValueError):
        params = []
    try:
        if len(params) >= 3:
            rc = fn(ctx.log, ctx.cancelled, step_ctx)
        else:
            rc = fn(ctx.log, ctx.cancelled)
    except BaseException:
        step_ctx.discard()
        raise
    if isinstance(rc, int) and rc != 0:
        step_ctx.discard()
        if rc < 0:
            raise ff.CancelledError("cancelled")
        raise RuntimeError(f"step {p.steps[0] + 1} failed ({rc})")
    step_ctx.finish()
    if not os.path.exists(p.writes) and p.made and os.path.exists(p.made):
        os.replace(p.made, p.writes)  # the job wrote where it was told when set up
    if not os.path.exists(p.writes):
        raise RuntimeError(f"step {p.steps[0] + 1} wrote nothing")


class LocalContext:
    """A TaskContext for work run outside the render queue (a step's preview
    video): *on_fraction(f)* hears the progress, *cancelled()* stops it."""

    def __init__(
        self, on_fraction: Callable[[float], object], cancelled: Callable[[], bool]
    ) -> None:
        self.task = RenderTask(id="builder-preview", name="preview")
        self._on = on_fraction
        self._cancel = cancelled
        self._span = (0.0, 1.0)
        self._temps: list[str] = []

    def log(self, line: str) -> None:
        logger.debug("preview: %s", line)

    def cancelled(self) -> bool:
        return self._cancel()

    def progress(self, fraction: float | None) -> None:
        if fraction is not None:
            self._on(fraction)

    def span(self, start: float, end: float) -> LocalContext:
        self._span = (start, end)
        return self

    def tempdir(self) -> str:
        from core.paths import temp_dir

        d = temp_dir("step_")
        self._temps.append(d)
        return d

    def output(self, path: str | None = None) -> str:
        return path or self.task.output_path

    def run(
        self,
        cmd: Sequence[str],
        *,
        duration: float | None = None,
        check: bool = True,
        cwd: str | None = None,
        tail: int = 200,
    ) -> ff.RunResult:
        lo, hi = self._span

        def on_prog(p: ff.Progress) -> None:
            if p.fraction is not None:
                self._on(lo + (hi - lo) * p.fraction)

        res = ff.run(
            cmd, duration=duration, on_progress=on_prog, cancel=self._cancel, cwd=cwd, tail=tail
        )
        self._span = (hi, hi)
        if check:
            if res.cancelled:
                raise ff.CancelledError("cancelled")
            if res.returncode != 0:
                raise ff.CommandError(res, cmd)
        return res

    def cleanup(self) -> None:
        import shutil

        for d in self._temps:
            shutil.rmtree(d, ignore_errors=True)
