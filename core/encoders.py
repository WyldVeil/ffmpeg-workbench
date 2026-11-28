"""
core/encoders.py - Choose encoders and build their arguments correctly.

Every tab used to hand-roll ``-c:v``/``-crf``/``-preset`` strings and guess the
GPU from ``nvidia-smi``.  This module replaces that with:

* :func:`list_encoders` - encoders compiled into the active FFmpeg (cached).
* :func:`hw_available` - *functional* test of each hardware encoder family
  (NVENC, Quick Sync, VAAPI, AMF, VideoToolbox) by encoding a few frames;
  FFmpeg happily lists encoders the machine cannot actually use.
* :func:`video` - one quality vocabulary (``very_high`` … ``lowest``) and one
  speed vocabulary (``fastest`` … ``slowest``) mapped onto the right flags for
  libx264, libx265, SVT-AV1, libaom, libvpx-vp9, NVENC, QSV, VAAPI, AMF,
  VideoToolbox, ProRes, DNxHR, FFV1 and MPEG-4.
* :func:`audio` - audio codec arguments, and container compatibility helpers.

Results of the hardware probe are cached in memory and on disk (keyed by the
FFmpeg binary) so start-up stays fast.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any

from core import proc as _proc
from core import settings, tasks

logger = logging.getLogger("crossfader.encoders")

# ── Vocabularies ─────────────────────────────────────────────────────────────

QUALITIES = ("very_high", "high", "good", "low", "lowest")
SPEEDS = ("fastest", "fast", "balanced", "slow", "slowest")
HW_KINDS = ("nvenc", "qsv", "vaapi", "amf", "videotoolbox")

#: Legacy UI labels used throughout the old tabs -> quality keys.
QUALITY_LABELS = {
    "Very High": "very_high",
    "High": "high",
    "Good": "good",
    "Medium": "good",
    "Low": "low",
    "Lowest": "lowest",
}

HW_LABELS = {
    "nvenc": "NVIDIA NVENC",
    "qsv": "Intel Quick Sync",
    "vaapi": "VA-API",
    "amf": "AMD AMF",
    "videotoolbox": "Apple VideoToolbox",
}

_SOFTWARE = {
    "h264": "libx264",
    "hevc": "libx265",
    "av1": "libsvtav1",
    "vp9": "libvpx-vp9",
    "vp8": "libvpx",
    "prores": "prores_ks",
    "dnxhr": "dnxhd",
    "ffv1": "ffv1",
    "mpeg4": "mpeg4",
    "mjpeg": "mjpeg",
    "gif": "gif",
    "png": "png",
}
_SOFTWARE_FALLBACKS = {"av1": ("libsvtav1", "libaom-av1", "librav1e")}

_HW = {
    ("h264", "nvenc"): "h264_nvenc",
    ("hevc", "nvenc"): "hevc_nvenc",
    ("av1", "nvenc"): "av1_nvenc",
    ("h264", "qsv"): "h264_qsv",
    ("hevc", "qsv"): "hevc_qsv",
    ("av1", "qsv"): "av1_qsv",
    ("vp9", "qsv"): "vp9_qsv",
    ("h264", "vaapi"): "h264_vaapi",
    ("hevc", "vaapi"): "hevc_vaapi",
    ("av1", "vaapi"): "av1_vaapi",
    ("vp9", "vaapi"): "vp9_vaapi",
    ("h264", "amf"): "h264_amf",
    ("hevc", "amf"): "hevc_amf",
    ("av1", "amf"): "av1_amf",
    ("h264", "videotoolbox"): "h264_videotoolbox",
    ("hevc", "videotoolbox"): "hevc_videotoolbox",
    ("prores", "videotoolbox"): "prores_videotoolbox",
}

# Constant-quality values per encoder family, indexed by QUALITIES.
_Q = {
    "libx264": (16, 19, 22, 26, 30),  # -crf
    "libx265": (18, 21, 24, 28, 32),  # -crf
    "libsvtav1": (22, 28, 34, 40, 48),  # -crf (0-63)
    "libaom-av1": (22, 28, 34, 40, 48),  # -crf + -b:v 0
    "librav1e": (60, 80, 100, 130, 160),  # -qp (0-255)
    "libvpx-vp9": (20, 28, 33, 38, 45),  # -crf + -b:v 0
    "libvpx": (6, 10, 16, 24, 32),  # -crf (4-63) + -b:v cap
    "nvenc": (17, 20, 24, 28, 32),  # -cq (h264)
    "nvenc_hevc": (19, 22, 26, 30, 34),  # -cq (hevc)
    "nvenc_av1": (24, 30, 36, 42, 50),  # -cq (0-63)
    "qsv": (18, 21, 24, 28, 32),  # -global_quality (ICQ)
    "vaapi": (18, 21, 24, 28, 32),  # -qp with -rc_mode CQP
    "amf": (18, 21, 24, 28, 32),  # -qp_i/-qp_p/-qp_b with -rc cqp
    "videotoolbox": (75, 65, 55, 45, 35),  # -q:v (1-100, higher = better)
    "mpeg4": (2, 3, 5, 8, 12),  # -q:v (2-31, lower = better)
    "mjpeg": (2, 3, 5, 8, 12),
}

_X26X_PRESET = {
    "fastest": "ultrafast",
    "fast": "veryfast",
    "balanced": "medium",
    "slow": "slow",
    "slowest": "veryslow",
}
_SVT_PRESET = {"fastest": 12, "fast": 10, "balanced": 8, "slow": 5, "slowest": 3}
_AOM_CPU = {"fastest": 8, "fast": 6, "balanced": 4, "slow": 3, "slowest": 1}
_VPX_CPU = {"fastest": 5, "fast": 4, "balanced": 2, "slow": 1, "slowest": 0}
_NVENC_PRESET = {"fastest": "p1", "fast": "p3", "balanced": "p5", "slow": "p6", "slowest": "p7"}
_QSV_PRESET = {
    "fastest": "veryfast",
    "fast": "faster",
    "balanced": "medium",
    "slow": "slow",
    "slowest": "veryslow",
}
_AMF_QUALITY = {
    "fastest": "speed",
    "fast": "speed",
    "balanced": "balanced",
    "slow": "quality",
    "slowest": "quality",
}
_PRORES_PROFILE = {"very_high": 3, "high": 3, "good": 2, "low": 1, "lowest": 0}
_DNXHR_PROFILE = {
    "very_high": ("dnxhr_hqx", "yuv422p10le"),
    "high": ("dnxhr_hq", "yuv422p"),
    "good": ("dnxhr_sq", "yuv422p"),
    "low": ("dnxhr_lb", "yuv422p"),
    "lowest": ("dnxhr_lb", "yuv422p"),
}


def quality_key(value: str) -> str:
    """Accept a quality key or a legacy UI label; default ``high``."""
    if value in QUALITIES:
        return value
    return QUALITY_LABELS.get(str(value).strip(), "high")


# ── Encoder discovery ────────────────────────────────────────────────────────

_lock = threading.Lock()
_ENCODERS: dict[str, set[str]] = {}  # ffmpeg path -> encoder names
_HW_OK: dict[str, dict[str, bool]] = {}  # ffmpeg path -> {kind: bool}
_VAAPI_DEVICE: dict[str, str] = {}  # ffmpeg path -> render node


def _ffmpeg() -> str:
    from core.hardware import get_binary_path

    return get_binary_path("ffmpeg")


def _cache_file() -> str | None:
    try:
        from core.paths import cache_dir

        return os.path.join(cache_dir(), "encoders.json")
    except Exception:
        logger.debug("_cache_file: best-effort step failed", exc_info=True)
        return None


def _binary_key(ff: str) -> str:
    try:
        st = os.stat(ff)
        return f"{os.path.abspath(ff)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        return ff


def _load_disk_cache(ff: str) -> dict[str, Any] | None:
    path = _cache_file()
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get(_binary_key(ff))
    except (OSError, ValueError):
        return None


def _save_disk_cache(ff: str, entry: dict[str, Any]) -> None:
    path = _cache_file()
    if not path:
        return
    try:
        data: dict[str, Any] = {}
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        data[_binary_key(ff)] = entry
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, path)
    except (OSError, ValueError):
        pass


def list_encoders(ffmpeg: str | None = None) -> set[str]:
    """Names of every encoder compiled into *ffmpeg* (cached)."""
    ff = ffmpeg or _ffmpeg()
    with _lock:
        if ff in _ENCODERS:
            return _ENCODERS[ff]
    names: set[str] = set()
    try:
        r = _proc.run([ff, "-hide_banner", "-encoders"], timeout=15)
        started = False
        for line in r.stdout.splitlines():
            if line.strip().startswith("------"):
                started = True
                continue
            if started:
                parts = line.split()
                if len(parts) >= 2 and len(parts[0]) == 6:
                    names.add(parts[1])
    except _proc.ProcessError:
        pass
    with _lock:
        _ENCODERS[ff] = names
    return names


def has_encoder(name: str) -> bool:
    return name in list_encoders()


def _vaapi_nodes() -> list[str]:
    return sorted(glob.glob("/dev/dri/renderD*")) if sys.platform.startswith("linux") else []


def _hw_test(ff: str, kind: str) -> bool:
    enc = _HW.get(("h264", kind)) or _HW.get(("hevc", kind))
    if not enc or enc not in list_encoders(ff):
        return False
    src = ["-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=0.5"]
    tail = ["-frames:v", "8", "-c:v", enc, "-f", "null", "-"]
    base = [ff, "-hide_banner", "-nostdin", "-loglevel", "error"]
    attempts: list[tuple[str | None, list[str]]] = []
    if kind == "vaapi":
        for node in _vaapi_nodes():
            attempts.append(
                (node, [*base, "-vaapi_device", node, *src, "-vf", "format=nv12,hwupload", *tail])
            )
    elif kind == "qsv":
        attempts.append((None, base + src + ["-pix_fmt", "nv12"] + tail))
    else:
        attempts.append((None, base + src + ["-pix_fmt", "yuv420p"] + tail))
    for node, cmd in attempts:
        try:
            r = _proc.run(cmd, timeout=20, text=False)
        except _proc.ProcessError:
            continue
        if r.returncode == 0:
            if node:
                _VAAPI_DEVICE[ff] = node
            return True
    return False


def _platform_kinds() -> tuple[str, ...]:
    if sys.platform == "darwin":
        return ("videotoolbox",)
    if sys.platform.startswith("win"):
        return ("nvenc", "qsv", "amf")
    return ("nvenc", "vaapi", "qsv", "amf")


def hw_available(refresh: bool = False, ffmpeg: str | None = None) -> dict[str, bool]:
    """Return ``{kind: bool}`` for each hardware encoder family that applies to
    this platform, after a functional test.  Cached in memory and on disk."""
    ff = ffmpeg or _ffmpeg()
    with _lock:
        if not refresh and ff in _HW_OK:
            return dict(_HW_OK[ff])
    if not refresh:
        disk = _load_disk_cache(ff)
        if disk and isinstance(disk.get("hw"), dict):
            with _lock:
                _HW_OK[ff] = dict(disk["hw"])
                if disk.get("vaapi_device"):
                    _VAAPI_DEVICE[ff] = disk["vaapi_device"]
            return dict(disk["hw"])
    result = {kind: _hw_test(ff, kind) for kind in _platform_kinds()}
    with _lock:
        _HW_OK[ff] = result
    _save_disk_cache(ff, {"hw": result, "vaapi_device": _VAAPI_DEVICE.get(ff, "")})
    return dict(result)


def hw_known(ffmpeg: str | None = None) -> bool:
    """True when :func:`hw_available` can answer without testing (no program runs)."""
    ff = ffmpeg or _ffmpeg()
    with _lock:
        if ff in _HW_OK:
            return True
    disk = _load_disk_cache(ff)
    return bool(disk and isinstance(disk.get("hw"), dict))


def working_hw_kinds() -> list[str]:
    return [k for k, ok in hw_available().items() if ok]


def detect_in_background() -> Future[dict[str, bool]]:
    """Warm the encoder caches without blocking the UI."""
    return tasks.submit(hw_available, name="EncoderProbe")


def preferred_hw() -> str | None:
    """The hardware family to use when the user setting is ``auto``."""
    kinds = working_hw_kinds()
    for k in ("nvenc", "videotoolbox", "qsv", "amf", "vaapi"):
        if k in kinds:
            return k
    return None


def _setting_hw_mode() -> str:
    mode = settings.HW_ENCODE.get()
    return mode if mode in ("auto", "off", *HW_KINDS) else "auto"


# ── Video arguments ──────────────────────────────────────────────────────────


@dataclass
class VideoEncode:
    """Everything needed to encode video with a chosen encoder.

    * ``pre_input`` - global options that must precede the inputs
      (e.g. ``-vaapi_device``).
    * ``args`` - output options (``-c:v`` …, ``-pix_fmt`` …).
    * ``filter_suffix`` - must be appended to the END of the video filter
      chain (VAAPI upload).  Use :meth:`vf` to merge it with your own chain.
    """

    encoder: str
    codec: str
    hw: str | None = None
    pre_input: list[str] = field(default_factory=list[str])
    args: list[str] = field(default_factory=list[str])
    filter_suffix: str = ""
    pix_fmt: str = ""

    def vf(self, chain: str | None = None) -> str | None:
        parts = [p for p in ((chain or "").strip(","), self.filter_suffix) if p]
        return ",".join(parts) if parts else None

    def vf_args(self, chain: str | None = None) -> list[str]:
        v = self.vf(chain)
        return ["-vf", v] if v else []

    @property
    def label(self) -> str:
        return f"{self.encoder} ({HW_LABELS[self.hw]})" if self.hw else self.encoder

    def set_option(self, flag: str, value: str) -> bool:
        """Change *flag*'s value in the output options, if the encoder uses it."""
        if flag not in self.args:
            return False
        self.args[self.args.index(flag) + 1] = value
        return True

    def set_quality(self, crf: str) -> None:
        """Use *crf* (a whole number) when the encoder has a CRF; anything else is ignored."""
        if crf.strip().isdigit():
            self.set_option("-crf", crf.strip())


