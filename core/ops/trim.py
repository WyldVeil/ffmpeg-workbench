"""
core/ops/trim.py - the Quick Trimmer (tabs/cutting/trimmer.py): keyframes, the
preview player, and the lossless (stream copy) and frame-exact cuts.
"""

from __future__ import annotations

import logging

from core import encoders as enc
from core import ffmpeg as ff

logger = logging.getLogger("crossfader.ops.trim")


def keyframe_at_or_before(path: str, at: float, lookback: float = 30.0) -> float | None:
    """PTS of the last video keyframe at or before *at* seconds (None if the
    probe fails).  Stream copy can only start on a keyframe."""
    window = max(0.0, at - lookback)
    try:
        r = ff.capture(
            [
                ff.ffprobe_path(),
                "-v",
                "error",
                "-read_intervals",
                f"{window:.3f}%{at + 0.05:.3f}",
                "-select_streams",
                "v:0",
                "-skip_frame",
                "nokey",
                "-show_entries",
                "frame=pts_time,best_effort_timestamp_time",
                "-of",
                "csv=p=0",
                path,
            ],
            timeout=30,
        )
    except Exception:
        logger.debug("keyframe_at_or_before: best-effort step failed", exc_info=True)
        return None
    best: float | None = None
    for line in r.stdout.splitlines():
        for part in line.split(","):
            try:
                pts = float(part)
            except ValueError:
                continue
            if pts <= at + 1e-3 and (best is None or pts > best):
                best = pts
            break
    if best is None and window > 0:
        return keyframe_at_or_before(path, at, lookback=lookback * 4) if lookback < 600 else 0.0
    return best if best is not None else 0.0


def preview_cmd(src: str, start: float, end: float, title: str) -> list[str]:
    """Play *start*..*end* of *src* on a loop in a small ffplay window."""
    span = ["-ss", str(start), "-i", src, "-t", str(max(0.1, end - start)), "-loop", "0"]
    window = ["-window_title", title, "-x", "800", "-y", "450", "-loglevel", "quiet"]
    return [ff.ffplay_path(), *span, *window]


def copy_cmd(src: str, keyframe: float, end: float, out: str, container: str) -> list[str]:
    """Lossless: seek the input to *keyframe* (so picture and sound both start at
    0; an output-side seek with stream copy left the first second without video)."""
    cut = [ff.ffmpeg_path(), "-y", "-ss", ff.ts(keyframe), "-i", src, "-t", ff.ts(end - keyframe)]
    keep = ["-map", "0:v:0?", "-map", "0:a?", "-c", "copy", "-sn", "-dn"]
    cmd = [*cut, *keep, "-map_metadata", "0", "-map_chapters", "-1"]
    if container in ("mp4", "m4v", "mov"):
        cmd += ["-movflags", "+faststart"]
    return [*cmd, out]


def encode_cmd(
    src: str, start: float, end: float, out: str, container: str, crf: str, *, has_audio: bool
) -> list[str]:
    """Frame-exact: H.264 at *crf* (the encoder's default if *crf* isn't a number)."""
    venc = enc.video("h264", "high", speed="balanced", hw="off", container=container or "mp4")
    if crf.replace(".", "", 1).isdigit():
        venc.set_option("-crf", crf)
    audio = enc.audio("aac", "192k", container=container or "mp4") if has_audio else []
    cut = [ff.ffmpeg_path(), "-y", "-ss", ff.ts(start), "-i", src, "-t", ff.ts(end - start)]
    streams = ["-map", "0:v:0", "-map", "0:a?", "-sn", "-dn"]
    return [*cut, *streams, *venc.args, *audio, "-map_metadata", "0", "-map_chapters", "-1", out]
