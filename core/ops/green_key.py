"""
core/ops/green_key.py - chroma, colour and luma keying for the Green Screen
Keyer (tabs/visuals/green_key.py).

Values in, filter graphs and FFmpeg arguments out (two samplers read a
frame to find the screen's colour).
"""

from __future__ import annotations

import os
import statistics
from dataclasses import dataclass

from core import ffmpeg as ff
from core import filters as fl
from core.ops.shared import keep_audio_args

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".avif")


def hex_colour(value: str) -> str:
    v = value.strip().lstrip("#").upper()
    if len(v) != 6 or any(c not in "0123456789ABCDEF" for c in v):
        raise ValueError(f"Not a colour: {value}")
    return v


def spill_type(hex_rgb: str) -> str | None:
    r, g, b = (int(hex_rgb[i : i + 2], 16) for i in (0, 2, 4))
    if g > r and g >= b:
        return "green"
    if b > r and b > g:
        return "blue"
    return None


def key_chain(method: str, colour: str, similarity: float, blend: float, despill: bool) -> str:
    """Filter chain that makes the background transparent (yuva output)."""
    if method == "luma_white":
        return (
            f"format=yuva420p,lumakey=threshold=1:tolerance={similarity:.3f}:softness={blend:.3f}"
        )
    if method == "luma_black":
        return (
            f"format=yuva420p,lumakey=threshold=0:tolerance={similarity:.3f}:softness={blend:.3f}"
        )
    if method == "color":
        return f"colorkey=color=0x{colour}:similarity={similarity:.3f}:blend={blend:.3f}"
    chain = f"chromakey=color=0x{colour}:similarity={similarity:.3f}:blend={blend:.3f}"
    kind = spill_type(colour)
    if despill and kind:
        chain += f",despill=type={kind}:mix=0.5:expand=0.1"
    return chain


def background_chain(width: int, height: int, fps: str) -> str:
    """Cover-fit a background stream to WxH at *fps* (no distortion)."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps={fps}"
    )


def sample_border_colour(path: str, at: float = 0.5) -> str | None:
    """Median colour of the frame edges (where a backdrop usually is)."""
    w, h = 64, 36
    r = ff.capture(
        [
            ff.ffmpeg_path(),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            ff.ts(at),
            "-i",
            path,
            "-frames:v",
            "1",
            "-vf",
            f"scale={w}:{h}:flags=area,format=rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        timeout=30,
        text=False,
    )
    data = r.stdout or b""
    if r.returncode != 0 or len(data) != w * h * 3:
        return None
    px = [
        data[(y * w + x) * 3 : (y * w + x) * 3 + 3]
        for y in range(h)
        for x in range(w)
        if x < 3 or x >= w - 3 or y < 3 or y >= h - 3
    ]
    med = [int(statistics.median(p[i] for p in px)) for i in range(3)]
    return "".join(f"{c:02X}" for c in med)


def sample_point(path: str, at: float, x: int, y: int) -> str | None:
    """Average colour of a 5x5 patch at source pixel (x, y)."""
    r = ff.capture(
        [
            ff.ffmpeg_path(),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            ff.ts(at),
            "-i",
            path,
            "-frames:v",
            "1",
            "-vf",
            f"crop=5:5:{max(0, x - 2)}:{max(0, y - 2)},scale=1:1:flags=area,format=rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        timeout=30,
        text=False,
    )
    data = r.stdout or b""
    if r.returncode != 0 or len(data) != 3:
        return None
    return "".join(f"{c:02X}" for c in data)


def preview_graph(
    kind: str, keyer: str, size: tuple[int, int], fps: str, bg: str, bg_colour: str
) -> str:
    """The preview's graph from [in]: the keyed picture over the background
    (*kind* "file", "color", or "alpha" - a checkerboard showing transparency)."""
    w, h = size
    if kind == "file":
        src = f"{fl.preview_movie(bg)}{background_chain(w, h, fps)}[bg]"
    elif kind == "color":
        src = f"color=c=0x{bg_colour}:s={w}x{h}[bg]"
    else:
        src = f"nullsrc=s={w}x{h},geq=lum='if(mod(floor(X/24)+floor(Y/24),2),200,140)':cb=128:cr=128[bg]"
    return f"{src};[in]{keyer}[fg];[bg][fg]overlay=format=auto:shortest=1"


def transparent_cmd(src: str, out: str, keyer: str, info: ff.MediaInfo) -> list[str]:
    """The keyed picture with its transparency: ProRes 4444 (.mov/.mkv) or VP9 with alpha (.webm)."""
    container = os.path.splitext(out)[1].lower().lstrip(".")
    if container == "webm":
        graph = f"[0:v]{keyer},format=yuva420p[v]"
        venc = [
            "-c:v",
            "libvpx-vp9",
            "-pix_fmt",
            "yuva420p",
            "-crf",
            "28",
            "-b:v",
            "0",
            "-row-mt",
            "1",
            "-auto-alt-ref",
            "0",
        ]
    else:
        graph = f"[0:v]{keyer},format=yuva444p10le[v]"
        venc = [
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4444",
            "-pix_fmt",
            "yuva444p10le",
            "-alpha_bits",
            "16",
            "-vendor",
            "apl0",
        ]
    head = [ff.ffmpeg_path(), "-y", "-i", src, "-filter_complex", graph, "-map", "[v]"]
    return [*head, *keep_audio_args(info, container), *venc, out]


@dataclass(frozen=True, slots=True, kw_only=True)
class Backdrop:
    """What goes behind: a *colour* (kind "color") or the file at *path*
    (a *still* picture, or a video that loops, or holds its last frame)."""

    kind: str
    colour: str
    path: str = ""
    still: bool = False
    loop: bool = False


def composite_graph(
    b: Backdrop, keyer: str, size: tuple[int, int], fps: str
) -> tuple[str, list[str]]:
    """(graph ending [v], extra inputs): the keyed source over the backdrop."""
    w, h = size
    extra_inputs: list[str] = []
    if b.kind == "color":
        bg_src = f"color=c=0x{b.colour}:s={w}x{h}:r={fps},setsar=1[bg]"
    else:
        hold = ""
        if b.still:
            extra_inputs = ["-loop", "1", "-framerate", fps, "-i", b.path]
        elif b.loop:
            extra_inputs = ["-stream_loop", "-1", "-i", b.path]
        else:
            extra_inputs = ["-i", b.path]
            hold = ",tpad=stop_mode=clone:stop=-1"
        bg_src = f"[1:v]{background_chain(w, h, fps)}{hold}[bg]"
    return (
        f"{bg_src};[0:v]setsar=1,{keyer}[fg];[bg][fg]overlay=format=auto:shortest=1[v]",
        extra_inputs,
    )