def default_audio(container: str, bitrate: str | None = None) -> list[str]:
    """Sound encoded with *container*'s usual codec."""
    codec = default_audio_for(container)
    return (
        audio(codec, bitrate, container=container) if bitrate else audio(codec, container=container)
    )


def full_chroma(venc: VideoEncode) -> None:
    """4:4:4 colour (sharp coloured text in screen recordings) on encoders that take it."""
    if venc.encoder in ("libx264", "libx265", "h264_nvenc", "hevc_nvenc"):
        venc.set_option("-pix_fmt", "yuv444p")


def choose_encoder(codec: str = "h264", hw: str = "auto") -> tuple[str, str | None]:
    """Return ``(encoder_name, hw_kind_or_None)`` for *codec*.

    *hw*: ``"auto"`` (user setting, then best working GPU), ``"off"``, or a
    specific family from :data:`HW_KINDS`.  Falls back to software whenever
    the requested hardware path is unavailable.
    """
    codec = codec.lower()
    mode = _setting_hw_mode() if hw == "auto" else hw
    kind: str | None = None
    if mode == "auto":
        kind = preferred_hw()
    elif mode in HW_KINDS:
        kind = mode if hw_available().get(mode) else None
    if kind and (codec, kind) in _HW and _HW[(codec, kind)] in list_encoders():
        return _HW[(codec, kind)], kind
    for name in _SOFTWARE_FALLBACKS.get(codec, (_SOFTWARE.get(codec, codec),)):
        if name in list_encoders():
            return name, None
    return _SOFTWARE.get(codec, codec), None


