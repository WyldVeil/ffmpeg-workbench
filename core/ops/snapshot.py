"""
core/ops/snapshot.py - one frame as a faithful picture, for tabs/social/snapshot.py.

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from core import ffmpeg as ff
from core import hdr

Box = tuple[int, int, int, int]  # x, y, width, height


def colour_matrix(info: ff.MediaInfo) -> str:
    """The matrix to turn this video's YUV into RGB (FFmpeg assumes BT.601 when
    the file doesn't say, which is wrong for HD)."""
    v = info.require_video()
    cs = (v.color_space or "").lower() if v else ""
    if cs in ("bt709",):
        return "bt709"
    if cs in ("bt470bg", "smpte170m", "bt601"):
        return "bt601"
    if cs.startswith("bt2020"):
        return "bt2020"
    if cs == "smpte240m":
        return "smpte240m"
    return "bt709" if (v and v.height >= 720) else "bt601"


def sar(v: ff.Stream) -> float:
    """The pixel shape (sample aspect ratio) as a number: 1.0 = square pixels."""
    try:
        num, den = (int(x) for x in (v.sar or "1:1").replace("/", ":").split(":"))
        return num / den if num > 0 and den > 0 else 1.0
    except ValueError:
        return 1.0


def picture_size(
    v: ff.Stream, square_pixels: bool = True, crop: Box | None = None
) -> tuple[float, float]:
    """Width, height of the frame as stored (or of *crop*), with the pixel shape
    corrected when *square_pixels*, and turned like the video (FFmpeg turns
    rotated phone video the right way up when decoding)."""
    w, h = (crop[2], crop[3]) if crop else (v.width, v.height)
    if square_pixels:
        shape = sar(v)
        if shape > 1:
            w = w * shape
        elif shape < 1:
            h = h / shape
    if v.rotation in (90, 270):
        w, h = h, w
    return w, h


def out_size(
    info: ff.MediaInfo, size: str = "own", square_pixels: bool = True, crop: Box | None = None
) -> tuple[int, int]:
    """Width, height of the picture (even numbers)."""
    w, h = picture_size(info.require_video(), square_pixels, crop)
    k = {"half": 0.5, "double": 2.0}.get(size, 1.0)
    return max(2, round(w * k / 2) * 2), max(2, round(h * k / 2) * 2)


@dataclass(frozen=True, slots=True, kw_only=True)
class Picture:
    """How a frame becomes the picture: colour-accurate conversion, square
    pixels, HDR tone mapping, deinterlacing, a crop box (x, y, w, h), a size
    ("own", "half", "double"), sharpening (CAS strength), denoising (hqdn3d
    strength), brightness / contrast / saturation, 16-bit output (*deep*) and
    the GPU for tone mapping."""

    accurate: bool = True
    square_pixels: bool = True
    tonemap: bool = True
    deinterlace: bool = True
    crop: Box | None = None
    size: str = "own"
    sharpen: float = 0.0
    denoise: int = 0
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    deep: bool = False
    gpu: bool = False


def build_chain(info: ff.MediaInfo, pic: Picture | None = None) -> str:
    """The FFmpeg filter chain that turns the frame into the picture."""
    pic = pic or Picture()
    accurate, square_pixels, tonemap, deinterlace = (
        pic.accurate,
        pic.square_pixels,
        pic.tonemap,
        pic.deinterlace,
    )
    crop, size, sharpen, denoise = pic.crop, pic.size, pic.sharpen, pic.denoise
    brightness, contrast, saturation, deep, gpu = (
        pic.brightness,
        pic.contrast,
        pic.saturation,
        pic.deep,
        pic.gpu,
    )
    v = info.require_video()
    steps: list[str] = []
    if deinterlace and v and v.field_order not in ("", "progressive", "unknown"):
        steps.append("bwdif=mode=send_frame:parity=auto:deint=all")
    hdr_done = False
    if tonemap and info.is_hdr:
        steps.append(hdr.sdr_chain(gpu=gpu, out_fmt="yuv444p16le" if deep else "yuv444p"))
        hdr_done = True
    if crop:
        x, y, w, h = crop
        steps.append(f"crop={w}:{h}:{x}:{y}")
    if denoise:
        steps.append(
            f"hqdn3d=luma_spatial={denoise}:chroma_spatial={denoise * 0.75:g}:luma_tmp=0:chroma_tmp=0"
        )
    if (brightness, contrast, saturation) != (0.0, 1.0, 1.0):
        steps.append(
            f"eq=brightness={brightness:g}:contrast={contrast:g}:saturation={saturation:g}"
        )
    w, h = out_size(info, size, square_pixels, crop)
    rgb = "gbrp16le" if deep else "gbrp"
    if accurate:
        matrix = "bt709" if hdr_done else colour_matrix(info)
        rng = "pc" if (v and (v.color_range or "").lower() in ("pc", "jpeg")) else "tv"
        steps.append(
            f"scale={w}:{h}:flags=lanczos+accurate_rnd+full_chroma_int+full_chroma_inp:"
            f"in_color_matrix={matrix}:in_range={rng}:out_range=pc,format={rgb}"
        )
    else:
        steps.append(f"scale={w}:{h},format={rgb}")
    if sharpen:
        steps.append(f"cas=strength={sharpen:g}")
    steps.append("format=rgb48be" if deep else "format=rgb24")
    return ",".join(steps)


def frame_name(path: str, at: float) -> str:
    """``meadow 00-01-02.345.png`` - the video's name and the frame's time."""
    ms = round(max(0.0, at) * 1000)
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    stem = os.path.splitext(os.path.basename(path))[0]
    return f"{stem} {h:02d}-{m:02d}-{rest // 1000:02d}.{rest % 1000:03d}.png"


def save_command(path: str, at: float, chain: str, out: str) -> list[str]:
    gpu = "libplacebo" in chain
    return [
        ff.ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        *(["-init_hw_device", "vulkan"] if gpu else []),
        "-ss",
        ff.ts(at),
        "-i",
        path,
        "-frames:v",
        "1",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        chain,
        "-c:v",
        "png",
        "-pred",
        "mixed",
        out,
    ]
