"""
core/ops/media_gen.py - test patterns, countdown leaders and line-up signals,
for tabs/visuals/media_gen.py.

Pure functions: values in, lavfi sources and an FFmpeg command out - no
widgets, no Tk.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl

AUDIO_EXTS = (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus")


def _fps_float(rate: str) -> float:
    num, _, den = rate.partition("/")
    return float(num) / float(den or 1)


def video_source(
    pattern: str,
    w: int,
    h: int,
    rate: str,
    dur: float,
    colour: str = "202020",
    textfile: str | None = None,
) -> str:
    """A lavfi graph producing the pattern (single output)."""
    common_opts = f"size={w}x{h}:rate={rate}:duration={dur:.3f}"
    if pattern == "smpte_hd":
        return f"smptehdbars={common_opts}"
    if pattern == "smpte":
        return f"smptebars={common_opts}"
    if pattern == "ebu":
        return f"pal75bars={common_opts}"
    if pattern == "testsrc":
        return f"testsrc2={common_opts}"
    if pattern == "color":
        return f"color=c=0x{colour}:{common_opts}"
    if pattern == "black":
        return f"color=c=black:{common_opts}"
    if pattern == "static":
        return f"color=c=0x808080:{common_opts},format=yuv420p,noise=c0s=100:c0f=t+u"
    if pattern == "gradient":
        return (
            f"gradients={common_opts}:speed=0.015:nb_colors=4:type=radial:"
            f"c0=0x1E3A8A:c1=0x7C3AED:c2=0xDB2777:c3=0xF59E0B"
        )
    if pattern == "mandelbrot":
        return f"mandelbrot=size={w}x{h}:rate={rate},trim=duration={dur:.3f}"
    if pattern == "life":
        return (
            f"life=size={max(16, w // 4)}x{max(16, h // 4)}:rate={rate}:mold=10:ratio=0.1:"
            f"life_color=0x00FF88:death_color=0x802040:mold_color=0x202040,"
            f"scale={w}:{h}:flags=neighbor,trim=duration={dur:.3f}"
        )
    if pattern == "countdown":
        return countdown_source(w, h, rate, dur, textfile)
    raise ValueError(f"unknown pattern {pattern}")


def countdown_length(dur: float) -> int:
    return max(3, round(dur))


def countdown_source(w: int, h: int, rate: str, dur: float, textfile: str | None) -> str:
    """Academy-style leader: N…3 with a clock sweep, one frame of "2", then black."""
    n = countdown_length(dur)
    frame = 1.0 / _fps_float(rate)
    cut = n - 2 + frame
    hw, hh = max(2, w // 4 * 2), max(2, h // 4 * 2)  # sweep drawn at half size
    sweep = (
        "geq=lum='st(0,hypot(X-W/2,Y-H/2));st(1,atan2(X-W/2,H/2-Y));"
        "st(1,if(lt(ld(1),0),ld(1)+2*PI,ld(1)));"
        "if(lt(abs(ld(0)-H*0.42),2)+lt(abs(ld(0)-H*0.34),1.5)+lt(abs(X-W/2),1)+lt(abs(Y-H/2),1),"
        "225,if(lt(ld(0),H*0.42)*lt(ld(1),mod(T,1)*2*PI),150,70))'"
    )
    number = fl.DrawText(
        textfile=textfile,
        font="sans bold",
        fontsize=int(h * 0.36),
        fontcolor="white",
        x="(w-text_w)/2",
        y="(h-text_h)/2",
        expansion="normal",
        enable=f"lt(t,{cut:.4f})",
    ).filter()
    return (
        f"color=c=black:size={hw}x{hh}:rate={rate}:duration={n},format=gray,{sweep},"
        f"scale={w}:{h}:flags=bicubic,format=yuv420p,{number},"
        f"drawbox=c=black:t=fill:enable='gte(t,{cut:.4f})'"
    )


def audio_source(
    kind: str, dur: float, *, freq: float = 1000.0, pop_at: float = 0.0, frame: float = 0.04
) -> str | None:
    """A lavfi graph producing a stereo, 48 kHz signal (exact levels: the mono
    generators are duplicated to both channels, not down-mixed by -3 dB)."""
    stereo = ",pan=stereo|c0=c0|c1=c0"
    if kind == "none":
        return None
    if kind == "tone18":  # FFmpeg's sine is 1/8 of full scale = -18.06 dBFS peak
        return f"sine=frequency=1000:sample_rate=48000:duration={dur:.3f}{stereo}"
    if kind == "tone20":
        return f"sine=frequency=1000:sample_rate=48000:duration={dur:.3f},volume=-1.94dB{stereo}"
    if kind == "tone":
        return f"sine=frequency={freq:g}:sample_rate=48000:duration={dur:.3f}{stereo}"
    if kind == "pop":
        return (
            f"sine=frequency=1000:sample_rate=48000:duration={frame:.4f},"
            f"adelay=delays={pop_at * 1000:.0f}:all=1,apad=whole_dur={dur:.3f}{stereo}"
        )
    if kind in ("pink", "white"):
        return f"anoisesrc=color={kind}:amplitude=0.25:sample_rate=48000:duration={dur:.3f}{stereo}"
    return f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={dur:.3f}"


@dataclass(frozen=True, slots=True, kw_only=True)
class Generate:
    """What to make: a *pattern* (or "none") and an *audio* signal (or "none")."""

    pattern: str
    audio: str
    seconds: float
    width: int = 1920
    height: int = 1080
    rate: str = "25"
    colour: str = "202020"
    freq: float = 1000.0


def generate_cmd(g: Generate, out: str) -> tuple[list[str], float, str | None]:
    """(argv, seconds, countdown text file to delete afterwards).  An audio
    file name (or pattern "none") makes sound only; a countdown runs whole seconds."""
    ext = os.path.splitext(out)[1].lower()
    audio_only = g.pattern == "none" or ext in AUDIO_EXTS
    dur = float(countdown_length(g.seconds)) if g.pattern == "countdown" else g.seconds
    cmd, maps = [ff.ffmpeg_path(), "-y"], list[str]()
    textfile = None
    if not audio_only:
        if g.pattern == "countdown":
            textfile = fl.drawtext_text_file(f"%{{eif:{countdown_length(dur)}-trunc(t):d}}")
        cmd += [
            "-f",
            "lavfi",
            "-i",
            video_source(g.pattern, g.width, g.height, g.rate, dur, g.colour, textfile),
        ]
        maps += ["-map", "0:v"]
    frame = 1.0 / _fps_float(g.rate)
    asrc = audio_source(g.audio, dur, freq=g.freq, pop_at=max(0.0, dur - 2.0), frame=frame)
    if asrc:
        cmd += ["-f", "lavfi", "-i", asrc]
        maps += ["-map", f"{0 if audio_only else 1}:a"]
    cmd += maps
    container = ext.lstrip(".") or "mp4"
    if not audio_only:
        codec = {"mov": "prores", "webm": "vp9"}.get(container, "h264")
        venc = enc.video(codec, "high", hw="off", container=container)
        cmd += [*venc.args, "-t", f"{dur:.3f}"]
    if asrc:
        if container in ("wav", "mov"):
            cmd += ["-c:a", "pcm_s24le"]
        else:
            cmd += enc.default_audio(container, "256k")
        cmd += ["-t", f"{dur:.3f}"]
    return [*cmd, out], dur, textfile
