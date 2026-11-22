"""
core/ops/effects.py - the filters and commands of Special Effects (tabs/visuals/effects.py).

Values in, filter graphs and FFmpeg arguments out - no widgets, no Tk (one
probe, first_visible_time, runs FFmpeg to find the end of a black opening).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from core import ffmpeg as ff
from core import filters as fl

logger = logging.getLogger("crossfader.ops.effects")

#: every effect's output: even size, square pixels, 8-bit 4:2:0
FX_GUARD = "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1,format=yuv420p"


def first_visible_time(path: str, scan: float = 10.0) -> float:
    """End of any black opening (blackdetect), or 0.0; fast, single pass."""
    try:
        r = ff.capture(
            [
                ff.ffmpeg_path(),
                "-hide_banner",
                "-nostdin",
                "-t",
                f"{scan:.1f}",
                "-i",
                path,
                "-vf",
                "blackdetect=d=0.05:pix_th=0.10",
                "-an",
                "-f",
                "null",
                "-",
            ],
            timeout=60,
        )
        m = re.search(r"black_start:0(?:\.0+)?\s+black_end:([\d.]+)", r.stderr or "")
        return float(m.group(1)) + 0.05 if m else 0.0
    except Exception:
        logger.debug("_first_visible_time: best-effort step failed", exc_info=True)
        return 0.0


def fadein_graph(fade: float, still_at: float):
    graph = (
        f"[0:v]setpts=PTS-STARTPTS,split[m][s];"
        f"[s]trim=start={still_at:.3f},setpts=PTS-STARTPTS,trim=end_frame=1,"
        f"loop=loop=-1:size=1,trim=duration={fade:.3f},setpts=PTS-STARTPTS,"
        f"format=rgba,fade=t=out:st=0:d={fade:.3f}:alpha=1[ov];"
        f"[m][ov]overlay=eof_action=pass,{FX_GUARD}[v]"
    )
    return graph


def watermark_cmd(crf: str, filter_str: str, out: str, src: str):
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        "-i",
        src,
        "-vf",
        filter_str,
        "-c:v",
        "libx264",
        "-crf",
        crf,
        "-preset",
        "fast",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        out,
    ]
    return cmd


def cinebars_graph(bar: int, fps: str, h: int, w: int, ybot: str, ytop: str):
    graph = (
        f"color=c=black:s={w}x{bar}:r={fps}[tb];color=c=black:s={w}x{bar}:r={fps}[bb];"
        f"[0:v]scale={w}:{h}[base];"
        f"[base][tb]overlay=x=0:y='{ytop}':shortest=1[o1];"
        f"[o1][bb]overlay=x=0:y='{ybot}':shortest=1,{FX_GUARD}[v]"
    )
    return graph


def meme_cmd(crf: str, filters: list[str], out: str, src: str):
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        "-i",
        src,
        "-vf",
        ",".join(filters),
        "-c:v",
        "libx264",
        "-crf",
        crf,
        "-preset",
        "fast",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        out,
    ]
    return cmd


def whippan_graph(at: float, d: float, sx: int, sy: int):
    graph = f"avgblur=sizeX={sx}:sizeY={sy}:enable='between(t,{at:.3f},{at + d:.3f})',{FX_GUARD}"
    return graph


def kenburns_graph(fps: str, h: int, p: str, w: int, x: str, y: str, z0: float, z1: float):
    graph = f"zoompan=z='{z0}+({z1}-{z0})*{p}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={fps},{FX_GUARD}"
    return graph


def zoompunch_graph(fps: str, h: int, w: int, zexpr: str):
    graph = (
        f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:"
        f"s={w}x{h}:fps={fps},{FX_GUARD}"
    )
    return graph


def lightleak_graph(colour: str, dur: float, fps: str, h: int, half: float, start: float, w: int):
    graph = (
        f"gradients=s={w}x{h}:r={fps}:c0=0x{colour}:c1=0x000000:x0=0:y0=0:x1={w}:y1={h // 3}:"
        f"nb_colors=2:speed=0:d={dur:.3f},format=gbrp,"
        f"fade=t=in:st={start:.3f}:d={half:.3f}:color=black,"
        f"fade=t=out:st={start + half:.3f}:d={half:.3f}:color=black[leak];"
        f"[0:v]setpts=PTS-STARTPTS,scale={w}:{h},format=gbrp[base];"
        f"[base][leak]blend=all_mode=screen:shortest=1,{FX_GUARD}[v]"
    )
    return graph


def vignette_cmd(angle: str, crf: str, out: str, src: str):
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        "-i",
        src,
        "-vf",
        f"vignette=angle={angle}",
        "-c:v",
        "libx264",
        "-crf",
        crf,
        "-preset",
        "fast",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        out,
    ]
    return cmd


def grain_cmd(amount: int, crf: str, out: str, src: str):
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        "-i",
        src,
        "-vf",
        f"noise=alls={amount}:allf=t+u",
        "-c:v",
        "libx264",
        "-crf",
        crf,
        "-preset",
        "fast",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        out,
    ]
    return cmd


def vhs_cmd(
    chroma_blur: int, crf: str, luma_blur: float, noise: int, out: str, scan_alpha: float, src: str
):
    filter_str = (
        # Chroma blur simulates colour bleed
        "split=2[main][cb];"
        f"[cb]hue=s=1.3,boxblur=luma_radius={chroma_blur}:chroma_radius={chroma_blur}[cblur];"
        "[main][cblur]blend=all_mode=overlay:all_opacity=0.3[blended];"
        # Luma softness
        f"[blended]unsharp=3:3:-{luma_blur}[soft];"
        # Noise (grain)
        f"[soft]noise=alls={noise}:allf=t[noisy];"
        # Scanlines
        "[noisy]drawgrid="
        "width=0:height=2:thickness=1:"
        f"color=black@{scan_alpha}[v]"
    )

    cmd = [
        ff.ffmpeg_path(),
        "-y",
        "-i",
        src,
        "-filter_complex",
        filter_str,
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-crf",
        crf,
        "-preset",
        "fast",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        out,
    ]
    return cmd


def rgb_cmd(crf: str, enable: str, offset: int, out: str, src: str):
    filter_str = (
        "[0:v]split=3[r0][g0][b0];"
        "[r0]geq=r='r(X,Y)':g=0:b=0[r];"
        "[g0]geq=r=0:g='g(X,Y)':b=0[g];"
        "[b0]geq=r=0:g=0:b='b(X,Y)'[b];"
        f"[r]pad=iw+{offset}:ih:0:0:color=black[rp];"
        f"[b]pad=iw+{offset}:ih:{offset}:0:color=black[bp];"
        f"[g]pad=iw+{offset}:ih:{offset // 2}:0:color=black[gp];"
        "[rp][gp]blend=all_mode=addition[rg];"
        "[rg][bp]blend=all_mode=addition,"
        f"crop=iw-{offset}:ih:0:0"
        "[glitch];"
        f"[0:v][glitch]overlay=0:0:enable='{enable}'[v]"
    )

    cmd = [
        ff.ffmpeg_path(),
        "-y",
        "-i",
        src,
        "-filter_complex",
        filter_str,
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-crf",
        crf,
        "-preset",
        "fast",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        out,
    ]
    return cmd


def progressbar_graph(bar_h: int, colour: str, dur: float, fps: str, w: int, y: int):
    graph = (
        f"color=c=0x{colour}:s={w}x{bar_h}:r={fps}[bar];"
        f"[0:v][bar]overlay=x='-w+w*t/{max(dur, 0.1):.3f}':y={y}:shortest=1,{FX_GUARD}[v]"
    )
    return graph


def countdown_cmd(
    count_from: int, crf: str, end: float, out: str, size: int, src: str, start: float
):
    filter_str = (
        "drawtext="
        f"text='%{{eif\\:{count_from}-(t-{start})\\:d}}':"
        f"fontsize={size}:"
        "fontcolor=white:"
        "x=(w-text_w)/2:y=(h-text_h)/2:"
        "shadowcolor=black@0.8:shadowx=4:shadowy=4:"
        "box=1:boxcolor=black@0.5:boxborderw=12:"
        f"enable='between(t,{start},{end})'"
    )

    cmd = [
        ff.ffmpeg_path(),
        "-y",
        "-i",
        src,
        "-vf",
        filter_str,
        "-c:v",
        "libx264",
        "-crf",
        crf,
        "-preset",
        "fast",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        out,
    ]
    return cmd


def lowerthird_graph(
    bar_h: int, colour: str, enable: str, fps: str, slide: str, sub: str, title: str, w: int
):
    graph = (
        f"color=c=0x{colour}@0.85:s={w}x{bar_h}:r={fps},format=rgba[bar];"
        f"[0:v][bar]overlay=x=0:y='{slide}':enable='{enable}':shortest=1,"
        f"{title},{sub},{FX_GUARD}[v]"
    )
    return graph


def irisclose_graph(
    d: float, dur: float, fps: str, h: int, info: ff.MediaInfo, start: float, w: int
):
    graph = (
        f"[0:v]setpts=PTS-STARTPTS,trim=duration={dur:.3f},scale={w}:{h},fps={fps},"
        f"format=yuv420p,setsar=1,settb=AVTB[v0];"
        f"color=c=black:s={w}x{h}:r={fps}:d={d:.3f},format=yuv420p,setsar=1,settb=AVTB[blk];"
        f"[v0][blk]xfade=transition=circleclose:duration={d:.3f}:offset={start:.3f},{FX_GUARD}[v]"
    )
    audio = ["-an"]
    if info.has_audio:
        audio = [
            "-map",
            "0:a:0",
            "-af",
            f"afade=t=out:st={start:.3f}:d={d:.3f}",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]
    return audio, graph


def freezezoom_graph(
    dur: float, extra: float, fade: float, fps: str, h: int, hold: float, info: ff.MediaInfo, w: int
):
    graph = (
        f"[0:v]setpts=PTS-STARTPTS,trim=duration={dur:.3f},tpad=stop_mode=clone:stop_duration={extra:.3f},"
        f"zoompan=z='if(gte(it,{dur:.3f}),max(1,1.1-0.1*(it-{dur:.3f})/{max(hold, 0.1):.3f}),1)':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps}"
        + (f",fade=t=out:st={dur + hold:.3f}:d={fade:.3f}" if fade > 0 else "")
        + f",{FX_GUARD}[v]"
    )
    audio = ["-an"]
    if info.has_audio:
        af = f"apad=pad_dur={extra:.3f}" + (
            f",afade=t=out:st={dur + hold:.3f}:d={fade:.3f}" if fade > 0 else ""
        )
        audio = ["-map", "0:a:0", "-af", af, "-c:a", "aac", "-b:a", "192k"]
    return audio, graph


def fadeout_cmd(crf: str, dur: float, out: str, src: str, start: float):
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        "-i",
        src,
        "-vf",
        f"fade=type=out:st={start}:d={dur}",
        "-af",
        f"afade=type=out:st={start}:d={dur}",
        "-c:v",
        "libx264",
        "-crf",
        crf,
        "-preset",
        "fast",
        "-movflags",
        "+faststart",
        out,
    ]
    return cmd


def splitopen_graph(d: float, fps: str, h: int, trans: str, w: int):
    graph = (
        f"color=c=black:s={w}x{h}:r={fps}:d={d:.3f},format=yuv420p,setsar=1,settb=AVTB[blk];"
        f"[0:v]setpts=PTS-STARTPTS,scale={w}:{h},fps={fps},format=yuv420p,setsar=1,settb=AVTB[vid];"
        f"[blk][vid]xfade=transition={trans}:duration={d:.3f}:offset=0,{FX_GUARD}[v]"
    )
    return graph


def spinin_graph(d: float, k: str, turns: float):
    graph = (
        f"[0:v]setpts=PTS-STARTPTS,split[a][b];[a]drawbox=c=black:t=fill[bg];"
        f"[b]format=rgba,rotate=a='if(lte(t,{d:.3f}),{turns}*2*PI*(1-pow(t/{d:.3f},2)),0)':c=none,"
        f"scale=w='max(2,trunc(iw*{k}/2)*2)':h='max(2,trunc(ih*{k}/2)*2)':eval=frame[s];"
        f"[bg][s]overlay=x='(W-w)/2':y='(H-h)/2':eval=frame,{FX_GUARD}[v]"
    )
    return graph


def slidein_graph(x: str, y: str):
    graph = (
        f"[0:v]setpts=PTS-STARTPTS,split[a][b];[a]drawbox=c=black:t=fill[bg];"
        f"[bg][b]overlay=x='{x}':y='{y}':eval=frame,{FX_GUARD}[v]"
    )
    return graph


def zoomburst_graph(fps: str, h: int, n: int, w: int, z: float):
    graph = (
        f"zoompan=z='if(lte(on,{n}),{z}-({z}-1)*on/{n},1)':x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps},{FX_GUARD}"
    )
    return graph


def ensure_420(cmd: list[str]) -> None:
    """An x264 command without a pixel format gets 4:2:0, which every player plays (in place)."""
    if "libx264" in cmd and "-pix_fmt" not in cmd:
        cmd[-1:-1] = ["-pix_fmt", "yuv420p"]


def first_input(cmd: list[str]) -> str | None:
    """The file after the first -i of *cmd* (None: no input)."""
    return cmd[cmd.index("-i") + 1] if "-i" in cmd[:-1] else None


def geometry(info: ff.MediaInfo) -> tuple[int, int, str, float]:
    """(even width, even height, exact frame rate, duration) of a source."""
    v = info.require_video()
    w, h = v.display_size
    w, h = max(2, w // 2 * 2), max(2, h // 2 * 2)
    fps = str(v.raw.get("avg_frame_rate") or "30/1")
    if fps in ("0/0", "") or not v.fps or v.fps > 240:
        fps = "30/1"
    dur = v.duration if v.duration > 0.05 else info.duration
    return w, h, fps, dur


def x264_args(crf_text: str) -> list[str]:
    """x264 at the CRF typed (18 when it isn't a whole number), 4:2:0, ready to stream."""
    crf = crf_text.strip() or "18"
    if not crf.isdigit():
        crf = "18"
    return [
        "-c:v",
        "libx264",
        "-crf",
        crf,
        "-preset",
        "fast",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]


def effect_audio(info: ff.MediaInfo, copy: bool = True) -> list[str]:
    """The source's sound: copied when the container takes it, else AAC; none if it has none."""
    if not info.has_audio:
        return ["-an"]
    if copy and (info.require_audio().codec in ("aac", "mp3", "ac3", "opus", "alac", "flac")):
        return ["-map", "0:a:0", "-c:a", "copy"]
    return ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Shake:
    """Camera shake: *amp* pixels each way, *freq1*/*freq2* the sideways and
    up-down speeds, from *start* for *span* seconds (None: to the end)."""

    amp: int
    start: float
    span: float | None
    freq1: int
    freq2: int
    crf: str


def shake_cmd(src: str, out: str, size: tuple[int, int], total: float, s: Shake) -> list[str]:
    """The shaken copy: a moving crop (hiding the edges the movement would show), scaled back."""
    w, h = size
    end = total if s.span is None else s.start + s.span
    enable = f"between(t,{s.start},{end})"
    cw, ch = w - s.amp * 2, h - s.amp * 2
    filter_str = (
        f"crop={cw}:{ch}:"
        f"x='if({enable},{s.amp}+{s.amp}*sin(t*{s.freq1}),{s.amp})':"
        f"y='if({enable},{s.amp}+{s.amp}*cos(t*{s.freq2}),{s.amp})',"
        f"scale={w}:{h},setsar=1,format=yuv420p"
    )
    return [ff.ffmpeg_path(), "-y", "-i", src, "-vf", filter_str, *x264_fast(s.crf), out]


def x264_fast(crf: str) -> list[str]:
    """The quick x264 encode these overlay effects share (the sound copied)."""
    return [
        "-c:v",
        "libx264",
        "-crf",
        crf,
        "-preset",
        "fast",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
    ]


@dataclass(frozen=True, slots=True, kw_only=True)
class Pop:
    """An emoji that pops in at (*xpct*, *ypct*) of the frame for *hold* seconds."""

    text: str
    size: int
    xpct: float
    ypct: float
    start: float
    hold: float
    crf: str


def emoji_cmd(src: str, out: str, frame: tuple[int, int], p: Pop) -> list[str]:
    """The emoji grows from nothing to full size over 0.2 s, then holds."""
    w, h = frame
    end = p.start + p.hold
    x_px, y_px = int(w * p.xpct), int(h * p.ypct)  # centred on the chosen point
    scale_expr = f"if(lt(t-{p.start},0.2),max(1,{p.size}*(t-{p.start})/0.2),{p.size})"
    filter_str = fl.DrawText(
        textfile=fl.drawtext_text_file(p.text),
        font="emoji",
        fontsize=scale_expr,
        fontcolor="white",
        x=f"{x_px}-text_w/2",
        y=f"{y_px}-text_h/2",
        shadowx=3,
        shadowy=3,
        shadowcolor="black@0.7",
        enable=f"between(t,{p.start},{end})",
    ).filter()
    return [ff.ffmpeg_path(), "-y", "-i", src, "-vf", filter_str, *x264_fast(p.crf), out]


#: where the subscribe banner sits (the drop-down's choices)
BANNER_PLACES = {
    "Bottom Centre": ("(w-text_w)/2", "h-text_h-40"),
    "Top Centre": ("(w-text_w)/2", "40"),
    "Bottom Left": ("40", "h-text_h-40"),
    "Bottom Right": ("w-text_w-40", "h-text_h-40"),
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Banner:
    """The subscribe reminder: *text* from *start* to *end* at *position* (a BANNER_PLACES key)."""

    text: str
    size: int
    start: float
    end: float
    position: str
    crf: str


def subscribe_cmd(src: str, out: str, b: Banner) -> list[str]:
    x_expr, y_expr = BANNER_PLACES.get(b.position, BANNER_PLACES["Bottom Centre"])
    filter_str = fl.DrawText(
        textfile=fl.drawtext_text_file(b.text),
        font="sans bold",
        fontsize=b.size,
        fontcolor="white",
        x=x_expr,
        y=y_expr,
        shadowx=3,
        shadowy=3,
        shadowcolor="black@0.8",
        box=True,
        boxcolor="black@0.45",
        boxborderw=8,
        enable=f"between(t,{b.start},{b.end})",
    ).filter()
    return [ff.ffmpeg_path(), "-y", "-i", src, "-vf", filter_str, *x264_fast(b.crf), out]
