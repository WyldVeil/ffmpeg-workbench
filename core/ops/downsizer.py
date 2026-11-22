"""
core/ops/downsizer.py - command builders for tabs/transcoder/downsizer.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl
from core.ops.shared import Steps

#: acceptable bits per pixel per frame - below this the picture falls apart
MIN_BPP = {"h264": 0.045, "hevc": 0.03, "av1": 0.025}


LADDER = (2160, 1440, 1080, 900, 720, 540, 480, 360, 240, 180)


MARGIN = 0.97  # container overhead + encoder slack


@dataclass(frozen=True, slots=True)
class Goal:
    """What the smaller file must be: under *target_bytes*, in *codec*, with its
    audio kept ('copy'), re-encoded ('auto') or dropped ('none'); the size and
    frame rate may be lowered only where allowed."""

    target_bytes: int
    codec: str = "h264"
    audio_mode: str = "auto"
    allow_res: bool = True
    allow_fps: bool = True


@dataclass
class Plan:
    video_kbps: int
    audio_kbps: int
    mono: bool
    width: int
    height: int
    fps: float
    notes: list[str]
    goal: Goal


def _audio_budget(
    goal: Goal, budget: float, *, has_audio: bool, audio_src_kbps: int
) -> tuple[int, bool, list[str]]:
    """(kb/s for the sound, mono?, notes): none, the original's, or what the budget allows."""
    if not has_audio or goal.audio_mode == "none":
        return 0, False, []
    if goal.audio_mode == "copy":
        if audio_src_kbps:
            return audio_src_kbps, False, []
        return 320, False, ["The original audio bitrate is unknown - 320 kb/s is reserved for it."]
    a_kbps = 128 if budget >= 2000 else 96 if budget >= 800 else 64 if budget >= 350 else 48
    return a_kbps, a_kbps <= 48, []


def _bpp(v_kbps: int, w: float, h: float, fps: float) -> float:
    """Bits per pixel per frame at this bitrate."""
    return v_kbps * 1000 / max(1, w * h * fps)


def _smaller_size(
    v_kbps: int, size: tuple[int, int], fps: float, min_bpp: float
) -> tuple[int, int]:
    """The first rung of the ladder below *size* where the picture stays clean (or the lowest)."""
    w, h = size
    out = size
    short = min(w, h)
    for rung in LADDER:
        if rung >= short:
            continue
        scale = rung / short
        out = max(2, round(w * scale / 2) * 2), max(2, round(h * scale / 2) * 2)
        if _bpp(v_kbps, *out, fps) >= min_bpp:
            break
    return out


def plan(
    goal: Goal,
    duration: float,
    size: tuple[int, int],
    fps: float,
    *,
    has_audio: bool = True,
    audio_src_kbps: int = 0,
) -> Plan:
    """Work out bitrates, size and frame rate for *goal*.  Raises ValueError
    when the target cannot hold a watchable video."""
    if duration <= 0:
        raise ValueError("The video's length is unknown.")
    budget = goal.target_bytes * 8 / 1000 / duration * MARGIN  # kbit/s for everything
    a_kbps, mono, notes = _audio_budget(
        goal, budget, has_audio=has_audio, audio_src_kbps=audio_src_kbps
    )
    v_kbps = int(budget - a_kbps)
    if v_kbps < 40:
        raise ValueError(
            f"{goal.target_bytes / 1e6:g} MB is too small for {ff.format_time(duration)} of video "
            f"(only {max(0, v_kbps)} kb/s would be left for the picture). Trim the video, "
            "remove the audio or choose a bigger size."
        )
    out_fps = fps if fps and fps > 0 else 30.0
    min_bpp = MIN_BPP.get(goal.codec, 0.045)
    if goal.allow_fps and out_fps > 31 and _bpp(v_kbps, *size, out_fps) < min_bpp * 1.5:
        out_fps = out_fps / 2 if out_fps / 2 >= 23.9 else 30.0
        notes.append(f"Frame rate lowered to {out_fps:g} fps.")
    out_w, out_h = size
    if goal.allow_res and _bpp(v_kbps, *size, out_fps) < min_bpp:
        out_w, out_h = _smaller_size(v_kbps, size, out_fps, min_bpp)
        if (out_w, out_h) != size:
            notes.append(f"Resolution lowered to {out_w}×{out_h} so the picture stays clean.")
    if _bpp(v_kbps, out_w, out_h, out_fps) < min_bpp * 0.6:
        notes.append(
            "Expect visible blockiness - a shorter clip or a bigger size would look better."
        )
    return Plan(v_kbps, a_kbps, mono, out_w, out_h, out_fps, notes, goal)


def retry_kbps(v_kbps: int, target_bytes: int, actual_bytes: int, audio_bytes: int = 0) -> int:
    """Lower video bitrate after an attempt came out over the limit."""
    video_room = max(1, target_bytes - audio_bytes)
    video_used = max(1, actual_bytes - audio_bytes)
    return max(20, int(v_kbps * video_room / video_used * 0.96))


