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
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Optional

from core.hardware import CREATE_NO_WINDOW

# ── Vocabularies ─────────────────────────────────────────────────────────────

QUALITIES = ("very_high", "high", "good", "low", "lowest")
SPEEDS = ("fastest", "fast", "balanced", "slow", "slowest")
HW_KINDS = ("nvenc", "qsv", "vaapi", "amf", "videotoolbox")

#: Legacy UI labels used throughout the old tabs -> quality keys.
QUALITY_LABELS = {
    "Very High": "very_high", "High": "high", "Good": "good",
    "Medium": "good", "Low": "low", "Lowest": "lowest",
}

HW_LABELS = {
    "nvenc": "NVIDIA NVENC", "qsv": "Intel Quick Sync", "vaapi": "VA-API",
    "amf": "AMD AMF", "videotoolbox": "Apple VideoToolbox",
}

_SOFTWARE = {
    "h264": "libx264", "hevc": "libx265", "av1": "libsvtav1", "vp9": "libvpx-vp9",
    "vp8": "libvpx", "prores": "prores_ks", "dnxhr": "dnxhd", "ffv1": "ffv1",
    "mpeg4": "mpeg4", "mjpeg": "mjpeg", "gif": "gif", "png": "png",
}
_SOFTWARE_FALLBACKS = {"av1": ("libsvtav1", "libaom-av1", "librav1e")}

_HW = {
    ("h264", "nvenc"): "h264_nvenc", ("hevc", "nvenc"): "hevc_nvenc", ("av1", "nvenc"): "av1_nvenc",
    ("h264", "qsv"): "h264_qsv", ("hevc", "qsv"): "hevc_qsv", ("av1", "qsv"): "av1_qsv", ("vp9", "qsv"): "vp9_qsv",
    ("h264", "vaapi"): "h264_vaapi", ("hevc", "vaapi"): "hevc_vaapi", ("av1", "vaapi"): "av1_vaapi", ("vp9", "vaapi"): "vp9_vaapi",
    ("h264", "amf"): "h264_amf", ("hevc", "amf"): "hevc_amf", ("av1", "amf"): "av1_amf",
    ("h264", "videotoolbox"): "h264_videotoolbox", ("hevc", "videotoolbox"): "hevc_videotoolbox",
    ("prores", "videotoolbox"): "prores_videotoolbox",
}

# Constant-quality values per encoder family, indexed by QUALITIES.
_Q = {
    "libx264":      (16, 19, 22, 26, 30),     # -crf
    "libx265":      (18, 21, 24, 28, 32),     # -crf
    "libsvtav1":    (22, 28, 34, 40, 48),     # -crf (0-63)
    "libaom-av1":   (22, 28, 34, 40, 48),     # -crf + -b:v 0
    "librav1e":     (60, 80, 100, 130, 160),  # -qp (0-255)
    "libvpx-vp9":   (20, 28, 33, 38, 45),     # -crf + -b:v 0
    "libvpx":       (6, 10, 16, 24, 32),      # -crf (4-63) + -b:v cap
    "nvenc":        (17, 20, 24, 28, 32),     # -cq (h264)
    "nvenc_hevc":   (19, 22, 26, 30, 34),     # -cq (hevc)
    "nvenc_av1":    (24, 30, 36, 42, 50),     # -cq (0-63)
    "qsv":          (18, 21, 24, 28, 32),     # -global_quality (ICQ)
    "vaapi":        (18, 21, 24, 28, 32),     # -qp with -rc_mode CQP
    "amf":          (18, 21, 24, 28, 32),     # -qp_i/-qp_p/-qp_b with -rc cqp
    "videotoolbox": (75, 65, 55, 45, 35),     # -q:v (1-100, higher = better)
    "mpeg4":        (2, 3, 5, 8, 12),         # -q:v (2-31, lower = better)
    "mjpeg":        (2, 3, 5, 8, 12),
}

_X26X_PRESET = {"fastest": "ultrafast", "fast": "veryfast", "balanced": "medium",
                "slow": "slow", "slowest": "veryslow"}
