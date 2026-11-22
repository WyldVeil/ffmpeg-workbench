"""
core/ops/mixer.py - the Audio Mixer's filter graph (tabs/audio/mixer.py).

Pure functions: values in, a filter graph out - no widgets, no Tk.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

_STEREO = "aformat=sample_rates=48000:channel_layouts=stereo"


@dataclass(frozen=True, slots=True)
class Track:
    """One added audio track: it starts *offset* s into the video, fades in and
    out, sits *pan* -1 (left) .. 1 (right), and repeats when *loop*."""

    path: str
    duration: float  # the track's own length
    offset: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    volume: float = 1.0
    pan: float = 0.0
    loop: bool = False


@dataclass(frozen=True, slots=True)
class Master:
    """The video's own sound (None: left out), the final volume and loudness."""

    video_volume: float | None = None
    volume: float = 1.0
    normalise: bool = False


def track_chain(t: Track, input_no: int, total: float) -> str:
    """The track as input *input_no*, placed on a *total* s timeline -> [t<n>]."""
    clip_len = max(0.01, (total - t.offset) if t.loop else min(t.duration, total - t.offset))
    chain = [
        f"[{input_no}:a:0]{_STEREO}",
        "asetpts=PTS-STARTPTS",
        f"atrim=duration={clip_len:.3f}",
        f"volume={t.volume:.3f}",
    ]
    chain += [f"afade=t=in:st=0:d={t.fade_in:.2f}"] if t.fade_in > 0 else []
    chain += (
        [f"afade=t=out:st={max(0.0, clip_len - t.fade_out):.3f}:d={t.fade_out:.2f}"]
        if t.fade_out > 0
        else []
    )
    if abs(t.pan) > 0.02:
        chain.append(
            f"pan=stereo|c0={min(1.0, 1.0 - t.pan):.3f}*c0|c1={min(1.0, 1.0 + t.pan):.3f}*c1"
        )
    chain += [f"adelay=delays={round(t.offset * 1000)}:all=1"] if t.offset > 0 else []
    return ",".join([*chain, "apad", f"atrim=duration={total:.3f}"]) + f"[t{input_no}]"


def input_args(tracks: Sequence[Track]) -> list[str]:
    """-i for every track after the video (looping ones repeat)."""
    args: list[str] = []
    for t in tracks:
        args += [*(["-stream_loop", "-1"] if t.loop else []), "-i", t.path]
    return args


def mix_graph(tracks: Sequence[Track], master: Master, total: float) -> str:
    """filter_complex mixing the video's sound (input 0) and *tracks* -> [aout]."""
    parts: list[str] = []
    mix: list[str] = []
    if master.video_volume is not None:
        parts.append(
            f"[0:a:0]{_STEREO},volume={master.video_volume:.3f},apad,atrim=duration={total:.3f}[orig]"
        )
        mix.append("[orig]")
    for n, t in enumerate(tracks, start=1):
        parts.append(track_chain(t, n, total))
        mix.append(f"[t{n}]")
    volume = f"volume={master.volume:.3f}"
    last = (
        f"{mix[0]}{volume}"
        if len(mix) == 1
        else "".join(mix) + f"amix=inputs={len(mix)}:duration=longest:normalize=0,{volume}"
    )
    if master.normalise:
        last += ",loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,asetnsamples=n=4096:p=0"
    return ";".join([*parts, last + ",alimiter=limit=0.97[aout]"])
