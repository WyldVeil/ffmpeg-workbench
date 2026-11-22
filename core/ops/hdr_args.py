"""
core/ops/hdr_args.py - command builders for tabs/transcoder/hdr.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

from core import ffmpeg as ff

TEXT_SUBS = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}


def subtitle_args(info: ff.MediaInfo, container: str) -> list[str]:
    """Keep subtitles where the container can hold them (text ones converted for MP4/MOV/WebM)."""
    subs = [s for s in info.streams if s.type == "subtitle"]
    if not subs:
        return []
    if container == "mkv":
        return ["-map", "0:s?", "-c:s", "copy"]
    target = {"mp4": "mov_text", "mov": "mov_text", "m4v": "mov_text", "webm": "webvtt"}.get(
        container
    )
    maps = (
        [a for s in subs if s.codec in TEXT_SUBS for a in ("-map", f"0:{s.index}")]
        if target
        else []
    )
    return [*maps, "-c:s", target] if maps and target else []
