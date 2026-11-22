"""
core/ops/silence.py - command builders for tabs/audio/silence.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

import re

from core import ffmpeg as ff


def detect_cmd(src: str, threshold_db: float, min_silence: float, stream: int = 0) -> list:
    return [
        ff.ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-i",
        src,
        "-map",
        f"0:a:{stream}",
        "-vn",
        "-sn",
        "-af",
        f"silencedetect=noise={threshold_db:g}dB:d={min_silence:g}",
        "-f",
        "null",
        "-",
    ]


def parse_silences(text: str, duration: float) -> list:
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", text)]
    if len(ends) < len(starts):  # silent until the very end
        ends.append(duration)
    return [(max(0.0, a), b) for a, b in zip(starts, ends, strict=False) if b > a]


def keep_zones(
    silences, duration: float, padding: float, fps: float = 0.0, min_keep: float = 0.05
) -> list:
    """Parts to keep: everything except silences, with *padding* left around speech."""
    zones, pos = [], 0.0
    for a, b in silences:
        end = min(duration, a + padding)
        if end - pos > min_keep:
            zones.append((pos, end))
        pos = max(pos, b - padding)
    if duration - pos > min_keep:
        zones.append((pos, duration))
    if fps:

        def snap(x):
            return round(x * fps) / fps

        zones = [(snap(a), snap(b)) for a, b in zones if snap(b) > snap(a)]
    merged = []
    for a, b in zones:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def cut_graph(zones, has_video: bool, has_audio: bool) -> str:
    n = len(zones)
    parts = []
    if has_video:
        parts.append(f"[0:v]split={n}" + "".join(f"[vs{i}]" for i in range(n)))
    if has_audio:
        parts.append(f"[0:a:0]asplit={n}" + "".join(f"[as{i}]" for i in range(n)))
    pads = ""
    for i, (a, b) in enumerate(zones):
        if has_video:
            parts.append(f"[vs{i}]trim=start={a:.4f}:end={b:.4f},setpts=PTS-STARTPTS[v{i}]")
            pads += f"[v{i}]"
        if has_audio:
            parts.append(f"[as{i}]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS[a{i}]")
            pads += f"[a{i}]"
    outs = ("[v]" if has_video else "") + ("[a]" if has_audio else "")
    parts.append(f"{pads}concat=n={n}:v={1 if has_video else 0}:a={1 if has_audio else 0}{outs}")
    return ";\n".join(parts)
