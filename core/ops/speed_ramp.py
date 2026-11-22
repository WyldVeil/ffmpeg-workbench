"""
core/ops/speed_ramp.py - command builders for tabs/visuals/speed_ramp.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""
from __future__ import annotations


def atempo_chain(speed: float, engine: str = "atempo") -> str:
    """Pitch-preserving constant tempo for any speed."""
    if engine == "rubberband" and 0.01 <= speed <= 100:
        return f"rubberband=tempo={speed:.6f}"
    parts, rem = [], speed
    while rem < 0.5:
        parts.append("atempo=0.5")
        rem /= 0.5
    while rem > 100.0:
        parts.append("atempo=100")
        rem /= 100.0
    parts.append(f"atempo={rem:.6f}")
    return ",".join(parts)
