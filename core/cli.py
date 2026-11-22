"""
core/cli.py  -  Command line for scripts and AI assistants

    ffmpeg-workbench --cli tools [--json]          what can be done
    ffmpeg-workbench --cli info FILE [--json]      what a file contains
    ffmpeg-workbench --cli paths [--json]          the bundled FFmpeg, yt-dlp...
    ffmpeg-workbench --cli OPERATION INPUT... [options] [-o OUTPUT] [--json]

(From the source tree: ``python main.py --cli ...``.)

Each operation runs the same code as the matching tool in the app.  Rules:
an input is never changed; an existing file is never replaced unless
--overwrite is given; the result is written to a temporary file first and
renamed only when it succeeded; progress goes to stderr; with --json the
last line on stdout is {"ok": true, "output": ..., ...} or
{"ok": false, "error": ...}.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, overload

from core import ffmpeg as ff
from core import proc as _proc
from core.encoders import VideoEncode
from core.errors import InputError
from core.ops.shared import Steps

logger = logging.getLogger("crossfader.cli")

Options = Callable[[argparse.ArgumentParser], None]  # adds an operation's arguments
Result = dict[str, Any]  # what an operation reports (printed as JSON)
Run = Callable[["Ctx"], Result]


@dataclass(frozen=True, slots=True)
class Operation:
    name: str
    summary: str
    category: str
    options: Options | None
    run: Run


OPERATIONS: dict[str, Operation] = {}


class CliError(InputError):
    """A command-line request that can't be done; the message says why."""


def operation(
    name: str, summary: str, category: str, *, options: Options | None = None
) -> Callable[[Run], Run]:
    """Register the decorated function as the operation *name*."""

    def register(fn: Run) -> Run:
        OPERATIONS[name] = Operation(name, summary, category, options, fn)
        return fn

    return register


# ── Shared helpers ────────────────────────────────────────────────────────────


class Ctx:
    """What an operation needs: paths, progress, a scratch folder."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.notes: list[str] = []
        self._tmp: str | None = None
        self._last = -10

    def tmp(self) -> str:
        if self._tmp is None:
            self._tmp = tempfile.mkdtemp(prefix="wme_cli_")
        return self._tmp

    def cleanup(self) -> None:
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)

    def log(self, text: str) -> None:
        print(text, file=sys.stderr, flush=True)

    def run(
        self,
        cmd: list[str],
        duration: float | None = None,
        lo: float = 0.0,
        hi: float = 1.0,
        lines: list[str] | None = None,
    ) -> ff.RunResult:
        """Run one FFmpeg command; progress to stderr.  *lines*, when given,
        receives every log line (for detectors whose output can be long)."""

        def prog(p: ff.Progress) -> None:
            frac = p.fraction
            if frac is None and duration and p.out_time:
                frac = min(1.0, p.out_time / duration)
            if frac is not None:
                pct = int((lo + (hi - lo) * frac) * 100)
                if pct >= self._last + 5:
                    self._last = pct
                    print(f"progress {pct}%", file=sys.stderr, flush=True)

        res = ff.run(
            cmd,
            duration=duration,
            on_progress=prog,
            on_line=lines.append if lines is not None else None,
        )
        if res.returncode != 0:
            raise CliError("FFmpeg failed:\n" + "\n".join(res.log_tail[-8:]))
        return res

    def render_chain(
        self,
        src: str,
        info: ff.MediaInfo,
        out: str,
        chain: str,
        *,
        pre: Sequence[str] | None = None,
        audio: Sequence[str] | None = None,
        extra: Sequence[str] | None = None,
        duration: float | None = None,
    ) -> None:
        """ffmpeg -i src -vf <chain> <encoder> <audio> out (atomically)."""
        container = container_of(out)
        ve = venc(self, container)
        part = part_of(out)
        cmd = [
            ff.ffmpeg_path(),
            "-y",
            *(pre or []),
            *ve.pre_input,
            "-i",
            src,
            "-map",
            "0:v:0",
            *ve.vf_args(chain or None),
        ]
        cmd += list(audio if audio is not None else keep_audio(info, container))
        cmd += [*(extra or []), *ve.args, "-map_metadata", "0", part]
        self.run(cmd, duration or info.duration)
        finish(part, out)

    def render_graph(
        self,
        inputs: Sequence[str],
        info: ff.MediaInfo,
        out: str,
        graph: str,
        *,
        has_audio: bool,
        pre: Sequence[str] | None = None,
        duration: float | None = None,
        input_opts: Mapping[int, Sequence[str]] | None = None,
    ) -> None:
        """ffmpeg <inputs> -filter_complex <graph> (-> [v], and [a] when *has_audio*)
        <encoder> out (atomically); *input_opts* go before input n's ``-i``."""
        from core import encoders as enc

        container = container_of(out)
        ve = venc(self, container)
        part = part_of(out)
        cmd = [ff.ffmpeg_path(), "-y", *(pre or []), *ve.pre_input]
        for i, path in enumerate(inputs):
            cmd += [*list((input_opts or {}).get(i, [])), "-i", path]
        if ve.filter_suffix:
            graph += f";[v]{ve.filter_suffix}[venc]"
        cmd += ["-filter_complex", graph, "-map", "[venc]" if ve.filter_suffix else "[v]"]
        cmd += ["-map", "[a]", *enc.default_audio(container)] if has_audio else ["-an"]
        cmd += [*ve.args]
        if duration:
            cmd += ["-t", f"{duration:.3f}"]
        cmd += [part]
        self.run(cmd, duration or info.duration)
        finish(part, out)


def _same(a: str, b: str) -> bool:
    try:
        return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


def output_path(ctx: Ctx, inputs: Sequence[str], suffix: str, ext: str | None = None) -> str:
    """The output file: -o, or '<first input> (suffix).ext' next to it."""
    args = ctx.args
    if args.output:
        out = os.path.abspath(args.output)
        if os.path.isdir(out):
            base = os.path.splitext(os.path.basename(inputs[0]))[0] if inputs else "output"
            out = os.path.join(out, f"{base}_{suffix}{ext or os.path.splitext(inputs[0])[1]}")
    else:
        src = inputs[0]
        base, src_ext = os.path.splitext(os.path.abspath(src))
        out = f"{base}_{suffix}{ext or src_ext}"
        n = 2
        while os.path.exists(out):
            out = f"{base}_{suffix}_{n}{ext or src_ext}"
            n += 1
    if ext and not out.lower().endswith(ext.lower()) and not args.output:
        out = os.path.splitext(out)[0] + ext
    for i in inputs:
        if _same(i, out):
            raise CliError(f"The output would replace the input {i} - choose another -o.")
    if os.path.exists(out) and not args.overwrite:
        raise CliError(f"{out} already exists - add --overwrite to replace it.")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    return out


def part_of(out: str) -> str:
    base, ext = os.path.splitext(out)
    return f"{base}.part{ext}"


def finish(part: str, out: str) -> None:
    if not os.path.isfile(part) or os.path.getsize(part) == 0:
        raise CliError("No output was produced.")
    os.replace(part, out)


def probe(path: str, video: bool = False) -> ff.MediaInfo:
    if not os.path.isfile(path):
        raise CliError(f"File not found: {path}")
    info = ff.try_probe(path)
    if info is None:
        raise CliError(f"Can't read {path} as audio or video.")
    if video and not info.has_video:
        raise CliError(f"{path} has no video.")
    return info


def venc(ctx: Ctx, container: str) -> VideoEncode:
    from core import encoders as enc

    a = ctx.args
    return enc.video(
        getattr(a, "codec", "h264"),
        getattr(a, "quality", "high"),
        speed=getattr(a, "speed", "balanced"),
        hw=getattr(a, "gpu", "auto"),
        container=container,
    )


def container_of(out: str) -> str:
    return os.path.splitext(out)[1].lstrip(".").lower() or "mp4"


def keep_audio(info: ff.MediaInfo, container: str, filters: str = "") -> list[str]:
    from core.ops.shared import keep_audio_args

    return keep_audio_args(info, container, filters=filters)


def result(out: str, **extra: Any) -> Result:
    info = ff.try_probe(out, use_cache=False) if os.path.isfile(out) else None
    d: Result = {"ok": True, "output": out}
    if os.path.isfile(out):
        d["size_bytes"] = os.path.getsize(out)
    if info is not None:
        d["duration"] = round(info.duration, 3)
        if info.has_video:
            d["video"] = (
                f"{info.require_video().codec} {info.require_video().width}x{info.require_video().height} {info.require_video().fps:.3g}fps"
            )
        if info.has_audio:
            d["audio"] = (
                f"{info.require_audio().codec} {info.require_audio().channels}ch {info.require_audio().sample_rate}Hz"
            )
    d.update(extra)
    return d


