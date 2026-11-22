"""
core/ops/waveform.py - the Waveform Editor (tabs/audio/waveform.py): peak levels to
draw, and regions muted, boosted or ducked with short ramps at their edges.
"""

from __future__ import annotations

import math
import os
from collections.abc import Sequence

from core import ffmpeg as ff
from core.ops.shared import audio_encoder_args

RAMP = 0.02  # seconds of fade at each region edge

Region = tuple[float, float, str, float]  # (start, end, "mute" | "boost" | "duck", dB)


def peaks_cmd(src: str, slice_samples: int, out_txt: str, stream: int = 0) -> list[str]:
    """Peak level (dBFS) of every *slice_samples* block, printed to *out_txt*."""
    af = (
        f"aformat=sample_fmts=flt:channel_layouts=mono,asetnsamples=n={slice_samples}:p=0,"
        "astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=Peak_level,"
        f"ametadata=mode=print:key=lavfi.astats.Overall.Peak_level:file={os.path.basename(out_txt)}"
    )
    return [
        ff.ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        src,
        "-map",
        f"0:a:{stream}",
        "-af",
        af,
        "-f",
        "null",
        "-",
    ]


def read_peaks(path: str) -> list[float]:
    """dBFS lines -> linear peaks 0..1."""
    vals: list[float] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("lavfi.astats.Overall.Peak_level="):
                try:
                    db = float(line.split("=", 1)[1])
                except ValueError:
                    db = -120.0
                vals.append(0.0 if math.isinf(db) or db < -120 else min(1.0, 10 ** (db / 20)))
    return vals


def region_filter(regions: Sequence[Region]) -> str:
    """Gain envelope for [(start, end, action, dB)] with RAMP-second edges."""
    parts = ["asetnsamples=n=256:p=0"] if regions else []
    for s, e, action, db in regions:
        gain = 0.0 if action == "mute" else 10 ** ((db if action == "boost" else -db) / 20)
        parts.append(
            f"volume='1+({gain:.6f}-1)*clip(min(t-{s:.3f},{e:.3f}-t)/{RAMP},0,1)':eval=frame"
        )
    return ",".join(parts)


def apply_cmd(src: str, out: str, regions: Sequence[Region], *, audio_only: bool) -> list[str]:
    """*src* with the regions applied: the sound alone, or the video (picture copied)."""
    af = region_filter(regions)
    cmd = [ff.ffmpeg_path(), "-y", "-i", src]
    if audio_only:
        return [*cmd, "-map", "0:a:0", "-af", af, "-vn", *audio_encoder_args(out), out]
    cmd += [
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-af",
        af,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "256k",
    ]
    if out.lower().endswith((".mp4", ".mov", ".m4v")):
        cmd += ["-movflags", "+faststart"]
    return [*cmd, out]