@dataclass(frozen=True, slots=True)
class Rate:
    """Average-bitrate mode instead of constant quality: *bitrate* (e.g. "4M"),
    optionally capped at *maxrate*, with a keyframe every *gop_seconds* at *fps*."""

    bitrate: str
    maxrate: str | None = None
    gop_seconds: float | None = None
    fps: float | None = None


@dataclass(frozen=True, slots=True)
class _Settings:
    """What every encoder family's arguments are made from."""

    codec: str
    q: int  # index into QUALITIES
    speed: str
    ten_bit: bool
    bitrate: str | None

    def pix(self, eight: str, ten: str) -> str:
        return ten if self.ten_bit else eight

    def quality(self, table: str, flag: str = "-crf", *after: str) -> list[str]:
        """The bitrate when one was asked for, else *flag* with the quality value."""
        if self.bitrate:
            return ["-b:v", self.bitrate]
        return [flag, str(_Q[table][self.q]), *after]


_Family = Callable[[VideoEncode, _Settings], list[str]]


def _x264(ve: VideoEncode, s: _Settings) -> list[str]:
    ve.pix_fmt = s.pix("yuv420p", "yuv420p10le")
    return ["-preset", _X26X_PRESET[s.speed], *s.quality("libx264")]


def _x265(ve: VideoEncode, s: _Settings) -> list[str]:
    ve.pix_fmt = s.pix("yuv420p", "yuv420p10le")
    return [
        "-preset",
        _X26X_PRESET[s.speed],
        *s.quality("libx265"),
        "-x265-params",
        "log-level=error",
    ]