_SVT_PRESET = {"fastest": 12, "fast": 10, "balanced": 8, "slow": 5, "slowest": 3}
_AOM_CPU = {"fastest": 8, "fast": 6, "balanced": 4, "slow": 3, "slowest": 1}
_VPX_CPU = {"fastest": 5, "fast": 4, "balanced": 2, "slow": 1, "slowest": 0}
_NVENC_PRESET = {"fastest": "p1", "fast": "p3", "balanced": "p5", "slow": "p6", "slowest": "p7"}
_QSV_PRESET = {"fastest": "veryfast", "fast": "faster", "balanced": "medium",
               "slow": "slow", "slowest": "veryslow"}
_AMF_QUALITY = {"fastest": "speed", "fast": "speed", "balanced": "balanced",
                "slow": "quality", "slowest": "quality"}
_PRORES_PROFILE = {"very_high": 3, "high": 3, "good": 2, "low": 1, "lowest": 0}
_DNXHR_PROFILE = {"very_high": ("dnxhr_hqx", "yuv422p10le"), "high": ("dnxhr_hq", "yuv422p"),
                  "good": ("dnxhr_sq", "yuv422p"), "low": ("dnxhr_lb", "yuv422p"),
                  "lowest": ("dnxhr_lb", "yuv422p")}


def quality_key(value: str) -> str:
    """Accept a quality key or a legacy UI label; default ``high``."""
    if value in QUALITIES:
        return value
    return QUALITY_LABELS.get(str(value).strip(), "high")


# ── Encoder discovery ────────────────────────────────────────────────────────

_lock = threading.Lock()
_ENCODERS: dict[str, set] = {}          # ffmpeg path -> encoder names
_HW_OK: dict[str, dict] = {}            # ffmpeg path -> {kind: bool}
_VAAPI_DEVICE: dict[str, str] = {}      # ffmpeg path -> render node


def _ffmpeg() -> str:
    from core.hardware import get_binary_path
    return get_binary_path("ffmpeg")


def _cache_file() -> Optional[str]:
    try:
        from core.paths import cache_dir
        return os.path.join(cache_dir(), "encoders.json")
    except Exception:
        return None


def _binary_key(ff: str) -> str:
    try:
        st = os.stat(ff)
        return f"{os.path.abspath(ff)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        return ff


def _load_disk_cache(ff: str) -> Optional[dict]:
    path = _cache_file()
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get(_binary_key(ff))
    except (OSError, ValueError):
        return None


def _save_disk_cache(ff: str, entry: dict) -> None:
    path = _cache_file()
    if not path:
        return
    try:
        data = {}
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


def list_encoders(ffmpeg: Optional[str] = None) -> set:
    """Names of every encoder compiled into *ffmpeg* (cached)."""
    ff = ffmpeg or _ffmpeg()
    with _lock:
        if ff in _ENCODERS:
            return _ENCODERS[ff]
    names: set = set()
    try:
        r = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=15, stdin=subprocess.DEVNULL,
                           creationflags=CREATE_NO_WINDOW)
        started = False
        for line in r.stdout.splitlines():
            if line.strip().startswith("------"):
                started = True
                continue
            if started:
                parts = line.split()
                if len(parts) >= 2 and len(parts[0]) == 6:
                    names.add(parts[1])
    except (OSError, subprocess.TimeoutExpired):
        pass
    with _lock:
        _ENCODERS[ff] = names
    return names


def has_encoder(name: str) -> bool:
    return name in list_encoders()


def _vaapi_nodes() -> list:
    return sorted(glob.glob("/dev/dri/renderD*")) if sys.platform.startswith("linux") else []


def _hw_test(ff: str, kind: str) -> bool:
    enc = _HW.get(("h264", kind)) or _HW.get(("hevc", kind))
    if not enc or enc not in list_encoders(ff):
        return False
    src = ["-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=0.5"]
    tail = ["-frames:v", "8", "-c:v", enc, "-f", "null", "-"]
    base = [ff, "-hide_banner", "-nostdin", "-loglevel", "error"]
    attempts = []
    if kind == "vaapi":
        for node in _vaapi_nodes():
            attempts.append((node, base + ["-vaapi_device", node] + src +
                             ["-vf", "format=nv12,hwupload"] + tail))
    elif kind == "qsv":
        attempts.append((None, base + src + ["-pix_fmt", "nv12"] + tail))
    else:
        attempts.append((None, base + src + ["-pix_fmt", "yuv420p"] + tail))
    for node, cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=20,
                               stdin=subprocess.DEVNULL,
                               creationflags=CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            if node:
                _VAAPI_DEVICE[ff] = node
            return True
    return False


