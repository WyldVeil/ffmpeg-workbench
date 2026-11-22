"""
core/ops/encode_queue.py - the Encode Queue's jobs (tabs/transcoder/encode_queue.py):
codec, quality, size, frame rate and sound, checked against the output type,
turned into one FFmpeg command.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from core import ffmpeg as ff
from core.ops import presets
from core.ops.scaler import rate_fraction

VIDEO_CODECS = ["libx264", "libx265", "libvpx-vp9", "libvpx", "prores_ks", "dnxhd", "copy"]
AUDIO_CODECS = ["aac", "libmp3lame", "libopus", "libvorbis", "flac", "pcm_s16le", "copy", "none"]
PRESETS = [  # x264/x265 speeds
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
]

OK_VIDEO = {
    "libx264": ("mp4", "mkv", "mov", "m4v", "ts"),
    "libx265": ("mp4", "mkv", "mov", "m4v", "ts"),
    "libvpx-vp9": ("webm", "mkv", "mp4"),
    "libvpx": ("webm", "mkv"),
    "prores_ks": ("mov", "mkv"),
    "dnxhd": ("mov", "mkv", "mxf"),
}
OK_AUDIO = {
    "aac": ("mp4", "mkv", "mov", "m4v", "ts"),
    "libmp3lame": ("mp4", "mkv", "mov", "ts", "avi"),
    "libopus": ("webm", "mkv", "mp4"),
    "libvorbis": ("webm", "mkv"),
    "flac": ("mkv", "mp4", "mov"),
    "pcm_s16le": ("mov", "mkv", "avi", "mxf"),
}
DEFAULT_EXT = {"libvpx-vp9": ".webm", "libvpx": ".webm", "prores_ks": ".mov", "dnxhd": ".mov"}


@dataclass(slots=True)
class EncodeJob:
    """Settings model for one encode job created in the editor panel."""

    src: str = ""
    out: str = ""
    vcodec: str = "libx264"
    crf: str = "18"
    preset: str = "fast"
    resolution: str = "Source"
    fps: str = "Source"
    acodec: str = "aac"
    abitrate: str = "192k"
    extra_vf: str = ""
    extra_args: str = ""

    def _check(self, ext: str) -> None:
        if ext not in OK_VIDEO.get(self.vcodec, (ext,)):
            fits = " / ".join("." + e for e in OK_VIDEO[self.vcodec])
            raise ValueError(f"{self.vcodec} can't be saved as .{ext} - use {fits}.")
        if self.acodec not in ("none", "copy") and ext not in OK_AUDIO.get(self.acodec, (ext,)):
            raise ValueError(f"{self.acodec} audio can't go into .{ext}.")

    def _filters(self) -> list[str]:
        filters: list[str] = []
        if self.resolution != "Source":
            w, h = (int(x) for x in self.resolution.split("x"))
            filters.append(
                f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
            )
        if self.fps not in ("Source", "0", ""):
            r = rate_fraction(self.fps)
            filters.append(f"fps={r.numerator}/{r.denominator}")
        if self.extra_vf.strip():
            filters.append(self.extra_vf.strip())
        if self.vcodec == "copy" and filters:
            raise ValueError(
                "The video can't be resized or filtered while it is copied - choose a codec."
            )
        return filters

    def _video_args(self, ext: str) -> list[str]:
        v = self.vcodec
        if v in ("libx264", "libx265"):
            args = ["-crf", self.crf, "-preset", self.preset, "-pix_fmt", "yuv420p"]
            if v == "libx265":
                args += [
                    "-x265-params",
                    "log-level=error",
                    *(["-tag:v", "hvc1"] if ext in ("mp4", "mov") else []),
                ]
            return ["-c:v", v, *args]
        rest = {
            "libvpx-vp9": [
                "-crf",
                self.crf,
                "-b:v",
                "0",
                "-row-mt",
                "1",
                "-deadline",
                "good",
                "-cpu-used",
                "2",
            ],
            "libvpx": ["-crf", self.crf, "-b:v", "20M", "-deadline", "good", "-cpu-used", "2"],
            "prores_ks": ["-profile:v", "3", "-vendor", "apl0", "-pix_fmt", "yuv422p10le"],
            "dnxhd": ["-profile:v", "dnxhr_hq", "-pix_fmt", "yuv422p"],
        }
        return ["-c:v", v, *rest.get(v, [])]

    def _audio_args(self) -> list[str]:
        if self.acodec == "none":
            return ["-an"]
        if self.acodec in ("copy", "flac", "pcm_s16le"):
            return ["-c:a", self.acodec]
        return ["-c:a", self.acodec, "-b:a", self.abitrate]

    def build_cmd(self) -> list[str]:
        """Argument list for this job; ValueError with a readable reason when
        the choices can't work together."""
        ext = os.path.splitext(self.out)[1].lower().lstrip(".")
        self._check(ext)
        filters = self._filters()
        cmd = [
            ff.ffmpeg_path(),
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            self.src,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
        ]
        cmd += ["-vf", ",".join(filters)] if filters else []
        cmd += [*self._video_args(ext), *self._audio_args()]
        if self.extra_args.strip():
            try:
                cmd += presets.split_args(self.extra_args)
            except ValueError as exc:
                raise ValueError(f"The extra arguments have an unclosed quote ({exc}).") from None
        cmd += ["-movflags", "+faststart"] if ext in ("mp4", "mov", "m4v") else []
        return [*cmd, self.out]
