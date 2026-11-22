"""
core/ops/sync.py - Audio Sync (tabs/audio/sync.py): one sound track moved earlier
or later against the picture, copied (by input offset) or re-encoded (exact).
"""

from __future__ import annotations

import os

from core import encoders as enc
from core import ffmpeg as ff


def lossless_cmd(src: str, out: str, offset: float, track: int, n_audio: int) -> list[str]:
    """Shift audio track *track* by *offset* seconds; other tracks untouched."""
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        "-i",
        src,
        "-itsoffset",
        f"{offset:.3f}",
        "-i",
        src,
        "-map",
        "0:v?",
    ]
    for i in range(n_audio):
        cmd += ["-map", f"{1 if i == track else 0}:a:{i}"]
    cmd += ["-map", "0:s?", "-c", "copy", "-map_metadata", "0"]
    if out.lower().endswith((".mp4", ".mov", ".m4v")):
        cmd += ["-movflags", "+faststart"]
    return [*cmd, out]


def reencode_filter(offset: float) -> str:
    """Delay the sound (*offset* > 0) or skip into it."""
    if offset >= 0:
        return f"adelay=delays={round(offset * 1000)}:all=1"
    return f"atrim=start={-offset:.3f},asetpts=PTS-STARTPTS"


def reencode_cmd(src: str, out: str, offset: float, track: int, duration: float) -> list[str]:
    """Exact: the picture copied, track *track* shifted and re-encoded, as long as the video."""
    container = os.path.splitext(out)[1].lstrip(".").lower() or "mp4"
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        "-i",
        src,
        "-map",
        "0:v?",
        "-c:v",
        "copy",
        "-map",
        f"0:a:{track}",
    ]
    cmd += ["-af", reencode_filter(offset) + ",apad", "-t", f"{duration:.3f}"]
    return [*cmd, *enc.default_audio(container), out]