def _size_chain(v: ff.Stream, p: Plan) -> str | None:
    """Tone-map HDR for H.264, scale to the plan's size, change the frame rate - as needed."""
    chain: list[str] = []
    if v.is_hdr and p.goal.codec == "h264":
        chain.append(fl.hdr_to_sdr_chain())
    w0, h0 = v.display_size
    if (p.width, p.height) != (max(2, w0 // 2 * 2), max(2, h0 // 2 * 2)) or (
        v.sar and v.sar not in ("1:1", "0:1")
    ):
        chain.append(f"scale={p.width}:{p.height}:flags=lanczos,setsar=1")
    if v.fps and abs(p.fps - v.fps) > 0.01:
        chain.append(f"fps={p.fps:g}")
    return ",".join(chain) or None


def _container_of(final: str) -> str:
    """The output's type, looking through a ".part" name."""
    base, ext = os.path.splitext(final)
    if ext.lower() == ".part":
        ext = os.path.splitext(base)[1]
    return ext.lstrip(".").lower() or "mp4"


def _audio_args(info: ff.MediaInfo, p: Plan) -> list[str]:
    if p.goal.audio_mode == "none" or not info.has_audio:
        return ["-an"]
    if p.goal.audio_mode == "copy":
        return ["-map", "0:a:0", "-c:a", "copy"]
    return [
        "-map",
        "0:a:0",
        "-c:a",
        "aac",
        "-b:a",
        f"{p.audio_kbps}k",
        *(["-ac", "1"] if p.mono else []),
    ]


@dataclass(frozen=True, slots=True, kw_only=True)
class _SizeEncode:
    """What stays the same between the attempts at one size."""

    src: str
    inputs: list[str]  # ffmpeg and the trim before -i
    chain: str | None
    audio: list[str]
    codec: str
    ten_bit: bool
    fast: bool  # on the GPU, one pass
    container: str
    fps: float

    def video(self, kbps: int) -> enc.VideoEncode:
        rate = enc.Rate(
            f"{kbps}k",
            maxrate=f"{int(kbps * 1.5)}k" if self.fast else None,
            gop_seconds=4,
            fps=self.fps,
        )
        hw = "auto" if self.fast else "off"
        return enc.video(
            self.codec,
            "high",
            speed="balanced",
            hw=hw,
            ten_bit=self.ten_bit,
            container=self.container,
            rate=rate,
        )

    def command(self, ve: enc.VideoEncode, *more: str) -> list[str]:
        head = [*ve.pre_input, *self.inputs, "-i", self.src, "-map", "0:v:0"]
        return [*head, *ve.vf_args(self.chain), *ve.args, *more]


def _first_pass(job: _SizeEncode, kbps: int, steps: Steps, length: float) -> bool:
    """Pass 1 of a two-pass encode, when the encoder has one (not on the GPU); whether it ran."""
    ve = job.video(kbps)
    if job.fast or ve.hw is not None or not enc.two_pass_args(ve.encoder, 1, "x"):
        return False
    steps.log(f"Pass 1 of 2 ({ve.label})…")
    passlog = os.path.join(steps.tmp, "pass")
    steps.run(
        job.command(ve, *enc.two_pass_args(ve.encoder, 1, passlog), "-an", "-f", "null", "-"),
        length,
        0.0,
        0.4,
    )
    return True


def encode_to_size(
    src: str,
    final: str,
    info: ff.MediaInfo,
    p: Plan,
    steps: Steps,
    *,
    fast: bool = False,
    start: float = 0.0,
    end: float | None = None,
) -> int:
    """Encode *src* into *final* under the plan's target size: two-pass on the
    CPU (or the GPU when *fast*), then re-encode at a lower bitrate until the
    file fits.  Shared by the tool and the command line."""
    end = info.duration if end is None else end
    length = end - start
    v = info.require_video()
    inputs = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y"]
    inputs += ["-ss", f"{start:.3f}"] if start > 0 else []
    inputs += ["-to", f"{end:.3f}"] if end < info.duration - 0.01 else []
    job = _SizeEncode(
        src=src,
        inputs=inputs,
        chain=_size_chain(v, p),
        audio=_audio_args(info, p),
        codec=p.goal.codec,
        ten_bit=p.goal.codec != "h264" and (v.bit_depth > 8 or v.is_hdr),
        fast=fast,
        container=_container_of(final),
        fps=p.fps,
    )
    kbps = p.video_kbps
    two_pass = _first_pass(job, kbps, steps, length)
    passlog = os.path.join(steps.tmp, "pass")
    lo = 0.4 if two_pass else 0.0
    for attempt in range(4):
        ve = job.video(kbps)
        steps.log(
            f"Encoding with {ve.label}…"
            if attempt == 0
            else f"Still over the limit - re-encoding at {kbps} kb/s…"
        )
        second = enc.two_pass_args(ve.encoder, 2, passlog) if two_pass else []
        cmd = job.command(
            ve, *second, *job.audio, "-map_metadata", "0", "-map_chapters", "0", final
        )
        steps.run(
            cmd,
            length,
            lo + (1 - lo) * attempt / 4,
            1.0 if attempt == 0 else lo + (1 - lo) * (attempt + 1) / 4,
        )
        size = os.path.getsize(final)
        steps.log(f"  {size / 1e6:.2f} MB")
        if size <= p.goal.target_bytes:
            return 0
        kbps = retry_kbps(kbps, p.goal.target_bytes, size, int(p.audio_kbps * 1000 / 8 * length))
    raise RuntimeError(
        "Could not get the video under the size limit - choose a bigger size, trim it, or allow a "
        "lower resolution."
    )