def _svt_av1(ve: VideoEncode, s: _Settings) -> list[str]:
    ve.pix_fmt = s.pix("yuv420p", "yuv420p10le")
    return ["-preset", str(_SVT_PRESET[s.speed]), *s.quality("libsvtav1")]


def _aom_av1(ve: VideoEncode, s: _Settings) -> list[str]:
    ve.pix_fmt = s.pix("yuv420p", "yuv420p10le")
    return [
        "-cpu-used",
        str(_AOM_CPU[s.speed]),
        "-row-mt",
        "1",
        *s.quality("libaom-av1", "-crf", "-b:v", "0"),
    ]


def _rav1e(ve: VideoEncode, s: _Settings) -> list[str]:
    ve.pix_fmt = s.pix("yuv420p", "yuv420p10le")
    speed = {"fastest": 10, "fast": 8, "balanced": 6, "slow": 4, "slowest": 2}[s.speed]
    return ["-speed", str(speed), *s.quality("librav1e", "-qp")]


def _vp9(ve: VideoEncode, s: _Settings) -> list[str]:
    ve.pix_fmt = s.pix("yuv420p", "yuv420p10le")
    cpu = str(_VPX_CPU[s.speed])
    return [
        "-deadline",
        "good",
        "-cpu-used",
        cpu,
        "-row-mt",
        "1",
        *s.quality("libvpx-vp9", "-crf", "-b:v", "0"),
    ]


