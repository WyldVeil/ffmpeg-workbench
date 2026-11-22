"""
core/ops/slideshow.py - photos into a video, for tabs/social/slideshow.py.

Pure functions: values in, FFmpeg arguments and subtitle scripts out - no
widgets, no Tk.  Each photo becomes a short clip (framed, then moved Ken
Burns style); the clips are joined with transitions, captions burned over
them and music laid under them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core import ass
from core import ffmpeg as ff


def motion_filter(motion: str, w: int, h: int, fps: str, frames: int, zoom: float = 1.2) -> str:
    """Ken Burns on ONE input frame: expressions use the output frame number."""
    d = max(frames - 1, 1)
    p = f"(on/{d})"
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "zoom_in":
        z, x, y = f"1+{zoom - 1:.4f}*{p}", cx, cy
    elif motion == "zoom_out":
        z, x, y = f"{zoom:.4f}-{zoom - 1:.4f}*{p}", cx, cy
    elif motion == "pan_right":
        z, x, y = f"{zoom:.4f}", f"(iw-iw/zoom)*{p}", cy
    elif motion == "pan_left":
        z, x, y = f"{zoom:.4f}", f"(iw-iw/zoom)*(1-{p})", cy
    elif motion == "pan_down":
        z, x, y = f"{zoom:.4f}", cx, f"(ih-ih/zoom)*{p}"
    elif motion == "pan_up":
        z, x, y = f"{zoom:.4f}", cx, f"(ih-ih/zoom)*(1-{p})"
    else:
        z, x, y = "1", "0", "0"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps}"


def frame_filter(fit: str, w: int, h: int) -> str:
    """Frame the photo at 2x the output size (smooth zoompan sub-pixel steps)."""
    W, H = 2 * w, 2 * h
    if fit == "blur":
        return (
            f"split[a][b];[a]scale={W // 8}:{H // 8}:force_original_aspect_ratio=increase,"
            f"crop={W // 8}:{H // 8},gblur=sigma=6,eq=brightness=-0.08,scale={W}:{H}[bg];"
            f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    if fit == "bars":
        return (
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
        )
    return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"


def timeline(n: int, dur: float, trans: float) -> tuple[list[float], float]:
    """(start time of each slide, total length) with overlapping transitions."""
    starts = [i * (dur - trans) for i in range(n)]
    return starts, (starts[-1] + dur) if n else 0.0


@dataclass(frozen=True, slots=True)
class Look:
    """Every slide's size and movement: *fit* "blur", "bars" or "crop"."""

    width: int
    height: int
    fps: str
    frames: int  # per slide
    fit: str = "blur"
    zoom: float = 1.2


def slide_cmd(path: str, motion: str, look: Look, clip: str) -> list[str]:
    """One photo -> a *look.frames*-frame clip at *clip*."""
    w, h = look.width, look.height
    graph = f"[0:v]{frame_filter(look.fit, w, h)},{motion_filter(motion, w, h, look.fps, look.frames, look.zoom)},format=yuv420p[v]"
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        "-i",
        path,
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-frames:v",
        str(look.frames),
    ]
    return [
        *cmd,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "14",
        "-pix_fmt",
        "yuv420p",
        clip,
    ]


def join_graph(n: int, trans: float, xfades: Sequence[str], starts: Sequence[float]) -> list[str]:
    """Filter steps joining inputs 0..n-1 -> [xf]: cut together, or crossfaded."""
    if n == 1:
        return ["[0:v]null[xf]"]
    if trans <= 0:
        return ["".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[xf]"]
    parts: list[str] = []
    last = "[0:v]"
    for i in range(1, n):
        label = "[xf]" if i == n - 1 else f"[x{i}]"
        parts.append(
            f"{last}[{i}:v]xfade=transition={xfades[i - 1]}:duration={trans:.3f}:offset={starts[i]:.3f}{label}"
        )
        last = label
    return parts


def caption_script(
    captions: Sequence[str],
    look: Look,
    timing: tuple[Sequence[float], float, float],
    font: str,
    position: str,
) -> ass.Script:
    """Each slide's caption while it shows; *timing* = (starts, seconds per slide,
    transition); *position* "bottom", "top" or "lower_third"."""
    starts, dur, td = timing
    w, h = look.width, look.height
    u = min(w, h) / 1080
    sc = ass.Script(w, h)
    sc.style(
        ass.Style(
            "Cap",
            font=font,
            size=round(46 * u),
            bold=True,
            outline=2.5 * u,
            shadow=1.5 * u,
            back_alpha=0.5,
            alignment={"top": 8, "lower_third": 1}.get(position, 2),
            margin_l=round(60 * u),
            margin_r=round(60 * u),
            margin_v=round((h * 0.12) if position == "lower_third" else 50 * u),
        )
    )
    n = len(captions)
    for i, text in enumerate(captions):
        if text.strip():
            a = starts[i] + (td / 2 if i else 0)
            b = starts[i] + dur - (td / 2 if i < n - 1 else 0)
            sc.add(a, b, f"{{\\fad(300,300)}}{ass.escape(text)}", "Cap")
    return sc


def music_filter(input_no: int, total: float, volume: float) -> str:
    """The music under the slides: trimmed to *total*, faded in and out -> [a]."""
    fo = min(3.0, total / 4)
    return (
        f"[{input_no}:a:0]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,volume={volume:.2f},"
        f"afade=t=in:st=0:d=0.5,afade=t=out:st={total - fo:.3f}:d={fo:.2f}[a]"
    )
