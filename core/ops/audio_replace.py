"""
core/ops/audio_replace.py - the Audio Replacer (tabs/audio/replacer.py): a video
with new sound (replacing or mixed with its own), or with its sound removed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from core import encoders as enc
from core import ffmpeg as ff


@dataclass(frozen=True, slots=True, kw_only=True)
class Replacement:
    """The new sound: moved by *offset* (s; negative skips into it), at
    *new_volume*, mixed with the original at *original_volume* when *mix*,
    looped to fill the video when *loop*, faded in and out over *fade* s."""

    mix: bool = False
    offset: float = 0.0
    new_volume: float = 1.0
    original_volume: float = 0.5
    loop: bool = False
    fade: float = 0.0


def replace_graph(r: Replacement, video_dur: float, *, has_orig: bool = True) -> str:
    """filter_complex with input 0 = video, 1 = new audio, output [a]."""
    st = "aformat=sample_rates=48000:channel_layouts=stereo"
    new = f"[1:a:0]{st},asetpts=PTS-STARTPTS"
    if r.offset < 0:
        new += f",atrim=start={-r.offset:.3f},asetpts=PTS-STARTPTS"
    elif r.offset > 0:
        new += f",adelay=delays={round(r.offset * 1000)}:all=1"
    new += f",volume={r.new_volume:.3f},apad,atrim=duration={video_dur:.3f}"
    if r.fade > 0:
        fade_out = max(video_dur - r.fade, 0)
        new += f",afade=t=in:st={max(r.offset, 0):.3f}:d={r.fade:.2f},afade=t=out:st={fade_out:.3f}:d={r.fade:.2f}"
    if r.mix and has_orig:
        orig = f"[0:a:0]{st},volume={r.original_volume:.3f},apad,atrim=duration={video_dur:.3f}[o]"
        return (
            f"{new}[n];{orig};[o][n]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]"
        )
    return f"{new}[a]"


def replace_cmd(src: str, audio: str, out: str, info: ff.MediaInfo, r: Replacement) -> list[str]:
    """*src*'s picture (copied) with the sound from *audio*, as long as the video."""
    container = os.path.splitext(out)[1].lstrip(".").lower() or "mp4"
    graph = replace_graph(r, info.duration, has_orig=info.has_audio)
    loop = ["-stream_loop", "-1"] if r.loop else []
    cmd = [ff.ffmpeg_path(), "-y", "-i", src, *loop, "-i", audio, "-filter_complex", graph]
    cmd += ["-map", "0:v", "-c:v", "copy", "-map", "[a]"]
    cmd += [
        *enc.audio(enc.default_audio_for(container), container=container),
        "-t",
        f"{info.duration:.3f}",
    ]
    if container in ("mp4", "mov", "m4v"):
        cmd += ["-movflags", "+faststart"]
    return [*cmd, out]


def strip_cmd(src: str, out: str) -> list[str]:
    """*src* without its sound (everything else copied)."""
    return [ff.ffmpeg_path(), "-y", "-i", src, "-map", "0", "-map", "-0:a", "-c", "copy", out]
