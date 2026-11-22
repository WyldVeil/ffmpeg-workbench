"""
core/ops/thumbnail.py - the Thumbnail Maker (tabs/social/thumbnail.py): one
frame (or one every few seconds) sized for a platform, adjusted, captioned.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from core import ffmpeg as ff
from core import filters as fl

#: picture format (the radio buttons' value) -> (what they show, extension, FFmpeg arguments)
PICTURE_FORMATS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "png": ("PNG", ".png", ("-c:v", "png")),
    "jpg95": ("JPEG (q95)", ".jpg", ("-c:v", "mjpeg", "-q:v", "2")),
    "jpg85": ("JPEG (q85)", ".jpg", ("-c:v", "mjpeg", "-q:v", "5")),
    "webp": ("WebP", ".webp", ("-c:v", "libwebp", "-quality", "90")),
}
TEXT_PLACES = {  # caption position -> (x, y)
    "center": ("(w-text_w)/2", "(h-text_h)/2"),
    "top": ("(w-text_w)/2", "h*0.05"),
    "bottom": ("(w-text_w)/2", "h-text_h-h*0.05"),
    "top-left": ("w*0.04", "h*0.05"),
    "top-right": ("w-text_w-w*0.04", "h*0.05"),
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Caption:
    text: str
    size: str = "64"
    colour: str = "white"
    place: str = "center"
    outline: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class Look:
    size: tuple[int, int] = (0, 0)  # (0, 0) keeps the frame's own
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    caption: Caption | None = None


def look_filters(look: Look, folder: str) -> list[str]:
    """Fit (letterboxed) to the size, adjust, caption; the caption's text goes
    to a UTF-8 file in *folder*, so quotes, % and colons are all safe."""
    parts: list[str] = []
    w, h = look.size
    if w > 0 and h > 0:
        parts.append(
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
        )
    if look.brightness != 0.0 or look.contrast != 1.0 or look.saturation != 1.0:
        parts.append(
            f"eq=brightness={look.brightness}:contrast={look.contrast}:saturation={look.saturation}"
        )
    c = look.caption
    if c is not None:
        textfile = os.path.join(folder, "caption.txt")
        with open(textfile, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(c.text)
        x, y = TEXT_PLACES.get(c.place, TEXT_PLACES["center"])
        text = fl.DrawText(
            textfile=textfile,
            font="impact",
            fontsize=c.size,
            fontcolor=c.colour,
            x=x,
            y=y,
            borderw=4 if c.outline else 0,
            bordercolor="black",
        )
        parts.append(text.filter())
    return parts


def frame_cmd(
    src: str, at: float, duration: float, filters: Sequence[str], fmt_args: Sequence[str], out: str
) -> list[str]:
    """One picture at *at* seconds.  At the very end, the last half second is
    decoded, each frame overwriting the picture, so the final frame is what
    remains whatever the timestamps say."""
    if at >= duration - 0.5:
        cmd = [ff.ffmpeg_path(), "-y", "-sseof", "-0.5", "-i", src, "-an"]
    else:
        cmd = [ff.ffmpeg_path(), "-y", "-ss", ff.ts(at), "-i", src, "-frames:v", "1", "-an"]
    if filters:
        cmd += ["-vf", ",".join(filters)]
    return [*cmd, *fmt_args, "-update", "1", out]


def batch_cmd(
    src: str, interval: float, filters: Sequence[str], fmt_args: Sequence[str], pattern: str
) -> list[str]:
    """A picture every *interval* seconds, named by *pattern* (%04d)."""
    vf = ",".join([f"fps=1/{interval:g}", *filters])
    return [ff.ffmpeg_path(), "-y", "-i", src, "-an", "-vf", vf, *fmt_args, pattern]
