"""
core/quality.py  -  How close is an encode to its original?

One FFmpeg pass measures VMAF (Netflix's model of what viewers notice),
SSIM and PSNR between an original and an encoded/edited copy, frame by
frame.  The copy is matched to the original first: same size (scaled with
bicubic), frame rate and pixel format, an optional offset when one of them
was trimmed, and an optional time limit.

``build(...)`` -> (argv, log files); run it in a scratch folder (cwd) so the
log paths need no escaping; ``read(...)`` -> :class:`Result` with per-frame
scores, averages, 1% lows and the worst moments.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from core import ffmpeg as ff

MODELS = {
    "hd": ("Standard - HD screens and laptops", "version=vmaf_v0.6.1"),
    "4k": ("4K TV, watched from a sofa", "version=vmaf_4k_v0.6.1"),
    "neg": ("Ignore sharpening tricks (NEG)", "version=vmaf_v0.6.1neg"),
}
METRICS = ("vmaf", "ssim", "psnr")


def verdict(vmaf: float) -> str:
    if vmaf >= 95:
        return "Visually identical for almost everyone"
    if vmaf >= 90:
        return "Excellent - very hard to tell apart"
    if vmaf >= 80:
        return "Good - small differences if you look closely"
    if vmaf >= 70:
        return "Fair - visible softness or blockiness in places"
    return "Poor - clearly worse than the original"


def _rate(info: ff.MediaInfo) -> str:
    v = info.video
    if v is None:
        return "30/1"
    rate = v.raw.get("avg_frame_rate") or v.raw.get("r_frame_rate") or "30/1"
    if rate in ("0/0", "") or not v.fps or v.fps > 240:
        return "30/1"
    return rate


def build(
    ref: str,
    dist: str,
    ref_info: ff.MediaInfo,
    dist_info: ff.MediaInfo,
    *,
    metrics: Iterable[str] = METRICS,
    model: str = "hd",
    skip_ref: float = 0.0,
    skip_dist: float = 0.0,
    seconds: float | None = None,
    subsample: int = 1,
    threads: int | None = None,
) -> tuple[list[str], dict[str, str]]:
    """(argv, {metric: log file name}).  *skip_ref* / *skip_dist*: seconds to
    skip at the start of the original / the copy, to line them up when one of
    them was trimmed."""
    wanted = set(metrics)
    chosen = [m for m in METRICS if m in wanted]
    if not chosen:
        raise ValueError("Choose at least one measurement.")
    if ref_info.video is None or dist_info.video is None:
        raise ValueError("Both files need a picture.")
    w, h = ref_info.video.display_size
    w, h = max(2, w // 2 * 2), max(2, h // 2 * 2)
    deep = max(ref_info.video.bit_depth, dist_info.video.bit_depth) > 8
    fmt = "yuv420p10le" if deep else "yuv420p"
    rate = _rate(ref_info)
    limit = f",trim=duration={seconds:.3f}" if seconds else ""
    d_trim = f"trim=start={skip_dist:.3f}," if skip_dist > 0 else ""
    r_trim = f"trim=start={skip_ref:.3f}," if skip_ref > 0 else ""
    n = len(chosen)
    # FFmpeg converts between differently tagged inputs and treats untagged video as SD (BT.601): a copy
    # that merely gained or lost its colour tags would be scored as colour-shifted.  Where only one side
    # is tagged, both are measured as tagged.
    tags = shared_tags(ref_info.video, dist_info.video)
    tag = ("setparams=" + ":".join(f"{k}={v}" for k, v in tags.items()) + ",") if tags else ""
    d_trim += tag
    r_trim += tag
    parts = [
        f"[0:v]{d_trim}setpts=PTS-STARTPTS,scale={w}:{h}:flags=bicubic,setsar=1,fps={rate},format={fmt}"
        f"{limit},setpts=PTS-STARTPTS"
        + (f",split={n}" + "".join(f"[d{i}]" for i in range(n)) if n > 1 else "[d0]"),
        f"[1:v]{r_trim}setpts=PTS-STARTPTS,scale={w}:{h}:flags=bicubic,setsar=1,fps={rate},format={fmt}"
        f"{limit},setpts=PTS-STARTPTS"
        + (f",split={n}" + "".join(f"[r{i}]" for i in range(n)) if n > 1 else "[r0]"),
    ]
    files: dict[str, str] = {}
    sync = "shortest=1:repeatlast=0"
    for i, m in enumerate(chosen):
        if m == "vmaf":
            files[m] = "vmaf.json"
            parts.append(
                f"[d{i}][r{i}]libvmaf=model={MODELS.get(model, MODELS['hd'])[1]}:log_fmt=json:"
                f"log_path=vmaf.json:n_threads={threads or max(1, min(16, os.cpu_count() or 4))}:"
                f"n_subsample={max(1, int(subsample))}:{sync}"
            )
        elif m == "ssim":
            files[m] = "ssim.log"
            parts.append(f"[d{i}][r{i}]ssim=stats_file=ssim.log:{sync}")
        else:
            files[m] = "psnr.log"
            parts.append(f"[d{i}][r{i}]psnr=stats_file=psnr.log:{sync}")
    argv = [
        ff.ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        dist,
        "-i",
        ref,
        "-filter_complex",
        ";".join(parts),
        "-an",
        "-sn",
        "-f",
        "null",
        "-",
    ]
    return argv, files


def shared_tags(a: ff.Stream, b: ff.Stream) -> dict[str, str]:
    """setparams values for colour tags present on only one of two streams."""
    out: dict[str, str] = {}
    for key, attr in (
        ("range", "color_range"),
        ("color_primaries", "color_primaries"),
        ("color_trc", "color_transfer"),
        ("colorspace", "color_space"),
    ):
        va, vb = (getattr(a, attr, "") or ""), (getattr(b, attr, "") or "")
        va, vb = ("" if v in ("unknown", "reserved") else v for v in (va, vb))
        if bool(va) != bool(vb):
            out[key] = va or vb
    return out


@dataclass
class Series:
    times: list[float] = field(default_factory=list[float])
    values: list[float] = field(default_factory=list[float])

    def mean(self) -> float:
        vals = [v for v in self.values if math.isfinite(v)]
        if not vals:  # every frame identical (PSNR is infinite) - or nothing measured
            return float("inf") if self.values else float("nan")
        return sum(vals) / len(vals)

    def low(self, share: float = 0.01) -> float:
        """Average of the worst *share* of frames (the '1% low')."""
        vals = sorted(v for v in self.values if math.isfinite(v))
        if not vals:
            return float("inf") if self.values else float("nan")
        k = max(1, int(len(vals) * share))
        return sum(vals[:k]) / k

    def minimum(self) -> float:
        vals = [v for v in self.values if math.isfinite(v)]
        return min(vals) if vals else (float("inf") if self.values else float("nan"))


@dataclass
class Result:
    fps: float
    vmaf: Series = field(default_factory=Series)
    ssim: Series = field(default_factory=Series)
    psnr: Series = field(default_factory=Series)
    vmaf_harmonic: float = float("nan")

    def worst(self, n: int = 8, gap: float = 2.0) -> list[tuple[float, float]]:
        """[(time, vmaf)] of the lowest-scoring moments, at least *gap* s apart."""
        pts = sorted(zip(self.vmaf.values, self.vmaf.times, strict=False))
        out: list[tuple[float, float]] = []
        for value, at in pts:
            if all(abs(at - t0) >= gap for t0, _v in out):
                out.append((at, value))
            if len(out) >= n:
                break
        return sorted(out)

    def summary(self) -> dict[str, dict[str, Any]]:
        d: dict[str, dict[str, Any]] = {}

        def num(v: float) -> float | str | None:
            return round(v, 4) if math.isfinite(v) else ("inf" if v > 0 else None)

        for name in METRICS:
            s: Series = getattr(self, name)
            if s.values:
                d[name] = {
                    "mean": num(s.mean()),
                    "low_1pct": num(s.low()),
                    "min": num(s.minimum()),
                    "frames": len(s.values),
                }
        if self.vmaf.values and math.isfinite(self.vmaf_harmonic):
            d["vmaf"]["harmonic_mean"] = round(self.vmaf_harmonic, 4)
        return d

    def to_csv(self, path: str) -> None:
        rows: dict[float, dict[str, float]] = {}
        for name in METRICS:
            s: Series = getattr(self, name)
            for t, v in zip(s.times, s.values, strict=False):
                rows.setdefault(round(t, 4), {})[name] = v
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("time," + ",".join(METRICS) + "\n")
            for t in sorted(rows):
                fh.write(
                    f"{t:.3f},"
                    + ",".join(f"{rows[t][m]:.4f}" if m in rows[t] else "" for m in METRICS)
                    + "\n"
                )


_SSIM = re.compile(r"n:(\d+).*?All:([\d.]+)")
_PSNR = re.compile(r"n:(\d+).*?psnr_avg:(inf|[\d.]+)")


def read(folder: str, files: Mapping[str, str], fps: float, skip_ref: float = 0.0) -> Result:
    """Parse the logs written by :func:`build`'s command; times are in the original's timeline."""
    start = max(0.0, skip_ref)
    res = Result(fps)
    path = os.path.join(folder, files.get("vmaf", ""))
    if "vmaf" in files and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for fr in data.get("frames", []):
            res.vmaf.times.append(start + fr["frameNum"] / fps)
            res.vmaf.values.append(float(fr["metrics"]["vmaf"]))
        pooled = ff.json_object(ff.json_object(data.get("pooled_metrics")).get("vmaf"))
        res.vmaf_harmonic = float(pooled.get("harmonic_mean", float("nan")))
    for name, rx in (("ssim", _SSIM), ("psnr", _PSNR)):
        path = os.path.join(folder, files.get(name, ""))
        if name not in files or not os.path.isfile(path):
            continue
        series: Series = getattr(res, name)
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = rx.search(line)
                if m:
                    series.times.append(start + (int(m.group(1)) - 1) / fps)
                    series.values.append(float("inf") if m.group(2) == "inf" else float(m.group(2)))
    return res


def ssim_db(ssim: float) -> float:
    return float("inf") if ssim >= 1 else -10 * math.log10(1 - ssim)
