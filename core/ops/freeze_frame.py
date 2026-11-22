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


def freeze_graph(freeze: Freeze, dur: float, *, has_audio: bool) -> tuple[str, float, bool]:
    """(filter_complex, output seconds, audio_out) for a *dur* s video."""
    mode, at, hold, audio_mode = freeze.mode, freeze.at, freeze.hold, freeze.audio_mode
    look: list[str] = []
    if freeze.mono:
        look.append("hue=s=0")
    if mode == "only":
        v = (
            f"[0:v]trim=start={at:.4f},setpts=PTS-STARTPTS,trim=end_frame=1,"
            f"tpad=stop_mode=clone:stop_duration={hold:.4f}"
        )
        if look:
            v += "," + ",".join(look)
        start, total = 0.0, hold
        parts = [v + "[fz]"]
        vlast = "[fz]"
    elif mode == "end":
        at = dur
        parts = [f"[0:v]setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={hold:.4f}[fz]"]
        vlast, start, total = "[fz]", dur, dur + hold
        if look:
            when = ":enable=gte(t\\," + f"{dur:.4f})"
            parts.append(vlast + ",".join(f + when for f in look) + "[fl]")
            vlast = "[fl]"
    else:
        parts = [
            "[0:v]setpts=PTS-STARTPTS,split[va][vb]",
            f"[va]trim=end={at:.4f},setpts=PTS-STARTPTS[v0]",
            f"[vb]trim=start={at:.4f},setpts=PTS-STARTPTS,"
            f"tpad=start_mode=clone:start_duration={hold:.4f}[v1]",
            "[v0][v1]concat=n=2:v=1:a=0[fz]",
        ]
        vlast, start, total = "[fz]", at, dur + hold
        if look:
            when = ":enable=between(t\\," + f"{at:.4f}" + "\\," + f"{at + hold:.4f})"
            parts.append(vlast + ",".join(f + when for f in look) + "[fl]")
            vlast = "[fl]"
    if freeze.flash:
        parts.append(f"{vlast}fade=t=in:st={start:.4f}:d=0.35:color=white[fx]")
        vlast = "[fx]"
    if freeze.caption_ass:
        parts.append(f"{vlast}{ass.subtitles_filter(freeze.caption_ass)}[cap]")
        vlast = "[cap]"
    parts.append(f"{vlast}null[v]")

    audio_out = has_audio and audio_mode != "mute"
    if audio_out:
        if mode == "only":
            audio_out = False
        elif mode == "end" or audio_mode == "continue":
            parts.append(f"[0:a:0]asetpts=PTS-STARTPTS,apad=pad_dur={hold:.4f}[a]")
        else:
            ms = round(hold * 1000)
            parts += [
                "[0:a:0]asetpts=PTS-STARTPTS,asplit[xa][xb]",
                f"[xa]atrim=end={at:.4f},asetpts=PTS-STARTPTS[a0]",
                f"[xb]atrim=start={at:.4f},asetpts=PTS-STARTPTS,adelay=delays={ms}:all=1[a1]",
                "[a0][a1]concat=n=2:v=0:a=1[a]",
            ]
    return ";".join(parts), total, audio_out
