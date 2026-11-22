"""
core/ops/codec.py - the Codec Cruncher (tabs/transcoder/codec.py): re-encode (or
copy) the picture, convert or copy every sound and subtitle track that fits the
container, tone-map HDR for 8-bit outputs; metadata and chapters kept.
"""

from __future__ import annotations

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t

DEFAULT_CONTAINER = {
    "h264": "mp4",
    "hevc": "mp4",
    "av1": "mp4",
    "vp9": "webm",
    "prores": "mov",
    "dnxhr": "mov",
    "ffv1": "mkv",
}
AUDIO: dict[str, tuple[str, str, str | None]] = {  # key: (label, codec, bitrate)
    "aac256": ("AAC 256 kb/s", "aac", "256k"),
    "aac192": ("AAC 192 kb/s", "aac", "192k"),
    "opus": ("Opus 160 kb/s", "opus", "160k"),
    "mp3": ("MP3 320 kb/s", "mp3", "320k"),
    "flac": ("FLAC (lossless)", "flac", None),
    "pcm24": ("PCM 24-bit (editing)", "pcm24", None),
    "copy": ("Copy (no change)", "copy", None),
    "none": (t("codec.remove_audio"), "", None),
}


def container_for(codec: str, choice: str) -> str:
    if choice != "auto" and enc.video_ok_for(choice, codec):
        return choice
    return DEFAULT_CONTAINER.get(codec, "mkv")


def audio_args(
    info: ff.MediaInfo, key: str, container: str, keep_all: bool
) -> tuple[list[str], list[str]]:
    """(args, notes) for the audio tracks."""
    label, codec, rate = AUDIO[key]
    audios = [s for s in info.streams if s.type == "audio"]
    if not audios or not codec:
        return ["-an"], []
    notes: list[str] = []
    tracks = audios if keep_all else audios[:1]
    args: list[str] = []
    for n, a in enumerate(tracks):
        args += ["-map", f"0:{a.index}"]
        want = codec
        if want == "copy":
            probe_key = enc.audio_key_from_probe(a.codec)
            if probe_key and enc.audio_ok_for(container, probe_key):
                args += [f"-c:a:{n}", "copy"]
                continue
            want = enc.default_audio_for(container)
            notes.append(
                f"Audio track {n + 1} ({a.codec}) can't be copied into {container.upper()} - "
                f"converted to {want}."
            )
        elif not enc.audio_ok_for(container, want):
            notes.append(
                f"{label} doesn't fit {container.upper()} - using {enc.default_audio_for(container)}."
            )
            want = enc.default_audio_for(container)
        spec = enc.audio(want, rate, container=container)
        args += [f"{x}:{n}" if x in ("-c:a", "-b:a", "-q:a") else x for x in spec]
    return args, notes


def video_args(
    info: ff.MediaInfo, venc: enc.VideoEncode | None, container: str
) -> tuple[list[str], list[str], list[str]]:
    """(before the input, video arguments, notes): copied when *venc* is None."""
    v = info.require_video()
    if venc is None:
        tag = ["-tag:v", "hvc1"] if v.codec == "hevc" and container in ("mp4", "mov") else []
        return [], ["-map", "0:v:0", "-c:v", "copy", *tag], []
    chain: list[str] = []
    notes: list[str] = []
    ten_bit = venc.pix_fmt.endswith(("10le", "p010le", "p010")) or venc.encoder in (
        "prores_ks",
        "dnxhd",
        "ffv1",
    )
    if v.is_hdr and not ten_bit:
        chain.append(fl.hdr_to_sdr_chain(out_fmt=venc.pix_fmt or "yuv420p"))
        notes.append(
            "HDR source tone-mapped to SDR for this 8-bit output (tick 10-bit to keep HDR)."
        )
    return (
        list(venc.pre_input),
        ["-map", "0:v:0", *venc.vf_args(",".join(chain) or None), *venc.args],
        notes,
    )


def subtitle_args(info: ff.MediaInfo, container: str) -> list[str]:
    """Every subtitle track the container can hold (text ones converted)."""
    sub_codec = {"mkv": "copy", "mp4": "mov_text", "mov": "mov_text", "webm": "webvtt"}.get(
        container
    )
    args: list[str] = []
    n = 0
    for s in info.streams:
        if s.type == "subtitle" and (
            sub_codec == "copy" or s.codec in ("subrip", "ass", "ssa", "webvtt", "mov_text")
        ):
            args += ["-map", f"0:{s.index}", f"-c:s:{n}", sub_codec or "copy"]
            n += 1
    return args


def crunch_cmd(
    src: str,
    out: str,
    info: ff.MediaInfo,
    venc: enc.VideoEncode | None,
    audio_key: str,
    container: str,
    *,
    keep_all: bool,
) -> tuple[list[str], list[str]]:
    """(command, notes for the user); *keep_all* keeps every sound and subtitle track."""
    pre, v_args, notes = video_args(info, venc, container)
    a_args, a_notes = audio_args(info, audio_key, container, keep_all)
    cmd = [ff.ffmpeg_path(), "-y", *pre, "-i", src, *v_args, *a_args]
    cmd += subtitle_args(info, container) if keep_all else []
    return [*cmd, "-map_metadata", "0", "-map_chapters", "0", out], [*a_notes, *notes]
