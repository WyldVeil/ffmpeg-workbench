"""
core/beats.py - where the beats are in a piece of music (the Beat Sync Cutter,
the Slideshow Maker).

FFmpeg turns the music into an energy envelope (ENV_RATE samples a second);
onsets are the rises in log-energy.  The tempo comes from the envelope's
autocorrelation (70-180 BPM, biased towards ~120 to avoid double/half tempo),
the phase from the best-aligned grid, and each beat is then nudged to the
nearest strong onset so live, drifting tempos are followed.  ``onset_peaks``
gives every hit instead (sensitivity = threshold).
"""

from __future__ import annotations

import array
import math
from collections.abc import Sequence

from core import ffmpeg as ff

ENV_RATE = 200  # energy envelope samples per second


def envelope(path: str, stream: int = 0, timeout: float = 600) -> list[float]:
    """Energy envelope of the audio at ENV_RATE Hz."""
    cmd = [
        ff.ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        path,
        "-map",
        f"0:a:{stream}",
        "-vn",
        "-sn",
        "-af",
        f"aformat=channel_layouts=mono,aresample=22050,highpass=f=30,"
        f"aeval=val(0)*val(0),aresample={ENV_RATE}",
        "-f",
        "f32le",
        "-",
    ]
    r = ff.capture(cmd, timeout=timeout, text=False)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("Could not read the music's audio.")
    data = array.array("f")
    data.frombytes(r.stdout[: len(r.stdout) // 4 * 4])
    return [max(0.0, v) for v in data]


def onset_strength(env: Sequence[float]) -> list[float]:
    logs = [math.log10(v + 1e-9) for v in env]
    on = [0.0] * len(logs)
    for i in range(2, len(logs)):
        on[i] = max(0.0, logs[i] - max(logs[i - 1], logs[i - 2]) * 0.5 - logs[i - 2] * 0.5)
    # light smoothing (3 samples = 15 ms)
    return [(on[max(i - 1, 0)] + on[i] + on[min(i + 1, len(on) - 1)]) / 3 for i in range(len(on))]


def estimate_tempo(onset: Sequence[float], lo_bpm: float = 70, hi_bpm: float = 180) -> float:
    """Beat period in envelope samples."""
    seg = onset[: ENV_RATE * 90]
    if len(seg) < ENV_RATE * 4:
        return ENV_RATE * 0.5
    mean = sum(seg) / len(seg)
    x = [v - mean for v in seg]
    best, best_lag = -1e18, ENV_RATE // 2  # 100: same value, an int for ac()
    for lag in range(int(ENV_RATE * 60 / hi_bpm), int(ENV_RATE * 60 / lo_bpm) + 1):
        r = sum(a * b for a, b in zip(x, x[lag:], strict=False)) / (len(x) - lag)
        bpm = 60 * ENV_RATE / lag
        weight = math.exp(-0.5 * (math.log2(bpm / 120) / 0.9) ** 2)
        if r * weight > best:
            best, best_lag = r * weight, lag

    # sub-sample refinement with the neighbours (parabolic)
    def ac(lag: int) -> float:
        return sum(a * b for a, b in zip(x, x[lag:], strict=False)) / (len(x) - lag)

    y0, y1, y2 = ac(best_lag - 1), ac(best_lag), ac(best_lag + 1)
    denom = y0 - 2 * y1 + y2
    shift = 0.5 * (y0 - y2) / denom if denom else 0.0
    return best_lag + max(-0.5, min(0.5, shift))


def beat_grid(onset: Sequence[float], period: float) -> list[float]:
    """Beat times (s): best phase, then each beat snapped to a nearby onset."""
    n = len(onset)
    if n == 0:
        return []
    best, phase = -1.0, 0
    for ph in range(int(period)):
        s = sum(onset[round(ph + k * period)] for k in range(int((n - ph) / period)))
        if s > best:
            best, phase = s, ph
    peak = sorted(onset)[int(len(onset) * 0.9)] if onset else 0
    beats: list[float] = []
    pos = float(phase)
    win = max(1, int(period * 0.12))
    while pos < n:
        i = round(pos)
        lo, hi = max(0, i - win), min(n, i + win + 1)
        j = max(range(lo, hi), key=lambda k: onset[k]) if hi > lo else i
        if onset[j] >= peak:
            pos = float(j)
        beats.append(pos / ENV_RATE)
        pos += period
    return beats


def onset_peaks(
    onset: Sequence[float], sensitivity: float = 0.5, min_gap: float = 0.15
) -> list[float]:
    """Every hit: local maxima above an adaptive threshold."""
    if not onset:
        return []
    ordered = sorted(onset)
    median = ordered[len(ordered) // 2]
    high = ordered[int(len(ordered) * 0.98)]
    thr = median + (high - median) * (1.0 - sensitivity) * 0.8
    gap = int(min_gap * ENV_RATE)
    out: list[float] = []
    last = -gap
    for i in range(1, len(onset) - 1):
        if (
            onset[i] >= thr
            and onset[i] >= onset[i - 1]
            and onset[i] > onset[i + 1]
            and i - last >= gap
        ):
            out.append(i / ENV_RATE)
            last = i
    return out


def find_beats(path: str, *, timeout: float = 600) -> tuple[list[float], float]:
    """(beat times in seconds, tempo in BPM) of the music in *path*."""
    onset = onset_strength(envelope(path, timeout=timeout))
    period = estimate_tempo(onset)
    return beat_grid(onset, period), 60 * ENV_RATE / period
