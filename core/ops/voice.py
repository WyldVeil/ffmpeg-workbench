"""
core/ops/voice.py - the Voice Changer (tabs/audio/voice_changer.py): pitch shifts
(rubberband, keeping formants when asked, or resampling) and character effects.
"""

from __future__ import annotations

from core import ffmpeg as ff
from core import filters as fl

PRESETS: dict[
    str, tuple[str, float | None, str]
] = {  # id: (label, semitones (None: the user's), extra filters)
    "chipmunk": ("Chipmunk (high pitch)", 6, ""),
    "deep": ("Deep / anonymous (low pitch)", -5, ""),
    "demon": ("Demon", -8, "aecho=0.8:0.85:60:0.35"),
    "robot": (
        "Robot",
        0,
        "afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':"
        "win_size=512:overlap=0.75,flanger=delay=3:depth=2:regen=40",
    ),
    "radio": (
        "Old radio",
        0,
        "highpass=f=300,lowpass=f=3400,acompressor=threshold=0.1:ratio=6,volume=1.5",
    ),
    "phone": (
        "Telephone",
        0,
        "highpass=f=400,lowpass=f=3000,aformat=sample_rates=8000,aresample=48000",
    ),
    "stadium": ("Stadium echo", 0, "aecho=0.8:0.9:1000|1800:0.3|0.2"),
    "cave": ("Cave reverb", 0, "aecho=0.8:0.88:60|120|200:0.4|0.3|0.2"),
    "whisper": (
        "Whisper",
        0,
        "afftfilt=real='hypot(re,im)*cos((random(0)*2-1)*2*3.14)':"
        "imag='hypot(re,im)*sin((random(1)*2-1)*2*3.14)':win_size=128:overlap=0.8,"
        "highpass=f=500",
    ),
    "custom": ("Custom pitch only", None, ""),
}


def voice_filter(
    preset: str, semitones: float, sample_rate: int, keep_formants: bool, rubberband: bool
) -> str:
    """The -af chain for a preset (semitones used for 'custom' and pitched presets)."""
    _label, default_st, extra = PRESETS[preset]
    st = semitones if default_st is None else default_st
    parts: list[str] = []
    if abs(st) > 0.01:
        k = 2 ** (st / 12)
        if rubberband:
            parts.append(
                f"rubberband=pitch={k:.5f}" + (":formant=preserved" if keep_formants else "")
            )
        else:
            sr = sample_rate or 48000
            parts += [f"asetrate={round(sr * k)}", f"aresample={sr}", fl.atempo_chain(1 / k)]
    if extra:
        parts.append(extra)
    return ",".join(parts) or "anull"


def rubberband_available() -> bool:
    return ff.has_filter("rubberband")