def _platform_kinds() -> tuple:
    if sys.platform == "darwin":
        return ("videotoolbox",)
    if sys.platform.startswith("win"):
        return ("nvenc", "qsv", "amf")
    return ("nvenc", "vaapi", "qsv", "amf")


def hw_available(refresh: bool = False, ffmpeg: Optional[str] = None) -> dict:
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


def working_hw_kinds() -> list:
    return [k for k, ok in hw_available().items() if ok]


def detect_in_background() -> threading.Thread:
    """Warm the encoder caches without blocking the UI."""
    t = threading.Thread(target=hw_available, name="EncoderProbe", daemon=True)
    t.start()
    return t


def preferred_hw() -> Optional[str]:
    """The hardware family to use when the user setting is ``auto``."""
    kinds = working_hw_kinds()
    for k in ("nvenc", "videotoolbox", "qsv", "amf", "vaapi"):
        if k in kinds:
            return k
    return None


def _setting_hw_mode() -> str:
    try:
        from core.settings import get
        mode = str(get("hw_encode", "auto") or "auto").lower()
    except Exception:
        mode = "auto"
    return mode if mode in ("auto", "off") + HW_KINDS else "auto"


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
    hw: Optional[str] = None
    pre_input: list = field(default_factory=list)
    args: list = field(default_factory=list)
    filter_suffix: str = ""
    pix_fmt: str = ""

    def vf(self, chain: Optional[str] = None) -> Optional[str]:
        parts = [p for p in ((chain or "").strip(","), self.filter_suffix) if p]
        return ",".join(parts) if parts else None

    def vf_args(self, chain: Optional[str] = None) -> list:
        v = self.vf(chain)
        return ["-vf", v] if v else []

    @property
    def label(self) -> str:
        return f"{self.encoder} ({HW_LABELS[self.hw]})" if self.hw else self.encoder


def choose_encoder(codec: str = "h264", hw: str = "auto") -> tuple[str, Optional[str]]:
    """Return ``(encoder_name, hw_kind_or_None)`` for *codec*.

    *hw*: ``"auto"`` (user setting, then best working GPU), ``"off"``, or a
    specific family from :data:`HW_KINDS`.  Falls back to software whenever
    the requested hardware path is unavailable.
    """
    codec = codec.lower()
    mode = _setting_hw_mode() if hw == "auto" else hw
    kind: Optional[str] = None
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


