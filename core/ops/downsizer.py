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

#: acceptable bits per pixel per frame - below this the picture falls apart
MIN_BPP = {"h264": 0.045, "hevc": 0.03, "av1": 0.025}


LADDER = (2160, 1440, 1080, 900, 720, 540, 480, 360, 240, 180)


MARGIN = 0.97  # container overhead + encoder slack


@dataclass
class Plan:
    video_kbps: int
    audio_kbps: int
    mono: bool
    width: int
    height: int
    fps: float
    notes: list


def plan(
    duration: float,
    target_bytes: int,
    size: tuple,
    fps: float,
    *,
    codec: str = "h264",
    has_audio: bool = True,
    audio_mode: str = "auto",
    audio_src_kbps: int = 0,
    allow_res: bool = True,
    allow_fps: bool = True,
) -> Plan:
    """Work out bitrates, size and frame rate for *target_bytes*.  Raises
    ValueError when the target cannot hold a watchable video."""
    if duration <= 0:
        raise ValueError("The video's length is unknown.")
    budget = target_bytes * 8 / 1000 / duration * MARGIN  # kbit/s for everything
    notes = []
    mono = False
    if not has_audio or audio_mode == "none":
        a_kbps = 0
    elif audio_mode == "copy":
        a_kbps = audio_src_kbps or 320
        if not audio_src_kbps:
            notes.append("The original audio bitrate is unknown - 320 kb/s is reserved for it.")
    else:
        a_kbps = 128 if budget >= 2000 else 96 if budget >= 800 else 64 if budget >= 350 else 48
        mono = a_kbps <= 48
    v_kbps = int(budget - a_kbps)
    if v_kbps < 40:
        raise ValueError(
            f"{target_bytes / 1e6:g} MB is too small for {ff.format_time(duration)} of video "
            f"(only {max(0, v_kbps)} kb/s would be left for the picture). Trim the video, "
            "remove the audio or choose a bigger size."
        )
    w, h = size
    out_fps = fps if fps and fps > 0 else 30.0
    min_bpp = MIN_BPP.get(codec, 0.045)

    def bpp(ww, hh, ff_):
        return v_kbps * 1000 / max(1, ww * hh * ff_)

    if allow_fps and out_fps > 31 and bpp(w, h, out_fps) < min_bpp * 1.5:
        out_fps = out_fps / 2 if out_fps / 2 >= 23.9 else 30.0
        notes.append(f"Frame rate lowered to {out_fps:g} fps.")
    out_w, out_h = w, h
    if allow_res and bpp(w, h, out_fps) < min_bpp:
        short = min(w, h)
        for rung in LADDER:
            if rung >= short:
                continue
            scale = rung / short
            cw, ch = max(2, round(w * scale / 2) * 2), max(2, round(h * scale / 2) * 2)
            out_w, out_h = cw, ch
            if bpp(cw, ch, out_fps) >= min_bpp:
                break
        if (out_w, out_h) != (w, h):
            notes.append(f"Resolution lowered to {out_w}×{out_h} so the picture stays clean.")
    if bpp(out_w, out_h, out_fps) < min_bpp * 0.6:
        notes.append(
            "Expect visible blockiness - a shorter clip or a bigger size would look better."
        )
    return Plan(v_kbps, a_kbps, mono, out_w, out_h, out_fps, notes)


def retry_kbps(v_kbps: int, target_bytes: int, actual_bytes: int, audio_bytes: int = 0) -> int:
    """Lower video bitrate after an attempt came out over the limit."""
    video_room = max(1, target_bytes - audio_bytes)
    video_used = max(1, actual_bytes - audio_bytes)
    return max(20, int(v_kbps * video_room / video_used * 0.96))


