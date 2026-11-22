"""
core/ops/crossfader.py - command builders for tabs/cutting/crossfader.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from core import encoders as enc
from core import ffmpeg as ff
from core.i18n import t

TRANS_DEFAULT = "fade"


def _tr(key: str, fallback: str) -> str:
    text = t(key)
    return fallback if text == key else text


@dataclass
class Clip:
    path: str
    duration: float          # video length in seconds
    width: int
    height: int
    fps: float
    fps_str: str             # exact rational, e.g. "30000/1001"
    has_audio: bool
    summary: str = ""

    @classmethod
    def from_info(cls, info: ff.MediaInfo) -> Clip:
        v = info.video
        if v is None:
            raise ValueError(_tr("crossfader.no_video_stream", "No video stream in this file."))
        dur = v.duration if v.duration > 0.05 else info.duration
        rate = v.raw.get("avg_frame_rate") or v.raw.get("r_frame_rate") or "30/1"
        if not v.fps or v.fps > 240 or rate in ("0/0", ""):
            rate = "30/1"
        w, h = v.display_size
        return cls(path=info.path, duration=float(dur), width=w, height=h,
                   fps=v.fps or 30.0, fps_str=rate, has_audio=info.has_audio,
                   summary=info.summary())


def _even(n: float) -> int:
    return max(2, round(n) // 2 * 2)


def segment_lengths(clips: list, T: float, freeze: bool) -> list:
    """Length of each clip as it enters the xfade chain (freeze pads it)."""
    n = len(clips)
    out = []
    for i, c in enumerate(clips):
        length = c.duration
        if freeze:
            length += (T if i > 0 else 0.0) + (T if i < n - 1 else 0.0)
        out.append(length)
    return out


def validate(clips: list, T: float, freeze: bool) -> str | None:
    """Return a user-facing problem description, or None when renderable."""
    if len(clips) < 2:
        return _tr("crossfader.need_two", "Add at least two clips.")
    if not (0.04 <= T <= 30):
        return _tr("crossfader.bad_duration", "The transition must last between 0.04 and 30 seconds.")
    if freeze:
        return None
    n = len(clips)
    for i, c in enumerate(clips):
        need = T if i in (0, n - 1) else 2 * T
        if c.duration + 1e-3 < need:
            return _tr("crossfader.clip_too_short",
                       "Clip {n} is {d:.2f} s long but needs at least {need:.2f} s for "
                       "{t:.2f} s transitions. Shorten the transition or use Freeze mode."
                       ).format(n=i + 1, d=c.duration, need=need, t=T)
    return None


def total_duration(clips: list, T: float, freeze: bool) -> float:
    lengths = segment_lengths(clips, T, freeze)
    return max(0.0, sum(lengths) - T * (len(clips) - 1))


def build_command(clips: list, out: str, *, T: float, transitions: list,
                  freeze: bool = False, audio_mode: str = "normal",
                  audio_curve: str = "tri", venc: enc.VideoEncode | None = None,
                  size: tuple | None = None, extra_output_args: list | None = None
                  ) -> tuple[list, float]:
    """Build the FFmpeg argv for the whole crossfade.  Returns (argv, seconds)."""
    n = len(clips)
    if venc is None:
        venc = enc.video("h264", "high", hw="off", container=os.path.splitext(out)[1] or "mp4")
    W, H = size or (_even(clips[0].width), _even(clips[0].height))
    fps = clips[0].fps_str
    pix = venc.pix_fmt or "yuv420p"
    lengths = segment_lengths(clips, T, freeze)
    use_audio = audio_mode != "none" and any(c.has_audio for c in clips)

    argv = [ff.ffmpeg_path(), "-y", *venc.pre_input]
    for c in clips:
        argv += ["-i", c.path]
    silent_index = {}
    if use_audio:
        for i, c in enumerate(clips):
            if not c.has_audio:
                silent_index[i] = n + len(silent_index)
                argv += ["-f", "lavfi", "-t", ff.ts(c.duration),
                         "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

    graph = []
    for i, c in enumerate(clips):
        chain = (f"[{i}:v]setpts=PTS-STARTPTS,"
                 f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                 f"pad={W}:{H}:-1:-1:color=black,setsar=1,fps={fps},"
                 f"trim=duration={c.duration:.6f},setpts=PTS-STARTPTS,format={pix}")
        if freeze:
            pads = []
            if i > 0:
                pads.append(f"start_mode=clone:start_duration={T:.6f}")
            if i < n - 1:
                pads.append(f"stop_mode=clone:stop_duration={T:.6f}")
            chain += ",tpad=" + ":".join(pads)
        graph.append(chain + f",settb=AVTB[v{i}]")

    last = "[v0]"
    offset = 0.0
    for i in range(1, n):
        offset += lengths[i - 1] - T
        trans = transitions[i - 1] if i - 1 < len(transitions) else TRANS_DEFAULT
        label = f"[x{i}]"
        graph.append(f"{last}[v{i}]xfade=transition={trans}:duration={T:.6f}:"
                     f"offset={offset:.6f}{label}")
        last = label
    if venc.filter_suffix:
        graph.append(f"{last}{venc.filter_suffix}[vout]")
    else:
        graph.append(f"{last}null[vout]")

    if use_audio:
        for i, c in enumerate(clips):
            src = f"[{silent_index[i]}:a]" if i in silent_index else f"[{i}:a]"
            chain = (f"{src}aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     f"asetpts=PTS-STARTPTS,apad,atrim=duration={c.duration:.6f}")
            if freeze:
                if i > 0:
                    chain += f",adelay=delays={round(T * 1000)}:all=1"
                if i < n - 1:
                    chain += f",apad=pad_dur={T:.6f}"
                chain += f",atrim=duration={lengths[i]:.6f}"
            graph.append(chain + f",asetpts=PTS-STARTPTS[a{i}]")
        last = "[a0]"
        for i in range(1, n):
            label = f"[ax{i}]" if i < n - 1 else "[aout]"
            graph.append(f"{last}[a{i}]acrossfade=d={T:.6f}:c1={audio_curve}:c2={audio_curve}{label}")
            last = label

    argv += ["-filter_complex", ";".join(graph), "-map", "[vout]"]
    if use_audio:
        container = os.path.splitext(out)[1].lstrip(".").lower() or "mp4"
        bitrate = "320k" if audio_mode == "hq" else "192k"
        argv += ["-map", "[aout]",
                 *enc.audio("opus" if container == "webm" else "aac",
                            "256k" if (container == "webm" and audio_mode == "hq") else bitrate,
                            container=container)]
    else:
        argv += ["-an"]
    argv += [*venc.args, *(extra_output_args or []), "-map_metadata", "-1", "-sn", out]
    return argv, total_duration(clips, T, freeze)
