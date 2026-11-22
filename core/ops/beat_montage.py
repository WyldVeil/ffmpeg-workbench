"""
core/ops/beat_montage.py - the Beat Sync Cutter (tabs/cutting/beat_sync.py): clips
cut on the music's beats (in order or shuffled), each cut frame-exact, joined
under the music.
"""

from __future__ import annotations

import itertools
import os
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl
from core.ops.shared import final_copy_args, intermediate_args
from core.queue_manager import TaskContext

Segment = tuple[str, float, int]  # (clip, in point, frames)


def frame_plan(cuts: Iterable[float], total: float, fps: float) -> list[tuple[float, int]]:
    """[(start_s, frames)] covering 0..total with cuts on the given times."""
    marks = [0.0] + [c for c in cuts if 0 < c < total] + [total]
    plan: list[tuple[float, int]] = []
    for a, b in itertools.pairwise(marks):
        frames = round(b * fps) - round(a * fps)
        if frames > 0:
            plan.append((a, frames))
    return plan


def assign_sources(
    plan: Sequence[tuple[float, int]],
    clips: Sequence[tuple[str, float]],
    order: str,
    fps: float,
    seed: int = 7,
) -> list[Segment]:
    """[(clip_path, in_point, frames)] for each planned segment."""
    rng = random.Random(seed)
    out: list[Segment] = []
    idx = 0
    pos = {c[0]: 0.0 for c in clips}
    for _start, frames in plan:
        seconds = frames / fps
        if order == "shuffle":
            path, dur = rng.choice(clips)
            start = rng.uniform(0, max(0.0, dur - seconds)) if dur > seconds else 0.0
        else:
            path, dur = clips[idx % len(clips)]
            start = pos[path]
            if start + seconds > dur:
                start = 0.0 if seconds >= dur else start % max(dur - seconds, 0.001)
            pos[path] = start + seconds
            idx += 1
        out.append((path, start, frames))
    return out


@dataclass(frozen=True, slots=True, kw_only=True)
class MontageJob:
    segments: Sequence[Segment]
    music: str
    size: tuple[int, int]
    rate: str  # frame rate as FFmpeg reads it
    fps: float
    duration: float  # the music's
    venc: enc.VideoEncode
    container: str

    def _segment_cmd(self, path: str, start: float, frames: int, out: str) -> list[str]:
        """*frames* frames from *path* at *start* (looping a clip that's too short),
        filled to the montage's size and rate."""
        w, h = self.size
        chain = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={self.rate},format=yuv420p"
        v = self.venc
        cut = [
            ff.ffmpeg_path(),
            "-y",
            *v.pre_input,
            "-stream_loop",
            "-1",
            "-ss",
            ff.ts(start),
            "-i",
            path,
        ]
        return [
            *cut,
            "-frames:v",
            str(frames),
            "-an",
            "-sn",
            *v.vf_args(chain),
            *intermediate_args(v),
            out,
        ]

    def _join_cmd(self, listing: str, out: str) -> list[str]:
        cmd = [
            ff.ffmpeg_path(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            listing,
            "-i",
            self.music,
        ]
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy"]
        cmd += enc.audio(
            enc.default_audio_for(self.container), container=self.container, bitrate="256k"
        )
        return [
            *cmd,
            "-t",
            f"{self.duration:.3f}",
            *final_copy_args(self.venc, self.container),
            out,
        ]

    def run(self, _p: object, _c: object, ctx: TaskContext) -> int:
        tmp = ctx.tempdir()
        n = len(self.segments)
        files: list[str] = []
        for i, (path, start, frames) in enumerate(self.segments):
            seg = os.path.join(tmp, f"seg{i:05d}.mkv")
            ctx.span(0.95 * i / n, 0.95 * (i + 1) / n).run(
                self._segment_cmd(path, start, frames, seg), duration=frames / self.fps
            )
            files.append(seg)
        lst = fl.write_concat_list(files, os.path.join(tmp, "list.txt"))
        ctx.span(0.95, 1.0).run(self._join_cmd(lst, ctx.output()), duration=self.duration)
        return 0