def video(codec: str = "h264", quality: str = "high", *, speed: str = "balanced",
          hw: str = "auto", ten_bit: bool = False, container: str = "mp4",
          bitrate: Optional[str] = None, maxrate: Optional[str] = None,
          gop_seconds: Optional[float] = None, fps: Optional[float] = None) -> VideoEncode:
    """Build arguments for encoding video.

    *quality* uses :data:`QUALITIES` (legacy labels such as ``"High"`` are
    accepted).  When *bitrate* is given (e.g. ``"4M"``) the encoder runs in
    average-bitrate mode instead of constant quality (for size targets use
    :func:`two_pass_args` with software encoders).
    """
    q = QUALITIES.index(quality_key(quality))
    speed = speed if speed in SPEEDS else "balanced"
    encoder, kind = choose_encoder(codec, hw)
    ve = VideoEncode(encoder=encoder, codec=codec, hw=kind)
    a = ["-c:v", encoder]

    def pix(eight: str, ten: str) -> str:
        return ten if ten_bit else eight

    if encoder == "libx264":
        ve.pix_fmt = pix("yuv420p", "yuv420p10le")
        a += ["-preset", _X26X_PRESET[speed]]
        a += ["-b:v", bitrate] if bitrate else ["-crf", str(_Q["libx264"][q])]
    elif encoder == "libx265":
        ve.pix_fmt = pix("yuv420p", "yuv420p10le")
        a += ["-preset", _X26X_PRESET[speed]]
        a += ["-b:v", bitrate] if bitrate else ["-crf", str(_Q["libx265"][q])]
        a += ["-x265-params", "log-level=error"]
    elif encoder == "libsvtav1":
        ve.pix_fmt = pix("yuv420p", "yuv420p10le")
        a += ["-preset", str(_SVT_PRESET[speed])]
        a += ["-b:v", bitrate] if bitrate else ["-crf", str(_Q["libsvtav1"][q])]
    elif encoder == "libaom-av1":
        ve.pix_fmt = pix("yuv420p", "yuv420p10le")
        a += ["-cpu-used", str(_AOM_CPU[speed]), "-row-mt", "1"]
        a += ["-b:v", bitrate] if bitrate else ["-crf", str(_Q["libaom-av1"][q]), "-b:v", "0"]
    elif encoder == "librav1e":
        ve.pix_fmt = pix("yuv420p", "yuv420p10le")
        a += ["-speed", str({"fastest": 10, "fast": 8, "balanced": 6, "slow": 4, "slowest": 2}[speed])]
        a += ["-b:v", bitrate] if bitrate else ["-qp", str(_Q["librav1e"][q])]
    elif encoder == "libvpx-vp9":
        ve.pix_fmt = pix("yuv420p", "yuv420p10le")
        a += ["-deadline", "good", "-cpu-used", str(_VPX_CPU[speed]), "-row-mt", "1"]
        a += ["-b:v", bitrate] if bitrate else ["-crf", str(_Q["libvpx-vp9"][q]), "-b:v", "0"]
    elif encoder == "libvpx":
        ve.pix_fmt = "yuv420p"
        a += ["-deadline", "good", "-cpu-used", str(_VPX_CPU[speed])]
        a += ["-b:v", bitrate] if bitrate else ["-crf", str(_Q["libvpx"][q]), "-b:v", "20M"]
    elif kind == "nvenc":
        table = {"h264": "nvenc", "hevc": "nvenc_hevc", "av1": "nvenc_av1"}[codec]
        ve.pix_fmt = pix("yuv420p", "p010le") if codec != "h264" else "yuv420p"
        a += ["-preset", _NVENC_PRESET[speed], "-tune", "hq", "-spatial-aq", "1"]
        if bitrate:
            a += ["-rc", "vbr", "-b:v", bitrate]
        else:
            a += ["-rc", "vbr", "-cq", str(_Q[table][q]), "-b:v", "0"]
        if speed in ("slow", "slowest") and codec != "av1":
            a += ["-multipass", "qres" if speed == "slow" else "fullres"]
    elif kind == "qsv":
        ve.pix_fmt = pix("nv12", "p010le")
        a += ["-preset", _QSV_PRESET[speed]]
        a += ["-b:v", bitrate] if bitrate else ["-global_quality", str(_Q["qsv"][q])]
    elif kind == "vaapi":
        node = _VAAPI_DEVICE.get(_ffmpeg()) or (_vaapi_nodes() or ["/dev/dri/renderD128"])[0]
        ve.pre_input = ["-vaapi_device", node]
        ve.filter_suffix = "format=p010,hwupload" if ten_bit else "format=nv12,hwupload"
        ve.pix_fmt = ""
        if bitrate:
            a += ["-rc_mode", "VBR", "-b:v", bitrate]
        else:
            a += ["-rc_mode", "CQP", "-qp", str(_Q["vaapi"][q])]
    elif kind == "amf":
        ve.pix_fmt = pix("nv12", "p010le")
        a += ["-quality", _AMF_QUALITY[speed]]
        if bitrate:
            a += ["-rc", "vbr_peak", "-b:v", bitrate]
        else:
            qp = str(_Q["amf"][q])
            a += ["-rc", "cqp", "-qp_i", qp, "-qp_p", qp]
            if codec == "h264":
                a += ["-qp_b", qp]
    elif kind == "videotoolbox":
        ve.pix_fmt = pix("yuv420p", "p010le")
        a += ["-b:v", bitrate] if bitrate else ["-q:v", str(_Q["videotoolbox"][q])]
        a += ["-allow_sw", "1"]
    elif encoder == "prores_ks":
        prof = _PRORES_PROFILE[QUALITIES[q]]
        ve.pix_fmt = "yuv422p10le"
        a += ["-profile:v", str(prof), "-vendor", "apl0"]
    elif encoder == "dnxhd":
        prof, pf = _DNXHR_PROFILE[QUALITIES[q]]
        ve.pix_fmt = pf
        a += ["-profile:v", prof]
    elif encoder == "ffv1":
        ve.pix_fmt = ""
        a += ["-level", "3", "-g", "1", "-slicecrc", "1"]
    elif encoder in ("mpeg4", "mjpeg"):
        ve.pix_fmt = "yuvj420p" if encoder == "mjpeg" else "yuv420p"
        a += ["-b:v", bitrate] if bitrate else ["-q:v", str(_Q[encoder][q])]
    else:
        if bitrate:
            a += ["-b:v", bitrate]

    if maxrate and encoder not in ("prores_ks", "dnxhd", "ffv1"):
        a += ["-maxrate", maxrate, "-bufsize", _double_rate(maxrate)]
    if gop_seconds and fps and encoder not in ("prores_ks", "dnxhd", "ffv1"):
        a += ["-g", str(max(1, int(round(gop_seconds * fps))))]
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


