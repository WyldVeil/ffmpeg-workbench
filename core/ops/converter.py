"""
core/ops/converter.py - command builders for tabs/transcoder/converter.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

from core import encoders as enc
from core import ffmpeg as ff

FORMATS = {  # key: (label, kind)
    "mp4": ("MP4 - plays everywhere", "video"),
    "mkv": ("MKV - holds any codec", "video"),
    "mov": ("MOV - Apple / editing", "video"),
    "webm": ("WebM - web (VP9/AV1 + Opus)", "video"),
    "m4v": ("M4V - Apple devices", "video"),
    "ts": ("TS - broadcast stream", "video"),
    "avi": ("AVI - legacy", "video"),
    "mp3": ("MP3 audio", "audio"),
    "m4a": ("M4A (AAC) audio", "audio"),
    "wav": ("WAV audio", "audio"),
    "flac": ("FLAC audio (lossless)", "audio"),
    "ogg": ("OGG Vorbis audio", "audio"),
    "opus": ("Opus audio", "audio"),
}


VIDEO_KEYS = {
    "h264": "h264",
    "hevc": "hevc",
    "av1": "av1",
    "vp9": "vp9",
    "vp8": "vp8",
    "mpeg4": "mpeg4",
    "prores": "prores",
    "dnxhd": "dnxhr",
    "mjpeg": "mjpeg",
    "ffv1": "ffv1",
}


TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}


SUB_CODEC = {
    "mp4": "mov_text",
    "m4v": "mov_text",
    "mov": "mov_text",
    "webm": "webvtt",
    "mkv": "copy",
}


#: legal in MP4/MOV but unplayable on Apple devices and many TVs/players: convert instead of copying
POOR_IN_MP4 = {"vp9", "vp8", "mpeg4", "opus", "flac", "vorbis"}


AUDIO_FORMAT_ENCODE = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "m4a": ["-c:a", "aac", "-b:a", "256k"],
    "flac": ["-c:a", "flac", "-compression_level", "8"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "6"],
    "opus": ["-c:a", "libopus", "-b:a", "160k", "-ar", "48000"],
}


def _copyable(smart: bool, fmt: str, key: str | None, fits: bool) -> bool:
    """Stream-copy when smart, the container takes the codec, and it isn't one MP4 plays badly."""
    return (
        smart and bool(key) and fits and not (fmt in ("mp4", "m4v", "mov") and key in POOR_IN_MP4)
    )


def _audio_only(
    audios: list[ff.Stream], fmt: str, smart: bool
) -> tuple[list[str], list[str], list[str], str]:
    if not audios:
        raise ValueError("no audio")
    a = audios[0]
    key = enc.audio_key_from_probe(a.codec)
    args = ["-map", f"0:{a.index}", "-vn"]
    if fmt == "wav":
        lossless = ("flac", "alac", "pcm_s24le", "pcm_s32le", "pcm_f32le")
        deep = (a.bits_per_raw_sample or 16) > 16 or a.codec in lossless
        args += ["-c:a", "pcm_s24le" if deep else "pcm_s16le"]
        action = "audio → WAV"
    elif smart and key and enc.audio_ok_for(fmt, key):
        args += ["-c:a", "copy"]
        action = f"audio {a.codec} copied"
    else:
        args += AUDIO_FORMAT_ENCODE[fmt]
        action = f"audio {a.codec} → {AUDIO_FORMAT_ENCODE[fmt][1]}"
    extra = len(audios) - 1
    notes = [f"{extra} extra audio track(s) left out (audio formats hold one)."] if extra else []
    return [], [*args, "-map_metadata", "0"], notes, action


def _video(v: ff.Stream, fmt: str, smart: bool) -> tuple[list[str], list[str], str]:
    """(pre-input args, output args, action) for the first video stream."""
    key = VIDEO_KEYS.get(v.codec, v.codec)
    args = ["-map", f"0:{v.index}"]
    if _copyable(smart, fmt, key, enc.video_ok_for(fmt, key)):
        args += [
            "-c:v",
            "copy",
            *(["-tag:v", "hvc1"] if key == "hevc" and fmt in ("mp4", "mov", "m4v") else []),
        ]
        return [], args, f"video {v.codec} copied"
    codec = enc.CONTAINER_VIDEO.get(fmt, ("h264",))[0]
    ve = enc.video(
        codec, "high", hw="auto", ten_bit=v.bit_depth > 8 and codec != "h264", container=fmt
    )
    return ve.pre_input, [*args, *ve.vf_args(), *ve.args], f"video {v.codec} → {ve.encoder}"


def _audio(n: int, a: ff.Stream, fmt: str, smart: bool) -> tuple[list[str], str]:
    """(output args, action) for audio track number *n*."""
    key = enc.audio_key_from_probe(a.codec)
    args = ["-map", f"0:{a.index}"]
    if _copyable(smart, fmt, key, bool(key) and enc.audio_ok_for(fmt, key or "")):
        return [*args, f"-c:a:{n}", "copy"], f"audio {a.codec} copied"
    spec = enc.audio(enc.default_audio_for(fmt), container=fmt)
    args += [f"{x}:{n}" if x in ("-c:a", "-b:a", "-q:a") else x for x in spec]
    return args, f"audio {a.codec} → {spec[1]}"


def _subtitles(subs: list[ff.Stream], fmt: str) -> tuple[list[str], list[str], int]:
    """(output args, notes, how many were kept): copied, converted or left out."""
    args: list[str] = []
    notes: list[str] = []
    kept = 0
    sub_codec = SUB_CODEC.get(fmt)
    for s in subs:
        if sub_codec == "copy" or (fmt == "ts" and s.codec == "dvb_subtitle"):
            codec = "copy"
        elif sub_codec and s.codec in TEXT_SUBS:
            codec = sub_codec
        else:
            notes.append(
                f"Subtitle track {s.index} ({s.codec}) can't go into {fmt.upper()} - left out."
            )
            continue
        args += ["-map", f"0:{s.index}", f"-c:s:{kept}", codec]
        kept += 1
    return args, notes, kept


def stream_args(
    info: ff.MediaInfo, fmt: str, smart: bool = True
) -> tuple[list[str], list[str], list[str], str]:
    """(pre-input args, output args, notes, summary) for converting *info* to *fmt*."""
    audios = [s for s in info.streams if s.type == "audio"]
    if FORMATS[fmt][1] == "audio":
        return _audio_only(audios, fmt, smart)
    pre: list[str] = []
    args: list[str] = []
    notes: list[str] = []
    actions: list[str] = []
    videos = [s for s in info.streams if s.type == "video" and not s.attached_pic]
    if videos:
        pre, args, action = _video(videos[0], fmt, smart)
        actions.append(action)
        if len(videos) > 1:
            notes.append(f"{len(videos) - 1} extra video stream(s) left out.")
    for n, a in enumerate(audios):
        audio_args, action = _audio(n, a, fmt, smart)
        args += audio_args
        actions.append(action)
    sub_args, sub_notes, kept_subs = _subtitles(
        [s for s in info.streams if s.type == "subtitle"], fmt
    )
    args += sub_args
    notes += sub_notes
    if kept_subs:
        actions.append(f"{kept_subs} subtitle track(s)")
    if fmt == "mkv":
        args += ["-map", "0:t?", "-c:t", "copy"]  # attached fonts
    args += ["-map_metadata", "0", "-map_chapters", "0"]
    if fmt in ("mp4", "mov", "m4v"):
        args += ["-movflags", "+faststart"]
    return pre, args, notes, ", ".join(actions)
