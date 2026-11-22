"""
core/ops/speed_ramp.py - the Speed Ramper (tabs/visuals/speed_ramp.py): a constant
speed, or a curve through (time, speed) points, for the picture (setpts with the
exact integral of the curve) and the sound (tempo commands following it).
"""

from __future__ import annotations

import itertools
import math
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl
from core.queue_manager import TaskContext

Point = tuple[float, float]  # (source time, speed)

# ── Speed curve maths (pure, tested) ─────────────────────────────────────────


def normalise_points(points: Iterable[tuple[float, float]], duration: float) -> list[Point]:
    """Sorted (time, speed) points clipped to [0, duration], with the speed
    held before the first and after the last point."""
    pts = sorted((max(0.0, min(float(tm), duration)), float(sp)) for tm, sp in points)
    merged: list[Point] = []
    for tm, sp in pts:
        if merged and abs(merged[-1][0] - tm) < 1e-6:
            merged[-1] = (tm, sp)
        else:
            merged.append((tm, sp))
    if not merged:
        return [(0.0, 1.0), (duration, 1.0)]
    if merged[0][0] > 0:
        merged.insert(0, (0.0, merged[0][1]))
    if merged[-1][0] < duration:
        merged.append((duration, merged[-1][1]))
    return merged


def speed_at(points: Sequence[Point], at: float) -> float:
    for (t0, v0), (t1, v1) in itertools.pairwise(points):
        if t0 <= at <= t1:
            return v0 if t1 - t0 < 1e-9 else v0 + (v1 - v0) * (at - t0) / (t1 - t0)
    return points[-1][1]


def segments(points: Sequence[Point]) -> list[tuple[float, float, float, float, float, float]]:
    """[(t0, t1, v0, v1, out_start, out_len)] for consecutive points."""
    out: list[tuple[float, float, float, float, float, float]] = []
    acc = 0.0
    for (t0, v0), (t1, v1) in itertools.pairwise(points):
        if t1 - t0 < 1e-9:
            continue
        if abs(v1 - v0) < 1e-9:
            seg = (t1 - t0) / v0
        else:
            k = (v1 - v0) / (t1 - t0)
            seg = math.log(v1 / v0) / k
        out.append((t0, t1, v0, v1, acc, seg))
        acc += seg
    return out


def output_duration(points: Sequence[Point]) -> float:
    segs = segments(points)
    return segs[-1][4] + segs[-1][5] if segs else 0.0


def setpts_expr(points: Sequence[Point]) -> str:
    """Output time (seconds) as an FFmpeg expression of the source time T."""
    pieces: list[tuple[float, str]] = []
    for t0, t1, v0, v1, start, _seg in segments(points):
        if abs(v1 - v0) < 1e-9:
            e = f"({start:.6f}+(T-{t0:.6f})/{v0:.6f})"
        else:
            k = (v1 - v0) / (t1 - t0)
            e = f"({start:.6f}+log(({v0:.6f}+{k:.6f}*(T-{t0:.6f}))/{v0:.6f})/{k:.6f})"
        pieces.append((t1, e))
    expr = pieces[-1][1]
    for t1, e in reversed(pieces[:-1]):
        expr = f"if(lt(T,{t1:.6f}),{e},{expr})"
    return expr


_ATEMPO_STAGES = ("a", "b", "c", "d")  # 4 chained atempo: 0.0625x - 10^8x


def tempo_commands(points: Sequence[Point], engine: str = "rubberband", step: float = 0.1) -> str:
    """asendcmd script that makes the audio tempo follow the speed curve.

    *engine* ``rubberband`` drives one rubberband filter (0.01x - 100x);
    ``atempo`` drives four named atempo filters with the 4th root of the
    speed each (atempo alone only accepts 0.5x - 100x)."""

    def line(at: float, speed: float) -> str:
        if engine == "rubberband":
            return f"{at:.3f} rubberband tempo {speed:.6f};"
        r = speed**0.25
        return f"{at:.3f} " + ", ".join(f"atempo@{n} tempo {r:.6f}" for n in _ATEMPO_STAGES) + ";"

    lines: list[str] = []
    for t0, t1, v0, v1, _s, _l in segments(points):
        if abs(v1 - v0) < 1e-9:
            lines.append(line(t0, v0))
            continue
        k = (v1 - v0) / (t1 - t0)
        tm = t0
        while tm < t1 - 1e-9:
            end = min(tm + step, t1)
            va, vb = v0 + k * (tm - t0), v0 + k * (end - t0)
            # constant speed with the same output length as this slice of the curve
            lines.append(line(tm, (end - tm) * k / math.log(vb / va)))
            tm = end
    return "\n".join(lines) + "\n"


