"""
core/ops/batch.py - what the Batch Processor (tabs/system/batch.py) does to each file.

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.  Each
operation is one planner; a plan is (output path, steps) or the reason the
file is skipped.  '{OUT}' in a step stands for the temporary output file.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core import encoders as enc
from core import ffmpeg as ff
from core import loudness
from core.ops.converter import stream_args
from core.outputs import free_name

if TYPE_CHECKING:
    from core.queue_manager import TaskContext

Step = list[str] | Callable[["TaskContext"], list[str]]
Plan = tuple[str, list[Step]] | str


@dataclass(frozen=True)
class _File:
    info: ff.MediaInfo
    src: str
    out_dir: str
    taken: set[str]
    opts: Mapping[str, Any]

    @property
    def ext(self) -> str:
        return os.path.splitext(self.src)[1].lower()

    def name(self, suffix: str, ext: str) -> str:
        base = os.path.splitext(os.path.basename(self.src))[0]
        return free_name(os.path.join(self.out_dir, f"{base}{suffix}{ext}"), self.taken)


def _ffmpeg() -> list[str]:
    return [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y"]


def _audio(f: _File, mp3: bool) -> Plan:
    if not f.info.has_audio:
        return "no audio"
    out = f.name("", ".mp3" if mp3 else ".m4a")
    codec = ["-c:a", "libmp3lame", "-q:a", "2"] if mp3 else ["-c:a", "aac", "-b:a", "192k"]
    cmd = [*_ffmpeg(), "-i", f.src, "-map", "0:a:0", "-vn", *codec, "-map_metadata", "0", "{OUT}"]
    return out, [cmd]


def _loudness(f: _File) -> Plan:
    info, src = f.info, f.src
    if not info.has_audio:
        return "no audio to normalise"
    kept = (".mp4", ".mkv", ".mov", ".webm", ".m4a", ".mp3", ".flac", ".wav")
    ext = f.ext if f.ext in kept else ".mkv"
    out = f.name("_loud", ext)
    rate = info.require_audio().sample_rate or 48000
    container = ext.lstrip(".")

    def second_pass(ctx: TaskContext) -> list[str]:
        res = ctx.run(
            loudness.measure_cmd(src, -16.0, -1.5, 11.0),
            duration=info.duration or None,
            tail=200000,
        )
        meas = loudness.parse_measurement(res.log_tail)
        own = {"wav": "pcm16", "flac": "flac", "mp3": "mp3"}
        acodec = own.get(container) or enc.default_audio_for(container)
        vargs = ["-map", "0:v?", "-c:v", "copy"]
        if info.has_video and info.require_video().codec == "hevc" and container in ("mp4", "mov"):
            vargs += ["-tag:v", "hvc1"]
        level = loudness.normalise_filter(-16.0, -1.5, 11.0, meas, rate)
        cmd = [*_ffmpeg(), "-i", src, *vargs, "-map", "0:a:0", "-af", level]
        cmd += enc.audio(acodec, "192k", container=container)
        return [*cmd, "-map", "0:s?", "-c:s", "copy", "-map_metadata", "0", "{OUT}"]

    return out, [second_pass]


def _strip_meta(f: _File) -> Plan:
    out = f.name("_clean", f.ext or ".mkv")
    keep: list[str] = []
    for st in f.info.streams:
        lang = st.tags.get("language")
        if lang:
            keep += [f"-metadata:s:{st.index}", f"language={lang}"]
    cmd = [*_ffmpeg(), "-i", f.src, "-map", "0", "-c", "copy"]
    cmd += ["-map_metadata", "-1", "-map_chapters", "-1", *keep, "-fflags", "+bitexact", "{OUT}"]
    return out, [cmd]


def _to_mp4(f: _File) -> Plan:
    out = f.name("", ".mp4") if f.ext != ".mp4" else f.name("_converted", ".mp4")
    pre, args, _notes, _summary = stream_args(f.info, "mp4", True)
    return out, [[*_ffmpeg(), *pre, "-i", f.src, *args, "{OUT}"]]


#: x264 preset names (what the page offers) -> the app's encoder speeds
_SPEEDS = {
    "ultrafast": "fastest",
    "superfast": "fastest",
    "veryfast": "fast",
    "faster": "fast",
    "fast": "fast",
    "medium": "balanced",
    "slow": "slow",
    "slower": "slowest",
    "veryslow": "slowest",
}


def _quality(crf: int) -> str:
    for limit, name in ((18, "very_high"), (22, "high"), (26, "good"), (30, "low")):
        if crf <= limit:
            return name
    return "lowest"


def _h264(f: _File, vf: str | None, suffix: str) -> Plan:
    speed = _SPEEDS.get(f.opts["preset"], "balanced")
    ve = enc.video("h264", _quality(f.opts["crf"]), speed=speed, hw="auto", container="mp4")
    cmd = [*_ffmpeg(), *ve.pre_input, "-i", f.src, "-map", "0:v:0", *ve.vf_args(vf), *ve.args]
    cmd += ["-map", "0:a?", "-c:a", "aac", "-b:a", "192k", "-map_metadata", "0", "{OUT}"]
    return f.name(suffix, ".mp4"), [cmd]


def _resize(f: _File) -> Plan:
    bw, bh = (int(x) for x in f.opts["res"].split("x"))
    w, h = f.info.require_video().display_size
    if h > w:
        bw, bh = bh, bw  # portrait videos get a portrait box
    if w <= bw and h <= bh:
        return "already that size or smaller"
    vf = f"scale={bw}:{bh}:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1"
    return _h264(f, vf, f"_{min(bw, bh)}p")


def _mp3(f: _File) -> Plan:
    return _audio(f, mp3=True)


def _aac(f: _File) -> Plan:
    return _audio(f, mp3=False)


def _reencode(f: _File) -> Plan:
    return _h264(f, None, "_h264")


_ANY_FILE: dict[str, Callable[[_File], Plan]] = {
    "audio_mp3": _mp3,
    "audio_aac": _aac,
    "loudness": _loudness,
    "strip_meta": _strip_meta,
}
_VIDEO: dict[str, Callable[[_File], Plan]] = {"to_mp4": _to_mp4, "resize": _resize}


def plan(
    op: str,
    info: ff.MediaInfo,
    src: str,
    out_dir: str,
    opts: Mapping[str, Any],
    taken: set[str],
) -> Plan:
    """(output path, [argv | callable(ctx) -> argv]) for *op* on *src*, or the reason to skip it."""
    f = _File(info, src, out_dir, taken, opts)
    if op in _ANY_FILE:
        return _ANY_FILE[op](f)
    if not info.has_video:
        return "no video"
    return _VIDEO.get(op, _reencode)(f)
