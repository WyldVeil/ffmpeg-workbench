"""
core/ops/all_in_one.py - one FFmpeg command from every enabled section of
tabs/all_in_one.py (trim, rotate, scale, speed, frame rate, colour, denoise,
sharpen, caption, fades, volume, codecs).

Pure functions: a Recipe in, an argv out - no widgets, no Tk.  Order matters:
trim on the INPUT side (source time), then rotate, scale, speed, frame rate,
colour, denoise, sharpen, text and fades (fade times are in OUTPUT time, i.e.
after the speed change).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from core import ffmpeg as ff
from core import filters as fl

_NVENC_PRESET = {
    "ultrafast": "p1",
    "superfast": "p2",
    "veryfast": "p3",
    "faster": "p3",
    "fast": "p4",
    "medium": "p5",
    "slow": "p6",
    "slower": "p7",
    "veryslow": "p7",
}
_AMF_QUALITY = {
    "ultrafast": "speed",
    "superfast": "speed",
    "veryfast": "speed",
    "faster": "speed",
    "fast": "balanced",
    "medium": "balanced",
    "slow": "quality",
    "slower": "quality",
    "veryslow": "quality",
}
_SVT_PRESET = {
    "ultrafast": 12,
    "superfast": 11,
    "veryfast": 10,
    "faster": 9,
    "fast": 8,
    "medium": 7,
    "slow": 6,
    "slower": 5,
    "veryslow": 4,
}


def video_codec_args(vcodec: str, crf: str, preset: str, container: str) -> list[str]:
    """Encoder arguments per encoder family (x264 preset names and CRF are
    translated - NVENC/AMF/QSV reject x264 names)."""
    q = int(crf) if str(crf).isdigit() else 18
    if vcodec in ("libx264", "libx265"):
        args = ["-c:v", vcodec, "-crf", str(q), "-preset", preset, "-pix_fmt", "yuv420p"]
        if vcodec == "libx265":
            args += ["-x265-params", "log-level=error"]
    elif vcodec.endswith("_nvenc"):
        args = [
            "-c:v",
            vcodec,
            "-preset",
            _NVENC_PRESET.get(preset, "p5"),
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            str(q),
            "-b:v",
            "0",
            "-pix_fmt",
            "yuv420p",
        ]
    elif vcodec.endswith("_amf"):
        args = [
            "-c:v",
            vcodec,
            "-quality",
            _AMF_QUALITY.get(preset, "balanced"),
            "-rc",
            "cqp",
            "-qp_i",
            str(q),
            "-qp_p",
            str(q + 2),
            "-pix_fmt",
            "yuv420p",
        ]
    elif vcodec.endswith("_qsv"):
        qsv_preset = (
            preset
            if preset in ("veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
            else "medium"
        )
        args = [
            "-c:v",
            vcodec,
            "-preset",
            qsv_preset,
            "-global_quality",
            str(q),
            "-pix_fmt",
            "nv12",
        ]
    elif vcodec == "libsvtav1":
        args = [
            "-c:v",
            "libsvtav1",
            "-crf",
            str(min(63, round(q * 1.6))),
            "-preset",
            str(_SVT_PRESET.get(preset, 8)),
            "-pix_fmt",
            "yuv420p",
        ]
    elif vcodec in ("libvpx-vp9", "libvpx"):
        args = [
            "-c:v",
            vcodec,
            "-b:v",
            "0",
            "-crf",
            str(min(63, round(q * 1.65))),
            "-row-mt",
            "1",
            "-deadline",
            "good",
            "-cpu-used",
            "2" if preset in ("slow", "slower", "veryslow") else "4",
            "-pix_fmt",
            "yuv420p",
        ]
    elif vcodec == "prores_ks":
        args = [
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            "-vendor",
            "apl0",
            "-pix_fmt",
            "yuv422p10le",
        ]
    else:
        args = ["-c:v", vcodec]
    if container in ("mp4", "mov", "m4v") and ("265" in vcodec or "hevc" in vcodec):
        args += ["-tag:v", "hvc1"]
    return args


def resolve_auto(vcodec: str) -> str:
    """'auto_h264' / 'auto_hevc' -> the best working encoder on this machine."""
    if not vcodec.startswith("auto_"):
        return vcodec
    from core import encoders

    encoder, _hw = encoders.choose_encoder(vcodec[5:], "auto")
    return encoder


def codec_container(vcodec: str, out: str) -> str:
    """Output path with an extension the codec can live in."""
    base, ext = os.path.splitext(out)
    ext = ext.lower()
    if vcodec == "prores_ks" and ext not in (".mov", ".mkv"):
        return base + ".mov"
    if vcodec in ("libvpx-vp9", "libvpx") and ext not in (".webm", ".mkv"):
        return base + ".webm"
    return out


_DENOISE = {
    "Light": (2, 2, 3, 3),
    "Medium": (4, 4, 6, 6),
    "Strong": (8, 8, 12, 12),
    "Aggressive": (14, 14, 20, 20),
}
_ROTATIONS = {"90": ["transpose=clock"], "180": ["hflip", "vflip"], "270": ["transpose=cclock"]}


@dataclass(frozen=True, slots=True)
class Scale:
    width: str = "1920"  # "-1" or "" keeps the aspect ratio
    height: str = "1080"
    algorithm: str = "lanczos"
    letterbox: bool = False


@dataclass(frozen=True, slots=True)
class Colour:
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    gamma: float = 1.0
    hue: float = 0.0


@dataclass(frozen=True, slots=True)
class Caption:
    textfile: str  # the text, in a UTF-8 file (any characters are safe)
    position: tuple[str, str]  # drawtext x, y expressions
    size: str = "42"
    colour: str = "white"
    enable: str | None = None  # when it shows (a timeline expression)


@dataclass(frozen=True, slots=True)
class Fades:
    fade_in: float | None = None  # seconds, or None for no fade
    fade_out: float | None = None
    audio: bool = True  # the sound fades too


@dataclass(frozen=True, slots=True)
class Encode:
    """The Encode section; *chosen* is False while it's switched off (then H.264/AAC,
    or a stream copy when nothing changes the picture)."""

    chosen: bool = False
    vcodec: str = "libx264"  # an encoder, "auto_h264"/"auto_hevc" or "copy"
    acodec: str = "aac"  # an encoder, "copy" or "none"
    crf: str = "18"
    preset: str = "fast"
    audio_bitrate: str = "192k"


@dataclass(frozen=True, slots=True, kw_only=True)
class Recipe:
    src: str
    out: str
    duration: float = 0.0  # the source's length (0: unknown)
    sample_rate: int = 48000
    start: float = 0.0
    end: float | None = None
    speed: float = 1.0
    keep_pitch: bool = True
    rotate: str = "0"
    flip_h: bool = False
    flip_v: bool = False
    scale: Scale | None = None
    fps: str | None = None
    colour: Colour | None = None
    denoise: tuple[str, str] | None = None  # (hqdn3d|nlmeans, strength)
    sharpen: tuple[int, float] | None = None  # (matrix size, amount)
    caption: Caption | None = None
    fades: Fades | None = None
    volume: float | None = None
    loudness: str | None = None  # target LUFS
    encode: Encode = field(default_factory=Encode)

    @property
    def output_length(self) -> float:
        src_len = (
            ((self.end if self.end is not None else self.duration) - self.start)
            if self.duration or self.end
            else 0.0
        )
        return src_len / self.speed if src_len else 0.0


@dataclass(frozen=True, slots=True)
class Built:
    argv: list[str]
    notes: list[str]  # what was adjusted, for the person to read
    length: float  # the output's expected length (0: unknown)


def _scale(s: Scale, notes: list[str]) -> list[str]:
    w = "-2" if s.width.strip() in ("-1", "") else s.width.strip()
    h = "-2" if s.height.strip() in ("-1", "") else s.height.strip()
    if s.letterbox and "-2" not in (w, h):
        return [
            f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags={s.algorithm},"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
            "setsar=1",
        ]
    if s.letterbox:
        notes.append("letterboxing needs both width and height")
    return [f"scale={w}:{h}:flags={s.algorithm}", "setsar=1"]


def _colour(c: Colour) -> list[str]:
    defaults = (
        ("brightness", c.brightness, 0),
        ("contrast", c.contrast, 1),
        ("saturation", c.saturation, 1),
        ("gamma", c.gamma, 1),
    )
    eq = [f"{k}={v:.3f}" for k, v, d in defaults if abs(v - d) > 1e-3]
    return (["eq=" + ":".join(eq)] if eq else []) + (
        [f"hue=h={c.hue:.1f}"] if abs(c.hue) > 0.1 else []
    )


def _fade_filters(f: Fades, length: float, prefix: str = "") -> list[str]:
    """fade (or afade, with *prefix* "a") in at the start / out at the end."""
    out = [f"{prefix}fade=t=in:st=0:d={f.fade_in:.2f}"] if f.fade_in is not None else []
    if f.fade_out is not None and length > 0:
        out.append(f"{prefix}fade=t=out:st={max(0.0, length - f.fade_out):.3f}:d={f.fade_out:.2f}")
    return out


def video_filters(r: Recipe, notes: list[str]) -> list[str]:
    vf = (
        [*_ROTATIONS.get(r.rotate, [])]
        + (["hflip"] if r.flip_h else [])
        + (["vflip"] if r.flip_v else [])
    )
    if r.scale is not None:
        vf += _scale(r.scale, notes)
    if abs(r.speed - 1.0) > 1e-3:
        vf.append(f"setpts=(PTS-STARTPTS)/{r.speed:.4f}")
    if r.fps is not None:
        vf.append(f"fps={r.fps.strip() or '30'}")
    if r.colour is not None:
        vf += _colour(r.colour)
    if r.denoise is not None:
        algorithm, strength = r.denoise
        p = _DENOISE.get(strength, (4, 4, 6, 6))
        vf.append("hqdn3d={}:{}:{}:{}".format(*p) if algorithm == "hqdn3d" else f"nlmeans=s={p[0]}")
    if r.sharpen is not None:
        lx, la = r.sharpen
        vf.append(f"unsharp={lx}:{lx}:{la}:{lx}:{lx}:0")
    if r.caption is not None:
        c = r.caption
        x, y = c.position
        text = fl.DrawText(
            textfile=c.textfile,
            font="sans bold",
            fontsize=c.size,
            fontcolor=c.colour,
            borderw=3,
            x=x,
            y=y,
            enable=c.enable,
        )
        vf.append(text.filter())
    if r.fades is not None:
        vf += _fade_filters(r.fades, r.output_length)
    return vf


def audio_filters(r: Recipe) -> list[str]:
    af: list[str] = []
    if abs(r.speed - 1.0) > 1e-3:
        sr = r.sample_rate or 48000
        af += (
            [fl.atempo_chain(r.speed)]
            if r.keep_pitch
            else [f"asetrate={round(sr * r.speed)}", f"aresample={sr}"]
        )
    if r.volume is not None:
        af.append(f"volume={r.volume:.2f}")
    if r.loudness is not None:
        af += [
            f"loudnorm=I={r.loudness or '-23'}:TP=-1:LRA=11",
            "aresample=48000",
            "asetnsamples=n=4096:p=0",
        ]
    if r.fades is not None and r.fades.audio:
        af += _fade_filters(r.fades, r.output_length, "a")
    return af


def _codecs(r: Recipe, vf: list[str], af: list[str], notes: list[str]) -> tuple[str, str, str]:
    """(video encoder, audio encoder, output path) after fitting them together."""
    e = r.encode
    vcodec = resolve_auto(e.vcodec) if e.chosen else "libx264"
    acodec = e.acodec if e.chosen else "aac"
    if vcodec == "copy" and vf:
        notes.append("'Copy' can't apply video changes - encoding with H.264")
        vcodec = "libx264"
    if not e.chosen and not vf:
        vcodec = "copy"
    out = codec_container(vcodec, r.out)
    if out != r.out:
        notes.append(f"{os.path.splitext(out)[1]} is required for this codec")
    container = os.path.splitext(out)[1].lower().lstrip(".")
    if acodec == "copy" and af:
        notes.append("'copy' can't apply audio changes - encoding audio")
        acodec = "libopus" if container == "webm" else "aac"
    if container == "webm" and acodec in ("aac", "libmp3lame"):
        acodec = "libopus"
    return vcodec, acodec, out


def build(r: Recipe) -> Built:
    """The command for *r*."""
    notes: list[str] = []
    argv = [ff.ffmpeg_path(), "-y"]
    if r.start > 0:
        argv += ["-ss", ff.ts(r.start)]
    if r.end is not None:
        argv += ["-to", ff.ts(r.end)]
    argv += ["-i", r.src]
    vf, af = video_filters(r, notes), audio_filters(r)
    vcodec, acodec, out = _codecs(r, vf, af, notes)
    container = os.path.splitext(out)[1].lower().lstrip(".")
    e = r.encode
    argv += ["-map", "0:v:0", *(["-map", "0:a?"] if acodec != "none" else [])]
    argv += ["-map", "0:s?", "-c:s", "copy"] if container == "mkv" else []
    argv += ["-vf", ",".join(vf)] if vf else []
    argv += ["-af", ",".join(af)] if af and acodec != "none" else []
    if vcodec == "copy":
        argv += ["-c:v", "copy"]
    else:
        argv += video_codec_args(
            vcodec, e.crf if e.chosen else "18", e.preset if e.chosen else "fast", container
        )
    if acodec in ("none", "copy"):
        argv += ["-an"] if acodec == "none" else ["-c:a", "copy"]
    else:
        lossy = acodec in ("aac", "libmp3lame", "libopus", "libvorbis")
        argv += [
            "-c:a",
            acodec,
            *(["-b:a", e.audio_bitrate if e.chosen else "192k"] if lossy else []),
        ]
    argv += ["-map_metadata", "0", "-map_chapters", "0"]
    argv += ["-movflags", "+faststart"] if container in ("mp4", "mov", "m4v") else []
    return Built([*argv, out], notes, r.output_length)