def ramp_audio_chain(points: Sequence[Point], engine: str = "rubberband") -> str:
    """Audio filters (after asendcmd) for a speed curve starting at points[0]."""
    v0 = points[0][1]
    if engine == "rubberband":
        return f"asendcmd=f=tempo.cmd,rubberband=tempo={v0:.6f}"
    r = v0**0.25
    return "asendcmd=f=tempo.cmd," + ",".join(f"atempo@{n}={r:.6f}" for n in _ATEMPO_STAGES)


def tempo_engine() -> str:
    return "rubberband" if ff.has_filter("rubberband") else "atempo"


def frame_filter(mode: str, fps: str) -> str:
    if mode == "blend":
        return f"framerate=fps={fps}"
    if mode == "flow":
        return f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
    return f"fps={fps}"


@dataclass(frozen=True, slots=True, kw_only=True)
class Speed:
    """A constant *speed* (when *points* is None) or a curve; the picture made
    smooth by *slowmo* ("dup", "blend", "flow") at *rate*; the sound kept at its
    pitch, or played faster/slower like a tape (*keep_pitch* False)."""

    points: Sequence[Point] | None
    speed: float = 1.0
    slowmo: str = "dup"
    rate: str = "30"
    keep_pitch: bool = True
    audio: bool = True
    sample_rate: int = 48000


def speed_graph(s: Speed, engine: str) -> str:
    """filter_complex: [v], and [a] with sound."""
    frames = frame_filter(s.slowmo, s.rate)
    if s.points is None:
        graph = f"[0:v]setpts=(PTS-STARTPTS)/{s.speed:.6f},{frames}[v]"
        if s.audio:
            sr = s.sample_rate
            af = (
                fl.atempo_chain(s.speed, engine)
                if s.keep_pitch
                else f"asetrate={sr * s.speed:.0f},aresample={sr}"
            )
            graph += f";[0:a:0]asetpts=PTS-STARTPTS,{af}[a]"
        return graph
    graph = f"[0:v]setpts=PTS-STARTPTS,setpts='({setpts_expr(s.points)})/TB',{frames}[v]"
    if s.audio:
        graph += f";[0:a:0]asetpts=PTS-STARTPTS,{ramp_audio_chain(s.points, engine)}[a]"
    return graph


@dataclass(frozen=True, slots=True, kw_only=True)
class SpeedJob:
    src: str
    speed: Speed
    engine: str  # "rubberband" or "atempo" (tempo_engine())
    venc: enc.VideoEncode
    container: str
    duration: float  # of the output

    def command(self, out: str) -> list[str]:
        v = self.venc
        graph = speed_graph(self.speed, self.engine)
        vmap = "[v]"
        if v.filter_suffix:
            graph += f";[v]{v.filter_suffix}[venc]"
            vmap = "[venc]"
        audio = [
            "-map",
            "[a]",
            *enc.default_audio(self.container),
        ]
        cmd = [
            ff.ffmpeg_path(),
            "-y",
            *v.pre_input,
            "-i",
            self.src,
            "-filter_complex",
            graph,
            "-map",
            vmap,
        ]
        return [*cmd, *(audio if self.speed.audio else ["-an"]), *v.args, out]

    def run(self, _p: object, _c: object, ctx: TaskContext) -> int:
        workdir = ctx.tempdir()  # the tempo commands are read from here
        s = self.speed
        if s.points is not None and s.audio:
            with open(os.path.join(workdir, "tempo.cmd"), "w", encoding="ascii") as fh:
                fh.write(tempo_commands(s.points, self.engine))
        ctx.run(self.command(ctx.output()), duration=self.duration, cwd=workdir)
        return 0
