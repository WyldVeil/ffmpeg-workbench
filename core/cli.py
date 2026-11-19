"""
core/cli.py  -  Command line for scripts and AI assistants

    wyldveil-media-editor --cli tools [--json]          what can be done
    wyldveil-media-editor --cli info FILE [--json]      what a file contains
    wyldveil-media-editor --cli paths [--json]          the bundled FFmpeg, yt-dlp...
    wyldveil-media-editor --cli OPERATION INPUT... [options] [-o OUTPUT] [--json]

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
import json
import os
import shutil
import sys
import tempfile
import time
from fractions import Fraction
from typing import Callable, Optional

from core import ffmpeg as ff

OPERATIONS: dict = {}          # name -> (summary, category, configure(parser), run(args) -> dict)


class CliError(Exception):
    pass


def operation(name: str, summary: str, category: str):
    def deco(fn):
        OPERATIONS[name] = (summary, category, getattr(fn, "_configure", None), fn)
        return fn
    return deco


def options(configure: Callable):
    def deco(fn):
        fn._configure = configure
        return fn
    return deco


# ── Shared helpers ────────────────────────────────────────────────────────────

class Ctx:
    """What an operation needs: paths, progress, a scratch folder."""

    def __init__(self, args):
        self.args = args
        self.notes: list = []
        self._tmp = None
        self._last = -10

    def tmp(self) -> str:
        if self._tmp is None:
            self._tmp = tempfile.mkdtemp(prefix="wme_cli_")
        return self._tmp

    def cleanup(self):
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)

    def log(self, text: str):
        print(text, file=sys.stderr, flush=True)

    def run(self, cmd: list, duration: Optional[float] = None, lo: float = 0.0, hi: float = 1.0,
            lines: Optional[list] = None):
        """Run one FFmpeg command; progress to stderr.  *lines*, when given,
        receives every log line (for detectors whose output can be long)."""
        def prog(p):
            frac = p.fraction
            if frac is None and duration and p.out_time:
                frac = min(1.0, p.out_time / duration)
            if frac is not None:
                pct = int((lo + (hi - lo) * frac) * 100)
                if pct >= self._last + 5:
                    self._last = pct
                    print(f"progress {pct}%", file=sys.stderr, flush=True)
        res = ff.run(cmd, duration=duration, on_progress=prog,
                     on_line=lines.append if lines is not None else None)
        if res.returncode != 0:
            raise CliError("FFmpeg failed:\n" + "\n".join(res.log_tail[-8:]))
        return res


def _same(a: str, b: str) -> bool:
    try:
        return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


def output_path(ctx: Ctx, inputs: list, suffix: str, ext: Optional[str] = None) -> str:
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


def venc(ctx: Ctx, container: str):
    from core import encoders as enc
    a = ctx.args
    return enc.video(getattr(a, "codec", "h264"), getattr(a, "quality", "high"), speed=getattr(a, "speed", "balanced"),
                     hw=getattr(a, "gpu", "auto"), container=container)


def container_of(out: str) -> str:
    return os.path.splitext(out)[1].lstrip(".").lower() or "mp4"


def keep_audio(info, container: str, filters: str = "") -> list:
    from tabs import common
    return common.keep_audio_args(info, container, filters=filters)


def render_chain(ctx: Ctx, src: str, info, out: str, chain: str, *, pre: Optional[list] = None,
                 audio: Optional[list] = None, extra: Optional[list] = None, duration: Optional[float] = None):
    """ffmpeg -i src -vf <chain> <encoder> <audio> out (atomically)."""
    container = container_of(out)
    ve = venc(ctx, container)
    part = part_of(out)
    cmd = [ff.ffmpeg_path(), "-y", *(pre or []), *ve.pre_input, "-i", src, "-map", "0:v:0", *ve.vf_args(chain or None)]
    cmd += list(audio if audio is not None else keep_audio(info, container))
    cmd += [*(extra or []), *ve.args, "-map_metadata", "0", part]
    ctx.run(cmd, duration or info.duration)
    finish(part, out)


def render_graph(ctx: Ctx, inputs: list, info, out: str, graph: str, *, has_audio: bool, pre: Optional[list] = None,
                 duration: Optional[float] = None, input_opts: Optional[dict] = None):
    from core import encoders as enc
    container = container_of(out)
    ve = venc(ctx, container)
    part = part_of(out)
    cmd = [ff.ffmpeg_path(), "-y", *(pre or []), *ve.pre_input]
    for i, path in enumerate(inputs):
        cmd += list((input_opts or {}).get(i, [])) + ["-i", path]
    if ve.filter_suffix:
        graph += f";[v]{ve.filter_suffix}[venc]"
    cmd += ["-filter_complex", graph, "-map", "[venc]" if ve.filter_suffix else "[v]"]
    cmd += (["-map", "[a]", *enc.audio(enc.default_audio_for(container), container=container)] if has_audio
            else ["-an"])
    cmd += [*ve.args]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += [part]
    ctx.run(cmd, duration or info.duration)
    finish(part, out)


def result(out: str, **extra) -> dict:
    info = ff.try_probe(out, use_cache=False) if os.path.isfile(out) else None
    d = {"ok": True, "output": out}
    if os.path.isfile(out):
        d["size_bytes"] = os.path.getsize(out)
    if info is not None:
        d["duration"] = round(info.duration, 3)
        if info.has_video:
            d["video"] = f"{info.video.codec} {info.video.width}x{info.video.height} {info.video.fps:.3g}fps"
        if info.has_audio:
            d["audio"] = f"{info.audio.codec} {info.audio.channels}ch {info.audio.sample_rate}Hz"
    d.update(extra)
    return d


def time_value(text: Optional[str], default: Optional[float] = None) -> Optional[float]:
    if text in (None, ""):
        return default
    try:
        return ff.parse_time(str(text))
    except ValueError:
        raise CliError(f"Not a time: {text} (use seconds or HH:MM:SS)") from None


def _video_opts(p):
    p.add_argument("--codec", default="h264", choices=["h264", "hevc", "av1", "vp9", "prores", "ffv1"],
                   help="video codec (default h264 - plays everywhere)")
    p.add_argument("--quality", default="high", choices=["very_high", "high", "good", "low", "lowest"])
    p.add_argument("--speed", default="balanced", choices=["fastest", "fast", "balanced", "slow", "slowest"])
    p.add_argument("--gpu", default="auto", choices=["auto", "off", "nvenc", "qsv", "vaapi", "amf", "videotoolbox"],
                   help="use a GPU encoder when available (auto) or not (off)")


# ── Information ───────────────────────────────────────────────────────────────

def describe_file(path: str) -> dict:
    from core import metaclean as mc
    info = probe(path)
    d = {"ok": True, "file": os.path.abspath(path), "format": info.format_name, "duration": round(info.duration, 3),
         "size_bytes": info.size, "streams": []}
    for s in info.streams:
        e = {"index": s.index, "type": s.type, "codec": s.codec}
        if s.type == "video":
            e.update(width=s.width, height=s.height, fps=round(s.fps or 0, 3), pix_fmt=s.pix_fmt,
                     hdr=s.is_hdr, rotation=s.rotation, cover_art=bool(s.attached_pic))
        elif s.type == "audio":
            e.update(channels=s.channels, sample_rate=s.sample_rate, language=s.tags.get("language", ""))
        elif s.type == "subtitle":
            e.update(language=s.tags.get("language", ""), title=s.tags.get("title", ""))
        d["streams"].append(e)
    d["chapters"] = len(info.chapters or [])
    d["personal_metadata"] = [f.line() for f in mc.inspect(info)]
    return d


def tool_paths() -> dict:
    from core.hardware import find_binary
    return {name: find_binary(name) for name in ("ffmpeg", "ffprobe", "ffplay", "yt-dlp", "deno", "whisper-cli",
                                                  "fluidsynth")}


# ── Operations ────────────────────────────────────────────────────────────────
# Each takes (ctx) and returns a result dict.  Grouped like the app's sidebar.

def _in(p, n: str = "+"):
    p.add_argument("inputs", nargs=n, metavar="INPUT", help="input file(s)")


@operation("trim", "Keep only part of a video (exact, or --copy for a fast keyframe cut)", "Cut")
@options(lambda p: (_in(p, 1), p.add_argument("--start", default="0"), p.add_argument("--end"),
                    p.add_argument("--copy", action="store_true", help="no re-encode (starts at a keyframe)"),
                    _video_opts(p)))
def op_trim(ctx):
    src = ctx.args.inputs[0]
    info = probe(src)
    a, b = time_value(ctx.args.start, 0.0), time_value(ctx.args.end, info.duration)
    if b <= a:
        raise CliError("--end must be after --start.")
    out = output_path(ctx, [src], "trim")
    part = part_of(out)
    if ctx.args.copy:
        ctx.run([ff.ffmpeg_path(), "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", src, "-map", "0", "-c", "copy",
                 "-avoid_negative_ts", "make_zero", part], b - a)
        finish(part, out)
    else:
        render_chain(ctx, src, info, out, "", pre=["-ss", f"{a:.3f}", "-to", f"{b:.3f}"], duration=b - a)
    return result(out)


@operation("join", "Join clips one after another (lossless when they match, else normalised)", "Cut")
@options(lambda p: (_in(p), p.add_argument("--copy", action="store_true", help="require a lossless join"),
                    _video_opts(p)))
def op_join(ctx):
    from core import concat
    paths = ctx.args.inputs
    if len(paths) < 2:
        raise CliError("Give at least two files to join.")
    infos = [probe(x) for x in paths]
    out = output_path(ctx, paths, "joined")
    part = part_of(out)
    problems = concat.copy_compatibility(infos)
    if not problems:
        ctx.run(concat.copy_join_command(paths, part, os.path.join(ctx.tmp(), "list.txt")), sum(i.duration for i in infos))
        finish(part, out)
        return result(out, method="lossless")
    if ctx.args.copy:
        raise CliError("These clips can't be joined losslessly: " + "; ".join(problems))
    pieces = [concat.Piece(x, i) for x, i in zip(paths, infos)]
    argv, secs = concat.filter_join_command(pieces, part, venc=venc(ctx, container_of(out)))
    ctx.run(argv, secs)
    finish(part, out)
    return result(out, method="re-encoded (" + "; ".join(problems) + ")")


@operation("transition", "Join two clips with a transition (fade, wipeleft, circleopen, ...)", "Cut")
@options(lambda p: (_in(p), p.add_argument("--type", default="fade"),
                    p.add_argument("--duration", type=float, default=1.0), _video_opts(p)))
def op_transition(ctx):
    from tabs.cutting import crossfader as xf
    paths = ctx.args.inputs
    if len(paths) < 2:
        raise CliError("Give two (or more) clips.")
    clips = [xf.Clip.from_info(probe(x, video=True)) for x in paths]
    problem = xf.validate(clips, ctx.args.duration, False)
    if problem:
        raise CliError(problem)
    out = output_path(ctx, paths, "transition")
    part = part_of(out)
    argv, secs = xf.build_command(clips, part, T=ctx.args.duration, transitions=[ctx.args.type] * (len(clips) - 1),
                                  venc=venc(ctx, container_of(out)))
    ctx.run(argv, secs)
    finish(part, out)
    return result(out)


@operation("rotate", "Rotate (90/180/270) and/or mirror; --lossless only changes the orientation flag", "Cut")
@options(lambda p: (_in(p, 1), p.add_argument("--degrees", type=int, default=90, choices=[0, 90, 180, 270]),
                    p.add_argument("--flip", choices=["h", "v"]), p.add_argument("--lossless", action="store_true"),
                    _video_opts(p)))
def op_rotate(ctx):
    from tabs.cutting import rotate_flip as rf
    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    out = output_path(ctx, [src], "rotated")
    h, v = ctx.args.flip == "h", ctx.args.flip == "v"
    if ctx.args.lossless:
        part = part_of(out)
        ctx.run([ff.ffmpeg_path(), "-y", *rf.lossless_args(info.video.rotation, ctx.args.degrees, h, v), "-i", src,
                 "-map", "0", "-c", "copy", part], info.duration)
        finish(part, out)
    else:
        render_chain(ctx, src, info, out, rf.reencode_chain(ctx.args.degrees, h, v))
    return result(out)


@operation("crop", "Crop to a rectangle (--rect X:Y:W:H), an aspect (--aspect 9:16) or remove black bars (--auto)",
           "Cut")
@options(lambda p: (_in(p, 1), p.add_argument("--rect"), p.add_argument("--aspect"),
                    p.add_argument("--auto", action="store_true"), _video_opts(p)))
def op_crop(ctx):
    from tabs.cutting import crop as cr
    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    w, h = info.video.display_size
    if ctx.args.rect:
        x, y, cw, ch = (int(v) for v in ctx.args.rect.replace(",", ":").split(":"))
    elif ctx.args.aspect:
        rw, rh = (int(v) for v in ctx.args.aspect.replace("/", ":").split(":"))
        x, y, cw, ch = cr.fit_aspect(w, h, rw, rh)
    elif ctx.args.auto:
        from tabs.social import auto_cropper
        box = auto_cropper.detect_bars(src, info.duration)
        if not box:
            raise CliError("No black bars found.")
        x, y, cw, ch = box
    else:
        raise CliError("Give --rect, --aspect or --auto.")
    out = output_path(ctx, [src], "cropped")
    render_chain(ctx, src, info, out, f"crop={cw // 2 * 2}:{ch // 2 * 2}:{x}:{y},setsar=1")
    return result(out, crop=[x, y, cw, ch])


@operation("speed", "Play faster or slower (--factor 2 = twice as fast), sound kept in pitch", "Cut")
@options(lambda p: (_in(p, 1), p.add_argument("--factor", type=float, required=True), _video_opts(p)))
def op_speed(ctx):
    from tabs.visuals.speed_ramp import atempo_chain
    src = ctx.args.inputs[0]
    info = probe(src)
    f = ctx.args.factor
    if not 0.1 <= f <= 100:
        raise CliError("--factor must be between 0.1 and 100.")
    out = output_path(ctx, [src], f"x{f:g}")
    container = container_of(out)
    audio = keep_audio(info, container, filters=atempo_chain(f)) if info.has_audio else ["-an"]
    if info.has_video:
        render_chain(ctx, src, info, out, f"setpts=PTS/{f:g}", audio=audio, duration=info.duration / f)
    else:
        part = part_of(out)
        ctx.run([ff.ffmpeg_path(), "-y", "-i", src, *audio, part], info.duration / f)
        finish(part, out)
    return result(out)


@operation("reverse", "Play backwards (video and sound; clips up to 10 minutes)", "Cut")
@options(lambda p: (_in(p, 1), _video_opts(p)))
def op_reverse(ctx):
    src = ctx.args.inputs[0]
    info = probe(src)
    if info.duration > 600:
        raise CliError("Use the app's Video Reverser for clips over 10 minutes (it works in memory-safe chunks).")
    out = output_path(ctx, [src], "reversed")
    audio = keep_audio(info, container_of(out), filters="areverse") if info.has_audio else ["-an"]
    render_chain(ctx, src, info, out, "reverse", audio=audio)
    return result(out)


@operation("remove-silence", "Cut out silent parts (talking-head videos, podcasts)", "Audio")
@options(lambda p: (_in(p, 1), p.add_argument("--threshold", type=float, default=-40.0, help="dB"),
                    p.add_argument("--min-silence", type=float, default=0.6),
                    p.add_argument("--padding", type=float, default=0.15), _video_opts(p)))
def op_remove_silence(ctx):
    from tabs.audio import silence as sl
    src = ctx.args.inputs[0]
    info = probe(src)
    if not info.has_audio:
        raise CliError("The file has no sound to look for silence in.")
    lines: list = []
    ctx.run(sl.detect_cmd(src, ctx.args.threshold, ctx.args.min_silence), info.duration, 0.0, 0.3, lines=lines)
    silences = sl.parse_silences("\n".join(lines), info.duration)
    zones = sl.keep_zones(silences, info.duration, ctx.args.padding, fps=info.video.fps if info.has_video else 0)
    if not zones:
        raise CliError("Everything is silent at that threshold.")
    out = output_path(ctx, [src], "nosilence")
    graph = sl.cut_graph(zones, info.has_video, info.has_audio)
    if info.has_video:
        render_graph(ctx, [src], info, out, graph, has_audio=True, duration=sum(b - a for a, b in zones))
    else:
        part = part_of(out)
        from tabs import common
        ctx.run([ff.ffmpeg_path(), "-y", "-i", src, "-filter_complex", graph, "-map", "[a]",
                 *common.audio_encoder_args(out), part], sum(b - a for a, b in zones), 0.3, 1.0)
        finish(part, out)
    return result(out, removed_seconds=round(info.duration - sum(b - a for a, b in zones), 2))


@operation("compress", "Make a video fit a size limit (e.g. --size 10MB for Discord) at the best quality", "Convert")
@options(lambda p: (_in(p, 1), p.add_argument("--size", required=True, help="e.g. 10MB, 25MB, 500KB"),
                    p.add_argument("--codec", default="h264", choices=["h264", "hevc", "av1"]),
                    p.add_argument("--fast", action="store_true", help="GPU encoding, less exact"),
                    p.add_argument("--audio", default="auto", choices=["auto", "copy", "none"]),
                    p.add_argument("--start"), p.add_argument("--end"),
                    p.add_argument("--keep-resolution", action="store_true"),
                    p.add_argument("--keep-framerate", action="store_true")))
def op_compress(ctx):
    from tabs.transcoder import downsizer as ds
    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    text = ctx.args.size.strip().upper().replace(" ", "")
    mult = 1_000_000 if text.endswith("MB") else 1_000 if text.endswith("KB") else 1_000_000_000 if text.endswith(
        "GB") else 1_000_000
    try:
        target = int(float(text.rstrip("MKGB")) * mult)
    except ValueError:
        raise CliError("--size like 10MB") from None
    a, b = time_value(ctx.args.start, 0.0), time_value(ctx.args.end, info.duration)
    w, h = info.video.display_size
    try:
        plan = ds.plan(b - a, target, (w // 2 * 2, h // 2 * 2), info.video.fps, codec=ctx.args.codec,
                       has_audio=info.has_audio, audio_mode=ctx.args.audio,
                       audio_src_kbps=int((info.audio.bit_rate or 0) / 1000) if info.has_audio else 0,
                       allow_res=not ctx.args.keep_resolution, allow_fps=not ctx.args.keep_framerate)
    except ValueError as exc:
        raise CliError(str(exc)) from None
    for n in plan.notes:
        ctx.log(n)
    out = output_path(ctx, [src], "small", ".mp4")
    part = part_of(out)
    ds.encode_to_size(src, part, info, plan, target, codec=ctx.args.codec, fast=ctx.args.fast,
                      audio_mode=ctx.args.audio, start=a, end=b, tmp=ctx.tmp(), run=ctx.run, log=ctx.log)
    finish(part, out)
    return result(out, target_bytes=target, resolution=f"{plan.width}x{plan.height}")


@operation("convert", "Change the format (mp4, mkv, mov, webm, mp3, m4a, wav, flac, ogg, opus) - copies what fits",
           "Convert")
@options(lambda p: (_in(p, 1), p.add_argument("--to", required=True,
                                              choices=["mp4", "mkv", "mov", "webm", "m4v", "ts", "avi", "mp3",
                                                       "m4a", "wav", "flac", "ogg", "opus"]),
                    p.add_argument("--reencode", action="store_true")))
def op_convert(ctx):
    from tabs.transcoder.converter import stream_args
    src = ctx.args.inputs[0]
    info = probe(src)
    out = output_path(ctx, [src], "converted", "." + ctx.args.to)
    part = part_of(out)
    try:
        pre, args, notes, summary = stream_args(info, ctx.args.to, not ctx.args.reencode)
    except ValueError as exc:
        raise CliError(str(exc)) from None
    ctx.run([ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y", *pre, "-i", src, *args, part], info.duration)
    finish(part, out)
    return result(out, what=summary, notes=notes)


@operation("scale", "Resize (--size 1280x720 | --factor 2 | --height 720), --fit fit|pad|crop|stretch", "Convert")
@options(lambda p: (_in(p, 1), p.add_argument("--size"), p.add_argument("--factor", type=float),
                    p.add_argument("--height", type=int),
                    p.add_argument("--fit", default="fit", choices=["fit", "pad", "crop", "stretch"]),
                    p.add_argument("--sharpen", type=float, default=0.3), _video_opts(p)))
def op_scale(ctx):
    from core import filters as fl
    from tabs.transcoder import scaler as sc
    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    w, h = info.video.display_size
    if ctx.args.size:
        bw, bh = (int(v) for v in ctx.args.size.lower().split("x"))
    elif ctx.args.factor:
        bw, bh = w * ctx.args.factor, h * ctx.args.factor
    elif ctx.args.height:
        bw, bh = w * ctx.args.height / h, ctx.args.height
    else:
        raise CliError("Give --size, --factor or --height.")
    gpu = fl.vulkan_ok() and ctx.args.gpu != "off"
    chain = sc.scale_chain((w // 2 * 2, h // 2 * 2), (bw, bh), ctx.args.fit, "auto", sharpen=ctx.args.sharpen, gpu=gpu)
    out = output_path(ctx, [src], "scaled")
    render_chain(ctx, src, info, out, chain, pre=["-init_hw_device", "vulkan"] if "libplacebo" in chain else None)
    return result(out)


@operation("framerate", "Change the frame rate (--rate 60) by motion interpolation / blending, or --slowmo 2",
           "Convert")
@options(lambda p: (_in(p, 1), p.add_argument("--rate", default="60"),
                    p.add_argument("--method", default="motion", choices=["motion", "blend", "dup"]),
                    p.add_argument("--slowmo", type=float, default=1.0), _video_opts(p)))
def op_framerate(ctx):
    from tabs.transcoder import scaler as sc
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
        from tabs.visuals.speed_ramp import atempo_chain
        audio = keep_audio(info, container_of(out), filters=atempo_chain(1 / factor)) if factor > 1 else \
            keep_audio(info, container_of(out))
    render_chain(ctx, src, info, out, chain, audio=audio, duration=info.duration * factor)
    return result(out)


@operation("hdr-to-sdr", "Convert HDR video (HDR10, HDR10+, HLG, Dolby Vision) to normal SDR for phones/TVs/web",
           "Convert")
@options(lambda p: (_in(p, 1), p.add_argument("--curve", default="bt2390",
                                              choices=["bt2390", "spline", "bt2446a", "hable", "mobius", "reinhard",
                                                       "clip"]),
                    p.add_argument("--assume", choices=["pq", "hlg"],
                                   help="treat an untagged file as HDR10 (pq) or HLG"), _video_opts(p)))
def op_hdr_to_sdr(ctx):
    from core import filters as fl
    from core import hdr
    from tabs.transcoder.hdr import subtitle_args
    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    h = hdr.inspect(info, src)
    if not h.is_hdr and not ctx.args.assume:
        raise CliError("This video isn't tagged as HDR, so there is nothing to convert. If you know it is HDR "
                       "(the tags were lost), add --assume pq (HDR10) or --assume hlg.")
    gpu = ctx.args.gpu != "off" and fl.vulkan_ok()
    if h.needs_gpu and not gpu:
        ctx.notes.append("Dolby Vision without an HDR10 layer converted on the CPU: colours may be off.")
    out = output_path(ctx, [src], "sdr")
    ve = venc(ctx, container_of(out))
    ten = bool(ve.pix_fmt) and ve.pix_fmt.endswith(("10le", "p010le", "p010"))
    chain = hdr.sdr_chain(gpu=gpu, curve=ctx.args.curve, out_fmt="yuv420p10le" if ten else "yuv420p",
                          assume=ctx.args.assume or "")
    render_chain(ctx, src, info, out, chain, pre=["-init_hw_device", "vulkan"] if gpu else None,
                 extra=subtitle_args(info, container_of(out)))
    return result(out, source=h.label() if h.is_hdr else f"assumed {ctx.args.assume}",
                  engine="GPU" if gpu else "CPU", notes=ctx.notes)


@operation("extract-audio", "Save the sound as mp3 / m4a / wav / flac / opus (or --copy as is)", "Audio")
@options(lambda p: (_in(p, 1), p.add_argument("--format", default="mp3", choices=["mp3", "m4a", "wav", "flac",
                                                                                 "opus", "ogg"]),
                    p.add_argument("--copy", action="store_true")))
def op_extract_audio(ctx):
    from tabs import common
    from tabs.audio.extractor import copy_extension
    src = ctx.args.inputs[0]
    info = probe(src)
    if not info.has_audio:
        raise CliError("The file has no sound.")
    ext = copy_extension(info.audio.codec) if ctx.args.copy else "." + ctx.args.format
    out = output_path(ctx, [src], "audio", ext)
    part = part_of(out)
    codec = ["-c:a", "copy"] if ctx.args.copy else common.audio_encoder_args(out)
    ctx.run([ff.ffmpeg_path(), "-y", "-i", src, "-map", "0:a:0", "-vn", *codec, part], info.duration)
    finish(part, out)
    return result(out)


@operation("normalize", "Even out loudness to a standard (two-pass): streaming, youtube, ebu, atsc, film", "Audio")
@options(lambda p: (_in(p, 1), p.add_argument("--target", default="streaming",
                                              choices=["streaming", "youtube", "ebu", "atsc", "film"])))
def op_normalize(ctx):
    from core import loudness as ln
    from tabs import common
    src = ctx.args.inputs[0]
    info = probe(src)
    if not info.has_audio:
        raise CliError("The file has no sound.")
    _label, i, tp, lra = ln.TARGETS[ctx.args.target]
    res = ctx.run(ln.measure_cmd(src, i, tp, lra), info.duration, 0.0, 0.5)
    meas = ln.parse_measurement(res.log_tail)
    af = ln.normalise_filter(i, tp, lra, meas, info.audio.sample_rate or 48000)
    out = output_path(ctx, [src], "normalized")
    part = part_of(out)
    ctx.run([ff.ffmpeg_path(), "-y", "-i", src, "-af", af, *common.audio_job_args(part, info), "-map_metadata", "0",
             part], info.duration, 0.5, 1.0)
    finish(part, out)
    return result(out, loudness_target=i)


@operation("clean-voice", "Remove background noise from speech (neural or spectral)", "Audio")
@options(lambda p: (_in(p, 1), p.add_argument("--method", default="neural", choices=["neural", "fft"])))
def op_clean_voice(ctx):
    from tabs import common
    from tabs.audio import voice_isolation as vi
    src = ctx.args.inputs[0]
    info = probe(src)
    if not info.has_audio:
        raise CliError("The file has no sound.")
    af = vi.denoise_filter(ctx.args.method, model=os.path.join(vi.models_dir(), "sh.rnnn"))
    out = output_path(ctx, [src], "clean")
    part = part_of(out)
    ctx.run([ff.ffmpeg_path(), "-y", "-i", src, "-af", af, *common.audio_job_args(part, info), part], info.duration)
    finish(part, out)
    return result(out)


@operation("subtitles", "Make subtitles from speech, offline (SRT next to the video, or --burn into it)", "Social")
@options(lambda p: (_in(p, 1), p.add_argument("--model", default="base", choices=["tiny", "base", "small", "turbo"]),
                    p.add_argument("--language", default="auto"), p.add_argument("--translate", action="store_true",
                                                                                 help="into English"),
                    p.add_argument("--burn", action="store_true"), _video_opts(p)))
def op_subtitles(ctx):
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
    res = ff.run(whisper.transcribe_cmd(wav, whisper.model_path(ctx.args.model), base, language=ctx.args.language,
                                        translate=ctx.args.translate))
    if res.returncode != 0 or not os.path.isfile(base + ".srt"):
        raise CliError("Speech recognition failed:\n" + "\n".join(res.log_tail[-6:]))
    if not ctx.args.burn:
        out = output_path(ctx, [src], "subs", ".srt") if ctx.args.output else os.path.splitext(src)[0] + ".srt"
        if os.path.exists(out) and not ctx.args.overwrite:
            raise CliError(f"{out} already exists - add --overwrite or -o.")
        shutil.copyfile(base + ".srt", out)
        return result(out)
    if not info.has_video:
        raise CliError("--burn needs a video.")
    out = output_path(ctx, [src], "subtitled")
    from core import filters as fl
    render_chain(ctx, src, info, out, fl.subtitles_filter(base + ".srt"))
    return result(out)


@operation("burn-subs", "Burn an existing subtitle file (.srt/.ass/.vtt) into the picture", "Social")
@options(lambda p: (_in(p, 1), p.add_argument("--subs", required=True), _video_opts(p)))
def op_burn_subs(ctx):
    from core import filters as fl
    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    if not os.path.isfile(ctx.args.subs):
        raise CliError(f"Subtitle file not found: {ctx.args.subs}")
    out = output_path(ctx, [src], "subtitled")
    render_chain(ctx, src, info, out, fl.subtitles_filter(ctx.args.subs))
    return result(out)


@operation("gif", "Animated GIF (or --webp) of part of a video, optionally under --max-mb", "Social")
@options(lambda p: (_in(p, 1), p.add_argument("--start", default="0"), p.add_argument("--duration", default="4"),
                    p.add_argument("--fps", type=float, default=15), p.add_argument("--width", type=int, default=480),
                    p.add_argument("--max-mb", type=float, default=0), p.add_argument("--webp", action="store_true")))
def op_gif(ctx):
    from tabs.social.gif_maker import make_animation
    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    start = time_value(ctx.args.start, 0.0)
    dur = min(time_value(ctx.args.duration, 4.0), info.duration - start)
    if dur <= 0:
        raise CliError("--start is past the end.")
    out = output_path(ctx, [src], "anim", ".webp" if ctx.args.webp else ".gif")
    part = part_of(out)
    make_animation(src, part, start=start, dur=dur, fps=ctx.args.fps, width=ctx.args.width // 2 * 2,
                   cap=int(ctx.args.max_mb * 1024 * 1024), webp=ctx.args.webp, run=ctx.run, log=ctx.log)
    finish(part, out)
    return result(out)


@operation("shorts", "Reformat for Shorts/TikTok/Reels/square... (--platform, --background blur|crop|mirror|black)",
           "Social")
@options(lambda p: (_in(p, 1), p.add_argument("--platform", default="shorts"),
                    p.add_argument("--background", default="blur", choices=["blur", "crop", "mirror", "black",
                                                                            "white"]),
                    p.add_argument("--anchor", default="center", choices=["center", "top", "bottom", "left", "right"]),
                    _video_opts(p)))
def op_shorts(ctx):
    from tabs.social import shortifier as sh
    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    if ctx.args.platform not in sh.PLATFORMS or ctx.args.platform == "custom":
        raise CliError("--platform one of: " + ", ".join(k for k in sh.PLATFORMS if k != "custom"))
    _n, tw, th, limit = sh.PLATFORMS[ctx.args.platform]
    graph = sh.reframe_graph(tw, th, ctx.args.background, ctx.args.anchor)
    out = output_path(ctx, [src], ctx.args.platform)
    dur = min(info.duration, limit) if limit else info.duration
    render_graph(ctx, [src], info, out, graph + (";[0:a:0]anull[a]" if info.has_audio else ""),
                 has_audio=info.has_audio, duration=dur)
    return result(out)


@operation("thumbnail", "Save one frame as an image (--at TIME)", "Social")
@options(lambda p: (_in(p, 1), p.add_argument("--at", default="1"), p.add_argument("--format", default="jpg",
                                                                                  choices=["jpg", "png", "webp"])))
def op_thumbnail(ctx):
    from tabs.social.frame_extractor import quality_args
    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    at = min(time_value(ctx.args.at, 1.0), max(0.0, info.duration - 0.05))
    out = output_path(ctx, [src], "frame", "." + ctx.args.format)
    part = part_of(out)
    ctx.run([ff.ffmpeg_path(), "-y", "-ss", f"{at:.3f}", "-i", src, "-frames:v", "1", "-update", "1",
             *quality_args(ctx.args.format, 92), part], 1.0)
    finish(part, out)
    return result(out)


@operation("frames", "Export frames as images (--every SECONDS or --count N) into a folder", "Social")
@options(lambda p: (_in(p, 1), p.add_argument("--every", type=float), p.add_argument("--count", type=int),
                    p.add_argument("--format", default="jpg", choices=["jpg", "png", "webp"])))
def op_frames(ctx):
    from tabs.social.frame_extractor import quality_args
    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    every = ctx.args.every or (info.duration / max(1, ctx.args.count) if ctx.args.count else 1.0)
    folder = ctx.args.output or os.path.splitext(src)[0] + "_frames"
    if os.path.exists(folder) and os.listdir(folder) and not ctx.args.overwrite:
        raise CliError(f"{folder} isn't empty - choose another -o or add --overwrite.")
    os.makedirs(folder, exist_ok=True)
    pattern = os.path.join(folder.replace("%", "%%"), f"frame_%05d.{ctx.args.format}")
    ctx.run([ff.ffmpeg_path(), "-y", "-i", src, "-vf", f"fps=1/{every:.6f}", *quality_args(ctx.args.format, 92),
             pattern], info.duration)
    return {"ok": True, "output": folder, "files": len(os.listdir(folder))}


@operation("logo", "Put a logo/watermark image on a video (--logo FILE --position br --size 15)", "Social")
@options(lambda p: (_in(p, 1), p.add_argument("--logo", required=True), p.add_argument("--position", default="br",
                                                                                      choices=["tl", "tc", "tr", "ml",
                                                                                               "c", "mr", "bl", "bc",
                                                                                               "br"]),
                    p.add_argument("--size", type=float, default=15), p.add_argument("--opacity", type=float, default=0.85),
                    _video_opts(p)))
def op_logo(ctx):
    from tabs.social.image_watermark import logo_graph
    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    probe(ctx.args.logo)
    w, h = info.video.display_size
    graph = logo_graph(w // 2 * 2, h // 2 * 2, pct=ctx.args.size, opacity=ctx.args.opacity, place=ctx.args.position)
    out = output_path(ctx, [src], "logo")
    container = container_of(out)
    ve = venc(ctx, container)
    part = part_of(out)
    cmd = [ff.ffmpeg_path(), "-y", *ve.pre_input, "-i", src, "-loop", "1", "-i", ctx.args.logo,
           "-filter_complex", graph + (f";[v]{ve.filter_suffix}[venc]" if ve.filter_suffix else ""),
           "-map", "[venc]" if ve.filter_suffix else "[v]", *keep_audio(info, container), *ve.args, "-t",
           f"{info.duration:.3f}", part]
    ctx.run(cmd, info.duration)
    finish(part, out)
    return result(out)


@operation("collage", "Put several clips on one screen (auto layout; --size, --fill, --no-loop, --end, --audio)",
           "Social")
@options(lambda p: (_in(p), p.add_argument("--size", default="1920x1080"), p.add_argument("--fill", action="store_true"),
                    p.add_argument("--gap", type=int, default=4), p.add_argument("--no-loop", action="store_true"),
                    p.add_argument("--end", default="hold", choices=["hold", "background", "reflow"]),
                    p.add_argument("--length", default="longest", help="longest, shortest or seconds"),
                    p.add_argument("--audio", default="first", choices=["first", "mix", "none"]),
                    p.add_argument("--background", default="black"), _video_opts(p)))
def op_collage(ctx):
    from core import grid
    from tabs import common
    paths = ctx.args.inputs
    infos = [probe(x, video=True) for x in paths]
    W, H = (int(v) // 2 * 2 for v in ctx.args.size.lower().split("x"))
    clips = []
    for i, info in enumerate(infos):
        w, h = info.video.display_size
        dur = info.video.duration if info.video.duration > 0.05 else info.duration
        clips.append(grid.Clip(i, w / max(1, h), dur, info.has_audio))
    durs = [c.duration for c in clips]
    total = max(durs) if ctx.args.length == "longest" else min(durs) if ctx.args.length == "shortest" else \
        float(ctx.args.length)
    loop = not ctx.args.no_loop
    graph, has_a = grid.collage_filter(clips, W, H, fps=common.frame_rate(infos[0]), gap=ctx.args.gap,
                                       mode="fill" if ctx.args.fill else "fit", bg=ctx.args.background, total=total,
                                       loop=loop, end=ctx.args.end, audio=ctx.args.audio)
    out = output_path(ctx, paths, "collage", ".mp4")
    render_graph(ctx, paths, infos[0], out, graph, has_audio=has_a, duration=total,
                 input_opts={i: ["-stream_loop", "-1"] for i in range(len(paths))} if loop else None)
    lay = grid.best_layout([c.aspect for c in clips], W, H, gap=ctx.args.gap, mode="fill" if ctx.args.fill else "fit")
    return result(out, layout=lay.describe())


@operation("blur-region", "Blur / pixelate / black-box a region (--region X,Y,W,H as % of the frame)", "Visuals")
@options(lambda p: (_in(p, 1), p.add_argument("--region", action="append", required=True,
                                              help="X,Y,W,H in percent; repeat for more regions"),
                    p.add_argument("--type", default="blur", choices=["blur", "pixelate", "box"]),
                    p.add_argument("--from", dest="start", default="0"), p.add_argument("--to", dest="end"),
                    _video_opts(p)))
def op_blur_region(ctx):
    from tabs.visuals.region_blur import Region, blur_graph
    src = ctx.args.inputs[0]
    info = probe(src, video=True)
    regions = []
    for r in ctx.args.region:
        x, y, w, h = (float(v) / 100 for v in r.split(","))
        regions.append(Region(x, y, w, h, ctx.args.type, 0.6, time_value(ctx.args.start, 0.0),
                              time_value(ctx.args.end) if ctx.args.end else None))
    W, H = info.video.display_size
    out = output_path(ctx, [src], "blurred")
    render_graph(ctx, [src], info, out, blur_graph(regions, W // 2 * 2, H // 2 * 2) +
                 (";[0:a:0]anull[a]" if info.has_audio else ""), has_audio=info.has_audio)
    return result(out)


def _chain_op(name: str, summary: str, build, suffix: str, add_args=None):
    def configure(p):
        _in(p, 1)
        if add_args:
            add_args(p)
        _video_opts(p)

    @operation(name, summary, "Visuals")
    @options(configure)
    def run(ctx):
        src = ctx.args.inputs[0]
        info = probe(src, video=True)
        out = output_path(ctx, [src], suffix)
        render_chain(ctx, src, info, out, build(ctx.args))
        return result(out)
    return run


def _denoise(a):
    from tabs.visuals.denoise import denoise_chain
    return denoise_chain(a.method, a.strength)


def _sharpen(a):
    from tabs.visuals.sharpen import sharpen_chain
    return sharpen_chain(a.method, a.amount)


def _deinterlace(a):
    from tabs.visuals.deinterlace import deinterlace_chain
    return deinterlace_chain(a.mode)


def _lut(a):
    from tabs.visuals.lut import build_chain
    return build_chain(a.lut, intensity=a.intensity)


_chain_op("denoise", "Reduce video noise/grain (--method fast|nlm|temporal|grain, --strength 1-10)", _denoise,
          "denoised", lambda p: (p.add_argument("--method", default="fast", choices=["fast", "nlm", "temporal",
                                                                                     "grain"]),
                                 p.add_argument("--strength", type=float, default=5)))
_chain_op("sharpen", "Sharpen (--method cas|unsharp|clarity, --amount 0-1)", _sharpen, "sharp",
          lambda p: (p.add_argument("--method", default="cas", choices=["cas", "unsharp", "clarity"]),
                     p.add_argument("--amount", type=float, default=0.45)))
_chain_op("deinterlace", "Remove interlacing combs (--mode smart|all|double|ivtc)", _deinterlace, "progressive",
          lambda p: p.add_argument("--mode", default="smart", choices=["smart", "all", "double", "ivtc"]))
_chain_op("lut", "Apply a colour look-up table (--lut FILE.cube, --intensity 0-1)", _lut, "graded",
          lambda p: (p.add_argument("--lut", required=True), p.add_argument("--intensity", type=float, default=1.0)))


@operation("metadata-clean", "Remove location, device, dates, names and telemetry - no re-encode", "System")
@options(lambda p: (_in(p, 1), p.add_argument("--deep", action="store_true"),
                    p.add_argument("--keep-chapters", action="store_true"),
                    p.add_argument("--keep-cover", action="store_true"),
                    p.add_argument("--drop-languages", action="store_true")))
def op_metadata_clean(ctx):
    from core import metaclean as mc
    src = ctx.args.inputs[0]
    info = probe(src)
    before = mc.inspect(info)
    out = output_path(ctx, [src], "clean")
    part = part_of(out)
    argv, notes = mc.clean_command(info, src, part, keep_language=not ctx.args.drop_languages,
                                   keep_chapters=ctx.args.keep_chapters, keep_cover=ctx.args.keep_cover,
                                   deep=ctx.args.deep)
    ctx.run(argv, info.duration)
    finish(part, out)
    after = mc.inspect(ff.probe(out, use_cache=False))
    left = mc.leftovers(after, keep_chapters=ctx.args.keep_chapters, keep_cover=ctx.args.keep_cover)
    return result(out, removed=[f.line() for f in before], still_there=[f.line() for f in left], notes=notes)


@operation("tts", "Speak text into an audio file (--text or --text-file; --offline for the system voice)", "Audio")
@options(lambda p: (p.add_argument("--text"), p.add_argument("--text-file"),
                    p.add_argument("--voice", default="en-GB-RyanNeural"), p.add_argument("--offline", action="store_true")))
def op_tts(ctx):
    from core import tts
    text = ctx.args.text or (open(ctx.args.text_file, encoding="utf-8").read() if ctx.args.text_file else "")
    if not text.strip():
        raise CliError("Give --text or --text-file.")
    engine = "system" if ctx.args.offline or not tts.edge_available() else "edge"
    out = ctx.args.output or os.path.abspath("speech" + (".mp3" if engine == "edge" else ".wav"))
    if os.path.exists(out) and not ctx.args.overwrite:
        raise CliError(f"{out} already exists - add --overwrite or -o.")
    tmp = os.path.join(ctx.tmp(), "speech" + (".mp3" if engine == "edge" else ".wav"))
    try:
        tts.synthesize(text, tmp, engine=engine, voice=ctx.args.voice)
    except tts.TTSError as exc:
        raise CliError(str(exc)) from None
    if os.path.splitext(out)[1].lower() == os.path.splitext(tmp)[1].lower():
        shutil.copyfile(tmp, out)
    else:
        from tabs import common
        ctx.run([ff.ffmpeg_path(), "-y", "-i", tmp, *common.audio_encoder_args(out), out])
    return result(out, engine=engine)


@operation("download", "Download a video (or --audio-only) from YouTube and 1000+ sites, following the app's "
           "network settings", "Social")
@options(lambda p: (p.add_argument("url"), p.add_argument("--audio-only", action="store_true"),
                    p.add_argument("--max-height", type=int, default=0),
                    p.add_argument("--format", default="mp4", choices=["mp4", "mkv", "webm"])))
def op_download(ctx):
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
        fmt = f"bv*[height<={ctx.args.max_height}]+ba/b[height<={ctx.args.max_height}]" if ctx.args.max_height else \
            "bv*+ba/b"
        argv += ["-f", fmt, "--merge-output-format", ctx.args.format]
    argv += ["--no-overwrites", "--no-playlist", "--print", "after_move:filepath", "-o", template, ctx.args.url]
    import subprocess
    r = subprocess.run(argv, capture_output=True, text=True, env=network.subprocess_env(url=ctx.args.url))
    if r.returncode != 0:
        raise CliError("Download failed:\n" + "\n".join((r.stderr or r.stdout).strip().splitlines()[-6:]))
    path = (r.stdout.strip().splitlines() or [""])[-1]
    return result(path) if path and os.path.isfile(path) else {"ok": True, "output": folder}


# ── Entry point ───────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="wyldveil-media-editor --cli",
                                 description="Edit and convert video/audio from the command line (same engine as the "
                                             "app). Inputs are never changed; outputs never replace existing files "
                                             "unless --overwrite.")
    sub = ap.add_subparsers(dest="command", metavar="COMMAND")
    t = sub.add_parser("tools", help="list everything that can be done")
    t.add_argument("--json", action="store_true")
    i = sub.add_parser("info", help="describe a media file (streams, HDR, rotation, personal metadata)")
    i.add_argument("file")
    i.add_argument("--json", action="store_true")
    pth = sub.add_parser("paths", help="where the bundled FFmpeg, ffprobe, yt-dlp... are")
    pth.add_argument("--json", action="store_true")
    sub.add_parser("guide", help="print the guide for AI assistants and scripts (AGENTS.md)")
    for name, (summary, _cat, configure, _fn) in OPERATIONS.items():
        p = sub.add_parser(name, help=summary.replace("%", "%%"), description=summary.replace("%", "%%"))
        if configure:
            configure(p)
        p.add_argument("-o", "--output", help="output file (or folder); default: next to the input")
        p.add_argument("--overwrite", action="store_true", help="allow replacing an existing output file")
        p.add_argument("--json", action="store_true", help="print the result as JSON (last line)")
    return ap


def tools_catalogue(parser: argparse.ArgumentParser) -> list:
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    out = []
    for name, (summary, cat, _c, _f) in OPERATIONS.items():
        p = sub.choices[name]
        opts = []
        for a in p._actions:
            if isinstance(a, argparse._HelpAction) or a.dest in ("output", "overwrite", "json"):
                continue
            opts.append({"name": a.option_strings[0] if a.option_strings else a.dest,
                         "required": bool(getattr(a, "required", False)) or not a.option_strings,
                         "choices": list(a.choices) if a.choices else None,
                         "default": a.default if a.default not in (None, False, argparse.SUPPRESS) else None,
                         "help": a.help or ""})
        out.append({"name": name, "category": cat, "summary": summary, "options": opts})
    return out


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = getattr(args, "json", False)

    def emit(d: dict) -> None:
        if as_json:
            print(json.dumps(d, ensure_ascii=False))
        elif d.get("ok"):
            for k, v in d.items():
                if k != "ok":
                    print(f"{k}: {v}")
        else:
            print("error: " + d.get("error", "failed"), file=sys.stderr)

    if args.command in (None, "tools"):
        cat = tools_catalogue(parser)
        if as_json:
            print(json.dumps({"ok": True, "operations": cat}, ensure_ascii=False))
        else:
            current = None
            for op in sorted(cat, key=lambda o: (o["category"], o["name"])):
                if op["category"] != current:
                    current = op["category"]
                    print(f"\n{current}:")
                print(f"  {op['name']:<16} {op['summary']}")
            print("\nDetails: wyldveil-media-editor --cli OPERATION --help")
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
            res = OPERATIONS[args.command][3](ctx)
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
                attached = bool(ctypes.windll.kernel32.AttachConsole(-1))    # ATTACH_PARENT_PROCESS
            except Exception:
                attached = False
        target = "CONOUT$" if attached else os.devnull
        if sys.stdout is None:
            sys.stdout = open(target, "w", encoding="utf-8", errors="replace", buffering=1)
        if sys.stderr is None:
            sys.stderr = open(target, "w", encoding="utf-8", errors="replace", buffering=1)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")      # JSON and names with any characters
        except (AttributeError, ValueError):
            pass


def entry(argv: list) -> int:
    """Called by main.py before any window code is imported."""
    _ensure_streams()
    os.environ.setdefault("CROSSFADER_NO_NOTIFY", "1")
    if argv[:1] == ["--cli"]:
        argv = argv[1:]
    try:
        return main(argv)
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130
