"""
core/ops/deshake.py - the Deshaker (tabs/visuals/deshaker.py): two-pass vid.stab
(analyse the camera's motion, then smooth it), or the single-pass deshake filter
on builds without vid.stab; optionally side by side with the original.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core import encoders as enc
from core import ffmpeg as ff
from core.queue_manager import TaskContext

TRF = "transforms.trf"  # relative: pass 1 and 2 run inside the job's temp dir


def detect_filter(shakiness: int, accuracy: int, tripod: bool) -> str:
    f = f"vidstabdetect=shakiness={shakiness}:accuracy={accuracy}:result={TRF}"
    return f + (":tripod=1" if tripod else "")


def transform_filter(
    smoothing: int, zoom_mode: int, zoom: float, edges: str, tripod: bool, sharpen: bool
) -> str:
    f = (
        f"vidstabtransform=input={TRF}:smoothing={smoothing}:optzoom={zoom_mode}"
        f":zoom={zoom:g}:crop={edges}:interpol=bicubic"
    )
    if tripod:
        f += ":tripod=1"
    if sharpen:
        f += ",unsharp=5:5:0.8:3:3:0.4"
    return f


def fallback_filter(edges: str) -> str:
    return f"deshake=rx=32:ry=32:edge={'blank' if edges == 'black' else 'clamp'}"


def vidstab_available() -> bool:
    return ff.has_filter("vidstabdetect") and ff.has_filter("vidstabtransform")


@dataclass(frozen=True, slots=True, kw_only=True)
class Stabilise:
    shakiness: int = 5
    accuracy: int = 15
    smoothing: int = 10
    zoom_mode: int = 1  # vid.stab optzoom: 0 none, 1 static, 2 adaptive
    zoom: float = 0.0  # extra zoom, %
    edges: str = "keep"  # "keep" (previous frame) or "black"
    tripod: bool = False
    sharpen: bool = False
    compare: bool = False  # the original on the left, stabilised on the right


def stabilise_filter(s: Stabilise, *, two_pass: bool) -> str:
    if two_pass:
        return transform_filter(s.smoothing, s.zoom_mode, s.zoom, s.edges, s.tripod, s.sharpen)
    return fallback_filter(s.edges)


def detect_cmd(src: str, s: Stabilise) -> list[str]:
    """Pass 1: the camera's motion, written to TRF in the working folder."""
    vf = detect_filter(s.shakiness, s.accuracy, s.tripod)
    return [ff.ffmpeg_path(), "-y", "-i", src, "-an", "-sn", "-vf", vf, "-f", "null", "-"]


def encode_cmd(
    src: str, s: Stabilise, venc: enc.VideoEncode, audio: Sequence[str], out: str, *, two_pass: bool
) -> list[str]:
    stab = stabilise_filter(s, two_pass=two_pass)
    if s.compare:
        graph = f"[0:v]setsar=1,split[a][b];[b]{stab},setsar=1[s];[a][s]hstack=inputs=2[v]"
    else:
        graph = f"[0:v]{stab}[v]"
    vlabel = "[v]"
    if venc.filter_suffix:
        graph += f";[v]{venc.filter_suffix}[venc]"
        vlabel = "[venc]"
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        *venc.pre_input,
        "-i",
        src,
        "-filter_complex",
        graph,
        "-map",
        vlabel,
    ]
    return [*cmd, *audio, *venc.args, out]


@dataclass(frozen=True, slots=True, kw_only=True)
class DeshakeJob:
    src: str
    settings: Stabilise
    two_pass: bool
    venc: enc.VideoEncode
    audio: Sequence[str]
    duration: float | None

    def run(self, _p: object, _c: object, ctx: TaskContext) -> int:
        workdir = ctx.tempdir()  # both passes run here: TRF is relative
        if self.two_pass:
            ctx.log("Pass 1 of 2 - analysing camera motion…")
            ctx.span(0.0, 0.4).run(
                detect_cmd(self.src, self.settings), duration=self.duration, cwd=workdir
            )
            ctx.log("Pass 2 of 2 - stabilising and encoding…")
            ctx.span(0.4, 1.0)
        cmd = encode_cmd(
            self.src, self.settings, self.venc, self.audio, ctx.output(), two_pass=self.two_pass
        )
        ctx.run(cmd, duration=self.duration, cwd=workdir)
        return 0
