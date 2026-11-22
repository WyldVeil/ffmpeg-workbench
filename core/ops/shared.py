"""
core/ops/shared.py - argument builders many tools share (audio encoding, frame rates).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""
from __future__ import annotations

import os

from core import encoders as enc
from core import ffmpeg as ff


def keep_audio_args(info: ff.MediaInfo | None, container: str, *, index: int = 0,
                    filters: str = "") -> list:
    """Map all audio of input *index*: stream-copied when the container accepts
    the codec and no *filters* are applied, otherwise re-encoded."""
    audio = info.audio if info is not None else None
    if audio is None:
        return ["-an"]
    args = ["-map", f"{index}:a?"]
    if filters:
        return [*args, "-af", filters, *enc.audio(enc.default_audio_for(container), container=container)]
    key = enc.audio_key_from_probe(audio.codec)
    if key and enc.audio_ok_for(container, key):
        return [*args, "-c:a", "copy"]
    return args + enc.audio(enc.default_audio_for(container), container=container)


def frame_rate(info: ff.MediaInfo) -> str:
    """Exact source frame rate as a rational string (e.g. 30000/1001)."""
    v = info.video
    if v is None or not (0 < (v.fps or 0) <= 240):
        return "30"
    for key in ("avg_frame_rate", "r_frame_rate"):
        rate = v.raw.get(key) or ""
        if rate and not rate.startswith("0/") and rate != "0":
            return rate
    return f"{v.fps:g}"


AUDIO_ONLY_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".mka", ".aiff", ".aif")


def audio_encoder_args(out: str) -> list:
    """Encoder for processed audio, chosen from the output file's type."""
    ext = os.path.splitext(out)[1].lower()
    return {
        ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
        ".wav": ["-c:a", "pcm_s16le"],
        ".aiff": ["-c:a", "pcm_s16be"], ".aif": ["-c:a", "pcm_s16be"],
        ".flac": ["-c:a", "flac", "-compression_level", "8"],
        ".m4a": ["-c:a", "aac", "-b:a", "256k"], ".aac": ["-c:a", "aac", "-b:a", "256k"],
        ".ogg": ["-c:a", "libvorbis", "-q:a", "6"],
        ".opus": ["-c:a", "libopus", "-b:a", "160k"],
        ".mka": ["-c:a", "flac"],
        ".webm": ["-c:a", "libopus", "-b:a", "160k"],
    }.get(ext, ["-c:a", "aac", "-b:a", "256k"])


def audio_job_args(out: str, info: ff.MediaInfo | None, *, stream: int = 0) -> list:
    """Mapping + codecs when an audio tool writes *out*: audio-only files get
    just the processed track; video files keep the picture (copied) and
    subtitles where the container allows."""
    ext = os.path.splitext(out)[1].lower()
    if ext in AUDIO_ONLY_EXTS or info is None or not info.has_video:
        return ["-vn", "-sn", "-map", f"0:a:{stream}", *audio_encoder_args(out)]
    args = ["-map", "0:v?", "-c:v", "copy", "-map", f"0:a:{stream}", *audio_encoder_args(out)]
    if ext == ".mkv":
        args += ["-map", "0:s?", "-c:s", "copy"]
    if ext in (".mp4", ".mov", ".m4v"):
        args += ["-movflags", "+faststart"]
    return args