def _vp8(ve: VideoEncode, s: _Settings) -> list[str]:
    ve.pix_fmt = "yuv420p"
    cpu = str(_VPX_CPU[s.speed])
    return ["-deadline", "good", "-cpu-used", cpu, *s.quality("libvpx", "-crf", "-b:v", "20M")]


def _prores(ve: VideoEncode, s: _Settings) -> list[str]:
    ve.pix_fmt = "yuv422p10le"
    return ["-profile:v", str(_PRORES_PROFILE[QUALITIES[s.q]]), "-vendor", "apl0"]


def _dnxhr(ve: VideoEncode, s: _Settings) -> list[str]:
    prof, ve.pix_fmt = _DNXHR_PROFILE[QUALITIES[s.q]]
    return ["-profile:v", prof]


def _ffv1(ve: VideoEncode, _s: _Settings) -> list[str]:
    ve.pix_fmt = ""
    return ["-level", "3", "-g", "1", "-slicecrc", "1"]


def _mpeg4(ve: VideoEncode, s: _Settings) -> list[str]:
    ve.pix_fmt = "yuvj420p" if ve.encoder == "mjpeg" else "yuv420p"
    return s.quality(ve.encoder, "-q:v")


def _nvenc(ve: VideoEncode, s: _Settings) -> list[str]:
    table = {"h264": "nvenc", "hevc": "nvenc_hevc", "av1": "nvenc_av1"}[s.codec]
    ve.pix_fmt = s.pix("yuv420p", "p010le") if s.codec != "h264" else "yuv420p"
    a = ["-preset", _NVENC_PRESET[s.speed], "-tune", "hq", "-spatial-aq", "1", "-rc", "vbr"]
    a += s.quality(table, "-cq", "-b:v", "0")
    if s.speed in ("slow", "slowest") and s.codec != "av1":
        a += ["-multipass", "qres" if s.speed == "slow" else "fullres"]
    return a


