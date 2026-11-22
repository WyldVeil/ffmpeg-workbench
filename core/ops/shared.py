"""
core/ops/shared.py - argument builders many tools share (audio encoding, frame rates).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from core import encoders as enc
from core import ffmpeg as ff

#: Runs one command of a multi-step job: (argv, expected seconds, progress from, progress to)
Runner = Callable[[list[str], float, float, float], object]
#: Receives a line for the job's log
Log = Callable[[str], object]


@dataclass(frozen=True, slots=True)
class Steps:
    """How a multi-step job runs: its command runner (the queue's or the command
    line's), where its log lines go, and a scratch folder."""

    run: Runner
    log: Log = print
    tmp: str = ""


def keep_audio_args(
    info: ff.MediaInfo | None, container: str, *, index: int = 0, filters: str = ""
) -> list[str]:
    """Map all audio of input *index*: stream-copied when the container accepts
    the codec and no *filters* are applied, otherwise re-encoded."""
    audio = info.audio if info is not None else None
    if audio is None:
        return ["-an"]
    args = ["-map", f"{index}:a?"]
    if filters:
        return [
            *args,
            "-af",
            filters,
            *enc.audio(enc.default_audio_for(container), container=container),
        ]
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


AUDIO_ONLY_EXTS = (
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".mka",
    ".aiff",
    ".aif",
)


def audio_encoder_args(out: str) -> list[str]:
    """Encoder for processed audio, chosen from the output file's type."""
    ext = os.path.splitext(out)[1].lower()
    return {
        ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
        ".wav": ["-c:a", "pcm_s16le"],
        ".aiff": ["-c:a", "pcm_s16be"],
        ".aif": ["-c:a", "pcm_s16be"],
        ".flac": ["-c:a", "flac", "-compression_level", "8"],
        ".m4a": ["-c:a", "aac", "-b:a", "256k"],
        ".aac": ["-c:a", "aac", "-b:a", "256k"],
        ".ogg": ["-c:a", "libvorbis", "-q:a", "6"],
        ".opus": ["-c:a", "libopus", "-b:a", "160k"],
        ".mka": ["-c:a", "flac"],
        ".webm": ["-c:a", "libopus", "-b:a", "160k"],
    }.get(ext, ["-c:a", "aac", "-b:a", "256k"])


def audio_job_args(out: str, info: ff.MediaInfo | None, *, stream: int = 0) -> list[str]:
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


# ── The everyday render: one source, one chain or graph ────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderJob:
    """One render: ``ffmpeg [pre_input] -i src [extra_inputs] <chain|graph> <audio>
    <encoder> [extra_args] out``.

    *chain* is a simple ``-vf`` chain; *graph* a ``-filter_complex`` graph whose
    video output is labelled ``[v]``.  *audio* defaults to keeping the source's
    audio; *inputs* are further files the job reads (guarded against overwriting)."""

    src: str
    info: ff.MediaInfo
    out: str
    name: str
    chain: str = ""
    graph: str = ""
    extra_inputs: Sequence[str] = ()
    audio: Sequence[str] | None = None
    duration: float | None = None
    extra_args: Sequence[str] = ()
    inputs: Sequence[str] = ()
    pre_input: Sequence[str] = ()
    extra_files: Sequence[str] = ()  # more inputs (1, 2, ...), after *extra_inputs*
    limit: float | None = None  # stop after this many seconds

    def command(self, venc: enc.VideoEncode) -> list[str]:
        container = os.path.splitext(self.out)[1].lower().lstrip(".") or "mp4"
        cmd = [
            ff.ffmpeg_path(),
            "-y",
            *self.pre_input,
            *venc.pre_input,
            "-i",
            self.src,
            *self.extra_inputs,
            *(a for f in self.extra_files for a in ("-i", f)),
        ]
        if self.graph:
            if venc.filter_suffix:
                cmd += [
                    "-filter_complex",
                    f"{self.graph};[v]{venc.filter_suffix}[venc]",
                    "-map",
                    "[venc]",
                ]
            else:
                cmd += ["-filter_complex", self.graph, "-map", "[v]"]
        else:
            cmd += ["-map", "0:v:0", *venc.vf_args(self.chain)]
        cmd += list(self.audio if self.audio is not None else keep_audio_args(self.info, container))
        limit = ["-t", f"{self.limit:.3f}"] if self.limit is not None else []
        return [*cmd, *self.extra_args, *limit, *venc.args, "-map_metadata", "0", self.out]


def intermediate_args(venc: enc.VideoEncode) -> list[str]:
    """Encoder args for temporary pieces that are joined later: drops the
    container-specific flags (faststart, hvc1 tag) that only belong on the
    final file - see :func:`final_copy_args`."""
    out: list[str] = []
    skip = False
    for a in venc.args:
        if skip:
            skip = False
            continue
        if a in ("-movflags", "-tag:v"):
            skip = True
            continue
        out.append(a)
    return out


def final_copy_args(venc: enc.VideoEncode, container: str) -> list[str]:
    """Flags for the final stream-copied file made from intermediate pieces."""
    args: list[str] = []
    if container in ("mp4", "mov", "m4v"):
        args += ["-movflags", "+faststart"]
        if venc.codec == "hevc":
            args += ["-tag:v", "hvc1"]
    return args


def piece_cmd(
    src: str,
    start: float,
    length: float,
    out: str,
    venc: enc.VideoEncode,
    *,
    vf: str,
    af: str | None,
) -> list[str]:
    """One intermediate piece: *length* s of *src* from *start* (cut on the input,
    so speed changes in *vf*/*af* come after), sound as PCM (*af* None: no sound)."""
    cut = [
        ff.ffmpeg_path(),
        "-y",
        *venc.pre_input,
        "-ss",
        ff.ts(start),
        "-t",
        ff.ts(length),
        "-i",
        src,
    ]
    sound = (
        ["-map", "0:a:0", "-af", af, "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"]
        if af is not None
        else ["-an"]
    )
    return [*cut, "-map", "0:v:0", *venc.vf_args(vf), *sound, *intermediate_args(venc), out]


def join_pieces_cmd(
    listing: str, out: str, venc: enc.VideoEncode, container: str, *, audio: bool
) -> list[str]:
    """The pieces in the concat *listing*, joined: picture copied, sound encoded once."""
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        listing,
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
    ]
    sound = ["-map", "0:a:0", *enc.audio(enc.default_audio_for(container), container=container)]
    return [*cmd, *(sound if audio else ["-an"]), *final_copy_args(venc, container), out]


