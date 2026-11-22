"""
core/ops/subtitles.py - subtitle tracks added to a video as they are (soft
subtitles, switchable in the player): Auto Subtitles (tabs/social/auto_subs.py).
"""

from __future__ import annotations

import os

from core import ffmpeg as ff

SOFT_SUB_CODEC = {  # container -> the subtitle codec it holds
    "mp4": "mov_text",
    "m4v": "mov_text",
    "mov": "mov_text",
    "mkv": "srt",
    "webm": "webvtt",
}


def add_track_cmd(src: str, subs: str, out: str, language: str, *, after: int) -> list[str]:
    """*src* (all copied) with *subs* added as the default subtitle track, after
    its *after* existing ones; a container without text subtitles gets SRT (MKV)."""
    container = os.path.splitext(out)[1].lstrip(".").lower()
    codec = SOFT_SUB_CODEC.get(container, "srt")
    n = after
    cmd = [ff.ffmpeg_path(), "-y", "-i", src, "-i", subs, "-map", "0", "-map", "1:0", "-c", "copy"]
    cmd += [
        f"-c:s:{n}",
        codec,
        f"-metadata:s:s:{n}",
        f"language={language}",
        f"-disposition:s:{n}",
        "default",
    ]
    return [*cmd, out]
