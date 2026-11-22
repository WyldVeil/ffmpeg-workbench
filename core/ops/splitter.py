"""
core/ops/splitter.py - one clip of the Multi-Splitter (tabs/cutting/splitter.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

from core import encoders as enc
from core import ffmpeg as ff


def clip_command(
    src: str, start: float, end: float, ext: str, *, keyframe: float | None = None
) -> tuple[list[str], float]:
    """(argv without its output, seconds) for [start, end] of *src*.

    With *keyframe* the clip is copied from that keyframe (at or before *start*,
    so it starts a little early but loses no quality); otherwise it is
    re-encoded and starts exactly."""
    begin = start if keyframe is None else keyframe
    cmd = [ff.ffmpeg_path(), "-y", "-ss", ff.ts(begin), "-i", src, "-t", ff.ts(end - begin)]
    cmd += ["-map", "0:v:0?", "-map", "0:a?"]
    if keyframe is not None:
        cmd += ["-c", "copy", "-sn", "-dn"]
    else:
        cmd += ["-sn", "-dn"]
        cmd += enc.video("h264", "very_high", hw="off", container="mp4").args
        cmd += enc.audio("aac", "192k", container="mp4")
    if ext.lower() in (".mp4", ".mov", ".m4v") and "-movflags" not in cmd:
        cmd += ["-movflags", "+faststart"]
    return cmd, end - begin