def _qsv(ve: VideoEncode, s: _Settings) -> list[str]:
    ve.pix_fmt = s.pix("nv12", "p010le")
    return ["-preset", _QSV_PRESET[s.speed], *s.quality("qsv", "-global_quality")]


def _vaapi(ve: VideoEncode, s: _Settings) -> list[str]:
    node = _VAAPI_DEVICE.get(_ffmpeg()) or (_vaapi_nodes() or ["/dev/dri/renderD128"])[0]
    ve.pre_input = ["-vaapi_device", node]
    ve.filter_suffix = "format=p010,hwupload" if s.ten_bit else "format=nv12,hwupload"
    ve.pix_fmt = ""
    return ["-rc_mode", "VBR" if s.bitrate else "CQP", *s.quality("vaapi", "-qp")]


def _amf(ve: VideoEncode, s: _Settings) -> list[str]:
    ve.pix_fmt = s.pix("nv12", "p010le")
    a = ["-quality", _AMF_QUALITY[s.speed]]
    if s.bitrate:
        return [*a, "-rc", "vbr_peak", "-b:v", s.bitrate]
    qp = str(_Q["amf"][s.q])
    return [
        *a,
        "-rc",
        "cqp",
        "-qp_i",
        qp,
        "-qp_p",
        qp,
        *(["-qp_b", qp] if s.codec == "h264" else []),
    ]


def _videotoolbox(ve: VideoEncode, s: _Settings) -> list[str]:
    ve.pix_fmt = s.pix("yuv420p", "p010le")
    return [*s.quality("videotoolbox", "-q:v"), "-allow_sw", "1"]


def _any(_ve: VideoEncode, s: _Settings) -> list[str]:
    return ["-b:v", s.bitrate] if s.bitrate else []


#: Each encoder's own arguments: by encoder name, then by hardware family
_BY_ENCODER: dict[str, _Family] = {
    "libx264": _x264,
    "libx265": _x265,
    "libsvtav1": _svt_av1,
    "libaom-av1": _aom_av1,
    "librav1e": _rav1e,
    "libvpx-vp9": _vp9,
    "libvpx": _vp8,
    "prores_ks": _prores,
    "dnxhd": _dnxhr,
    "ffv1": _ffv1,
    "mpeg4": _mpeg4,
    "mjpeg": _mpeg4,
}
_BY_HARDWARE: dict[str, _Family] = {
    "nvenc": _nvenc,
    "qsv": _qsv,
    "vaapi": _vaapi,
    "amf": _amf,
    "videotoolbox": _videotoolbox,
}
_INTRA_ONLY = ("prores_ks", "dnxhd", "ffv1")  # no bitrate cap, no keyframe interval


