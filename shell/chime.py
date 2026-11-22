"""
shell/chime.py - the soft start-up chime, made with the standard library.

A C-major arpeggio (C5 E5 G5 C6) and a sustained dyad, rendered as a 44.1 kHz
16-bit mono WAV in memory.  Played on a worker thread; silence on failure.
"""

from __future__ import annotations

import io
import math
import os
import struct
import sys
import tempfile
import wave

from core import proc

RATE = 44100


def _note(freq: float, seconds: float, volume: float = 0.28) -> list[float]:
    out: list[float] = []
    for i in range(int(RATE * seconds)):
        t = i / RATE
        envelope = (1.0 - math.exp(-t * 55.0)) * math.exp(-t * 5.5)  # fast attack, smooth decay
        tone = math.sin(2 * math.pi * freq * t) + 0.18 * math.sin(
            4 * math.pi * freq * t
        )  # warm 2nd harmonic
        out.append(envelope * volume * tone / 1.18)
    return out


def wav_bytes() -> bytes:
    samples: list[float] = []
    for freq, seconds in ((523.25, 0.13), (659.25, 0.13), (783.99, 0.14), (1046.50, 0.17)):
        samples.extend(_note(freq, seconds))
    for i in range(int(RATE * 0.45)):  # C5 + G5 tail
        t = i / RATE
        envelope = (1.0 - math.exp(-t * 18.0)) * math.exp(-t * 3.2)
        samples.append(
            envelope
            * 0.22
            * (math.sin(2 * math.pi * 523.25 * t) + math.sin(2 * math.pi * 783.99 * t))
            * 0.5
        )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(
            b"".join(struct.pack("<h", max(-32767, min(32767, int(s * 32767)))) for s in samples)
        )
    return buf.getvalue()


def play() -> None:
    """Worker: play the chime (Windows and macOS; Linux has no standard player)."""
    data = wav_bytes()
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(data, winsound.SND_MEMORY)
    elif sys.platform == "darwin":
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(data)
        try:
            proc.run(["afplay", f.name], timeout=5, text=False)
        finally:
            os.remove(f.name)
