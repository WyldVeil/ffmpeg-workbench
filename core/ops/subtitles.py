"""
core/ops/subtitles.py - subtitle tracks added to a video as they are (soft
subtitles, switchable in the player): Auto Subtitles (tabs/social/auto_subs.py);
and subtitles burnt into the picture: the Hard-Subber's style and its UTF-8
copy of the file (tabs/social/hard_subber.py).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from core import ass
from core import ffmpeg as ff

logger = logging.getLogger("crossfader.ops.subtitles")

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


# ── Burnt-in subtitles ───────────────────────────────────────────────────────

#: where burnt-in subtitles go: key -> (label, ASS alignment)
POSITIONS = {
    "bottom": ("Bottom centre", 2),
    "top": ("Top centre", 8),
    "middle": ("Middle", 5),
    "bottom_left": ("Bottom left", 1),
    "bottom_right": ("Bottom right", 3),
}


def to_utf8_copy(path: str, folder: str) -> str:
    """Copy a subtitle file into *folder* as UTF-8 with a safe name."""
    raw = Path(path).read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        try:
            text = _guess_text(raw)
        except Exception:
            logger.debug("to_utf8_copy: best-effort step failed", exc_info=True)
            text = None
    if text is None:
        text = raw.decode("cp1252", errors="replace")
    ext = os.path.splitext(path)[1].lower() or ".srt"
    out = os.path.join(folder, f"subs{ext}")
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.replace("\r\n", "\n"))
    return out


def force_style(
    *,
    font: str,
    size: int,
    colour: str,
    outline_colour: str,
    outline: float,
    box: bool,
    position: str,
    margin: int,
) -> str:
    align = POSITIONS.get(position, POSITIONS["bottom"])[1]
    parts = [
        f"FontName={font}",
        f"FontSize={size}",
        f"PrimaryColour={ass.colour(colour)}",
        f"OutlineColour={ass.colour(outline_colour)}",
        f"Alignment={align}",
        f"MarginV={margin}",
        "Bold=0",
    ]
    if box:
        parts += [
            "BorderStyle=3",
            f"Outline={max(1.0, outline):g}",
            "Shadow=0",
            f"BackColour={ass.colour(outline_colour, 0.35)}",
        ]
    else:
        parts += [
            "BorderStyle=1",
            f"Outline={outline:g}",
            "Shadow=1",
            f"BackColour={ass.colour('#000000', 0.5)}",
        ]
    return ",".join(parts)


def _guess_text(raw: bytes) -> str | None:
    """*raw* decoded by charset_normalizer's best guess, if it is installed."""
    import importlib

    from_bytes = importlib.import_module("charset_normalizer").from_bytes  # optional
    best = from_bytes(raw).best()
    return str(best) if best is not None else None