def video(
    codec: str = "h264",
    quality: str = "high",
    *,
    speed: str = "balanced",
    hw: str = "auto",
    ten_bit: bool = False,
    container: str = "mp4",
    rate: Rate | None = None,
) -> VideoEncode:
    """Build arguments for encoding video.

    *quality* uses :data:`QUALITIES` (legacy labels such as ``"High"`` are
    accepted).  With a *rate* the encoder runs in average-bitrate mode instead
    of constant quality (for size targets use :func:`two_pass_args` with
    software encoders).
    """
    settings = _Settings(
        codec=codec,
        q=QUALITIES.index(quality_key(quality)),
        speed=speed if speed in SPEEDS else "balanced",
        ten_bit=ten_bit,
        bitrate=rate.bitrate if rate else None,
    )
    encoder, kind = choose_encoder(codec, hw)
    ve = VideoEncode(encoder=encoder, codec=codec, hw=kind)
    family = _BY_ENCODER.get(encoder) or _BY_HARDWARE.get(kind or "") or _any
    a = ["-c:v", encoder, *family(ve, settings)]
    if rate and encoder not in _INTRA_ONLY:
        if rate.maxrate:
            a += ["-maxrate", rate.maxrate, "-bufsize", _double_rate(rate.maxrate)]
        if rate.gop_seconds and rate.fps:
            a += ["-g", str(max(1, round(rate.gop_seconds * rate.fps)))]
    if ve.pix_fmt:
        a += ["-pix_fmt", ve.pix_fmt]
    a += container_video_args(container, codec)
    ve.args = a
    return ve


def _double_rate(rate: str) -> str:
    text = str(rate).strip()
    unit = text[-1] if text and text[-1].isalpha() else ""
    num = text[:-1] if unit else text
    try:
        return f"{float(num) * 2:g}{unit}"
    except ValueError:
        return text


def two_pass_args(encoder: str, pass_no: int, passlog: str) -> list[str]:
    """Options for pass 1 or 2 of a two-pass software encode."""
    if encoder in ("libx264", "libvpx-vp9", "libvpx", "libaom-av1", "mpeg4"):
        return ["-pass", str(pass_no), "-passlogfile", passlog]
    if encoder == "libx265":
        return ["-x265-params", f"pass={pass_no}:stats={passlog}.log:log-level=error"]
    if encoder == "libsvtav1":
        return ["-pass", str(pass_no), "-passlogfile", passlog]
    return []


def container_video_args(container: str, codec: str) -> list[str]:
    c = container.lower().lstrip(".")
    out: list[str] = []
    if c in ("mp4", "m4v", "mov"):
        out += ["-movflags", "+faststart"]
        if codec == "hevc":
            out += ["-tag:v", "hvc1"]  # Apple players require hvc1
    return out


# ── Audio arguments ──────────────────────────────────────────────────────────

AUDIO_ENCODERS = {
    "aac": "aac",
    "mp3": "libmp3lame",
    "opus": "libopus",
    "vorbis": "libvorbis",
    "flac": "flac",
    "alac": "alac",
    "ac3": "ac3",
    "eac3": "eac3",
    "pcm16": "pcm_s16le",
    "pcm24": "pcm_s24le",
    "wav": "pcm_s16le",
    "copy": "copy",
}

#: Which audio codecs each container accepts (first = default).
CONTAINER_AUDIO = {
    "mp4": ("aac", "mp3", "opus", "flac", "alac", "ac3", "eac3"),
    "m4v": ("aac", "mp3", "ac3"),
    "m4a": ("aac", "alac"),
    "mov": ("aac", "alac", "pcm16", "pcm24", "mp3", "ac3"),
    "mkv": ("opus", "aac", "flac", "mp3", "vorbis", "ac3", "eac3", "pcm16", "pcm24", "alac"),
    "webm": ("opus", "vorbis"),
    "avi": ("mp3", "pcm16", "ac3"),
    "ogg": ("vorbis", "opus", "flac"),
    "opus": ("opus",),
    "mp3": ("mp3",),
    "flac": ("flac",),
    "wav": ("pcm16", "pcm24"),
    "aac": ("aac",),
    "ac3": ("ac3",),
    "mxf": ("pcm24", "pcm16"),
    "ts": ("aac", "mp3", "ac3", "eac3", "opus"),
}