def encode_to_size(
    src: str,
    final: str,
    info,
    p: Plan,
    target: int,
    *,
    codec: str = "h264",
    fast: bool = False,
    audio_mode: str = "auto",
    start: float = 0.0,
    end: float | None = None,
    tmp: str,
    run,
    log=print,
) -> int:
    """Encode *src* into *final* under *target* bytes following plan *p*:
    two-pass on the CPU (or the GPU when *fast*), then re-encode at a lower
    bitrate until the file fits.  *run(cmd, duration, lo, hi)* runs one
    command (lo/hi = its share of the progress).  Shared by the tool and the
    command line."""
    end = info.duration if end is None else end
    length = end - start
    v = info.video
    hdr = v.is_hdr
    ten_bit = codec != "h264" and (v.bit_depth > 8 or hdr)
    chain = []
    if hdr and codec == "h264":
        chain.append(fl.hdr_to_sdr_chain())
    w0, h0 = v.display_size
    sw, sh = max(2, w0 // 2 * 2), max(2, h0 // 2 * 2)
    if (p.width, p.height) != (sw, sh) or (v.sar and v.sar not in ("1:1", "0:1")):
        chain.append(f"scale={p.width}:{p.height}:flags=lanczos,setsar=1")
    if v.fps and abs(p.fps - v.fps) > 0.01:
        chain.append(f"fps={p.fps:g}")
    container = os.path.splitext(final)[1].lstrip(".").lower() or "mp4"
    if container == "part":
        container = os.path.splitext(os.path.splitext(final)[0])[1].lstrip(".").lower() or "mp4"

    def video_args(kbps):
        return enc.video(
            codec,
            "high",
            speed="balanced",
            hw="auto" if fast else "off",
            ten_bit=ten_bit,
            container=container,
            bitrate=f"{kbps}k",
            maxrate=f"{int(kbps * 1.5)}k" if fast else None,
            gop_seconds=4,
            fps=p.fps,
        )

    if audio_mode == "none" or not info.has_audio:
        audio = ["-an"]
    elif audio_mode == "copy":
        audio = ["-map", "0:a:0", "-c:a", "copy"]
    else:
        audio = ["-map", "0:a:0", "-c:a", "aac", "-b:a", f"{p.audio_kbps}k"] + (
            ["-ac", "1"] if p.mono else []
        )
    inp = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y"]
    if start > 0:
        inp += ["-ss", f"{start:.3f}"]
    if end < info.duration - 0.01:
        inp += ["-to", f"{end:.3f}"]
    kbps = p.video_kbps
    ve = video_args(kbps)
    two_pass = not fast and ve.hw is None and bool(enc.two_pass_args(ve.encoder, 1, "x"))
    passlog = os.path.join(tmp, "pass")
    if two_pass:
        log(f"Pass 1 of 2 ({ve.label})…")
        run(
            ve.pre_input
            + inp
            + ["-i", src, "-map", "0:v:0"]
            + ve.vf_args(",".join(chain) or None)
            + ve.args
            + enc.two_pass_args(ve.encoder, 1, passlog)
            + ["-an", "-f", "null", "-"],
            length,
            0.0,
            0.4,
        )
    lo = 0.4 if two_pass else 0.0
    for attempt in range(4):
        ve = video_args(kbps)
        log(
            f"Encoding with {ve.label}…"
            if attempt == 0
            else f"Still over the limit - re-encoding at {kbps} kb/s…"
        )
        run(
            ve.pre_input
            + inp
            + ["-i", src, "-map", "0:v:0"]
            + ve.vf_args(",".join(chain) or None)
            + ve.args
            + (enc.two_pass_args(ve.encoder, 2, passlog) if two_pass else [])
            + audio
            + ["-map_metadata", "0", "-map_chapters", "0", final],
            length,
            lo + (1 - lo) * attempt / 4,
            1.0 if attempt == 0 else lo + (1 - lo) * (attempt + 1) / 4,
        )
        size = os.path.getsize(final)
        log(f"  {size / 1e6:.2f} MB")
        if size <= target:
            return 0
        kbps = retry_kbps(kbps, target, size, int(p.audio_kbps * 1000 / 8 * length))
    raise RuntimeError(
        "Could not get the video under the size limit - choose a bigger size, trim it, or allow a "
        "lower resolution."
    )
