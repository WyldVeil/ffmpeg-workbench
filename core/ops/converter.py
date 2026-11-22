"""
core/ops/converter.py - command builders for tabs/transcoder/converter.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""
from __future__ import annotations

from core import encoders as enc
from core import ffmpeg as ff

FORMATS = {   # key: (label, kind)
    "mp4": ("MP4 - plays everywhere", "video"), "mkv": ("MKV - holds any codec", "video"),
    "mov": ("MOV - Apple / editing", "video"), "webm": ("WebM - web (VP9/AV1 + Opus)", "video"),
    "m4v": ("M4V - Apple devices", "video"), "ts": ("TS - broadcast stream", "video"),
    "avi": ("AVI - legacy", "video"),
    "mp3": ("MP3 audio", "audio"), "m4a": ("M4A (AAC) audio", "audio"), "wav": ("WAV audio", "audio"),
    "flac": ("FLAC audio (lossless)", "audio"), "ogg": ("OGG Vorbis audio", "audio"),
    "opus": ("Opus audio", "audio"),
}


VIDEO_KEYS = {"h264": "h264", "hevc": "hevc", "av1": "av1", "vp9": "vp9", "vp8": "vp8", "mpeg4": "mpeg4",
              "prores": "prores", "dnxhd": "dnxhr", "mjpeg": "mjpeg", "ffv1": "ffv1"}


TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}


SUB_CODEC = {"mp4": "mov_text", "m4v": "mov_text", "mov": "mov_text", "webm": "webvtt", "mkv": "copy"}


#: legal in MP4/MOV but unplayable on Apple devices and many TVs/players: convert instead of copying
POOR_IN_MP4 = {"vp9", "vp8", "mpeg4", "opus", "flac", "vorbis"}


AUDIO_FORMAT_ENCODE = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"], "m4a": ["-c:a", "aac", "-b:a", "256k"],
    "flac": ["-c:a", "flac", "-compression_level", "8"], "ogg": ["-c:a", "libvorbis", "-q:a", "6"],
    "opus": ["-c:a", "libopus", "-b:a", "160k", "-ar", "48000"],
}


def stream_args(info: ff.MediaInfo, fmt: str, smart: bool = True) -> tuple[list, list, list, str]:
    """(pre-input args, output args, notes, summary) for converting *info* to *fmt*."""
    pre, args, notes, actions = [], [], [], []
    audio_only = FORMATS[fmt][1] == "audio"
    videos = [s for s in info.streams if s.type == "video" and not s.attached_pic]
    audios = [s for s in info.streams if s.type == "audio"]
    subs = [s for s in info.streams if s.type == "subtitle"]
    if audio_only:
        if not audios:
            raise ValueError("no audio")
        a = audios[0]
        key = enc.audio_key_from_probe(a.codec)
        args += ["-map", f"0:{a.index}", "-vn"]
        if fmt == "wav":
            deep = (a.bits_per_raw_sample or 16) > 16 or a.codec in ("flac", "alac", "pcm_s24le", "pcm_s32le",
                                                                      "pcm_f32le")
            args += ["-c:a", "pcm_s24le" if deep else "pcm_s16le"]
            actions.append("audio → WAV")
        elif smart and key and enc.audio_ok_for(fmt, key):
            args += ["-c:a", "copy"]
            actions.append(f"audio {a.codec} copied")
        else:
            args += AUDIO_FORMAT_ENCODE[fmt]
            actions.append(f"audio {a.codec} → {AUDIO_FORMAT_ENCODE[fmt][1]}")
        if len(audios) > 1:
            notes.append(f"{len(audios) - 1} extra audio track(s) left out (audio formats hold one).")
        return pre, [*args, "-map_metadata", "0"], notes, ", ".join(actions)

    if videos:
        v = videos[0]
        key = VIDEO_KEYS.get(v.codec, v.codec)
        args += ["-map", f"0:{v.index}"]
        if smart and enc.video_ok_for(fmt, key) and not (fmt in ("mp4", "m4v", "mov") and key in POOR_IN_MP4):
            args += ["-c:v", "copy"]
            if key == "hevc" and fmt in ("mp4", "mov", "m4v"):
                args += ["-tag:v", "hvc1"]
            actions.append(f"video {v.codec} copied")
        else:
            codec = enc.CONTAINER_VIDEO.get(fmt, ("h264",))[0]
            ten = (v.bit_depth > 8) and codec != "h264"
            ve = enc.video(codec, "high", hw="auto", ten_bit=ten, container=fmt)
            pre += ve.pre_input
            args += ve.vf_args() + ve.args
            actions.append(f"video {v.codec} → {ve.encoder}")
        if len(videos) > 1:
            notes.append(f"{len(videos) - 1} extra video stream(s) left out.")
    for n, a in enumerate(audios):
        key = enc.audio_key_from_probe(a.codec)
        args += ["-map", f"0:{a.index}"]
        if smart and key and enc.audio_ok_for(fmt, key) and not (fmt in ("mp4", "m4v", "mov")
                                                                  and key in POOR_IN_MP4):
            args += [f"-c:a:{n}", "copy"]
            actions.append(f"audio {a.codec} copied")
        else:
            target = enc.default_audio_for(fmt)
            spec = enc.audio(target, container=fmt)
            args += [f"{x}:{n}" if x in ("-c:a", "-b:a", "-q:a") else x for x in spec]
            actions.append(f"audio {a.codec} → {spec[1]}")
    kept_subs = 0
    sub_codec = SUB_CODEC.get(fmt)
    for s in subs:
        if sub_codec == "copy" or (fmt == "ts" and s.codec == "dvb_subtitle"):
            args += ["-map", f"0:{s.index}", f"-c:s:{kept_subs}", "copy"]
            kept_subs += 1
        elif sub_codec and s.codec in TEXT_SUBS:
            args += ["-map", f"0:{s.index}", f"-c:s:{kept_subs}", sub_codec]
            kept_subs += 1
        else:
            notes.append(f"Subtitle track {s.index} ({s.codec}) can't go into {fmt.upper()} - left out.")
    if kept_subs:
        actions.append(f"{kept_subs} subtitle track(s)")
    if fmt == "mkv":
        args += ["-map", "0:t?", "-c:t", "copy"]          # attached fonts
    args += ["-map_metadata", "0", "-map_chapters", "0"]
    if fmt in ("mp4", "mov", "m4v"):
        args += ["-movflags", "+faststart"]
    return pre, args, notes, ", ".join(actions)