@overload
def time_value(text: str | None, default: float) -> float: ...
@overload
def time_value(text: str | None, default: None = None) -> float | None: ...


def time_value(text: str | None, default: float | None = None) -> float | None:
    if text in (None, ""):
        return default
    try:
        return ff.parse_time(str(text))
    except ValueError:
        raise CliError(f"Not a time: {text} (use seconds or HH:MM:SS)") from None


def _video_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--codec",
        default="h264",
        choices=["h264", "hevc", "av1", "vp9", "prores", "ffv1"],
        help="video codec (default h264 - plays everywhere)",
    )
    p.add_argument(
        "--quality", default="high", choices=["very_high", "high", "good", "low", "lowest"]
    )
    p.add_argument(
        "--speed", default="balanced", choices=["fastest", "fast", "balanced", "slow", "slowest"]
    )
    p.add_argument(
        "--gpu",
        default="auto",
        choices=["auto", "off", "nvenc", "qsv", "vaapi", "amf", "videotoolbox"],
        help="use a GPU encoder when available (auto) or not (off)",
    )


# ── Information ───────────────────────────────────────────────────────────────


def describe_file(path: str) -> Result:
    from core import metaclean as mc

    info = probe(path)
    streams: list[dict[str, Any]] = []
    d: Result = {
        "ok": True,
        "file": os.path.abspath(path),
        "format": info.format_name,
        "duration": round(info.duration, 3),
        "size_bytes": info.size,
        "streams": streams,
    }
    for s in info.streams:
        e: dict[str, Any] = {"index": s.index, "type": s.type, "codec": s.codec}
        if s.type == "video":
            e.update(
                width=s.width,
                height=s.height,
                fps=round(s.fps or 0, 3),
                pix_fmt=s.pix_fmt,
                hdr=s.is_hdr,
                rotation=s.rotation,
                cover_art=bool(s.attached_pic),
            )
        elif s.type == "audio":
            e.update(
                channels=s.channels, sample_rate=s.sample_rate, language=s.tags.get("language", "")
            )
        elif s.type == "subtitle":
            e.update(language=s.tags.get("language", ""), title=s.tags.get("title", ""))
        streams.append(e)
    d["chapters"] = len(info.chapters or [])
    d["personal_metadata"] = [f.line() for f in mc.inspect(info)]
    return d


def tool_paths() -> dict[str, str | None]:
    from core.hardware import find_binary

    return {
        name: find_binary(name)
        for name in ("ffmpeg", "ffprobe", "ffplay", "yt-dlp", "deno", "whisper-cli", "fluidsynth")
    }


# ── Operations ────────────────────────────────────────────────────────────────
# Each takes (ctx) and returns a result dict.  Grouped like the app's sidebar.


def _in(p: argparse.ArgumentParser, n: str | int = "+") -> None:
    p.add_argument("inputs", nargs=n, metavar="INPUT", help="input file(s)")


def _trim_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--start", default="0")
    p.add_argument("--end")
    p.add_argument("--copy", action="store_true", help="no re-encode (starts at a keyframe)")
    _video_opts(p)


@operation(
    "trim",
    "Keep only part of a video (exact, or --copy for a fast keyframe cut)",
    "Cut",
    options=_trim_options,
)
def op_trim(ctx: Ctx) -> Result:
    src = ctx.args.inputs[0]
    info = probe(src)
    a, b = time_value(ctx.args.start, 0.0), time_value(ctx.args.end, info.duration)
    if b <= a:
        raise CliError("--end must be after --start.")
    out = output_path(ctx, [src], "trim")
    part = part_of(out)
    if ctx.args.copy:
        ctx.run(
            [
                ff.ffmpeg_path(),
                "-y",
                "-ss",
                f"{a:.3f}",
                "-to",
                f"{b:.3f}",
                "-i",
                src,
                "-map",
                "0",
                "-c",
                "copy",
                "-avoid_negative_ts",
                "make_zero",
                part,
            ],
            b - a,
        )
        finish(part, out)
    else:
        ctx.render_chain(
            src, info, out, "", pre=["-ss", f"{a:.3f}", "-to", f"{b:.3f}"], duration=b - a
        )
    return result(out)


def _join_options(p: argparse.ArgumentParser) -> None:
    _in(p)
    p.add_argument("--copy", action="store_true", help="require a lossless join")
    _video_opts(p)


@operation(
    "join",
    "Join clips one after another (lossless when they match, else normalised)",
    "Cut",
    options=_join_options,
)
def op_join(ctx: Ctx) -> Result:
    from core import concat

    paths = ctx.args.inputs
    if len(paths) < 2:
        raise CliError("Give at least two files to join.")
    infos = [probe(x) for x in paths]
    out = output_path(ctx, paths, "joined")
    part = part_of(out)
    problems = concat.copy_compatibility(infos)
    if not problems:
        ctx.run(
            concat.copy_join_command(paths, part, os.path.join(ctx.tmp(), "list.txt")),
            sum(i.duration for i in infos),
        )
        finish(part, out)
        return result(out, method="lossless")
    if ctx.args.copy:
        raise CliError("These clips can't be joined losslessly: " + "; ".join(problems))
    pieces = [concat.Piece(x, i) for x, i in zip(paths, infos, strict=False)]
    argv, secs = concat.filter_join_command(pieces, part, venc=venc(ctx, container_of(out)))
    ctx.run(argv, secs)
    finish(part, out)
    return result(out, method="re-encoded (" + "; ".join(problems) + ")")


def _transition_options(p: argparse.ArgumentParser) -> None:
    _in(p)
    p.add_argument("--type", default="fade")
    p.add_argument("--duration", type=float, default=1.0)
    _video_opts(p)


@operation(
    "transition",
    "Join two clips with a transition (fade, wipeleft, circleopen, ...)",
    "Cut",
    options=_transition_options,
)
def op_transition(ctx: Ctx) -> Result:
    from core.ops import crossfader as xf

    paths = ctx.args.inputs
    if len(paths) < 2:
        raise CliError("Give two (or more) clips.")
    clips = [xf.Clip.from_info(probe(x, video=True)) for x in paths]
    problem = xf.validate(clips, ctx.args.duration, False)
    if problem:
        raise CliError(problem)
    out = output_path(ctx, paths, "transition")
    part = part_of(out)
    fade = xf.Crossfade(ctx.args.duration, [ctx.args.type] * (len(clips) - 1))
    argv, secs = xf.build_command(clips, part, fade, venc=venc(ctx, container_of(out)))
    ctx.run(argv, secs)
    finish(part, out)
    return result(out)


def _rotate_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--degrees", type=int, default=90, choices=[0, 90, 180, 270])
    p.add_argument("--flip", choices=["h", "v"])
    p.add_argument("--lossless", action="store_true")
    _video_opts(p)


@operation(
    "rotate",
    "Rotate (90/180/270) and/or mirror; --lossless only changes the orientation flag",
    "Cut",
    options=_rotate_options,
)
def op_rotate(ctx: Ctx) -> Result:
    from core.ops import rotate_flip as rf

    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    out = output_path(ctx, [src], "rotated")
    h, v = ctx.args.flip == "h", ctx.args.flip == "v"
    if ctx.args.lossless:
        part = part_of(out)
        ctx.run(
            [
                ff.ffmpeg_path(),
                "-y",
                *rf.lossless_args(info.require_video().rotation, ctx.args.degrees, h, v),
                "-i",
                src,
                "-map",
                "0",
                "-c",
                "copy",
                part,
            ],
            info.duration,
        )
        finish(part, out)
    else:
        ctx.render_chain(src, info, out, rf.reencode_chain(ctx.args.degrees, h, v))
    return result(out)


def _crop_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--rect")
    p.add_argument("--aspect")
    p.add_argument("--auto", action="store_true")
    _video_opts(p)


@operation(
    "crop",
    "Crop to a rectangle (--rect X:Y:W:H), an aspect (--aspect 9:16) or remove black bars (--auto)",
    "Cut",
    options=_crop_options,
)
def op_crop(ctx: Ctx) -> Result:
    from core.ops import crop as cr

    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    w, h = info.require_video().display_size
    if ctx.args.rect:
        x, y, cw, ch = (int(v) for v in ctx.args.rect.replace(",", ":").split(":"))
    elif ctx.args.aspect:
        rw, rh = (int(v) for v in ctx.args.aspect.replace("/", ":").split(":"))
        x, y, cw, ch = cr.fit_aspect(w, h, rw, rh)
    elif ctx.args.auto:
        from core.ops import auto_cropper

        box = auto_cropper.detect_bars(src, info.duration)
        if not box:
            raise CliError("No black bars found.")
        x, y, cw, ch = box
    else:
        raise CliError("Give --rect, --aspect or --auto.")
    out = output_path(ctx, [src], "cropped")
    ctx.render_chain(src, info, out, f"crop={cw // 2 * 2}:{ch // 2 * 2}:{x}:{y},setsar=1")
    return result(out, crop=[x, y, cw, ch])