def audio_filter_cmd(src: str, af: str, out: str, info: ff.MediaInfo) -> list[str]:
    """*src* with its sound through *af* (the picture, if any, kept)."""
    return [ff.ffmpeg_path(), "-y", "-i", src, "-af", af, *audio_job_args(out, info), out]


def strip_audio_cmd(src: str, out: str) -> list[str]:
    """*src* without its sound (everything else copied)."""
    return [ff.ffmpeg_path(), "-y", "-i", src, "-map", "0", "-map", "-0:a", "-c", "copy", out]


def chapters_cmd(src: str, metadata: str, out: str) -> list[str]:
    """A copy of *src* with the chapters from the ffmetadata file *metadata*."""
    inputs = [ff.ffmpeg_path(), "-y", "-i", src, "-f", "ffmetadata", "-i", metadata]
    return [*inputs, "-map", "0", "-map_metadata", "0", "-map_chapters", "1", "-c", "copy", out]


def graph_encode_cmd(
    inputs: Sequence[str],
    graph: str,
    venc: enc.VideoEncode,
    container: str,
    out: str,
    *,
    audio: bool,
    limit: float | None = None,
) -> list[str]:
    """*inputs* ("-i" arguments and their options) through *graph* - output [v],
    and [a] when *audio* - encoded; at most *limit* seconds."""
    vmap = "[v]"
    if venc.filter_suffix:
        graph += f";[v]{venc.filter_suffix}[venc]"
        vmap = "[venc]"
    amap = (
        ["-map", "[a]", *enc.audio(enc.default_audio_for(container), container=container)]
        if audio
        else ["-an"]
    )
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        *venc.pre_input,
        *inputs,
        "-filter_complex",
        graph,
        "-map",
        vmap,
        *amap,
        *venc.args,
    ]
    return [*cmd, *(["-t", f"{limit:.3f}"] if limit is not None else []), out]


def preview_audio_cmd(src: str, af: str, start: float, seconds: float, wav: str) -> list[str]:
    """A short stretch of *src*'s sound through filter *af*, as a WAV to listen to."""
    head = [ff.ffmpeg_path(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error"]
    cut = ["-ss", ff.ts(start), "-t", ff.ts(seconds), "-i", src, "-map", "0:a:0", "-vn"]
    return [*head, *cut, *(["-af", af] if af else []), "-c:a", "pcm_s16le", wav]
