"""
core/ops/scenes.py - finding scenes (shot changes) and cutting at them: the
Scene Detector (tabs/cutting/scene_detect.py) and the Frame Extractor's
scene frames.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence

from core import encoders as enc
from core import ffmpeg as ff
from core.queue_manager import TaskContext

CUTS_FILE = "cuts.txt"  # where the scan writes the shot changes (in its working folder)


def detect_cmd(src: str, threshold: float) -> list[str]:
    """Scan a small copy of the picture for shot changes above *threshold* (0..1);
    run it in a folder: the times go to CUTS_FILE there."""
    find = f"scale=320:-2,select='gt(scene,{threshold:.3f})',metadata=print:file={CUTS_FILE}"
    head = [ff.ffmpeg_path(), "-i", src, "-an", "-sn", "-dn", "-vf", find]
    return [*head, "-fps_mode", "passthrough", "-f", "null", "-"]


def read_cuts(folder: str) -> list[float]:
    """The shot changes detect_cmd found (none if it found none)."""
    try:
        with open(os.path.join(folder, CUTS_FILE), encoding="utf-8", errors="replace") as fh:
            return [float(m) for m in re.findall(r"pts_time:\s*([\d.]+)", fh.read())]
    except OSError:
        return []


def keep_scenes(cuts: Iterable[float], min_len: float) -> list[float]:
    """Scene start times: 0 plus each cut at least *min_len* after the last kept."""
    kept = [0.0]
    for c in sorted(cuts):
        if c - kept[-1] >= min_len:
            kept.append(c)
    return kept


def scene_ranges(starts: Sequence[float], duration: float) -> list[tuple[float, float]]:
    """[(start, end)] for each scene."""
    ends = [*list(starts[1:]), duration]
    return [(a, b) for a, b in zip(starts, ends, strict=False) if b - a > 0.01]


def detect(
    ctx: TaskContext, src: str, threshold: float, min_len: float, duration: float
) -> list[float]:
    """Scene start times (the first scene included), shorter scenes than *min_len* merged."""
    tmp = ctx.tempdir()
    ctx.run(detect_cmd(src, threshold), duration=duration, cwd=tmp)
    return keep_scenes(set(read_cuts(tmp)), min_len)


def split_copy_cmd(
    src: str, scenes: Sequence[tuple[float, float]], pattern: str, duration: float
) -> list[str]:
    """Lossless: one file per scene (cut at the keyframes nearest the changes), named by *pattern*."""
    times = ",".join(f"{a:.3f}" for a, _b in scenes[1:]) or f"{duration + 1:.3f}"
    cmd = [ff.ffmpeg_path(), "-y", "-i", src, "-map", "0:v:0", "-map", "0:a?", "-c", "copy"]
    cmd += ["-f", "segment", "-segment_times", times, "-reset_timestamps", "1"]
    if os.path.splitext(pattern)[1].lower() in (".mp4", ".mov", ".m4v"):
        cmd += ["-segment_format_options", "movflags=+faststart"]
    return [*cmd, pattern]


def split_encode_cmds(
    src: str,
    scenes: Sequence[tuple[float, float]],
    name: str,
    venc: enc.VideoEncode,
    *,
    has_audio: bool,
) -> list[list[str]]:
    """Re-encoded: one exact cut per scene, *name* % its number (1-based)."""
    audio = enc.audio("aac", container="mp4") if has_audio else []
    sound = ["-map", "0:a:0"] if has_audio else ["-an"]
    cmds: list[list[str]] = []
    for i, (a, b) in enumerate(scenes, 1):
        cut = [
            ff.ffmpeg_path(),
            "-y",
            *venc.pre_input,
            "-ss",
            ff.ts(a),
            "-t",
            ff.ts(b - a),
            "-i",
            src,
        ]
        cmds.append(
            [*cut, "-map", "0:v:0", *sound, *venc.vf_args(None), *venc.args, *audio, name % i]
        )
    return cmds
