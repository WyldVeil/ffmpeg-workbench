"""
core/ops/webm.py - WebM encoding for the WebM Maker (tabs/social/webm_maker.py).

Values in, FFmpeg arguments out; the queued job (WebmEncode) runs one or two
passes and, under a size limit, re-encodes at a lower bitrate until the file
fits.  Simple mode works everything out from a size profile and the clip's
length (simple_settings); advanced mode takes the person's choices.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass

from core import ffmpeg as ff
from core.queue_manager import TaskContext

VP9_PIXFMTS = frozenset(
    {
        "yuv420p",
        "yuv420p10le",
        "yuv422p",
        "yuv422p10le",
        "yuv440p",
        "yuv440p10le",
        "yuv444p",
        "yuv444p10le",
        "yuva420p",
        "gbrp",
        "gbrp10le",
    }
)

VP8_PIXFMTS = frozenset({"yuv420p", "yuva420p"})


def resolve_pixfmt(user_choice: str, source_pixfmt: str, codec: str) -> tuple[str, str | None]:
    """Resolve the effective FFmpeg `-pix_fmt` value.

    Args:
        user_choice: dropdown value. Either "source" (case-insensitive) for
            auto-detect, or an explicit pix_fmt name like "yuv420p".
        source_pixfmt: pix_fmt detected from ffprobe ("" if unknown).
        codec: "VP9" or "VP8".

    Returns:
        (effective_pixfmt, warning_msg_or_None). Warning is non-None only
        when the user requested "source" but the detected pix_fmt is
        unsupported by the chosen codec — never when source is unknown.
    """
    if user_choice.strip().lower() != "source":
        return user_choice, None

    if not source_pixfmt:
        return "yuv420p", None

    supported = VP9_PIXFMTS if codec == "VP9" else VP8_PIXFMTS
    if source_pixfmt in supported:
        return source_pixfmt, None

    return (
        "yuv420p",
        f"⚠ Source pix_fmt '{source_pixfmt}' not supported by {codec}; falling back to yuv420p.",
    )


def vp9_deadline(deadline: str, two_pass: bool) -> str:
    """Return the libvpx-vp9 ``-deadline`` value to actually use.

    libvpx-vp9 in ``realtime`` mode skips first-pass analysis, so the stats
    file stays empty and pass 2 fails with ``rc_twopass_stats_in.buf not set``.
    Force ``good`` when two-pass is on so the encode actually completes.
    """
    if two_pass and deadline == "realtime":
        return "good"
    return deadline


# ─── Simple mode: auto-compute engine (spec 2026-05-10) ──────────────────────
@dataclass(frozen=True, slots=True, kw_only=True)
class WebmJob:
    """What to encode: *dur* seconds from *start*, with these video and audio
    arguments (``{BITRATE}`` in *video* is the kbps for this attempt)."""

    start: float
    dur: float
    video: tuple[str, ...]
    audio: tuple[str, ...]
    extra: tuple[str, ...] = ()
    two_pass: bool = True
    bitrate: int = 0
    cap_bytes: int | None = None  # a size limit: re-encode until under it
    format: str = "webm"


@dataclass(frozen=True)
class WebmEncode:
    """One or two passes; with a size limit, re-encoded at a lower bitrate
    (up to three times) until the file fits."""

    src: str
    job: WebmJob

    def _cmd(self, bitrate: int, *tail: str) -> list[str]:
        job = self.job
        inp = [ff.ffmpeg_path(), "-y"]
        if job.start > 0:
            inp += ["-ss", ff.ts(job.start)]
        inp += ["-t", ff.ts(job.dur), "-i", self.src, "-map", "0:v:0"]
        return [*inp, *(a.replace("{BITRATE}", str(bitrate)) for a in job.video), *tail]

    def run(self, _p: object, _c: object, ctx: TaskContext) -> int:
        job = self.job
        two_pass, cap = job.two_pass, job.cap_bytes
        amap = ["-map", "0:a:0"] if job.audio != ("-an",) else []
        bitrate, final = job.bitrate, ctx.output()
        log = os.path.join(ctx.tempdir(), "pass")
        if two_pass:
            ctx.log("Pass 1 of 2: analysing…")
            first = self._cmd(bitrate, "-pass", "1", "-passlogfile", log, "-an", "-f", "null", "-")
            ctx.span(0.0, 0.4).run(first, duration=job.dur)
        passes = ["-pass", "2", "-passlogfile", log] if two_pass else []
        lo = 0.4 if two_pass else 0.0
        for attempt in range(4):
            ctx.log(
                "Encoding…"
                if attempt == 0
                else f"Over the size limit - re-encoding at {bitrate} kbps…"
            )
            ctx.span(
                lo + (1 - lo) * attempt / 4, lo + (1 - lo) * (attempt + 1) / 4 if attempt else 1.0
            )
            tail = [*passes, *amap, *job.audio, *job.extra, "-f", job.format, final]
            ctx.run(self._cmd(bitrate, *tail), duration=job.dur)
            if not cap or os.path.getsize(final) <= cap:
                break
            bitrate = retry_bitrate(bitrate, cap, os.path.getsize(final))
        else:
            raise RuntimeError(
                "Could not get the file under the size limit - try a shorter "
                "trim or a lower resolution."
            )
        ctx.log(f"Final size: {os.path.getsize(final) / 1024 / 1024:.2f} MB")
        return 0


@dataclass(frozen=True)
class SimpleEncodeSettings:
    audio_bitrate_kbps: int  # 0 == silent profile (drives -an)
    force_mono: bool  # adds -ac 1 when True; only meaningful if audio_bitrate_kbps > 0
    dynaudnorm: bool  # adds -af dynaudnorm; tied to force_mono
    fps_cap: float
    target_max_dim: int  # cap for the larger axis (W if landscape, H if portrait)
    video_bitrate_kbps: int


PROFILE_SIZE_BYTES = {
    "6mb_audio": 6 * 1024 * 1024,
    "4mb_audio": 4 * 1024 * 1024,
    "4mb_silent": 4 * 1024 * 1024,
}
PROFILE_DURATION_CAP_S = {"6mb_audio": 399.92, "4mb_audio": 300.0, "4mb_silent": 120.0}
PROFILE_HAS_AUDIO = {"6mb_audio": True, "4mb_audio": True, "4mb_silent": False}
PROFILE_LABELS = {
    "6mb_audio": "6 MB · audio · up to 6m 40s",
    "4mb_audio": "4 MB · audio · up to 5m 00s",
    "4mb_silent": "4 MB · silent · up to 2m 00s",
}

# duration → audio bitrate (kbps)
AUDIO_MAP_6MB: dict[float, float] = {
    15: 128,
    30: 120,
    45: 112,
    95: 96,
    120: 80,
    240: 64,
    300: 56,
    360: 48,
    400: 32,
}
AUDIO_MAP_4MB: dict[float, float] = {10: 96, 20: 64, 40: 56, 60: 48, 120: 32}
AUDIO_MAP_MUSIC: dict[float, float] = {
    15: 320,
    45: 256,
    100: 192,
    220: 160,
    285: 128,
    300: 120,
    330: 112,
    400: 96,
}

_FPS_MAP: dict[float, float] = {150.0: 60.0, 200.0: 30.0, 400.0: 24.0}
_BITRATE_COMP_MAP: dict[float, float] = {240.0: 0, 300.0: 2, 360.0: 3, 380.0: 4, 400.0: 6}
_RESOLUTION_TABLE = (480, 576, 640, 736, 854, 960, 1024, 1280, 1440, 1600, 1920, 2048)

VIDEO_BITRATE_FLOOR = 50  # kbps; below this, quality is unwatchable
_VIDEO_BITRATE_CEIL = 2800  # kbps cap (matches reference)
_LOG_A = 0.2311
_LOG_B = 35.47


def lookup_threshold_map(duration_s: float, table: Mapping[float, float]) -> float:
    """First value whose key is >= duration (keys ascending), else the last one."""
    keys = sorted(table)
    for k in keys:
        if duration_s <= k:
            return table[k]
    return table[keys[-1]]


def simple_settings(
    profile: str,
    duration_s: float,
    source_w: int,
    source_h: int,
    source_fps: float,
    music_mode: bool,
) -> SimpleEncodeSettings:
    """Pure: everything the Simple encoder needs from a profile and the duration."""
    duration_s = max(0.1, float(duration_s))
    # 1. audio bitrate
    if not PROFILE_HAS_AUDIO[profile]:
        audio_kbps = 0
    else:
        table = (
            AUDIO_MAP_MUSIC
            if music_mode
            else AUDIO_MAP_6MB
            if profile == "6mb_audio"
            else AUDIO_MAP_4MB
        )
        audio_kbps = int(lookup_threshold_map(duration_s, table))
    # 2. mixdown
    force_mono = dynaudnorm = 0 < audio_kbps <= 64
    # 3. fps cap
    fps_map = float(lookup_threshold_map(duration_s, _FPS_MAP))
    fps_cap = min(fps_map, source_fps) if source_fps and source_fps > 0 else fps_map
    # 4. video bitrate
    total_bits = PROFILE_SIZE_BYTES[profile] * 8
    audio_bits = audio_kbps * 1000 * duration_s
    comp = lookup_threshold_map(duration_s, _BITRATE_COMP_MAP)
    raw = (total_bits - audio_bits) / duration_s / 1000 - comp
    video_kbps = max(VIDEO_BITRATE_FLOOR, min(_VIDEO_BITRATE_CEIL, int(raw)))
    # 5. target resolution (logarithmic curve, normalised to a 1080 short side)
    source_w, source_h = max(2, int(source_w or 1920)), max(2, int(source_h or 1080))
    short = min(source_w, source_h)
    norm_w, norm_h = source_w * 1080.0 / short, source_h * 1080.0 / short
    scale_factor = _LOG_A * math.log(video_kbps / _LOG_B) if video_kbps > _LOG_B else 0.0
    if scale_factor <= 0:
        nearest = _RESOLUTION_TABLE[0]
    else:
        scaled_px = norm_w * norm_h * scale_factor
        max_dim_target = max(scaled_px / norm_h, scaled_px / norm_w)
        nearest = _RESOLUTION_TABLE[0]
        for r in _RESOLUTION_TABLE:
            if max_dim_target >= r:
                nearest = r
            else:
                break
    source_max = max(source_w, source_h)
    target = source_max if source_max <= nearest else nearest
    return SimpleEncodeSettings(audio_kbps, force_mono, dynaudnorm, fps_cap, target, video_kbps)


def retry_bitrate(video_kbps: int, cap_bytes: int, actual_bytes: int) -> int:
    """Lower bitrate for another pass 2 when the file came out over the cap."""
    return max(VIDEO_BITRATE_FLOOR // 2, int(video_kbps * cap_bytes / max(actual_bytes, 1) * 0.97))


# ── Building the job ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class Source:
    """What the job needs to know about the source."""

    width: int
    height: int
    fps: float
    pixfmt: str
    has_audio: bool


def simple_job(
    profile: str, start: float, dur: float, src: Source, music: bool
) -> tuple[WebmJob, SimpleEncodeSettings, str | None]:
    """The two-pass VP9 job for a size *profile* (and a warning about the pixel format, if any)."""
    st = simple_settings(profile, dur, src.width, src.height, src.fps, music)
    pixfmt, warn = resolve_pixfmt("source", src.pixfmt, "VP9")
    portrait = src.height > src.width
    vf: list[str] = []
    if st.target_max_dim < max(src.width, src.height):
        side = f"-2:{st.target_max_dim}" if portrait else f"{st.target_max_dim}:-2"
        vf.append(f"scale={side}:flags=lanczos")
    if st.fps_cap and (not src.fps or st.fps_cap < src.fps - 0.01):
        vf.append(f"fps={st.fps_cap:g}")
    video = [
        "-c:v",
        "libvpx-vp9",
        "-b:v",
        "{BITRATE}k",
        "-cpu-used",
        "1",
        "-deadline",
        vp9_deadline("good", True),
    ]
    video += [
        "-tile-columns",
        "2",
        "-row-mt",
        "1",
        "-undershoot-pct",
        "0",
        "-overshoot-pct",
        "0",
        "-pix_fmt",
        pixfmt,
    ]
    if vf:
        video += ["-vf", ",".join(vf)]
    audio = ["-an"]
    if st.audio_bitrate_kbps and src.has_audio:
        audio = ["-c:a", "libopus", "-b:a", f"{st.audio_bitrate_kbps}k"]
        audio += ["-ac", "1"] if st.force_mono else []
        audio += ["-af", "dynaudnorm"] if st.dynaudnorm else []
    job = WebmJob(
        start=start,
        dur=dur,
        video=tuple(video),
        audio=tuple(audio),
        extra=("-map_metadata", "-1"),
        two_pass=True,
        bitrate=st.video_bitrate_kbps,
        cap_bytes=PROFILE_SIZE_BYTES[profile],
    )
    return job, st, warn


@dataclass(frozen=True, slots=True, kw_only=True)
class Advanced:
    """The advanced mode's choices.  *size* (w, h) None keeps the source's;
    *fps* "" or "0" keeps its rate; *audio_codec* None: no sound."""

    codec: str  # "VP9" or "VP8"
    cpu_used: int
    deadline: str  # VP9's
    two_pass: bool
    tile_columns: str
    row_mt: bool
    pixfmt: str  # a PixFmt value: "source" or a format
    size: tuple[int, int] | None = None
    fps: str = ""
    audio_codec: str | None = None
    audio_bitrate: str = "128k"
    strip_metadata: bool = True
    loop: bool = False


def advanced_filters(a: Advanced) -> list[str]:
    """Resize (when asked) and frame rate (when set).  ValueError for a bad rate."""
    vf = [f"scale={a.size[0]}:{a.size[1]}:flags=lanczos"] if a.size else []
    fps = (a.fps or "0").strip()
    if fps and fps != "0":
        try:
            float(fps)
        except ValueError:
            raise ValueError("FPS must be a number (0 = keep).") from None
        vf.append(f"fps={fps}")
    return vf


def advanced_job(
    a: Advanced,
    start: float,
    dur: float,
    src: Source,
    *,
    crf: int = 0,
    bitrate: int = 0,
    cap_bytes: int | None = None,
) -> tuple[WebmJob, str | None]:
    """The job for the advanced choices: at *crf*, or at *bitrate* kbps under *cap_bytes*."""
    size_limited = cap_bytes is not None
    rate = ["-b:v", "{BITRATE}k"] if size_limited else ["-b:v", "0", "-crf", str(crf)]
    pixfmt, warn = resolve_pixfmt(a.pixfmt, src.pixfmt, a.codec)
    if a.codec == "VP9":
        video = [
            "-c:v",
            "libvpx-vp9",
            *rate,
            "-cpu-used",
            str(a.cpu_used),
            "-deadline",
            vp9_deadline(a.deadline, a.two_pass),
        ]
        video += [
            "-tile-columns",
            a.tile_columns,
            "-row-mt",
            "1" if a.row_mt else "0",
            "-pix_fmt",
            pixfmt,
        ]
        video += ["-undershoot-pct", "0", "-overshoot-pct", "0"] if size_limited else []
    else:
        video = ["-c:v", "libvpx", *rate, "-cpu-used", str(a.cpu_used), "-pix_fmt", pixfmt]
    vf = advanced_filters(a)
    if vf:
        video += ["-vf", ",".join(vf)]
    audio = (
        ["-c:a", a.audio_codec, "-b:a", a.audio_bitrate]
        if a.audio_codec and src.has_audio
        else ["-an"]
    )
    extra = ["-map_metadata", "-1"] if a.strip_metadata else []
    extra += ["-loop", "0"] if a.loop else []
    job = WebmJob(
        start=start,
        dur=dur,
        video=tuple(video),
        audio=tuple(audio),
        extra=tuple(extra),
        two_pass=a.two_pass,
        bitrate=bitrate,
        cap_bytes=cap_bytes,
    )
    return job, warn
