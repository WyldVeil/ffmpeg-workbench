"""
core/ops/looper.py - the Clip Looper (tabs/cutting/clip_looper.py): a section
repeated (plainly, faster each time, or fading), with the rest kept around it.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl
from core.ops.shared import final_copy_args, intermediate_args
from core.queue_manager import TaskContext

MAX_SPEED = 16.0

Piece = tuple[str, float, float, float, float]  # (kind, start, end, speed, level)


def loop_units(mode: str, count: int, factor: float) -> list[tuple[float, float]]:
    """(speed, level) for each repeat; level 1 = untouched picture/sound."""
    units: list[tuple[float, float]] = []
    for k in range(count):
        if mode == "accel":
            speed = min(factor**k, MAX_SPEED)
            units.append((speed, 1.0))
        elif mode == "echo":
            units.append((1.0, max(0.15, 0.7**k)))
        else:
            units.append((1.0, 1.0))
    return units


def plan(
    start: float,
    end: float,
    duration: float,
    units: Sequence[tuple[float, float]],
    *,
    keep_context: bool,
) -> list[Piece]:
    """The pieces in order: (with *keep_context*) what comes before, each repeat
    of start..end at its (speed, level), then what comes after."""
    pieces: list[Piece] = [("plain", 0.0, start, 1.0, 1.0)] if keep_context and start > 0.01 else []
    pieces += [("loop", start, end, sp, lv) for sp, lv in units]
    if keep_context and end < duration - 0.01:
        pieces.append(("plain", end, duration, 1.0, 1.0))
    return pieces


def output_length(pieces: Sequence[Piece]) -> float:
    return sum((b - a) / sp for _k, a, b, sp, _lv in pieces)


def base_filter(width: int, height: int, rate: str) -> str:
    """Every piece made alike, so they join without re-encoding."""
    return f"scale={width}:{height},setsar=1,fps={rate},format=yuv420p"


@dataclass(frozen=True, kw_only=True)
class LoopJob:
    """Each distinct piece (a part of the source at a speed and brightness) is
    made once, then all are joined in order."""

    src: str
    pieces: list[tuple[str, float, float, float, float]]  # kind, from, to, speed, level
    base_vf: str
    venc: enc.VideoEncode
    container: str
    audio: bool
    total: float

    def _piece_cmd(self, a: float, b: float, sp: float, lv: float, seg: str) -> list[str]:
        vf = (
            self.base_vf
            + (f",setpts=PTS/{sp:.5f}" if sp != 1 else "")
            + (f",lutyuv=y=val*{lv:.3f}" if lv < 1 else "")
        )
        af = (
            "anull"
            + (f",{fl.atempo_chain(sp)}" if sp != 1 else "")
            + (f",volume={lv:.3f}" if lv < 1 else "")
        )
        # -t before -i limits the SOURCE section (speed changes come after)
        cmd = [
            ff.ffmpeg_path(),
            "-y",
            *self.venc.pre_input,
            "-ss",
            ff.ts(a),
            "-t",
            ff.ts(b - a),
            "-i",
            self.src,
        ]
        cmd += ["-map", "0:v:0", *self.venc.vf_args(vf)]
        audio = ["-map", "0:a:0", "-af", af, "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"]
        return [
            *cmd,
            *(audio if self.audio else ["-an"]),
            *intermediate_args(self.venc),
            seg,
        ]

    def run(self, _p: object, _c: object, ctx: TaskContext) -> int:
        tmp = ctx.tempdir()
        made: dict[tuple[float, float, float, float], str] = {}
        files: list[str] = []
        done = 0.0
        for _kind, a, b, sp, lv in self.pieces:
            key, length = (a, b, sp, lv), (b - a) / sp
            if key not in made:
                seg = os.path.join(tmp, f"u{len(made):04d}.mkv")
                ctx.span(0.9 * done / self.total, 0.9 * (done + length) / self.total).run(
                    self._piece_cmd(a, b, sp, lv, seg), duration=length
                )
                made[key] = seg
            done += length
            files.append(made[key])
        lst = fl.write_concat_list(files, os.path.join(tmp, "list.txt"))
        final = [
            ff.ffmpeg_path(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            lst,
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
        ]
        audio = [
            "-map",
            "0:a:0",
            *enc.audio(enc.default_audio_for(self.container), container=self.container),
        ]
        final += audio if self.audio else ["-an"]
        ctx.span(0.9, 1.0).run(
            [*final, *final_copy_args(self.venc, self.container), ctx.output()],
            duration=self.total,
        )
        return 0
