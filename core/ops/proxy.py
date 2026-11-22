"""
core/ops/proxy.py - the Proxy Generator (tabs/transcoder/proxy.py): editing
proxies in the codecs editors expect, every audio track and the timecode kept,
each file written to a .part name and renamed only when it's complete.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from core import ffmpeg as ff
from core.ops.batch_files import run_batch
from core.queue_manager import TaskContext

PRESETS: dict[str, tuple[str, str, list[str], str]] = {  # key: (label, ext, video args, audio mode)
    "prores": (
        "ProRes Proxy (.mov) - Premiere, Resolve, Final Cut",
        ".mov",
        ["-c:v", "prores_ks", "-profile:v", "0", "-vendor", "apl0", "-pix_fmt", "yuv422p10le"],
        "pcm",
    ),
    "dnxhr": (
        "DNxHR LB (.mov) - Avid, Resolve",
        ".mov",
        ["-c:v", "dnxhd", "-profile:v", "dnxhr_lb", "-pix_fmt", "yuv422p"],
        "pcm",
    ),
    "h264": (
        "H.264 short-GOP (.mp4) - small, any editor",
        ".mp4",
        [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-tune",
            "fastdecode",
            "-g",
            "12",
            "-bf",
            "0",
            "-pix_fmt",
            "yuv420p",
        ],
        "aac",
    ),
    "mjpeg": (
        "MJPEG draft (.mov) - very light to decode",
        ".mov",
        ["-c:v", "mjpeg", "-q:v", "6", "-pix_fmt", "yuvj422p"],
        "pcm",
    ),
}
SIZES: dict[str, tuple[str, float | int]] = {
    "half": ("Half size", 0.5),
    "quarter": ("Quarter size", 0.25),
    "720": ("720p", 720),
    "540": ("540p", 540),
    "same": ("Same size", 1.0),
}


def scale_filter(size_key: str) -> str:
    val = SIZES[size_key][1]
    if size_key == "same":
        return "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1"
    if isinstance(val, float):
        return f"scale=trunc(iw*{val}/2)*2:trunc(ih*{val}/2)*2,setsar=1"
    return f"scale=-2:'min({val},ih)',setsar=1"


def proxy_args(info: ff.MediaInfo, preset: str, size_key: str, audio: bool) -> list[str]:
    _label, _ext, video, amode = PRESETS[preset]
    args: list[str] = ["-map", "0:v:0", "-vf", scale_filter(size_key), *video]
    if audio:
        for n, a in enumerate(s for s in info.streams if s.type == "audio"):
            args += ["-map", f"0:{a.index}"]
            args += (
                [f"-c:a:{n}", "pcm_s16le"]
                if amode == "pcm"
                else [f"-c:a:{n}", "aac", f"-b:a:{n}", "160k"]
            )
    else:
        args += ["-an"]
    tc = info.tags.get("timecode") or next(
        (s.tags.get("timecode") for s in info.streams if s.tags.get("timecode")), None
    )
    if tc:
        args += ["-timecode", str(tc)]
    args += ["-map_metadata", "0"]
    if preset == "h264":
        args += ["-movflags", "+faststart"]
    return args


def proxy_cmd(
    src: str, out: str, info: ff.MediaInfo, preset: str, size_key: str, *, audio: bool
) -> list[str]:
    head = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-i", src]
    return [*head, *proxy_args(info, preset, size_key, audio), out]


@dataclass(frozen=True, slots=True, kw_only=True)
class ProxyBatch:
    """(source, proxy) pairs made in turn; a file that fails is reported, and the
    batch fails at the end if any did."""

    jobs: Sequence[tuple[str, str]]
    preset: str
    size: str
    audio: bool

    def _one(self, ctx: TaskContext, src: str, part: str) -> None:
        info = ff.probe(src)
        if not info.has_video:
            raise ValueError("no video in this file")
        os.makedirs(os.path.dirname(part), exist_ok=True)
        ctx.run(
            proxy_cmd(src, part, info, self.preset, self.size, audio=self.audio),
            duration=info.duration or None,
        )

    def run(self, _p: object, _c: object, ctx: TaskContext) -> int:
        return run_batch(ctx, self.jobs, self._one, noun="proxies made")
