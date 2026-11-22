"""
core/ops/silence.py - command builders for tabs/audio/silence.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence

from core import encoders as enc
from core import ffmpeg as ff
from core.ops.shared import audio_encoder_args

Span = tuple[float, float]  # start, end in seconds


def detect_cmd(src: str, threshold_db: float, min_silence: float, stream: int = 0) -> list[str]:
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


def parse_silences(text: str, duration: float) -> list[Span]:
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", text)]
    if len(ends) < len(starts):  # silent until the very end
        ends.append(duration)
    return [(max(0.0, a), b) for a, b in zip(starts, ends, strict=False) if b > a]


def keep_zones(
    silences: Iterable[Span],
    duration: float,
    padding: float,
    fps: float = 0.0,
    min_keep: float = 0.05,
) -> list[Span]:
    """Parts to keep: everything except silences, with *padding* left around speech."""
    zones: list[Span] = []
    pos = 0.0
    for a, b in silences:
        end = min(duration, a + padding)
        if end - pos > min_keep:
            zones.append((pos, end))
        pos = max(pos, b - padding)
    if duration - pos > min_keep:
        zones.append((pos, duration))
    if fps:

        def snap(x: float) -> float:
            return round(x * fps) / fps

        zones = [(snap(a), snap(b)) for a, b in zones if snap(b) > snap(a)]
    merged: list[Span] = []
    for a, b in zones:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def cut_graph(zones: Sequence[Span], has_video: bool, has_audio: bool) -> str:
    n = len(zones)
    parts: list[str] = []
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


def find_zones(
    src: str,
    info: ff.MediaInfo,
    threshold_db: float,
    min_silence: float,
    pad: float,
    runner: Callable[[list[str]], ff.RunResult],
) -> list[Span]:
    """The parts to keep (sound, padded by *pad*, on frame boundaries); *runner*
    runs the detection (so it can report progress or be cancelled)."""
    res = runner(detect_cmd(src, threshold_db, min_silence))
    fps = info.require_video().fps if info.has_video and info.video else 0.0
    return keep_zones(
        parse_silences("\n".join(res.log_tail), info.duration), info.duration, pad, fps
    )


def tighten_cmd(
    src: str, graph_file: str, out: str, venc: enc.VideoEncode | None, container: str
) -> list[str]:
    """*src* with only the kept parts (the cut graph read from *graph_file*):
    the sound alone when *venc* is None."""
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        *(venc.pre_input if venc else []),
        "-i",
        src,
        "-/filter_complex",
        graph_file,
    ]
    if venc is None:
        return [*cmd, "-map", "[a]", *audio_encoder_args(out), out]
    audio = enc.audio(enc.default_audio_for(container), container=container)
    return [*cmd, "-map", "[v]", "-map", "[a]", *venc.args, *audio, out]