CONTAINER_VIDEO = {
    "mp4": ("h264", "hevc", "av1", "vp9", "mpeg4"),
    "m4v": ("h264", "hevc"),
    "mov": ("h264", "hevc", "prores", "dnxhr", "mjpeg"),
    "mkv": ("h264", "hevc", "av1", "vp9", "vp8", "ffv1", "prores", "mpeg4", "dnxhr"),
    "webm": ("vp9", "av1", "vp8"),
    "avi": ("mpeg4", "h264", "mjpeg"),
    "mxf": ("dnxhr",),
    "ts": ("h264", "hevc"),
    "gif": ("gif",),
}

# ffprobe codec_name -> our audio key
_PROBED_AUDIO = {
    "aac": "aac",
    "mp3": "mp3",
    "opus": "opus",
    "vorbis": "vorbis",
    "flac": "flac",
    "alac": "alac",
    "ac3": "ac3",
    "eac3": "eac3",
    "pcm_s16le": "pcm16",
    "pcm_s24le": "pcm24",
}


def audio_key_from_probe(codec_name: str) -> str | None:
    return _PROBED_AUDIO.get((codec_name or "").lower())


def audio_ok_for(container: str, key: str) -> bool:
    allowed = CONTAINER_AUDIO.get(container.lower().lstrip("."))
    return True if allowed is None else key in allowed


def video_ok_for(container: str, codec: str) -> bool:
    allowed = CONTAINER_VIDEO.get(container.lower().lstrip("."))
    return True if allowed is None else codec in allowed


def default_audio_for(container: str) -> str:
    allowed = CONTAINER_AUDIO.get(container.lower().lstrip("."))
    return allowed[0] if allowed else "aac"


def audio(
    codec: str = "aac",
    bitrate: str | None = "192k",
    *,
    container: str | None = None,
    channels: int | None = None,
    sample_rate: int | None = None,
    vbr_quality: float | None = None,
) -> list[str]:
    """Arguments for encoding audio.

    *codec* is a key from :data:`AUDIO_ENCODERS` (``"aac"``, ``"opus"`` …) or
    ``"copy"``.  If *container* cannot hold *codec*, the container's default
    codec is used instead.  *vbr_quality* selects VBR where it is the better
    mode (``libmp3lame -q:a``, ``libvorbis -q:a``).
    """
    key = codec.lower()
    if container and key != "copy" and not audio_ok_for(container, key):
        key = default_audio_for(container)
    enc = AUDIO_ENCODERS.get(key, key)
    a = ["-c:a", enc]
    if enc == "copy":
        return a
    if enc == "libmp3lame" or enc == "libvorbis":
        a += (
            ["-q:a", f"{vbr_quality:g}"] if vbr_quality is not None else ["-b:a", bitrate or "192k"]
        )
    elif enc == "libopus":
        a += ["-b:a", bitrate or "128k", "-vbr", "on"]
    elif enc in ("aac", "ac3", "eac3"):
        a += ["-b:a", bitrate or ("192k" if enc == "aac" else "384k")]
    elif enc == "flac":
        a += ["-compression_level", "8"]
    if channels:
        a += ["-ac", str(int(channels))]
    if sample_rate:
        a += ["-ar", str(int(sample_rate))]
    elif enc == "libopus":
        a += ["-ar", "48000"]
    return a


def summary() -> str:
    """Human-readable capability line for the About / System Info dialogs."""
    hw = hw_available()
    ok = [HW_LABELS[k] for k, v in hw.items() if v]
    sw = [
        n
        for n in ("libx264", "libx265", "libsvtav1", "libvpx-vp9", "libopus")
        if n in list_encoders()
    ]
    return (
        "Hardware encoders: "
        + (", ".join(ok) if ok else "none detected")
        + "\nSoftware encoders: "
        + ", ".join(sw)
    )
