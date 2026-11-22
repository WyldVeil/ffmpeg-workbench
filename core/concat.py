"""
core/concat.py - Join clips (or segments of clips) correctly.

Two strategies:

* :func:`copy_join_command` - lossless concat-demuxer join.  Only valid when
  every input has the same codecs and parameters, which
  :func:`copy_compatibility` checks (and explains when it can't).
* :func:`filter_join_command` - one FFmpeg pass with the ``concat`` filter.
  Each input (optionally only an in/out range) is normalised to the same
  size, aspect, frame rate, pixel format and audio layout; inputs without
  audio get generated silence so the joined file never loses its sound.
  No temporary files are written.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl
from core.filters import even_below


@dataclass
class Piece:
    """One input of a join: a whole file or the range [start, end)."""

    path: str
    info: ff.MediaInfo
    start: float = 0.0
    end: float | None = None

    @property
    def length(self) -> float:
        total = (
            self.info.video.duration
            if (self.info.video and self.info.video.duration)
            else self.info.duration
        )
        end = total if self.end is None else min(self.end, total)
        return max(0.0, end - self.start)

    @classmethod
    def of(cls, path: str, start: float = 0.0, end: float | None = None) -> Piece:
        return cls(path, ff.probe(path), start, end)


def copy_compatibility(infos: Sequence[ff.MediaInfo]) -> list[str]:
    """Reasons why the files can NOT be joined losslessly (empty = fine)."""
    problems: list[str] = []
    if not infos:
        return ["no files"]
    ref = infos[0]

    def vkey(i: ff.MediaInfo) -> tuple[object, ...] | None:
        v = i.video
        if not v:
            return None
        return (v.codec, v.profile, v.width, v.height, v.pix_fmt, round(v.fps, 3), v.sar or "1:1")

    def akey(i: ff.MediaInfo) -> tuple[object, ...] | None:
        a = i.audio
        if not a:
            return None
        return (a.codec, a.sample_rate, a.channels)

    for _n, info in enumerate(infos[1:], start=2):
        name = os.path.basename(info.path)
        if vkey(info) != vkey(ref):
            rv, iv = ref.video, info.video
            if not rv or not iv:
                problems.append(f"{name}: video stream missing in one of the files")
            else:
                diffs: list[str] = []
                for label, a, b in (
                    ("codec", rv.codec, iv.codec),
                    ("profile", rv.profile, iv.profile),
                    ("size", f"{rv.width}x{rv.height}", f"{iv.width}x{iv.height}"),
                    ("pixel format", rv.pix_fmt, iv.pix_fmt),
                    ("frame rate", f"{rv.fps:.3f}", f"{iv.fps:.3f}"),
                    ("aspect", rv.sar, iv.sar),
                ):
                    if a != b:
                        diffs.append(f"{label} {b} ≠ {a}")
                problems.append(f"{name}: " + ", ".join(diffs))
        if akey(info) != akey(ref):
            ra, ia = ref.audio, info.audio
            if not ra or not ia:
                problems.append(f"{name}: audio present in only some files")
            else:
                problems.append(
                    f"{name}: audio {ia.codec} {ia.sample_rate} Hz {ia.channels}ch "
                    f"≠ {ra.codec} {ra.sample_rate} Hz {ra.channels}ch"
                )
        if len(info.video_streams) != len(ref.video_streams) or len(info.audio_streams) != len(
            ref.audio_streams
        ):
            problems.append(f"{name}: different number of tracks")
    return problems


def copy_join_command(
    paths: Sequence[str],
    out: str,
    list_path: str,
    inpoints: Sequence[float | None] | None = None,
    outpoints: Sequence[float | None] | None = None,
) -> list[str]:
    fl.write_concat_list(paths, list_path, inpoints=inpoints, outpoints=outpoints)
    container = os.path.splitext(out)[1].lstrip(".").lower()
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_path,
        "-map",
        "0:v?",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-sn",
        "-dn",
    ]
    if container in ("mp4", "m4v", "mov"):
        cmd += ["-movflags", "+faststart"]
    return [*cmd, out]


def target_geometry(pieces: Sequence[Piece], mode: str = "first") -> tuple[int, int, str]:
    """(width, height, fps_str) for a join: ``first`` clip, ``largest`` clip,
    or an explicit ``"1920x1080"``."""
    vids = [p.info.video for p in pieces if p.info.video]
    if not vids:
        return 1280, 720, "30/1"
    if "x" in mode:
        w, h = mode.lower().split("x", 1)
        size = (even_below(int(w)), even_below(int(h)))
    elif mode == "largest":
        best = max(vids, key=lambda v: v.display_size[0] * v.display_size[1])
        size = (even_below(best.display_size[0]), even_below(best.display_size[1]))
    else:
        size = (even_below(vids[0].display_size[0]), even_below(vids[0].display_size[1]))
    fps = str(vids[0].raw.get("avg_frame_rate") or "30/1")
    if fps in ("0/0", "") or not vids[0].fps or vids[0].fps > 240:
        fps = "30/1"
    if mode == "largest":
        fps_v = max(vids, key=lambda v: v.fps or 0)
        fps = str(fps_v.raw.get("avg_frame_rate") or fps)
    return size[0], size[1], fps


def _silent_inputs(pieces: Sequence[Piece], first: int) -> tuple[list[str], dict[int, int]]:
    """Inputs of silence for the pieces without sound, numbered from *first*;
    (arguments, piece -> its silent input)."""
    args: list[str] = []
    silence: dict[int, int] = {}
    for i, p in enumerate(pieces):
        if not p.info.has_audio:
            silence[i] = first + len(silence)
            args += [
                "-f",
                "lavfi",
                "-t",
                ff.ts(p.length),
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
    return args, silence


def _piece_video(i: int, p: Piece, size: tuple[int, int], fps: str, pix: str) -> str:
    """Piece *i*'s picture, cut and fitted (letterboxed) to the join's size and rate."""
    w, h = size
    trim = f"trim=start={p.start:.6f}:duration={p.length:.6f},"
    fit = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:-1:-1:color=black,setsar=1"
    )
    return f"[{i}:v]{trim}setpts=PTS-STARTPTS,{fit},fps={fps},format={pix}[v{i}]"


def _piece_audio(i: int, p: Piece, silent_input: int | None) -> str:
    """Piece *i*'s sound (or silence), cut and made alike: 48 kHz stereo, exactly its length."""
    if silent_input is not None:
        src, atrim = f"[{silent_input}:a]", ""
    else:
        src, atrim = f"[{i}:a]", f"atrim=start={p.start:.6f}:duration={p.length:.6f},"
    alike = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    return f"{src}{atrim}asetpts=PTS-STARTPTS,{alike},apad,atrim=duration={p.length:.6f}[a{i}]"


