"""
core/ops/mute.py - the Muter (tabs/audio/muter.py): moments silenced or bleeped.
"""

from __future__ import annotations

from collections.abc import Iterable

SILENCE = "volume=0"  # all of the sound silenced (the track kept)


def gain_expr(ranges: Iterable[tuple[float, float]], fade: float) -> str:
    """1 outside every range, 0 inside, linear ramps of *fade* seconds."""
    f = max(fade, 0.001)
    terms = [f"clip(max(({a:.4f}-t)/{f:.4f}\\,(t-{b:.4f})/{f:.4f})\\,0\\,1)" for a, b in ranges]
    return "*".join(terms) if terms else "1"


def mute_filter(ranges: Iterable[tuple[float, float]], fade: float, bleep: bool) -> str:
    """Silence (or bleep over, with a 1 kHz tone) each range, with *fade*-second ramps."""
    g = gain_expr(ranges, fade)
    if bleep:
        return f"aeval=val(ch)*({g})+0.25*sin(2*PI*1000*t)*(1-({g})):c=same"
    return f"aeval=val(ch)*({g}):c=same"
