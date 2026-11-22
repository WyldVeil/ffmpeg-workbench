"""
core/loudness.py - EBU R128 loudness normalisation done properly.

Two passes: measure with ``loudnorm`` (JSON report), then normalise with the
measured values in linear mode, so the whole file gets one gain (no pumping)
and lands on target.  loudnorm works at 192 kHz internally, so the output is
resampled back to the source rate.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core import ffmpeg as ff

if TYPE_CHECKING:
    from core.queue_manager import TaskContext

TARGETS = {  # id: (label, integrated LUFS, true peak dBTP, LRA)
    "streaming": ("Streaming - Spotify, Apple Music, YouTube Music (-14 LUFS)", -14.0, -1.0, 11.0),
    "youtube": ("YouTube / podcasts (-16 LUFS)", -16.0, -1.5, 11.0),
    "ebu": ("Broadcast TV - EBU R128 (-23 LUFS)", -23.0, -1.0, 7.0),
    "atsc": ("Broadcast TV - ATSC A/85, US (-24 LUFS)", -24.0, -2.0, 7.0),
    "film": ("Film / cinema mix (-27 LUFS)", -27.0, -2.0, 15.0),
}


@dataclass
class Measurement:
    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    target_offset: float

    def as_args(self) -> str:
        return (
            f"measured_I={self.input_i:.2f}:measured_TP={self.input_tp:.2f}:"
            f"measured_LRA={self.input_lra:.2f}:measured_thresh={self.input_thresh:.2f}:"
            f"offset={self.target_offset:.2f}"
        )


def measure_cmd(
    path: str,
    i: float,
    tp: float,
    lra: float,
    *,
    stream: int = 0,
    start: float = 0.0,
    duration: float | None = None,
) -> list[str]:
    cmd = [ff.ffmpeg_path(), "-hide_banner", "-nostdin"]
    if start > 0:
        cmd += ["-ss", ff.ts(start)]
    if duration:
        cmd += ["-t", ff.ts(duration)]
    return [
        *cmd,
        "-i",
        path,
        "-map",
        f"0:a:{stream}",
        "-vn",
        "-sn",
        "-dn",
        "-af",
        f"loudnorm=I={i}:TP={tp}:LRA={lra}:print_format=json",
        "-f",
        "null",
        "-",
    ]


def parse_measurement(lines: Sequence[str]) -> Measurement:
    """Pull the JSON block that loudnorm prints at the end of the run."""
    text = "\n".join(lines)
    start = text.rfind("[Parsed_loudnorm")
    block = text[start:] if start >= 0 else text
    a, b = block.find("{"), block.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("loudnorm did not report a measurement")
    data = json.loads(block[a : b + 1])

    def num(key: str) -> float:
        v = str(data.get(key, "0")).strip()
        return -99.0 if v in ("-inf", "inf", "nan") else float(v)

    return Measurement(
        num("input_i"), num("input_tp"), num("input_lra"), num("input_thresh"), num("target_offset")
    )


def normalise_filter(
    i: float, tp: float, lra: float, m: Measurement | None, sample_rate: int = 48000
) -> str:
    """Second-pass filter (or single-pass when *m* is None) + resample back.

    loudnorm looks 3 s ahead, so a shorter clip comes out as one huge block
    at end-of-file and encoders with a block-size limit (FLAC, ALAC) refuse
    to open - asetnsamples re-chunks the output into normal 4096-sample blocks."""
    base = f"loudnorm=I={i}:TP={tp}:LRA={lra}"
    if m is not None and m.input_i > -70:
        base += f":{m.as_args()}:linear=true"
    return f"{base},aresample={sample_rate or 48000},asetnsamples=n=4096:p=0"


def measure(
    path: str,
    i: float = -16.0,
    tp: float = -1.5,
    lra: float = 11.0,
    *,
    stream: int = 0,
    cancel: Callable[[], bool] | None = None,
    ctx: TaskContext | None = None,
) -> Measurement:
    """Run the measurement pass (through *ctx* when inside a queue job)."""
    cmd = measure_cmd(path, i, tp, lra, stream=stream)
    if ctx is not None:
        res = ctx.run(cmd, duration=ff.duration_of(path) or None)
    else:
        res = ff.run(cmd, cancel=cancel)
        if res.returncode != 0:
            raise ff.CommandError(res, cmd)
    return parse_measurement(res.log_tail)
