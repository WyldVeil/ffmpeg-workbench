"""
core/ops/extractor.py - command builders for tabs/audio/extractor.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""
from __future__ import annotations

COPY_EXT = {"aac": ".m4a", "mp3": ".mp3", "opus": ".opus", "vorbis": ".ogg", "flac": ".flac", "alac": ".m4a",
            "ac3": ".ac3", "eac3": ".eac3", "dts": ".mka", "truehd": ".mka", "mp2": ".mp2"}


def copy_extension(codec: str) -> str:
    if codec.startswith("pcm_"):
        return ".wav"
    return COPY_EXT.get(codec, ".mka")
