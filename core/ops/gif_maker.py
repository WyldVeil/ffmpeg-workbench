"""
core/ops/gif_maker.py - command builders for tabs/social/gif_maker.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from core import ffmpeg as ff
from core.ops.shared import Steps


def gif_graph(fps: float, width: int, colours: int, dither: str) -> str:
    """One-pass palette graph from [0:v] to [v]."""
    if dither.startswith("bayer"):
        use = f"paletteuse=dither=bayer:bayer_scale={dither[-1]}:diff_mode=rectangle"
    else:
        use = f"paletteuse=dither={dither}:diff_mode=rectangle"
    return (
        f"[0:v]fps={fps:g},scale={width}:-2:flags=lanczos,split[a][b];"
        f"[a]palettegen=max_colors={colours}:stats_mode=diff[p];[b][p]{use}[v]"
    )


@dataclass(frozen=True, slots=True)
class Animation:
    """What the GIF (or animated WebP, *webp*) looks like.  *loops*: 0 forever,
    -1 once, n = n extra times; *cap*: a size limit in bytes (0: none)."""

    fps: float
    width: int
    colours: int = 256
    dither: str = "sierra2_4a"
    loops: int = 0
    cap: int = 0
    webp: bool = False


def make_animation(
    src: str, final: str, look: Animation, steps: Steps, *, start: float, dur: float
) -> int:
    """GIF (or animated WebP) of [start, start+dur]; with a size cap the
    animation is re-made smaller (width, then frame rate) until it fits.
    Shared with the command line."""
    colours, dither, loops, cap, webp = look.colours, look.dither, look.loops, look.cap, look.webp
    run, log = steps.run, steps.log
    w_now, fps_now = look.width, look.fps
    for attempt in range(6):
        inp = [ff.ffmpeg_path(), "-y", "-ss", ff.ts(start), "-t", ff.ts(dur), "-i", src]
        if webp:
            cmd = [
                *inp,
                "-vf",
                f"fps={fps_now:g},scale={w_now}:-2:flags=lanczos",
                "-an",
                "-c:v",
                "libwebp",
                "-lossless",
                "0",
                "-quality",
                "75",
                "-loop",
                str(max(loops, 0)),
                "-f",
                "webp",
                final,
            ]
        else:
            cmd = [
                *inp,
                "-filter_complex",
                gif_graph(fps_now, w_now, colours, dither),
                "-map",
                "[v]",
                "-an",
                "-loop",
                str(loops),
                "-f",
                "gif",
                final,
            ]
        run(cmd, dur, attempt / 6, (attempt + 1) / 6 if cap else 1.0)
        size = os.path.getsize(final)
        log(f"{w_now}px @ {fps_now:g} fps → {size / 1024 / 1024:.2f} MB")
        if not cap or size <= cap:
            return 0
        shrink = (cap / size) ** 0.5 * 0.95
        w_now = max(64, int(w_now * shrink) // 2 * 2)
        if fps_now > 10:
            fps_now = max(10, round(fps_now * 0.85))
    raise RuntimeError("Could not reach the size limit - shorten the clip or lower the limit.")
