"""
core/ops/extractor.py - command builders for tabs/audio/extractor.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core import ffmpeg as ff
from core import loudness as ln
from core.ops.shared import audio_encoder_args
from core.queue_manager import TaskContext

FORMATS: dict[
    str, tuple[str, str | None, list[str]]
] = {  # id: (label, extension or None = from codec, encoder args)
    "copy": ("Original (no re-encoding)", None, ["-c:a", "copy"]),
    "mp3": ("MP3 (VBR, very high quality)", ".mp3", ["-c:a", "libmp3lame", "-q:a", "0"]),
    "aac": ("AAC in .m4a (256 kbps)", ".m4a", ["-c:a", "aac", "-b:a", "256k"]),
    "flac": ("FLAC (lossless)", ".flac", ["-c:a", "flac", "-compression_level", "8"]),
    "wav": ("WAV (uncompressed)", ".wav", ["-c:a", "pcm_s24le"]),
    "opus": ("Opus (160 kbps)", ".opus", ["-c:a", "libopus", "-b:a", "160k"]),
    "ogg": ("Ogg Vorbis (quality 6)", ".ogg", ["-c:a", "libvorbis", "-q:a", "6"]),
}
COPY_EXT = {
    "aac": ".m4a",
    "mp3": ".mp3",
    "opus": ".opus",
    "vorbis": ".ogg",
    "flac": ".flac",
    "alac": ".m4a",
    "ac3": ".ac3",
    "eac3": ".eac3",
    "dts": ".mka",
    "truehd": ".mka",
    "mp2": ".mp2",
}


def copy_extension(codec: str) -> str:
    if codec.startswith("pcm_"):
        return ".wav"
    return COPY_EXT.get(codec, ".mka")


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractJob:
    """Sound track *stream* of *src* (start..end) encoded with *encode* (None:
    chosen from the output's type), loudness-normalised (two passes) when asked."""

    src: str
    stream: int
    start: float
    end: float | None
    encode: Sequence[str] | None
    normalise: bool
    sample_rate: int
    seconds: float  # of the result

    def command(self, out: str, af: str | None = None) -> list[str]:
        cmd = [ff.ffmpeg_path(), "-y"]
        cmd += ["-ss", ff.ts(self.start)] if self.start else []
        cmd += ["-to", ff.ts(self.end)] if self.end is not None else []
        cmd += ["-i", self.src, "-map", f"0:a:{self.stream}", "-vn", "-sn", "-dn"]
        cmd += ["-af", af] if af else []
        return [*cmd, *(self.encode if self.encode is not None else audio_encoder_args(out)), out]

    def run(self, _p: object, _c: object, ctx: TaskContext) -> int:
        m = ln.measure(self.src, -16, -1.5, 11, stream=self.stream, ctx=ctx.span(0, 0.4))
        af = ln.normalise_filter(-16, -1.5, 11, m, self.sample_rate)
        out = ctx.output()
        ctx.span(0.4, 1.0).run(self.command(out, af), duration=self.seconds)
        return 0