def two_pass_args(encoder: str, pass_no: int, passlog: str) -> list:
    """Options for pass 1 or 2 of a two-pass software encode."""
    if encoder in ("libx264", "libvpx-vp9", "libvpx", "libaom-av1", "mpeg4"):
        return ["-pass", str(pass_no), "-passlogfile", passlog]
    if encoder == "libx265":
        return ["-x265-params", f"pass={pass_no}:stats={passlog}.log:log-level=error"]
    if encoder == "libsvtav1":
        return ["-pass", str(pass_no), "-passlogfile", passlog]
    return []


def container_video_args(container: str, codec: str) -> list:
    c = container.lower().lstrip(".")
    out = []
    if c in ("mp4", "m4v", "mov"):
        out += ["-movflags", "+faststart"]
        if codec == "hevc":
            out += ["-tag:v", "hvc1"]      # Apple players require hvc1
    return out


# ── Audio arguments ──────────────────────────────────────────────────────────

AUDIO_ENCODERS = {
    "aac": "aac", "mp3": "libmp3lame", "opus": "libopus", "vorbis": "libvorbis",
    "flac": "flac", "alac": "alac", "ac3": "ac3", "eac3": "eac3",
    "pcm16": "pcm_s16le", "pcm24": "pcm_s24le", "wav": "pcm_s16le", "copy": "copy",
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
_PROBED_AUDIO = {"aac": "aac", "mp3": "mp3", "opus": "opus", "vorbis": "vorbis",
                 "flac": "flac", "alac": "alac", "ac3": "ac3", "eac3": "eac3",
                 "pcm_s16le": "pcm16", "pcm_s24le": "pcm24"}


def audio_key_from_probe(codec_name: str) -> Optional[str]:
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


def audio(codec: str = "aac", bitrate: Optional[str] = "192k", *,
          container: Optional[str] = None, channels: Optional[int] = None,
          sample_rate: Optional[int] = None, vbr_quality: Optional[float] = None) -> list:
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
    if enc == "libmp3lame":
        a += ["-q:a", f"{vbr_quality:g}"] if vbr_quality is not None else ["-b:a", bitrate or "192k"]
    elif enc == "libvorbis":
        a += ["-q:a", f"{vbr_quality:g}"] if vbr_quality is not None else ["-b:a", bitrate or "192k"]
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
    sw = [n for n in ("libx264", "libx265", "libsvtav1", "libvpx-vp9", "libopus")
          if n in list_encoders()]
    return ("Hardware encoders: " + (", ".join(ok) if ok else "none detected") +
            "\nSoftware encoders: " + ", ".join(sw))
