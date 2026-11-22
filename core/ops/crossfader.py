"""
core/ops/crossfader.py - command builders for tabs/cutting/crossfader.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from core import encoders as enc
from core import ffmpeg as ff
from core.i18n import t
from core.ops import transitions

TRANS_DEFAULT = "fade"


def _tr(key: str, fallback: str) -> str:
    text = t(key)
    return fallback if text == key else text


@dataclass
class Clip:
    path: str
    duration: float  # video length in seconds
    width: int
    height: int
    fps: float
    fps_str: str  # exact rational, e.g. "30000/1001"
    has_audio: bool
    summary: str = ""

    @classmethod
    def from_info(cls, info: ff.MediaInfo) -> Clip:
        v = info.video
        if v is None:
            raise ValueError(_tr("crossfader.no_video_stream", "No video stream in this file."))
        dur = v.duration if v.duration > 0.05 else info.duration
        rate = v.raw.get("avg_frame_rate") or v.raw.get("r_frame_rate") or "30/1"
        if not v.fps or v.fps > 240 or rate in ("0/0", ""):
            rate = "30/1"
        w, h = v.display_size
        return cls(
            path=info.path,
            duration=float(dur),
            width=w,
            height=h,
            fps=v.fps or 30.0,
            fps_str=rate,
            has_audio=info.has_audio,
            summary=info.summary(),
        )


def _even(n: float) -> int:
    return max(2, round(n) // 2 * 2)


def segment_lengths(clips: Sequence[Clip], T: float, freeze: bool) -> list[float]:
    """Length of each clip as it enters the xfade chain (freeze pads it)."""
    n = len(clips)
    out: list[float] = []
    for i, c in enumerate(clips):
        length = c.duration
        if freeze:
            length += (T if i > 0 else 0.0) + (T if i < n - 1 else 0.0)
        out.append(length)
    return out


def validate(clips: Sequence[Clip], T: float, freeze: bool) -> str | None:
    """Return a user-facing problem description, or None when renderable."""
    if len(clips) < 2:
        return _tr("crossfader.need_two", "Add at least two clips.")
    if not (0.04 <= T <= 30):
        return _tr(
            "crossfader.bad_duration", "The transition must last between 0.04 and 30 seconds."
        )
    if freeze:
        return None
    n = len(clips)
    for i, c in enumerate(clips):
        need = T if i in (0, n - 1) else 2 * T
        if c.duration + 1e-3 < need:
            return _tr(
                "crossfader.clip_too_short",
                "Clip {n} is {d:.2f} s long but needs at least {need:.2f} s for "
                "{t:.2f} s transitions. Shorten the transition or use Freeze mode.",
            ).format(n=i + 1, d=c.duration, need=need, t=T)
    return None


def total_duration(clips: Sequence[Clip], T: float, freeze: bool) -> float:
    lengths = segment_lengths(clips, T, freeze)
    return max(0.0, sum(lengths) - T * (len(clips) - 1))


@dataclass(frozen=True, slots=True)
class Crossfade:
    """How clips blend into each other.

    *seconds*: each transition's length; *transitions*: one xfade name per join
    (missing ones fade); *freeze* holds the edge frames instead of overlapping
    the clips; *audio_mode* 'normal', 'hq' or 'none'; *audio_curve* the
    acrossfade curve."""

    seconds: float
    transitions: Sequence[str] = ()
    freeze: bool = False
    audio_mode: str = "normal"
    audio_curve: str = "tri"


@dataclass(frozen=True, slots=True)
class _Plan:
    """What every part of the crossfade graph needs."""

    clips: Sequence[Clip]
    fade: Crossfade
    width: int
    height: int
    pix: str
    lengths: list[float]  # each clip's length as it enters the xfade chain


def _silent_inputs(clips: Sequence[Clip]) -> tuple[list[str], dict[int, int]]:
    """Silent tracks for clips without sound: (input args, {clip: its silent input})."""
    args: list[str] = []
    silent: dict[int, int] = {}
    for i, c in enumerate(clips):
        if not c.has_audio:
            silent[i] = len(clips) + len(silent)
            args += [
                "-f",
                "lavfi",
                "-t",
                ff.ts(c.duration),
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
    return args, silent


def _video_graph(p: _Plan, suffix: str) -> list[str]:
    T, n, fps = p.fade.seconds, len(p.clips), p.clips[0].fps_str
    graph: list[str] = []
    for i, c in enumerate(p.clips):
        chain = (
            f"[{i}:v]setpts=PTS-STARTPTS,"
            f"scale={p.width}:{p.height}:force_original_aspect_ratio=decrease,"
            f"pad={p.width}:{p.height}:-1:-1:color=black,setsar=1,fps={fps},"
            f"trim=duration={c.duration:.6f},setpts=PTS-STARTPTS,format={p.pix}"
        )
        if p.fade.freeze:
            pads = [f"start_mode=clone:start_duration={T:.6f}"] if i > 0 else []
            pads += [f"stop_mode=clone:stop_duration={T:.6f}"] if i < n - 1 else []
            chain += ",tpad=" + ":".join(pads)
        graph.append(chain + f",settb=AVTB[v{i}]")
    last, offset = "[v0]", 0.0
    for i in range(1, n):
        offset += p.lengths[i - 1] - T
        trans = p.fade.transitions[i - 1] if i - 1 < len(p.fade.transitions) else TRANS_DEFAULT
        graph.append(f"{last}[v{i}]{transitions.xfade(trans, T, offset, digits=6)}[x{i}]")
        last = f"[x{i}]"
    graph.append(f"{last}{suffix or 'null'}[vout]")
    return graph


def _audio_graph(p: _Plan, silent: dict[int, int]) -> list[str]:
    T, n, curve = p.fade.seconds, len(p.clips), p.fade.audio_curve
    graph: list[str] = []
    for i, c in enumerate(p.clips):
        src = f"[{silent[i]}:a]" if i in silent else f"[{i}:a]"
        chain = (
            f"{src}aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS,apad,atrim=duration={c.duration:.6f}"
        )
        if p.fade.freeze:
            chain += f",adelay=delays={round(T * 1000)}:all=1" if i > 0 else ""
            chain += f",apad=pad_dur={T:.6f}" if i < n - 1 else ""
            chain += f",atrim=duration={p.lengths[i]:.6f}"
        graph.append(chain + f",asetpts=PTS-STARTPTS[a{i}]")
    last = "[a0]"
    for i in range(1, n):
        label = f"[ax{i}]" if i < n - 1 else "[aout]"
        graph.append(f"{last}[a{i}]acrossfade=d={T:.6f}:c1={curve}:c2={curve}{label}")
        last = label
    return graph


def _audio_encoding(out: str, audio_mode: str) -> list[str]:
    container = os.path.splitext(out)[1].lstrip(".").lower() or "mp4"
    if container == "webm":
        return enc.audio("opus", "256k" if audio_mode == "hq" else "192k", container=container)
    return enc.audio("aac", "320k" if audio_mode == "hq" else "192k", container=container)


def build_command(
    clips: Sequence[Clip],
    out: str,
    fade: Crossfade,
    *,
    venc: enc.VideoEncode | None = None,
    size: tuple[int, int] | None = None,
) -> tuple[list[str], float]:
    """Build the FFmpeg argv for the whole crossfade.  Returns (argv, seconds)."""
    if venc is None:
        venc = enc.video("h264", "high", hw="off", container=os.path.splitext(out)[1] or "mp4")
    W, H = size or (_even(clips[0].width), _even(clips[0].height))
    plan = _Plan(
        clips,
        fade,
        W,
        H,
        venc.pix_fmt or "yuv420p",
        segment_lengths(clips, fade.seconds, fade.freeze),
    )
    use_audio = fade.audio_mode != "none" and any(c.has_audio for c in clips)
    argv = [ff.ffmpeg_path(), "-y", *venc.pre_input]
    for c in clips:
        argv += ["-i", c.path]
    graph = _video_graph(plan, venc.filter_suffix)
    if use_audio:
        silent_args, silent = _silent_inputs(clips)
        argv += silent_args
        graph += _audio_graph(plan, silent)
    argv += ["-filter_complex", ";".join(graph), "-map", "[vout]"]
    argv += ["-map", "[aout]", *_audio_encoding(out, fade.audio_mode)] if use_audio else ["-an"]
    argv += [*venc.args, "-map_metadata", "-1", "-sn", out]
    return argv, total_duration(clips, fade.seconds, fade.freeze)


def preview_cut(clips: Sequence[Clip], fade: Crossfade, folder: str) -> str:
    """A small, quick render of the cut between two clips (with a little of each
    either side) in *folder*; its path."""
    a, b = clips
    around = fade.seconds + 1.5
    parts: list[Clip] = []
    for i, (c, start) in enumerate(((a, max(0.0, a.duration - around)), (b, 0.0))):
        piece = os.path.join(folder, f"{i}.mp4")
        cut = [
            ff.ffmpeg_path(),
            "-hide_banner",
            "-nostdin",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{around:.3f}",
            "-i",
            c.path,
        ]
        small = [
            "-vf",
            "scale=-2:360",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "26",
            "-c:a",
            "aac",
            piece,
        ]
        res = ff.run([*cut, *small])
        if res.returncode != 0:
            raise RuntimeError("\n".join(res.log_tail[-4:]))
        parts.append(Clip.from_info(ff.probe(piece)))
    out = os.path.join(folder, "preview.mp4")
    venc = enc.video("h264", "low", speed="fastest", hw="off", container="mp4")
    argv, _secs = build_command(parts, out, fade, venc=venc)
    res = ff.run(argv)
    if res.returncode != 0:
        raise RuntimeError("\n".join(res.log_tail[-4:]))
    return out