def _join_graph(
    pieces: Sequence[Piece],
    size: tuple[int, int],
    fps: str,
    venc: enc.VideoEncode,
    silence: dict[int, int] | None,
) -> str:
    """Every piece made alike, then concatenated: [vout], and [aout] when *silence* is given."""
    pix = venc.pix_fmt or "yuv420p"
    graph: list[str] = []
    labels: list[str] = []
    for i, p in enumerate(pieces):
        graph.append(_piece_video(i, p, size, fps, pix))
        labels.append(f"[v{i}]")
        if silence is not None:
            graph.append(_piece_audio(i, p, silence.get(i)))
            labels.append(f"[a{i}]")
    concat = "".join(labels) + f"concat=n={len(pieces)}:v=1:a={1 if silence is not None else 0}"
    aout = "[aout]" if silence is not None else ""
    if venc.filter_suffix:
        graph += [f"{concat}[vc]{aout}", f"[vc]{venc.filter_suffix}[vout]"]
    else:
        graph.append(f"{concat}[vout]{aout}")
    return ";".join(graph)


def filter_join_command(
    pieces: Sequence[Piece],
    out: str,
    *,
    size: tuple[int, int] | None = None,
    fps: str | None = None,
    venc: enc.VideoEncode | None = None,
    audio: bool = True,
    audio_codec: str = "aac",
    audio_bitrate: str = "192k",
) -> tuple[list[str], float]:
    """Single-pass normalising join.  Returns (argv, expected_seconds)."""
    if venc is None:
        venc = enc.video("h264", "high", hw="off", container=os.path.splitext(out)[1] or "mp4")
    if size is None or fps is None:
        w, h, f = target_geometry(pieces)
        size = size or (w, h)
        fps = fps or f
    use_audio = audio and any(p.info.has_audio for p in pieces)
    argv = [ff.ffmpeg_path(), "-y", *venc.pre_input, *(a for p in pieces for a in ("-i", p.path))]
    silence: dict[int, int] | None = None
    if use_audio:
        silent_args, silence = _silent_inputs(pieces, len(pieces))
        argv += silent_args
    argv += ["-filter_complex", _join_graph(pieces, size, fps, venc, silence), "-map", "[vout]"]
    container = os.path.splitext(out)[1].lstrip(".").lower() or "mp4"
    if use_audio:
        argv += ["-map", "[aout]", *enc.audio(audio_codec, audio_bitrate, container=container)]
    else:
        argv += ["-an"]
    argv += [*venc.args, "-sn", "-dn", out]
    return argv, sum(p.length for p in pieces)


def copy_problems(pieces: Sequence[Piece], out: str) -> list[str]:
    """Why the pieces can't be joined without re-encoding into *out* (none: they can)."""
    problems = copy_compatibility([p.info for p in pieces])
    container = os.path.splitext(out)[1].lstrip(".").lower()
    src_ext = os.path.splitext(pieces[0].path)[1].lstrip(".").lower() if pieces else ""
    first = pieces[0].info if pieces else None
    if (
        not problems
        and first is not None
        and first.audio
        and container in ("mp4", "mov", "m4v")
        and src_ext not in ("mp4", "mov", "m4v")
        and not enc.audio_ok_for(
            container, enc.audio_key_from_probe(first.require_audio().codec) or "x"
        )
    ):
        problems.append(f"{first.require_audio().codec} audio can't be stored in .{container}")
    return problems
