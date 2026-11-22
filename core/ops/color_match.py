"""
core/ops/color_match.py - Colour Match (tabs/visuals/color_match.py): each RGB
channel's mean and spread measured on sampled frames of two videos, and a lutrgb
that moves one towards the other.
"""

from __future__ import annotations

from core import ffmpeg as ff

Stats = dict[str, tuple[float, float]]  # channel ("R", "G", "B") -> (mean, standard deviation)

_SQ = [i * i for i in range(256)]
_SAMPLE_W, _SAMPLE_H = 160, 90


def sample_times(info: ff.MediaInfo, count: int = 10) -> list[float]:
    if info.is_image or info.duration <= 0.2:
        return [0.0]
    d = info.duration
    count = max(1, min(count, int(d * 4) or 1))
    if count == 1:
        return [d / 2]
    return [d * (0.08 + 0.84 * i / (count - 1)) for i in range(count)]


def frame_cmd(path: str, at: float) -> list[str]:
    """One small RGB frame at *at* seconds, raw, to stdout."""
    cmd = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error"]
    cmd += ["-ss", ff.ts(at)] if at > 0 else []
    small = f"scale={_SAMPLE_W}:{_SAMPLE_H}:flags=area,format=rgb24"
    return [*cmd, "-i", path, "-frames:v", "1", "-an", "-sn", "-vf", small, "-f", "rawvideo", "-"]


def rgb_stats(path: str, count: int = 10) -> Stats:
    """{'R': (mean, std), 'G': ..., 'B': ...} over *count* sampled frames.

    Raises RuntimeError when no frame could be decoded."""
    info = ff.probe(path)
    raw = bytearray()
    for at in sample_times(info, count):
        cmd = frame_cmd(path, at)
        r = ff.capture(cmd, timeout=60, text=False)
        frame = r.stdout or b""
        if r.returncode == 0 and len(frame) == _SAMPLE_W * _SAMPLE_H * 3:
            raw += frame
    if not raw:
        raise RuntimeError(f"Could not decode any frames from {path}")
    data = bytes(raw)
    out: Stats = {}
    for i, ch in enumerate("RGB"):
        chan = data[i::3]
        n = len(chan)
        mean = sum(chan) / n
        var = max(sum(map(_SQ.__getitem__, chan)) / n - mean * mean, 0.0)
        out[ch] = (mean, var**0.5)
    return out


def build_filter(
    src: Stats, ref: Stats, *, strength: float = 1.0, channels: str = "RGB", contrast: bool = True
) -> str:
    """lutrgb chain moving *src* statistics towards *ref* ('' if nothing to do)."""
    parts: list[str] = []
    for ch in "RGB":
        if ch not in channels:
            continue
        sm, ss = src[ch]
        rm, rs = ref[ch]
        k = min(max(rs / max(ss, 1.0), 0.25), 4.0) if contrast else 1.0
        expr = f"clip(val+{strength:.3f}*((val-{sm:.2f})*{k:.4f}+{rm:.2f}-val),0,255)"
        parts.append(f"{ch.lower()}='{expr}'")
    return "lutrgb=" + ":".join(parts) if parts else ""
