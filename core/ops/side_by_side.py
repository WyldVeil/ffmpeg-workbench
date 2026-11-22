"""
core/ops/side_by_side.py - comparison, reaction and before/after layouts, for
tabs/cutting/side_by_side.py.

Pure functions: values in, a filter graph out - no widgets, no Tk.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core import ass
from core.ops.speed_ramp import atempo_chain

Cell = tuple[int, int, int, int]  # x, y, width, height


@dataclass
class Clip:
    path: str
    duration: float
    has_audio: bool
    label: str = ""
    speed: float = 1.0


def cells(layout: str, n: int, w: int, h: int) -> list[Cell]:
    """(x, y, cw, ch) of every cell."""
    if layout == "side":
        cw = w // 2 // 2 * 2
        return [(0, 0, cw, h), (cw, 0, w - cw, h)]
    if layout == "stack":
        ch = h // 2 // 2 * 2
        return [(0, 0, w, ch), (0, ch, w, h - ch)]
    if layout == "grid":
        cw, ch = w // 2 // 2 * 2, h // 2 // 2 * 2
        return [(0, 0, cw, ch), (cw, 0, w - cw, ch), (0, ch, cw, h - ch), (cw, ch, w - cw, h - ch)]
    return [(0, 0, w, h), (0, 0, w, h)]


@dataclass(frozen=True, slots=True, kw_only=True)
class Arrangement:
    """How the clips share the frame.

    *layout*: "side", "stack", "grid" or "wipe" (*wipe_pos* 0..1 across, moving
    back and forth every *wipe_period* s when set); *fill* crops clips to their
    cells instead of letterboxing; *audio*: a clip's number, "mix" or "none";
    *length*: "shortest", "loop" or "hold"; *labels_ass*: a subtitle file of
    clip names to burn in."""

    layout: str
    width: int
    height: int
    fps: str
    fill: bool = False
    audio: str = "0"
    length: str = "shortest"
    wipe_pos: float = 0.5
    wipe_period: float = 0.0
    divider: bool = True
    labels_ass: str | None = None


def build_graph(clips: Sequence[Clip], arr: Arrangement) -> tuple[str, float, bool]:
    """(filter_complex, total seconds, has_audio_out)."""
    layout, w, h, fps, fill = arr.layout, arr.width, arr.height, arr.fps, arr.fill
    audio, length, divider, labels_ass = arr.audio, arr.length, arr.divider, arr.labels_ass
    n = len(clips)
    durs = [c.duration / c.speed for c in clips]
    total = min(durs) if length == "shortest" else max(durs)
    parts: list[str] = []
    boxes = cells(layout, n, w, h)
    for i, c in enumerate(clips):
        _x, _y, cw, chh = boxes[min(i, len(boxes) - 1)]
        fit = (
            f"scale={cw}:{chh}:force_original_aspect_ratio=increase,crop={cw}:{chh}"
            if fill
            else f"scale={cw}:{chh}:force_original_aspect_ratio=decrease,"
            f"pad={cw}:{chh}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
        speed = (
            f"setpts=(PTS-STARTPTS)/{c.speed:.4f}"
            if abs(c.speed - 1) > 1e-6
            else "setpts=PTS-STARTPTS"
        )
        hold = ",tpad=stop_mode=clone:stop=-1" if length == "hold" else ""
        parts.append(f"[{i}:v]{speed},{fit},setsar=1,fps={fps},format=yuv420p{hold}[v{i}]")
    if layout == "wipe":
        p = min(max(arr.wipe_pos, 0.02), 0.98)
        line = f",drawbox=x={w - 2}:y=0:w=4:h={h}:color=white@0.9:t=fill" if divider else ""
        if arr.wipe_period > 0:
            cx = f"{w}*({p:.3f}+min({p:.3f},{1 - p:.3f})*0.9*sin(2*PI*t/{arr.wipe_period:.3f}))"
        else:
            cx = f"{round(w * p)}"
        parts.append(
            f"[v1]format=yuva420p,pad={2 * w}:{h}:{w}:0:color=black@0{line},"
            f"crop={w}:{h}:x='{w}-({cx})':y=0[bw]"
        )
        parts.append("[v0][bw]overlay=0:0:shortest=1,format=yuv420p[st]")
    else:
        if layout == "grid":
            ins = [f"[v{i}]" for i in range(n)]
            while len(ins) < 4:
                k = len(ins)
                _x, _y, cw, chh = boxes[k]
                parts.append(f"color=c=black:s={cw}x{chh}:r={fps}[blank{k}]")
                ins.append(f"[blank{k}]")
            parts.append(
                "".join(ins) + "xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0:shortest=1[st0]"
            )
        else:
            stack = "hstack" if layout == "side" else "vstack"
            parts.append(f"[v0][v1]{stack}=inputs=2:shortest=1[st0]")
        chain: list[str] = []
        if divider:
            if layout in ("side", "grid"):
                chain.append(f"drawbox=x={boxes[0][2] - 2}:y=0:w=4:h={h}:color=white@0.8:t=fill")
            if layout in ("stack", "grid"):
                chain.append(f"drawbox=x=0:y={boxes[0][3] - 2}:w={w}:h=4:color=white@0.8:t=fill")
        parts.append(f"[st0]{','.join(chain) if chain else 'null'}[st]")
    vlast = "[st]"
    if labels_ass:
        parts.append(f"{vlast}{ass.subtitles_filter(labels_ass)}[lab]")
        vlast = "[lab]"
    parts.append(f"{vlast}trim=duration={total:.3f}[v]")

    srcs = [i for i, c in enumerate(clips) if c.has_audio]
    want = (
        srcs if audio == "mix" else [int(audio)] if audio.isdigit() and int(audio) in srcs else []
    )
    for i in want:
        c = clips[i]
        tempo = "" if abs(c.speed - 1) < 1e-6 else f",{atempo_chain(c.speed)}"
        pad = ",apad" if length != "shortest" else ""
        parts.append(f"[{i}:a:0]asetpts=PTS-STARTPTS{tempo},aresample=48000{pad}[a{i}]")
    if len(want) > 1:
        parts.append(
            "".join(f"[a{i}]" for i in want)
            + f"amix=inputs={len(want)}:duration=longest:normalize=0,"
            f"atrim=duration={total:.3f}[a]"
        )
    elif want:
        parts.append(f"[a{want[0]}]atrim=duration={total:.3f}[a]")
    return ";".join(parts), total, bool(want)


def labels_script(
    layout: str, labels: Sequence[str], w: int, h: int, total: float, font: str = "Montserrat"
) -> ass.Script:
    """Each clip's name in the corner of its cell."""
    sc = ass.Script(w, h)
    u = min(w, h) / 1080
    sc.style(
        ass.Style(
            "Lab",
            font=font,
            size=round(40 * u),
            bold=True,
            outline=0,
            shadow=0,
            border_style=3,
            back_colour="#000000",
            back_alpha=0.45,
            alignment=7,
        )
    )
    boxes = cells(layout, len(labels), w, h)
    for i, text in enumerate(labels):
        if not text.strip():
            continue
        if layout == "wipe":
            x = 30 * u if i == 0 else w - 30 * u
            an = 7 if i == 0 else 9
            y = 30 * u
        else:
            bx, by, _cw, _ch = boxes[min(i, len(boxes) - 1)]
            x, y, an = bx + 24 * u, by + 24 * u, 7
        sc.add(0, total + 1, f"{{\\an{an}\\pos({x:.0f},{y:.0f})}} {ass.escape(text)} ", "Lab")
    return sc
