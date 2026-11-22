"""
core/ops/batch_files.py - one job over many files (the Format Converter, the Proxy
Generator): each written to a ".part" name and renamed only when complete; a
file that fails is reported and the rest carry on; the job fails at the end if
any did.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Callable, Sequence

from core import ffmpeg as ff
from core.queue_manager import TaskContext

logger = logging.getLogger("crossfader.ops.batch_files")

Make = Callable[[TaskContext, str, str], None]  # (ctx, source, file to write)


def part_name(out: str) -> str:
    base, ext = os.path.splitext(out)
    return f"{base}.part{ext}"


def _reason(exc: Exception, explain: Callable[[str], str] | None) -> str:
    lines = str(exc).strip().splitlines()
    reason = lines[-1] if lines else type(exc).__name__
    return explain(reason) if explain else reason


def run_batch(
    ctx: TaskContext,
    jobs: Sequence[tuple[str, str]],
    make: Make,
    *,
    noun: str,
    explain: Callable[[str], str] | None = None,
) -> int:
    """Make each (source, output) pair with *make*; log "<done> of <n> <noun>"."""
    n = len(jobs)
    failed: list[tuple[str, str]] = []
    for i, (src, out) in enumerate(jobs):
        ctx.span(i / n, (i + 1) / n)
        ctx.log(f"[{i + 1}/{n}] {os.path.basename(src)} → {os.path.basename(out)}")
        part = part_name(out)
        try:
            make(ctx, src, part)
            os.replace(part, out)
        except ff.CancelledError:
            _remove(part)
            raise
        except Exception as exc:  # one bad file must not stop the batch
            logger.debug("batch item failed", exc_info=True)
            _remove(part)
            reason = _reason(exc, explain)
            failed.append((src, reason))
            ctx.log(f"  ✖ failed: {reason}")
    ctx.log(f"{n - len(failed)} of {n} {noun}.")
    if failed:
        lines = "\n".join(f"• {os.path.basename(s)}: {r}" for s, r in failed)
        raise RuntimeError(f"{len(failed)} of {n} file(s) failed:\n{lines}")
    return 0


def _remove(path: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(path)
