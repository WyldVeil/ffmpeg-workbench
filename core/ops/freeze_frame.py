"""
core/ops/freeze_frame.py - pausing the picture on a moment, for tabs/cutting/freeze_frame.py.

Pure functions: values in, a filter graph out - no widgets, no Tk.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import ass


@dataclass(frozen=True, slots=True, kw_only=True)
class Freeze:
    """A freeze: *mode* "insert" (pause at *at* s), "end" (hold the last frame)
    or "only" (just the frozen moment), for *hold* s.  *audio_mode*: "pause",
    "continue" or "mute"; *flash* fades in from white; *mono* greys the frozen
    picture; *caption_ass* is a subtitle file burned over it."""

    mode: str
    at: float
    hold: float
    audio_mode: str = "pause"
    flash: bool = False
    mono: bool = False
    caption_ass: str | None = None


def _held_frame(freeze: Freeze, look: list[str]) -> tuple[list[str], float]:
    """ "only": just the frame at *at*, held; (graph steps, output length)."""
    v = (
        f"[0:v]trim=start={freeze.at:.4f},setpts=PTS-STARTPTS,trim=end_frame=1,"
        f"tpad=stop_mode=clone:stop_duration={freeze.hold:.4f}"
    )
    return [v + ("," + ",".join(look) if look else "") + "[fz]"], freeze.hold


def _held_end(freeze: Freeze, dur: float, look: list[str]) -> tuple[list[str], str]:
    """ "end": the last frame held after the video; (graph steps, last label)."""
    parts = [f"[0:v]setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={freeze.hold:.4f}[fz]"]
    if not look:
        return parts, "[fz]"
    when = ":enable=gte(t\\," + f"{dur:.4f})"
    return [*parts, "[fz]" + ",".join(f + when for f in look) + "[fl]"], "[fl]"


def _held_inside(freeze: Freeze, look: list[str]) -> tuple[list[str], str]:
    """ "insert": the video stops at *at* for *hold* seconds, then carries on."""
    at, hold = freeze.at, freeze.hold
    parts = [
        "[0:v]setpts=PTS-STARTPTS,split[va][vb]",
        f"[va]trim=end={at:.4f},setpts=PTS-STARTPTS[v0]",
        f"[vb]trim=start={at:.4f},setpts=PTS-STARTPTS,tpad=start_mode=clone:start_duration={hold:.4f}[v1]",
        "[v0][v1]concat=n=2:v=1:a=0[fz]",
    ]
    if not look:
        return parts, "[fz]"
    when = ":enable=between(t\\," + f"{at:.4f}" + "\\," + f"{at + hold:.4f})"
    return [*parts, "[fz]" + ",".join(f + when for f in look) + "[fl]"], "[fl]"


def _freeze_audio(freeze: Freeze) -> list[str]:
    """The sound carrying on under the hold ("continue", or the end) or pausing with it."""
    at, hold = freeze.at, freeze.hold
    if freeze.mode == "end" or freeze.audio_mode == "continue":
        return [f"[0:a:0]asetpts=PTS-STARTPTS,apad=pad_dur={hold:.4f}[a]"]
    ms = round(hold * 1000)
    return [
        "[0:a:0]asetpts=PTS-STARTPTS,asplit[xa][xb]",
        f"[xa]atrim=end={at:.4f},asetpts=PTS-STARTPTS[a0]",
        f"[xb]atrim=start={at:.4f},asetpts=PTS-STARTPTS,adelay=delays={ms}:all=1[a1]",
        "[a0][a1]concat=n=2:v=0:a=1[a]",
    ]


def freeze_graph(freeze: Freeze, dur: float, *, has_audio: bool) -> tuple[str, float, bool]:
    """(filter_complex, output seconds, audio_out) for a *dur* s video."""
    look = ["hue=s=0"] if freeze.mono else []
    if freeze.mode == "only":
        parts, total = _held_frame(freeze, look)
        vlast, start = "[fz]", 0.0
    elif freeze.mode == "end":
        parts, vlast = _held_end(freeze, dur, look)
        start, total = dur, dur + freeze.hold
    else:
        parts, vlast = _held_inside(freeze, look)
        start, total = freeze.at, dur + freeze.hold
    if freeze.flash:
        parts.append(f"{vlast}fade=t=in:st={start:.4f}:d=0.35:color=white[fx]")
        vlast = "[fx]"
    if freeze.caption_ass:
        parts.append(f"{vlast}{ass.subtitles_filter(freeze.caption_ass)}[cap]")
        vlast = "[cap]"
    parts.append(f"{vlast}null[v]")
    audio_out = has_audio and freeze.audio_mode != "mute" and freeze.mode != "only"
    if audio_out:
        parts += _freeze_audio(freeze)
    return ";".join(parts), total, audio_out
