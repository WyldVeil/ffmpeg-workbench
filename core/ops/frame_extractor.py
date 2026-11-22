"""
core/ops/frame_extractor.py - command builders for tabs/social/frame_extractor.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core import ffmpeg as ff
from core.ops import scenes
from core.queue_manager import TaskContext


def quality_args(ext: str, quality: int) -> list[str]:
    q = max(1, min(100, int(quality)))
    if ext == "jpg":
        return ["-c:v", "mjpeg", "-q:v", str(max(2, min(31, round(31 - (q - 1) * 29 / 99))))]
    if ext == "webp":
        return ["-c:v", "libwebp", "-quality", str(q), "-lossless", "0"]
    if ext == "tiff":
        return ["-c:v", "tiff", "-compression_algo", "deflate"]
    return ["-c:v", "png"]


def times_select(times: Sequence[float], fps: float) -> str:
    """select= expression picking the frame nearest to each time."""
    half = 0.5 / max(fps, 1.0)
    return "+".join(f"between(t,{tm - half:.4f},{tm + half - 1e-4:.4f})" for tm in times)


def single_cmd(src: str, at: float, out: str, quality: Sequence[str]) -> list[str]:
    """The frame at *at* as one picture."""
    head = [ff.ffmpeg_path(), "-y", "-ss", ff.ts(at), "-i", src, "-frames:v", "1", "-an"]
    return [*head, *quality, "-update", "1", out]


def interval_cmd(
    src: str,
    part: tuple[float, float],
    every: float,
    fps: float,
    pattern: str,
    quality: Sequence[str],
) -> list[str]:
    """One frame every *every* seconds of *part*, numbered into *pattern* (an image2 pattern)."""
    start, end = part
    pick = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{every - 0.5 / fps:.4f})'"
    head = [ff.ffmpeg_path(), "-y", "-ss", ff.ts(start), "-to", ff.ts(end), "-i", src, "-an"]
    return [*head, "-vf", pick, "-fps_mode", "passthrough", *quality, pattern]


@dataclass(frozen=True, slots=True, kw_only=True)
class SceneFrames:
    """The queued job: find the shot changes, then save the first frame of each
    scene (at least *min_len* seconds apart) into *pattern*."""

    src: str
    threshold: float
    min_len: float
    fps: float
    duration: float
    pattern: str
    quality: tuple[str, ...]

    def run(self, _p: object, _c: object, ctx: TaskContext) -> int:
        starts = scenes.detect(
            ctx.span(0.0, 0.7), self.src, self.threshold, self.min_len, self.duration
        )
        more = " …" if len(starts) > 20 else ""
        ctx.log(
            f"{len(starts)} scene(s): " + ", ".join(ff.format_time(s) for s in starts[:20]) + more
        )
        pick = f"select='{times_select(starts, self.fps)}'"
        save = [
            ff.ffmpeg_path(),
            "-y",
            "-i",
            self.src,
            "-an",
            "-sn",
            "-vf",
            pick,
            "-fps_mode",
            "passthrough",
        ]
        ctx.span(0.7, 1.0).run([*save, *self.quality, self.pattern], duration=self.duration)
        return 0
