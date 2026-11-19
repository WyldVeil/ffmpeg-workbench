"""
core/hdr.py  -  What kind of HDR a video is, and turning it into SDR

``inspect(info, path)`` tells HDR10, HDR10+, HLG and Dolby Vision apart and
reads the brightness the video was mastered for (mastering display peak,
MaxCLL/MaxFALL) from the stream and its first frame.

``sdr_chain(...)`` is the filter chain that converts it to normal (SDR,
BT.709) video:

* GPU (libplacebo on Vulkan) - the best result: BT.2390 tone mapping with
  scene-by-scene brightness detection, perceptual gamut mapping, and the
  only path that understands Dolby Vision profile 5 (whose colours are
  otherwise wrong - purple and green);
* CPU (zscale + tonemap) - works everywhere.

Both remove the HDR descriptions from the frames (filters.STRIP_HDR) so the
SDR file isn't mistaken for HDR by TVs and players.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from core import ffmpeg as ff
from core import filters as fl

#: id: (label, libplacebo curve, CPU tonemap curve)
CURVES = {
    "bt2390": ("Standard (BT.2390) - recommended", "bt.2390", "hable"),
    "spline": ("Softer highlights (spline)", "spline", "mobius"),
    "bt2446a": ("Broadcast (BT.2446a)", "bt.2446a", "hable"),
    "hable": ("Filmic, more contrast (Hable)", "hable", "hable"),
    "mobius": ("Vivid colours (Mobius)", "mobius", "mobius"),
    "reinhard": ("Simple (Reinhard)", "reinhard", "reinhard"),
    "clip": ("No compression - clip the highlights", "clip", "clip"),
}


@dataclass
class HdrInfo:
    kind: str = ""                  # "", "HDR10", "HDR10+", "HLG", "Dolby Vision"
    transfer: str = ""
    dv_profile: int = 0
    dv_compat: int = -1             # base layer: 0 none (profile 5), 1 HDR10, 2 SDR, 4 HLG
    mastering_nits: float = 0.0     # peak of the mastering display
    max_cll: int = 0                # brightest pixel (nits)
    max_fall: int = 0               # brightest frame average (nits)

    @property
    def is_hdr(self) -> bool:
        return bool(self.kind)

    @property
    def needs_gpu(self) -> bool:
        """Dolby Vision without an HDR10/HLG base layer: only libplacebo gets the colours right."""
        return self.kind == "Dolby Vision" and self.dv_compat in (0, -1)

    def label(self) -> str:
        if not self.kind:
            return "Not HDR"
        parts = [self.kind + (f" (profile {self.dv_profile})" if self.dv_profile else "")]
        if self.mastering_nits:
            parts.append(f"mastered for {self.mastering_nits:g} nits")
        if self.max_cll:
            parts.append(f"brightest {self.max_cll} nits")
        return " · ".join(parts)


def _ratio(value) -> float:
    try:
        text = str(value)
        if "/" in text:
            a, b = text.split("/", 1)
            return float(a) / float(b) if float(b) else 0.0
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _read_side_data(entries, out: HdrInfo) -> bool:
    """Fill *out* from ffprobe side data; True when HDR10+ metadata was seen."""
    plus = False
    for sd in entries or []:
        kind = str(sd.get("side_data_type", ""))
        if kind.startswith("DOVI configuration"):
            out.dv_profile = int(sd.get("dv_profile") or 0)
            out.dv_compat = int(sd.get("dv_bl_signal_compatibility_id", -1) or 0)
        elif kind.startswith("Dolby Vision"):
            out.dv_profile = out.dv_profile or -1
        elif kind.startswith("Mastering display"):
            out.mastering_nits = out.mastering_nits or round(_ratio(sd.get("max_luminance")), 1)
        elif kind.startswith("Content light level"):
            out.max_cll = out.max_cll or int(sd.get("max_content") or 0)
            out.max_fall = out.max_fall or int(sd.get("max_average") or 0)
        elif "2094-40" in kind or "HDR10+" in kind:
            plus = True
    return plus


def first_frame_side_data(path: str) -> list:
    try:
        r = ff.capture([ff.ffprobe_path(), "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+#1",
                        "-show_frames", "-show_entries", "frame=side_data_list", "-of", "json", path], timeout=30)
        frames = json.loads(r.stdout or "{}").get("frames") or []
        return frames[0].get("side_data_list") or [] if frames else []
    except (OSError, ValueError):
        return []


def inspect(info: ff.MediaInfo, path: Optional[str] = None) -> HdrInfo:
    v = info.video
    out = HdrInfo()
    if v is None:
        return out
    out.transfer = v.color_transfer or ""
    plus = _read_side_data(v.raw.get("side_data_list"), out)
    if path:
        plus = _read_side_data(first_frame_side_data(path), out) or plus
    if out.dv_profile:
        out.kind = "Dolby Vision"
        if out.dv_profile < 0:
            out.dv_profile = 0
    elif out.transfer == "smpte2084":
        out.kind = "HDR10+" if plus else "HDR10"
    elif out.transfer == "arib-std-b67":
        out.kind = "HLG"
    return out


def sdr_chain(*, gpu: bool, curve: str = "bt2390", out_fmt: str = "yuv420p", adaptive: bool = True,
              assume: str = "") -> str:
    """Filter chain HDR -> SDR BT.709 (GPU needs ``-init_hw_device vulkan`` before the input)."""
    _label, pl_curve, cpu_curve = CURVES.get(curve, CURVES["bt2390"])
    if not gpu:
        return fl.hdr_to_sdr_chain(method=cpu_curve, out_fmt=out_fmt, assume=assume)
    return (f"{fl.HDR_INPUTS.get(assume, '')}libplacebo=tonemapping={pl_curve}:gamut_mode=perceptual:"
            f"colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv:format={out_fmt}:"
            f"peak_detect={1 if adaptive else 0}:apply_dolbyvision=1,{fl.STRIP_HDR}")