def _speed_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--factor", type=float, required=True)
    _video_opts(p)


@operation(
    "speed",
    "Play faster or slower (--factor 2 = twice as fast), sound kept in pitch",
    "Cut",
    options=_speed_options,
)
def op_speed(ctx: Ctx) -> Result:
    from core.filters import atempo_chain

    src = ctx.args.inputs[0]
    info = probe(src)
    f = ctx.args.factor
    if not 0.1 <= f <= 100:
        raise CliError("--factor must be between 0.1 and 100.")
    out = output_path(ctx, [src], f"x{f:g}")
    container = container_of(out)
    audio = keep_audio(info, container, filters=atempo_chain(f)) if info.has_audio else ["-an"]
    if info.has_video:
        ctx.render_chain(
            src, info, out, f"setpts=PTS/{f:g}", audio=audio, duration=info.duration / f
        )
    else:
        part = part_of(out)
        ctx.run([ff.ffmpeg_path(), "-y", "-i", src, *audio, part], info.duration / f)
        finish(part, out)
    return result(out)


def _reverse_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    _video_opts(p)


@operation(
    "reverse",
    "Play backwards (video and sound; clips up to 10 minutes)",
    "Cut",
    options=_reverse_options,
)
def op_reverse(ctx: Ctx) -> Result:
    src = ctx.args.inputs[0]
    info = probe(src)
    if info.duration > 600:
        raise CliError(
            "Use the app's Video Reverser for clips over 10 minutes (it works in memory-safe chunks)."
        )
    out = output_path(ctx, [src], "reversed")
    audio = keep_audio(info, container_of(out), filters="areverse") if info.has_audio else ["-an"]
    ctx.render_chain(src, info, out, "reverse", audio=audio)
    return result(out)


def _remove_silence_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--threshold", type=float, default=-40.0, help="dB")
    p.add_argument("--min-silence", type=float, default=0.6)
    p.add_argument("--padding", type=float, default=0.15)
    _video_opts(p)


@operation(
    "remove-silence",
    "Cut out silent parts (talking-head videos, podcasts)",
    "Audio",
    options=_remove_silence_options,
)
def op_remove_silence(ctx: Ctx) -> Result:
    from core.ops import silence as sl

    src = ctx.args.inputs[0]
    info = probe(src)
    if not info.has_audio:
        raise CliError("The file has no sound to look for silence in.")
    lines: list[str] = []
    ctx.run(
        sl.detect_cmd(src, ctx.args.threshold, ctx.args.min_silence),
        info.duration,
        0.0,
        0.3,
        lines=lines,
    )
    silences = sl.parse_silences("\n".join(lines), info.duration)
    zones = sl.keep_zones(
        silences,
        info.duration,
        ctx.args.padding,
        fps=info.require_video().fps if info.has_video else 0,
    )
    if not zones:
        raise CliError("Everything is silent at that threshold.")
    out = output_path(ctx, [src], "nosilence")
    graph = sl.cut_graph(zones, info.has_video, info.has_audio)
    if info.has_video:
        ctx.render_graph(
            [src], info, out, graph, has_audio=True, duration=sum(b - a for a, b in zones)
        )
    else:
        part = part_of(out)
        from core.ops.shared import audio_encoder_args

        ctx.run(
            [
                ff.ffmpeg_path(),
                "-y",
                "-i",
                src,
                "-filter_complex",
                graph,
                "-map",
                "[a]",
                *audio_encoder_args(out),
                part,
            ],
            sum(b - a for a, b in zones),
            0.3,
            1.0,
        )
        finish(part, out)
    return result(out, removed_seconds=round(info.duration - sum(b - a for a, b in zones), 2))


def _compress_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--size", required=True, help="e.g. 10MB, 25MB, 500KB")
    p.add_argument("--codec", default="h264", choices=["h264", "hevc", "av1"])
    p.add_argument("--fast", action="store_true", help="GPU encoding, less exact")
    p.add_argument("--audio", default="auto", choices=["auto", "copy", "none"])
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--keep-resolution", action="store_true")
    p.add_argument("--keep-framerate", action="store_true")


