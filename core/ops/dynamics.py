"""
core/ops/dynamics.py - Audio Dynamics (tabs/audio/dynamics.py): gate, EQ,
compressor, de-esser and limiter, in that order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


def _linear(db: float) -> float:
    return 10 ** (db / 20)


@dataclass(frozen=True, slots=True)
class Gate:
    threshold: float  # dB
    ratio: float
    attack: float  # ms
    release: float  # ms


@dataclass(frozen=True, slots=True)
class Compressor:
    threshold: float  # dB
    ratio: float
    attack: float  # ms
    release: float  # ms
    makeup: float  # dB


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynamics:
    """Each stage is off when None (or, for the EQ, empty)."""

    gate: Gate | None = None
    eq: Sequence[tuple[str, int, float]] = ()  # (lowshelf | peaking | highshelf, Hz, dB)
    compressor: Compressor | None = None
    deesser: tuple[float, float] | None = None  # (Hz, intensity 0..1)
    limit: float | None = None  # ceiling, dB
    sample_rate: int = 48000


def _eq(bands: Sequence[tuple[str, int, float]]) -> list[str]:
    """Real shelving filters at the ends, peaking bands in between; flat bands skipped."""
    out: list[str] = []
    for ftype, freq, gain in bands:
        if abs(gain) <= 0.1:
            continue
        if ftype in ("lowshelf", "highshelf"):
            out.append(f"{ftype}=f={freq}:g={gain:.1f}")
        else:
            out.append(f"equalizer=f={freq}:t=q:w=1.0:g={gain:.1f}")
    return out


def dynamics_filter(d: Dynamics) -> str:
    parts: list[str] = []
    if d.gate is not None:
        g = d.gate
        parts.append(
            f"agate=threshold={_linear(g.threshold):.6f}:ratio={g.ratio:.1f}:attack={g.attack:.1f}:release={g.release:.1f}"
        )
    parts += _eq(d.eq)
    if d.compressor is not None:
        c = d.compressor
        parts.append(
            f"acompressor=threshold={_linear(c.threshold):.6f}:ratio={c.ratio:.1f}"
            f":attack={c.attack:.1f}:release={c.release:.1f}:makeup={_linear(c.makeup):.4f}"
        )
    if d.deesser is not None:
        # deesser: i = intensity 0-1, m = max reduction 0-1, f = frequency as a fraction of Nyquist
        freq, intensity = d.deesser
        f = min(max(freq / (d.sample_rate / 2), 0.01), 0.99)
        parts.append(f"deesser=i={min(max(intensity, 0.0), 1.0):.2f}:m=0.5:f={f:.3f}:s=o")
    if d.limit is not None:
        parts.append(
            f"alimiter=level_in=1:level_out={_linear(d.limit):.6f}:limit=1:attack=5:release=50"
        )
    return ",".join(parts) if parts else "anull"