@operation(
    "compress",
    "Make a video fit a size limit (e.g. --size 10MB for Discord) at the best quality",
    "Convert",
    options=_compress_options,
)
def op_compress(ctx: Ctx) -> Result:
    from core.ops import downsizer as ds

    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    text = ctx.args.size.strip().upper().replace(" ", "")
    mult = (
        1_000_000
        if text.endswith("MB")
        else 1_000
        if text.endswith("KB")
        else 1_000_000_000
        if text.endswith("GB")
        else 1_000_000
    )
    try:
        target = int(float(text.rstrip("MKGB")) * mult)
    except ValueError:
        raise CliError("--size like 10MB") from None
    a, b = time_value(ctx.args.start, 0.0), time_value(ctx.args.end, info.duration)
    w, h = info.require_video().display_size
    try:
        goal = ds.Goal(
            target,
            codec=ctx.args.codec,
            audio_mode=ctx.args.audio,
            allow_res=not ctx.args.keep_resolution,
            allow_fps=not ctx.args.keep_framerate,
        )
        plan = ds.plan(
            goal,
            b - a,
            (w // 2 * 2, h // 2 * 2),
            info.require_video().fps,
            has_audio=info.has_audio,
            audio_src_kbps=int((info.require_audio().bit_rate or 0) / 1000)
            if info.has_audio
            else 0,
        )
    except ValueError as exc:
        raise CliError(str(exc)) from None
    for n in plan.notes:
        ctx.log(n)
    out = output_path(ctx, [src], "small", ".mp4")
    part = part_of(out)
    steps = Steps(ctx.run, ctx.log, ctx.tmp())
    ds.encode_to_size(src, part, info, plan, steps, fast=ctx.args.fast, start=a, end=b)
    finish(part, out)
    return result(out, target_bytes=target, resolution=f"{plan.width}x{plan.height}")


def _convert_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument(
        "--to",
        required=True,
        choices=[
            "mp4",
            "mkv",
            "mov",
            "webm",
            "m4v",
            "ts",
            "avi",
            "mp3",
            "m4a",
            "wav",
            "flac",
            "ogg",
            "opus",
        ],
    )
    p.add_argument("--reencode", action="store_true")


@operation(
    "convert",
    "Change the format (mp4, mkv, mov, webm, mp3, m4a, wav, flac, ogg, opus) - copies what fits",
    "Convert",
    options=_convert_options,
)
def op_convert(ctx: Ctx) -> Result:
    from core.ops.converter import stream_args

    src = ctx.args.inputs[0]
    info = probe(src)
    out = output_path(ctx, [src], "converted", "." + ctx.args.to)
    part = part_of(out)
    try:
        pre, args, notes, summary = stream_args(info, ctx.args.to, not ctx.args.reencode)
    except ValueError as exc:
        raise CliError(str(exc)) from None
    ctx.run(
        [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y", *pre, "-i", src, *args, part],
        info.duration,
    )
    finish(part, out)
    return result(out, what=summary, notes=notes)


def _scale_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--size")
    p.add_argument("--factor", type=float)
    p.add_argument("--height", type=int)
    p.add_argument("--fit", default="fit", choices=["fit", "pad", "crop", "stretch"])
    p.add_argument(
        "--method",
        default="auto",
        choices=[
            "auto",
            "lanczos",
            "bicubic",
            "area",
            "gpu",
            "fsr",
            "nis",
            "artcnn",
            "artcnn_best",
            "anime4k",
        ],
        help="auto = FSR on a GPU for upscaling, else Lanczos; artcnn/anime4k suit animation",
    )
    p.add_argument("--sharpen", type=float, default=0.3)
    _video_opts(p)


@operation(
    "scale",
    "Resize (--size 1280x720 | --factor 2 | --height 720), --fit fit|pad|crop|stretch",
    "Convert",
    options=_scale_options,
)
def op_scale(ctx: Ctx) -> Result:
    from core import filters as fl
    from core.ops import scaler as sc

    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    w, h = info.require_video().display_size
    if ctx.args.size:
        bw, bh = (int(v) for v in ctx.args.size.lower().split("x"))
    elif ctx.args.factor:
        bw, bh = w * ctx.args.factor, h * ctx.args.factor
    elif ctx.args.height:
        bw, bh = w * ctx.args.height / h, ctx.args.height
    else:
        raise CliError("Give --size, --factor or --height.")
    from core import upscale

    gpu = fl.vulkan_ok() and ctx.args.gpu != "off"
    if ctx.args.method in sc.GPU_METHODS and not gpu:
        raise CliError(
            f"--method {ctx.args.method} needs a Vulkan graphics driver (and not --gpu off)."
        )
    try:
        chain = sc.scale_chain(
            (w // 2 * 2, h // 2 * 2),
            (bw, bh),
            ctx.args.fit,
            ctx.args.method,
            sharpen=ctx.args.sharpen,
            gpu=gpu,
            tags=upscale.tags_of(info.require_video()),
        )
    except ValueError as exc:
        raise CliError(str(exc)) from None
    out = output_path(ctx, [src], "scaled")
    ctx.render_chain(
        src,
        info,
        out,
        chain,
        pre=list(ff.VULKAN_DEVICE) if "libplacebo" in chain else None,
    )
    return result(out)


def _framerate_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--rate", default="60")
    p.add_argument("--method", default="motion", choices=["motion", "blend", "dup"])
    p.add_argument("--slowmo", type=float, default=1.0)
    _video_opts(p)


@operation(
    "framerate",
    "Change the frame rate (--rate 60) by motion interpolation / blending, or --slowmo 2",
    "Convert",
    options=_framerate_options,
)
def op_framerate(ctx: Ctx) -> Result:
    from core.ops import scaler as sc

    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    target = sc.rate_fraction(ctx.args.rate)
    factor = max(1.0, ctx.args.slowmo)
    chain = sc.fps_filter(ctx.args.method, target * Fraction(factor).limit_denominator(100))
    if factor > 1:
        chain += f",setpts={factor:.6f}*PTS,fps={target.numerator}/{target.denominator}"
    out = output_path(ctx, [src], "slowmo" if factor > 1 else f"{float(target):g}fps")
    audio = ["-an"]
    if info.has_audio:
        from core.filters import atempo_chain

        audio = (
            keep_audio(info, container_of(out), filters=atempo_chain(1 / factor))
            if factor > 1
            else keep_audio(info, container_of(out))
        )
    ctx.render_chain(src, info, out, chain, audio=audio, duration=info.duration * factor)
    return result(out)


def _compare_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("original")
    p.add_argument("copy")
    p.add_argument("--model", default="hd", choices=["hd", "4k", "neg"])
    p.add_argument("--metrics", default="vmaf,ssim,psnr")
    p.add_argument("--skip-original", type=float, default=0.0)
    p.add_argument("--skip-copy", type=float, default=0.0)
    p.add_argument("--seconds", type=float)
    p.add_argument("--every", type=int, default=1, help="VMAF on every Nth frame (faster)")
    p.add_argument("--csv", help="also save per-frame scores here")


@operation(
    "compare",
    "Measure how much quality a copy lost against its original: VMAF, SSIM, PSNR",
    "Convert",
    options=_compare_options,
)
def op_compare(ctx: Ctx) -> Result:
    from core import quality as qual

    a = ctx.args
    ri, di = probe(a.original, video=True), probe(a.copy, video=True)
    try:
        how = qual.Measure(
            metrics=[m.strip() for m in a.metrics.split(",")],
            model=a.model,
            skip_ref=a.skip_original,
            skip_dist=a.skip_copy,
            seconds=a.seconds,
            subsample=a.every,
        )
        argv, files = qual.build(a.original, a.copy, ri, di, how)
    except ValueError as exc:
        raise CliError(str(exc)) from None
    span = min(ri.duration - a.skip_original, di.duration - a.skip_copy)
    tmp = ctx.tmp()
    res = ff.run(argv, duration=min(span, a.seconds) if a.seconds else span, cwd=tmp)
    if res.returncode != 0:
        raise CliError("Measuring failed:\n" + "\n".join(res.log_tail[-6:]))
    result = qual.read(tmp, files, ri.require_video().fps or 30.0, a.skip_original)
    summary = result.summary()
    out = {
        "ok": True,
        "original": os.path.abspath(a.original),
        "copy": os.path.abspath(a.copy),
        **summary,
    }
    if "vmaf" in summary and isinstance(summary["vmaf"]["mean"], float):
        out["verdict"] = qual.verdict(summary["vmaf"]["mean"])
        out["worst_moments"] = [
            {"time": round(t, 2), "vmaf": round(v, 1)} for t, v in result.worst(5)
        ]
    if a.csv:
        result.to_csv(a.csv)
        out["csv"] = os.path.abspath(a.csv)
    return out


def _remux_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--to", choices=["mkv", "mp4", "mov", "webm"])
    p.add_argument("--keep-languages", help="e.g. eng,spa: keep only audio/subtitles in these")
    p.add_argument("--drop-subtitles", action="store_true")
    p.add_argument(
        "--drop-audio-except", help="keep only this audio track number (0 = first)", type=int
    )
    p.add_argument("--default-audio", help="language code (eng) or track number")
    p.add_argument("--default-subtitles", help="language code, track number, or 'none'")
    p.add_argument(
        "--add",
        action="append",
        default=[],
        help="FILE[:LANG[:DELAY]] - add audio/subtitle tracks from a file (repeatable)",
    )
    p.add_argument("--title")
    p.add_argument("--drop-chapters", action="store_true")


@operation(
    "remux",
    "Keep only some audio/subtitle tracks, set languages/defaults, change MKV/MP4/MOV - no "
    "re-encoding",
    "Convert",
    options=_remux_options,
)
def op_remux(ctx: Ctx) -> Result:
    from core import remux

    a = ctx.args
    src = a.inputs[0]
    probe(src)
    plan = remux.Plan()
    plan.load(src)
    langs = {x.strip() for x in (a.keep_languages or "").split(",") if x.strip()}
    audio_n = 0
    for tr in plan.tracks:
        if tr.kind == "audio":
            if a.drop_audio_except is not None and audio_n != a.drop_audio_except:
                tr.keep = False
            audio_n += 1
        if tr.kind == "subtitle" and a.drop_subtitles:
            tr.keep = False
        if langs and tr.kind in ("audio", "subtitle") and tr.language not in langs:
            tr.keep = False
    for spec in a.add:  # added afterwards: what's asked for explicitly is always kept
        path, lang, delay = _split_add(spec)
        try:
            plan.add_file(path, delay=delay, language=lang)
        except (ValueError, ff.ProbeError) as exc:
            raise CliError(str(exc)) from None

    def choose_default(kind: str, wanted: str | None) -> None:
        if wanted is None:
            return
        same = [i for i, tr in enumerate(plan.tracks) if tr.kind == kind and tr.keep]
        if wanted == "none":
            for i in same:
                plan.tracks[i].default = False
            return
        pick = [i for i in same if plan.tracks[i].language == wanted]
        if not pick and wanted.isdigit() and int(wanted) < len(same):
            pick = [same[int(wanted)]]
        if not pick:
            raise CliError(f"No kept {kind} track matches '{wanted}'.")
        plan.set_default(pick[0])

    choose_default("audio", a.default_audio)
    choose_default("subtitle", a.default_subtitles)
    container = a.to or (
        os.path.splitext(src)[1].lstrip(".").lower()
        if os.path.splitext(src)[1].lower() in (".mkv", ".mp4", ".mov", ".webm")
        else "mkv"
    )
    out = output_path(ctx, plan.inputs, "remux", "." + container)
    part = part_of(out)
    try:
        argv, notes = remux.remux_command(
            plan.inputs, plan.tracks, part, keep_chapters=not a.drop_chapters, title=a.title
        )
    except ValueError as exc:
        raise CliError(str(exc)) from None
    ctx.run(argv, probe(src).duration)
    finish(part, out)
    kept = [tr.describe() + (" [default]" if tr.default else "") for tr in plan.tracks if tr.keep]
    return result(out, tracks=kept, notes=notes)


def _split_add(spec: str) -> tuple[str, str, float]:
    """FILE[:LANG[:DELAY]] -> (file, lang, delay); FILE may itself contain ':' (C:\\...)."""
    if os.path.isfile(spec):
        return spec, "", 0.0
    head, _sep, last = spec.rpartition(":")
    if os.path.isfile(head):
        return head, last, 0.0
    head2, _sep, mid = head.rpartition(":")
    if os.path.isfile(head2):
        try:
            return head2, mid, float(last or 0)
        except ValueError:
            raise CliError(f"Not a delay in seconds: {last}") from None
    raise CliError(f"File not found: {spec}")


def _extract_track_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--track", type=int, required=True, help="stream index, as listed by info")


@operation(
    "extract-track",
    "Save one track (audio, subtitles, video, attached font) as its own file, as it is",
    "Convert",
    options=_extract_track_options,
)
def op_extract_track(ctx: Ctx) -> Result:
    from core import remux

    src = ctx.args.inputs[0]
    info = probe(src)
    tracks = {tr.index: tr for tr in remux.tracks_of(info, 0, src)}
    tr = tracks.get(ctx.args.track)
    if tr is None:
        raise CliError(f"No track {ctx.args.track} - tracks are {sorted(tracks)} (see info).")
    out = output_path(
        ctx,
        [src],
        f"track{tr.index}" + (f"_{tr.language}" if tr.language else ""),
        remux.extract_ext(tr),
    )
    part = out if tr.kind == "attachment" else part_of(out)
    ctx.run(
        remux.extract_command(src, tr, part),
        info.duration if tr.kind in ("audio", "video") else None,
    )
    if part != out:
        finish(part, out)
    return result(out, track=tr.describe())


def _hdr_to_sdr_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument(
        "--curve",
        default="bt2390",
        choices=["bt2390", "spline", "bt2446a", "hable", "mobius", "reinhard", "clip"],
    )
    p.add_argument(
        "--assume", choices=["pq", "hlg"], help="treat an untagged file as HDR10 (pq) or HLG"
    )
    _video_opts(p)


@operation(
    "hdr-to-sdr",
    "Convert HDR video (HDR10, HDR10+, HLG, Dolby Vision) to normal SDR for phones/TVs/web",
    "Convert",
    options=_hdr_to_sdr_options,
)
def op_hdr_to_sdr(ctx: Ctx) -> Result:
    from core import filters as fl
    from core import hdr
    from core.ops.hdr_args import subtitle_args

    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    h = hdr.inspect(info, src)
    if not h.is_hdr and not ctx.args.assume:
        raise CliError(
            "This video isn't tagged as HDR, so there is nothing to convert. If you know it is HDR "
            "(the tags were lost), add --assume pq (HDR10) or --assume hlg."
        )
    gpu = ctx.args.gpu != "off" and fl.vulkan_ok()
    if h.needs_gpu and not gpu:
        ctx.notes.append(
            "Dolby Vision without an HDR10 layer converted on the CPU: colours may be off."
        )
    out = output_path(ctx, [src], "sdr")
    ve = venc(ctx, container_of(out))
    ten = bool(ve.pix_fmt) and ve.pix_fmt.endswith(("10le", "p010le", "p010"))
    chain = hdr.sdr_chain(
        gpu=gpu,
        curve=ctx.args.curve,
        out_fmt="yuv420p10le" if ten else "yuv420p",
        assume=ctx.args.assume or "",
    )
    ctx.render_chain(
        src,
        info,
        out,
        chain,
        pre=list(ff.VULKAN_DEVICE) if gpu else None,
        extra=subtitle_args(info, container_of(out)),
    )
    return result(
        out,
        source=h.label() if h.is_hdr else f"assumed {ctx.args.assume}",
        engine="GPU" if gpu else "CPU",
        notes=ctx.notes,
    )


def _extract_audio_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--format", default="mp3", choices=["mp3", "m4a", "wav", "flac", "opus", "ogg"])
    p.add_argument("--copy", action="store_true")


@operation(
    "extract-audio",
    "Save the sound as mp3 / m4a / wav / flac / opus (or --copy as is)",
    "Audio",
    options=_extract_audio_options,
)
def op_extract_audio(ctx: Ctx) -> Result:
    from core.ops.extractor import copy_extension
    from core.ops.shared import audio_encoder_args

    src = ctx.args.inputs[0]
    info = probe(src)
    if not info.has_audio:
        raise CliError("The file has no sound.")
    ext = copy_extension(info.require_audio().codec) if ctx.args.copy else "." + ctx.args.format
    out = output_path(ctx, [src], "audio", ext)
    part = part_of(out)
    codec = ["-c:a", "copy"] if ctx.args.copy else audio_encoder_args(out)
    ctx.run(
        [ff.ffmpeg_path(), "-y", "-i", src, "-map", "0:a:0", "-vn", *codec, part], info.duration
    )
    finish(part, out)
    return result(out)


def _normalize_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument(
        "--target", default="streaming", choices=["streaming", "youtube", "ebu", "atsc", "film"]
    )


@operation(
    "normalize",
    "Even out loudness to a standard (two-pass): streaming, youtube, ebu, atsc, film",
    "Audio",
    options=_normalize_options,
)
def op_normalize(ctx: Ctx) -> Result:
    from core import loudness as ln
    from core.ops.shared import audio_job_args

    src = ctx.args.inputs[0]
    info = probe(src)
    if not info.has_audio:
        raise CliError("The file has no sound.")
    _label, i, tp, lra = ln.TARGETS[ctx.args.target]
    res = ctx.run(ln.measure_cmd(src, i, tp, lra), info.duration, 0.0, 0.5)
    meas = ln.parse_measurement(res.log_tail)
    af = ln.normalise_filter(i, tp, lra, meas, info.require_audio().sample_rate or 48000)
    out = output_path(ctx, [src], "normalized")
    part = part_of(out)
    ctx.run(
        [
            ff.ffmpeg_path(),
            "-y",
            "-i",
            src,
            "-af",
            af,
            *audio_job_args(part, info),
            "-map_metadata",
            "0",
            part,
        ],
        info.duration,
        0.5,
        1.0,
    )
    finish(part, out)
    return result(out, loudness_target=i)


def _clean_voice_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--method", default="neural", choices=["neural", "fft"])


@operation(
    "clean-voice",
    "Remove background noise from speech (neural or spectral)",
    "Audio",
    options=_clean_voice_options,
)
def op_clean_voice(ctx: Ctx) -> Result:
    from core.ops import voice_isolation as vi
    from core.ops.shared import audio_job_args

    src = ctx.args.inputs[0]
    info = probe(src)
    if not info.has_audio:
        raise CliError("The file has no sound.")
    af = vi.denoise_filter(ctx.args.method, model=os.path.join(vi.models_dir(), "sh.rnnn"))
    out = output_path(ctx, [src], "clean")
    part = part_of(out)
    ctx.run(
        [ff.ffmpeg_path(), "-y", "-i", src, "-af", af, *audio_job_args(part, info), part],
        info.duration,
    )
    finish(part, out)
    return result(out)


def _subtitles_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--model", default="base", choices=["tiny", "base", "small", "turbo"])
    p.add_argument("--language", default="auto")
    p.add_argument("--translate", action="store_true", help="into English")
    p.add_argument("--burn", action="store_true")
    _video_opts(p)


@operation(
    "subtitles",
    "Make subtitles from speech, offline (SRT next to the video, or --burn into it)",
    "Social",
    options=_subtitles_options,
)
def op_subtitles(ctx: Ctx) -> Result:
    from core import whisper

    src = ctx.args.inputs[0]
    info = probe(src)
    if not info.has_audio:
        raise CliError("The file has no sound.")
    if not whisper.cli_path():
        raise CliError("The speech engine (whisper-cli) is missing from this copy.")
    if not whisper.is_installed(ctx.args.model):
        ctx.log(f"Downloading the {ctx.args.model} speech model (one time)…")
        whisper.download_model(ctx.args.model)
    wav = os.path.join(ctx.tmp(), "audio.wav")
    ctx.run(whisper.audio_extract_cmd(src, wav), info.duration, 0.0, 0.1)
    base = os.path.join(ctx.tmp(), "subs")
    res = ff.run(
        whisper.transcribe_cmd(
            wav,
            whisper.model_path(ctx.args.model),
            base,
            language=ctx.args.language,
            translate=ctx.args.translate,
        )
    )
    if res.returncode != 0 or not os.path.isfile(base + ".srt"):
        raise CliError("Speech recognition failed:\n" + "\n".join(res.log_tail[-6:]))
    if not ctx.args.burn:
        out = (
            output_path(ctx, [src], "subs", ".srt")
            if ctx.args.output
            else os.path.splitext(src)[0] + ".srt"
        )
        if os.path.exists(out) and not ctx.args.overwrite:
            raise CliError(f"{out} already exists - add --overwrite or -o.")
        shutil.copyfile(base + ".srt", out)
        return result(out)
    if not info.has_video:
        raise CliError("--burn needs a video.")
    out = output_path(ctx, [src], "subtitled")
    from core import filters as fl

    ctx.render_chain(src, info, out, fl.subtitles_filter(base + ".srt"))
    return result(out)


def _burn_subs_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--subs", required=True)
    _video_opts(p)


@operation(
    "burn-subs",
    "Burn an existing subtitle file (.srt/.ass/.vtt) into the picture",
    "Social",
    options=_burn_subs_options,
)
def op_burn_subs(ctx: Ctx) -> Result:
    from core import filters as fl

    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    if not os.path.isfile(ctx.args.subs):
        raise CliError(f"Subtitle file not found: {ctx.args.subs}")
    out = output_path(ctx, [src], "subtitled")
    ctx.render_chain(src, info, out, fl.subtitles_filter(ctx.args.subs))
    return result(out)


def _gif_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--start", default="0")
    p.add_argument("--duration", default="4")
    p.add_argument("--fps", type=float, default=15)
    p.add_argument("--width", type=int, default=480)
    p.add_argument("--max-mb", type=float, default=0)
    p.add_argument("--webp", action="store_true")


@operation(
    "gif",
    "Animated GIF (or --webp) of part of a video, optionally under --max-mb",
    "Social",
    options=_gif_options,
)
def op_gif(ctx: Ctx) -> Result:
    from core.ops.gif_maker import Animation, make_animation

    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    start = time_value(ctx.args.start, 0.0)
    dur = min(time_value(ctx.args.duration, 4.0), info.duration - start)
    if dur <= 0:
        raise CliError("--start is past the end.")
    out = output_path(ctx, [src], "anim", ".webp" if ctx.args.webp else ".gif")
    part = part_of(out)
    look = Animation(
        ctx.args.fps,
        ctx.args.width // 2 * 2,
        cap=int(ctx.args.max_mb * 1024 * 1024),
        webp=ctx.args.webp,
    )
    make_animation(src, part, look, Steps(ctx.run, ctx.log), start=start, dur=dur)
    finish(part, out)
    return result(out)


def _shorts_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--platform", default="shorts")
    p.add_argument(
        "--background", default="blur", choices=["blur", "crop", "mirror", "black", "white"]
    )
    p.add_argument(
        "--anchor", default="center", choices=["center", "top", "bottom", "left", "right"]
    )
    _video_opts(p)


@operation(
    "shorts",
    "Reformat for Shorts/TikTok/Reels/square... (--platform, --background blur|crop|mirror|black)",
    "Social",
    options=_shorts_options,
)
def op_shorts(ctx: Ctx) -> Result:
    from core.ops import shortifier as sh

    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    if ctx.args.platform not in sh.PLATFORMS or ctx.args.platform == "custom":
        raise CliError("--platform one of: " + ", ".join(k for k in sh.PLATFORMS if k != "custom"))
    _n, tw, th, limit = sh.PLATFORMS[ctx.args.platform]
    graph = sh.reframe_graph(tw, th, ctx.args.background, ctx.args.anchor)
    out = output_path(ctx, [src], ctx.args.platform)
    dur = min(info.duration, limit) if limit else info.duration
    ctx.render_graph(
        [src],
        info,
        out,
        graph + (";[0:a:0]anull[a]" if info.has_audio else ""),
        has_audio=info.has_audio,
        duration=dur,
    )
    return result(out)


def _thumbnail_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--at", default="1")
    p.add_argument("--format", default="jpg", choices=["jpg", "png", "webp"])


@operation(
    "thumbnail", "Save one frame as an image (--at TIME)", "Social", options=_thumbnail_options
)
def op_thumbnail(ctx: Ctx) -> Result:
    from core.ops.frame_extractor import quality_args

    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    at = min(time_value(ctx.args.at, 1.0), max(0.0, info.duration - 0.05))
    out = output_path(ctx, [src], "frame", "." + ctx.args.format)
    part = part_of(out)
    ctx.run(
        [
            ff.ffmpeg_path(),
            "-y",
            "-ss",
            f"{at:.3f}",
            "-i",
            src,
            "-frames:v",
            "1",
            "-update",
            "1",
            *quality_args(ctx.args.format, 92),
            part,
        ],
        1.0,
    )
    finish(part, out)
    return result(out)


def _frames_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--every", type=float)
    p.add_argument("--count", type=int)
    p.add_argument("--format", default="jpg", choices=["jpg", "png", "webp"])


@operation(
    "frames",
    "Export frames as images (--every SECONDS or --count N) into a folder",
    "Social",
    options=_frames_options,
)
def op_frames(ctx: Ctx) -> Result:
    from core.ops.frame_extractor import quality_args

    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    every = ctx.args.every or (info.duration / max(1, ctx.args.count) if ctx.args.count else 1.0)
    folder: str = ctx.args.output or os.path.splitext(src)[0] + "_frames"
    if os.path.exists(folder) and os.listdir(folder) and not ctx.args.overwrite:
        raise CliError(f"{folder} isn't empty - choose another -o or add --overwrite.")
    os.makedirs(folder, exist_ok=True)
    pattern = os.path.join(folder.replace("%", "%%"), f"frame_%05d.{ctx.args.format}")
    ctx.run(
        [
            ff.ffmpeg_path(),
            "-y",
            "-i",
            src,
            "-vf",
            f"fps=1/{every:.6f}",
            *quality_args(ctx.args.format, 92),
            pattern,
        ],
        info.duration,
    )
    return {"ok": True, "output": folder, "files": len(os.listdir(folder))}


def _contact_sheet_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--grid", default="4x5", help="COLUMNSxROWS")
    p.add_argument("--width", type=int, default=384, help="tile width in pixels")
    p.add_argument("--set", type=int, default=1, help="which set of frames: 1, 2, 3...")
    p.add_argument("--scenes", action="store_true", help="take the frames at shot changes")
    p.add_argument("--skip", default="0,0", help="START,END: per cent of the video to leave out")
    p.add_argument("--title", default="", help="shown in place of the file name")
    p.add_argument("--times", default="br", choices=["br", "bl", "tr", "tl", "off"])
    p.add_argument("--numbers", action="store_true", help="number the tiles")
    p.add_argument("--format", default="png", choices=["png", "jpg", "webp"])


def _sheet_step(cmd: list[str], done: float) -> bool:
    ok = _proc.run(cmd, timeout=120).ok
    print(f"progress {int(done * 100)}%", file=sys.stderr, flush=True)
    return ok


@operation(
    "contact-sheet",
    "A grid of frames with the video's details (--grid 4x5, --set N for other frames)",
    "Social",
    options=_contact_sheet_options,
)
def op_contact_sheet(ctx: Ctx) -> Result:
    from core.ops import contact_sheet as cs

    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    try:
        cols, rows = (int(n) for n in ctx.args.grid.lower().split("x"))
        head, tail = (float(n) for n in ctx.args.skip.split(","))
    except ValueError:
        raise CliError("--grid is COLUMNSxROWS (4x5) and --skip is START,END (5,10)") from None
    if not (1 <= cols <= 12 and 1 <= rows <= 20 and 64 <= ctx.args.width <= 1920):
        raise CliError("The grid can be up to 12x20 tiles, each 64 to 1920 pixels wide.")
    sampling = cs.Sampling.SCENES if ctx.args.scenes else cs.Sampling.EVEN
    cuts: list[float] = []
    if ctx.args.scenes:
        lines: list[str] = []
        ctx.run(cs.scene_cmd(src), info.duration, 0.0, 0.3, lines=lines)
        cuts = cs.parse_cuts(lines)
    plan = cs.plan(
        info,
        cs.Layout(cols=cols, rows=rows, tile_width=ctx.args.width),
        cs.Labels(
            title=ctx.args.title, timestamps=cs.Corner(ctx.args.times), numbers=ctx.args.numbers
        ),
        cs.Picking(
            sampling=sampling, skip_start=head, skip_end=tail, variant=max(0, ctx.args.set - 1)
        ),
        cuts,
    )
    out = output_path(ctx, [src], "contact sheet", "." + ctx.args.format)
    part = part_of(out)
    cs.make(plan, ctx.tmp(), part, _sheet_step)
    finish(part, out)
    return result(out, frames=len(plan.times), width=plan.geo.width, height=plan.geo.height)


def _logo_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--logo", required=True)
    p.add_argument(
        "--position",
        default="br",
        choices=["tl", "tc", "tr", "ml", "c", "mr", "bl", "bc", "br"],
    )
    p.add_argument("--size", type=float, default=15)
    p.add_argument("--opacity", type=float, default=0.85)
    _video_opts(p)


@operation(
    "logo",
    "Put a logo/watermark image on a video (--logo FILE --position br --size 15)",
    "Social",
    options=_logo_options,
)
def op_logo(ctx: Ctx) -> Result:
    from core.ops.image_watermark import Logo, logo_graph

    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    probe(ctx.args.logo)
    w, h = info.require_video().display_size
    spec = Logo(pct=ctx.args.size, opacity=ctx.args.opacity, place=ctx.args.position)
    graph = logo_graph(w // 2 * 2, h // 2 * 2, spec)
    out = output_path(ctx, [src], "logo")
    container = container_of(out)
    ve = venc(ctx, container)
    part = part_of(out)
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        *ve.pre_input,
        "-i",
        src,
        "-loop",
        "1",
        "-i",
        ctx.args.logo,
        "-filter_complex",
        graph + (f";[v]{ve.filter_suffix}[venc]" if ve.filter_suffix else ""),
        "-map",
        "[venc]" if ve.filter_suffix else "[v]",
        *keep_audio(info, container),
        *ve.args,
        "-t",
        f"{info.duration:.3f}",
        part,
    ]
    ctx.run(cmd, info.duration)
    finish(part, out)
    return result(out)


def _collage_options(p: argparse.ArgumentParser) -> None:
    _in(p)
    p.add_argument("--size", default="1920x1080")
    p.add_argument("--fill", action="store_true")
    p.add_argument("--gap", type=int, default=4)
    p.add_argument("--no-loop", action="store_true")
    p.add_argument("--end", default="hold", choices=["hold", "background", "reflow"])
    p.add_argument("--length", default="longest", help="longest, shortest or seconds")
    p.add_argument("--audio", default="first", choices=["first", "mix", "none"])
    p.add_argument("--background", default="black")
    _video_opts(p)


@operation(
    "collage",
    "Put several clips on one screen (auto layout; --size, --fill, --no-loop, --end, --audio)",
    "Social",
    options=_collage_options,
)
def op_collage(ctx: Ctx) -> Result:
    from core import grid
    from core.ops.shared import frame_rate

    paths = ctx.args.inputs
    infos = [probe(x, video=True) for x in paths]
    W, H = (int(v) // 2 * 2 for v in ctx.args.size.lower().split("x"))
    clips: list[grid.Clip] = []
    for i, info in enumerate(infos):
        w, h = info.require_video().display_size
        dur = (
            info.require_video().duration if info.require_video().duration > 0.05 else info.duration
        )
        clips.append(grid.Clip(i, w / max(1, h), dur, info.has_audio))
    durs = [c.duration for c in clips]
    total = (
        max(durs)
        if ctx.args.length == "longest"
        else min(durs)
        if ctx.args.length == "shortest"
        else float(ctx.args.length)
    )
    loop = not ctx.args.no_loop
    canvas = grid.Canvas(
        W,
        H,
        fps=frame_rate(infos[0]),
        gap=ctx.args.gap,
        mode="fill" if ctx.args.fill else "fit",
        background=ctx.args.background,
    )
    timing = grid.Timing(total, loop=loop, end=ctx.args.end, audio=ctx.args.audio)
    graph, has_a = grid.collage_filter(clips, canvas, timing)
    out = output_path(ctx, paths, "collage", ".mp4")
    ctx.render_graph(
        paths,
        infos[0],
        out,
        graph,
        has_audio=has_a,
        duration=total,
        input_opts={i: ["-stream_loop", "-1"] for i in range(len(paths))} if loop else None,
    )
    lay = grid.best_layout(
        [c.aspect for c in clips], W, H, gap=ctx.args.gap, mode="fill" if ctx.args.fill else "fit"
    )
    return result(out, layout=lay.describe())


def _blur_region_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument(
        "--region",
        action="append",
        required=True,
        help="X,Y,W,H in percent; repeat for more regions",
    )
    p.add_argument("--type", default="blur", choices=["blur", "pixelate", "box"])
    p.add_argument("--from", dest="start", default="0")
    p.add_argument("--to", dest="end")
    _video_opts(p)


@operation(
    "blur-region",
    "Blur / pixelate / black-box a region (--region X,Y,W,H as % of the frame)",
    "Visuals",
    options=_blur_region_options,
)
def op_blur_region(ctx: Ctx) -> Result:
    from core.ops.region_blur import Region, blur_graph

    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    regions: list[Region] = []
    for r in ctx.args.region:
        x, y, w, h = (float(v) / 100 for v in r.split(","))
        regions.append(
            Region(
                x,
                y,
                w,
                h,
                ctx.args.type,
                0.6,
                time_value(ctx.args.start, 0.0),
                time_value(ctx.args.end) if ctx.args.end else None,
            )
        )
    W, H = info.require_video().display_size
    out = output_path(ctx, [src], "blurred")
    ctx.render_graph(
        [src],
        info,
        out,
        blur_graph(regions, W // 2 * 2, H // 2 * 2)
        + (";[0:a:0]anull[a]" if info.has_audio else ""),
        has_audio=info.has_audio,
    )
    return result(out)


def _chain_op(
    name: str,
    summary: str,
    build: Callable[[argparse.Namespace], str],
    suffix: str,
    add_args: Options | None = None,
) -> None:
    """Register a one-filter-chain visual operation: *build(args)* -> the -vf chain."""

    def options(p: argparse.ArgumentParser) -> None:
        _in(p, 1)
        if add_args:
            add_args(p)
        _video_opts(p)

    def run(ctx: Ctx) -> Result:
        src = ctx.args.inputs[0]
        info = probe(src, video=True)
        out = output_path(ctx, [src], suffix)
        ctx.render_chain(src, info, out, build(ctx.args))
        return result(out)

    operation(name, summary, "Visuals", options=options)(run)


def _denoise(a: argparse.Namespace) -> str:
    from core.ops.denoise import denoise_chain

    return denoise_chain(a.method, a.strength)


def _denoise_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--method", default="fast", choices=["fast", "nlm", "temporal", "grain"])
    p.add_argument("--strength", type=float, default=5)


def _sharpen(a: argparse.Namespace) -> str:
    from core.ops.sharpen import sharpen_chain

    return sharpen_chain(a.method, a.amount)


def _sharpen_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--method", default="cas", choices=["cas", "unsharp", "clarity"])
    p.add_argument("--amount", type=float, default=0.45)


def _deinterlace(a: argparse.Namespace) -> str:
    from core.ops.deinterlace import deinterlace_chain

    return deinterlace_chain(a.mode)


def _deinterlace_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mode", default="smart", choices=["smart", "all", "double", "ivtc"])


def _lut(a: argparse.Namespace) -> str:
    from core.ops.lut import build_chain

    return build_chain(a.lut, intensity=a.intensity)


def _lut_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--lut", required=True)
    p.add_argument("--intensity", type=float, default=1.0)


_chain_op(
    "denoise",
    "Reduce video noise/grain (--method fast|nlm|temporal|grain, --strength 1-10)",
    _denoise,
    "denoised",
    _denoise_options,
)
_chain_op(
    "sharpen",
    "Sharpen (--method cas|unsharp|clarity, --amount 0-1)",
    _sharpen,
    "sharp",
    _sharpen_options,
)
_chain_op(
    "deinterlace",
    "Remove interlacing combs (--mode smart|all|double|ivtc)",
    _deinterlace,
    "progressive",
    _deinterlace_options,
)
_chain_op(
    "lut",
    "Apply a colour look-up table (--lut FILE.cube, --intensity 0-1)",
    _lut,
    "graded",
    _lut_options,
)


def _metadata_clean_options(p: argparse.ArgumentParser) -> None:
    _in(p, 1)
    p.add_argument("--deep", action="store_true")
    p.add_argument("--keep-chapters", action="store_true")
    p.add_argument("--keep-cover", action="store_true")
    p.add_argument("--drop-languages", action="store_true")


@operation(
    "metadata-clean",
    "Remove location, device, dates, names and telemetry - no re-encode",
    "System",
    options=_metadata_clean_options,
)
def op_metadata_clean(ctx: Ctx) -> Result:
    from core import metaclean as mc

    src = ctx.args.inputs[0]
    info = probe(src)
    before = mc.inspect(info)
    out = output_path(ctx, [src], "clean")
    part = part_of(out)
    argv, notes = mc.clean_command(
        info,
        src,
        part,
        keep_language=not ctx.args.drop_languages,
        keep_chapters=ctx.args.keep_chapters,
        keep_cover=ctx.args.keep_cover,
        deep=ctx.args.deep,
    )
    ctx.run(argv, info.duration)
    finish(part, out)
    after = mc.inspect(ff.probe(out, use_cache=False))
    left = mc.leftovers(after, keep_chapters=ctx.args.keep_chapters, keep_cover=ctx.args.keep_cover)
    return result(
        out, removed=[f.line() for f in before], still_there=[f.line() for f in left], notes=notes
    )


def _tts_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--text")
    p.add_argument("--text-file")
    p.add_argument("--voice", default="en-GB-RyanNeural")
    p.add_argument("--offline", action="store_true")


@operation(
    "tts",
    "Speak text into an audio file (--text or --text-file; --offline for the system voice)",
    "Audio",
    options=_tts_options,
)
def op_tts(ctx: Ctx) -> Result:
    from core import tts

    text = ctx.args.text or (
        Path(ctx.args.text_file).read_text(encoding="utf-8") if ctx.args.text_file else ""
    )
    if not text.strip():
        raise CliError("Give --text or --text-file.")
    engine = "system" if ctx.args.offline or not tts.edge_available() else "edge"
    out = ctx.args.output or os.path.abspath("speech" + (".mp3" if engine == "edge" else ".wav"))
    if os.path.exists(out) and not ctx.args.overwrite:
        raise CliError(f"{out} already exists - add --overwrite or -o.")
    tmp = os.path.join(ctx.tmp(), "speech" + (".mp3" if engine == "edge" else ".wav"))
    try:
        tts.synthesize(text, tmp, tts.Voice(engine=engine, name=ctx.args.voice))
    except tts.TTSError as exc:
        raise CliError(str(exc)) from None
    if os.path.splitext(out)[1].lower() == os.path.splitext(tmp)[1].lower():
        shutil.copyfile(tmp, out)
    else:
        from core.ops.shared import audio_encoder_args

        ctx.run([ff.ffmpeg_path(), "-y", "-i", tmp, *audio_encoder_args(out), out])
    return result(out, engine=engine)


def _download_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("url")
    p.add_argument("--audio-only", action="store_true")
    p.add_argument("--max-height", type=int, default=0)
    p.add_argument("--format", default="mp4", choices=["mp4", "mkv", "webm"])


@operation(
    "download",
    "Download a video (or --audio-only) from YouTube and 1000+ sites, following the app's "
    "network settings",
    "Social",
    options=_download_options,
)
def op_download(ctx: Ctx) -> Result:
    from core import network
    from core.ytdlp import base_argv, ytdlp_path

    if not ytdlp_path():
        raise CliError("yt-dlp is missing from this copy.")
    folder = ctx.args.output or os.getcwd()
    os.makedirs(folder, exist_ok=True)
    try:
        argv = base_argv(url=ctx.args.url)
    except OSError as exc:
        raise CliError(network.explain_error(exc)) from None
    template = os.path.join(folder, "%(title).150B [%(id)s].%(ext)s")
    if ctx.args.audio_only:
        argv += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    else:
        fmt = (
            f"bv*[height<={ctx.args.max_height}]+ba/b[height<={ctx.args.max_height}]"
            if ctx.args.max_height
            else "bv*+ba/b"
        )
        argv += ["-f", fmt, "--merge-output-format", ctx.args.format]
    argv += [
        "--no-overwrites",
        "--no-playlist",
        "--print",
        "after_move:filepath",
        "-o",
        template,
        ctx.args.url,
    ]
    r = _proc.run(argv, timeout=None, env=network.subprocess_env(url=ctx.args.url))
    if r.returncode != 0:
        raise CliError(
            "Download failed:\n" + "\n".join((r.stderr or r.stdout).strip().splitlines()[-6:])
        )
    path = (r.stdout.strip().splitlines() or [""])[-1]
    return result(path) if path and os.path.isfile(path) else {"ok": True, "output": folder}


# ── Entry point ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="ffmpeg-workbench --cli",
        description="Edit and convert video/audio from the command line (same engine as the "
        "app). Inputs are never changed; outputs never replace existing files "
        "unless --overwrite.",
    )
    sub = ap.add_subparsers(dest="command", metavar="COMMAND")
    t = sub.add_parser("tools", help="list everything that can be done")
    t.add_argument("--json", action="store_true")
    i = sub.add_parser(
        "info", help="describe a media file (streams, HDR, rotation, personal metadata)"
    )
    i.add_argument("file")
    i.add_argument("--json", action="store_true")
    pth = sub.add_parser("paths", help="where the bundled FFmpeg, ffprobe, yt-dlp... are")
    pth.add_argument("--json", action="store_true")
    sub.add_parser("guide", help="print the guide for AI assistants and scripts (AGENTS.md)")
    for op in OPERATIONS.values():
        summary = op.summary.replace("%", "%%")
        p = sub.add_parser(op.name, help=summary, description=summary)
        if op.options:
            op.options(p)
        p.add_argument("-o", "--output", help="output file (or folder); default: next to the input")
        p.add_argument(
            "--overwrite", action="store_true", help="allow replacing an existing output file"
        )
        p.add_argument("--json", action="store_true", help="print the result as JSON (last line)")
    return ap


class _Recorder(argparse.ArgumentParser):
    """A parser that remembers the arguments an operation adds (argparse keeps its list private)."""

    def __init__(self) -> None:
        super().__init__(add_help=False)
        self.added: list[argparse.Action] = []

    def add_argument(self, *args: Any, **kwargs: Any) -> argparse.Action:
        action = super().add_argument(*args, **kwargs)
        self.added.append(action)
        return action


def _describe_option(a: argparse.Action) -> dict[str, Any]:
    return {
        "name": a.option_strings[0] if a.option_strings else a.dest,
        "required": a.required or not a.option_strings,
        "choices": list(a.choices) if a.choices else None,
        "default": a.default if a.default not in (None, False, argparse.SUPPRESS) else None,
        "help": a.help or "",
    }


def tools_catalogue() -> list[dict[str, Any]]:
    """Every operation with its options, for ``tools --json`` (scripts and AI assistants)."""
    out: list[dict[str, Any]] = []
    for op in OPERATIONS.values():
        recorder = _Recorder()
        if op.options:
            op.options(recorder)
        opts = [_describe_option(a) for a in recorder.added]
        out.append(
            {"name": op.name, "category": op.category, "summary": op.summary, "options": opts}
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = getattr(args, "json", False)

    def emit(d: Result) -> None:
        if as_json:
            print(json.dumps(d, ensure_ascii=False))
        elif d.get("ok"):
            for k, v in d.items():
                if k != "ok":
                    print(f"{k}: {v}")
        else:
            print("error: " + d.get("error", "failed"), file=sys.stderr)

    if args.command in (None, "tools"):
        cat = tools_catalogue()
        if as_json:
            print(json.dumps({"ok": True, "operations": cat}, ensure_ascii=False))
        else:
            current = None
            for op in sorted(cat, key=lambda o: (o["category"], o["name"])):
                if op["category"] != current:
                    current = op["category"]
                    print(f"\n{current}:")
                print(f"  {op['name']:<16} {op['summary']}")
            print("\nDetails: ffmpeg-workbench --cli OPERATION --help")
        return 0
    try:
        if args.command == "info":
            emit(describe_file(args.file))
            return 0
        if args.command == "guide":
            from core.paths import resource_path

            with open(resource_path("AGENTS.md"), encoding="utf-8") as fh:
                print(fh.read())
            return 0
        if args.command == "paths":
            emit({"ok": True, **tool_paths()})
            return 0
        ctx = Ctx(args)
        t0 = time.monotonic()
        try:
            res = OPERATIONS[args.command].run(ctx)
        finally:
            ctx.cleanup()
        res.setdefault("seconds", round(time.monotonic() - t0, 2))
        emit(res)
        return 0
    except (CliError, OSError, ValueError, RuntimeError) as exc:
        emit({"ok": False, "error": str(exc)})
        return 1


def _ensure_streams() -> None:
    """A windowed Windows build has no stdout/stderr: borrow the console of
    the shell that started it (or discard output when there is none)."""
    if sys.stdout is None or sys.stderr is None:
        attached = False
        if sys.platform == "win32":
            try:
                import ctypes

                attached = bool(ctypes.windll.kernel32.AttachConsole(-1))  # ATTACH_PARENT_PROCESS
            except Exception:
                logger.debug("_ensure_streams: best-effort step failed", exc_info=True)
                attached = False
        target = "CONOUT$" if attached else os.devnull
        # these stay open for the life of the process: they *are* its stdout and stderr
        if sys.stdout is None:
            sys.stdout = open(target, "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
        if sys.stderr is None:
            sys.stderr = open(target, "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):  # JSON and names with any characters
            with contextlib.suppress(ValueError):
                stream.reconfigure(encoding="utf-8", errors="replace")


def entry(argv: list[str]) -> int:
    """Called by main.py before any window code is imported."""
    _ensure_streams()
    os.environ.setdefault("CROSSFADER_NO_NOTIFY", "1")
    while argv[:1] == ["--cli"]:  # ffw-cli.bat adds it too; typing it again is harmless
        argv = argv[1:]
    try:
        return main(argv)
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130
