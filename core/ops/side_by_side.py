"""
core/ops/side_by_side.py - comparison, reaction and before/after layouts, for
tabs/cutting/side_by_side.py.

Pure functions: values in, a filter graph out - no widgets, no Tk.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core import ass
from core import encoders as enc
from core.filters import atempo_chain
from core.ops.shared import graph_encode_cmd

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


def _clip_chain(i: int, c: Clip, box: Cell, arr: Arrangement) -> str:
    """Clip *i* sped up, fitted (or cropped) to its cell -> [v<i>]."""
    _x, _y, cw, chh = box
    if arr.fill:
        fit = f"scale={cw}:{chh}:force_original_aspect_ratio=increase,crop={cw}:{chh}"
    else:
        fit = f"scale={cw}:{chh}:force_original_aspect_ratio=decrease,pad={cw}:{chh}:(ow-iw)/2:(oh-ih)/2:color=black"
    speed = (
        f"setpts=(PTS-STARTPTS)/{c.speed:.4f}" if abs(c.speed - 1) > 1e-6 else "setpts=PTS-STARTPTS"
    )
    hold = ",tpad=stop_mode=clone:stop=-1" if arr.length == "hold" else ""
    return f"[{i}:v]{speed},{fit},setsar=1,fps={arr.fps},format=yuv420p{hold}[v{i}]"


def _wipe(arr: Arrangement) -> list[str]:
    """[v1] revealed over [v0] up to a (maybe moving) line -> [st]."""
    w, h = arr.width, arr.height
    p = min(max(arr.wipe_pos, 0.02), 0.98)
    line = f",drawbox=x={w - 2}:y=0:w=4:h={h}:color=white@0.9:t=fill" if arr.divider else ""
    if arr.wipe_period > 0:
        cx = f"{w}*({p:.3f}+min({p:.3f},{1 - p:.3f})*0.9*sin(2*PI*t/{arr.wipe_period:.3f}))"
    else:
        cx = f"{round(w * p)}"
    return [
        f"[v1]format=yuva420p,pad={2 * w}:{h}:{w}:0:color=black@0{line},crop={w}:{h}:x='{w}-({cx})':y=0[bw]",
        "[v0][bw]overlay=0:0:shortest=1,format=yuv420p[st]",
    ]


def _tiles(n: int, boxes: list[Cell], arr: Arrangement) -> list[str]:
    """Cells side by side, stacked or in a 2x2 grid (empty cells black) -> [st]."""
    parts: list[str] = []
    if arr.layout == "grid":
        ins = [f"[v{i}]" for i in range(n)]
        for k in range(len(ins), 4):
            _x, _y, cw, chh = boxes[k]
            parts.append(f"color=c=black:s={cw}x{chh}:r={arr.fps}[blank{k}]")
            ins.append(f"[blank{k}]")
        parts.append("".join(ins) + "xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0:shortest=1[st0]")
    else:
        stack = "hstack" if arr.layout == "side" else "vstack"
        parts.append(f"[v0][v1]{stack}=inputs=2:shortest=1[st0]")
    chain: list[str] = []
    if arr.divider:
        if arr.layout in ("side", "grid"):
            chain.append(
                f"drawbox=x={boxes[0][2] - 2}:y=0:w=4:h={arr.height}:color=white@0.8:t=fill"
            )
        if arr.layout in ("stack", "grid"):
            chain.append(
                f"drawbox=x=0:y={boxes[0][3] - 2}:w={arr.width}:h=4:color=white@0.8:t=fill"
            )
    return [*parts, f"[st0]{','.join(chain) if chain else 'null'}[st]"]


def _audio(clips: Sequence[Clip], arr: Arrangement, total: float) -> list[str]:
    """[a]: one clip's sound (sped up with it) or every clip's mixed; [] for none."""
    srcs = [i for i, c in enumerate(clips) if c.has_audio]
    audio = arr.audio
    want = (
        srcs if audio == "mix" else [int(audio)] if audio.isdigit() and int(audio) in srcs else []
    )
    parts: list[str] = []
    for i in want:
        c = clips[i]
        tempo = "" if abs(c.speed - 1) < 1e-6 else f",{atempo_chain(c.speed)}"
        pad = ",apad" if arr.length != "shortest" else ""
        parts.append(f"[{i}:a:0]asetpts=PTS-STARTPTS{tempo},aresample=48000{pad}[a{i}]")
    if len(want) > 1:
        labels = "".join(f"[a{i}]" for i in want)
        parts.append(
            f"{labels}amix=inputs={len(want)}:duration=longest:normalize=0,atrim=duration={total:.3f}[a]"
        )
    elif want:
        parts.append(f"[a{want[0]}]atrim=duration={total:.3f}[a]")
    return parts


def build_graph(clips: Sequence[Clip], arr: Arrangement) -> tuple[str, float, bool]:
    """(filter_complex, total seconds, has_audio_out)."""
    n = len(clips)
    durs = [c.duration / c.speed for c in clips]
    total = min(durs) if arr.length == "shortest" else max(durs)
    boxes = cells(arr.layout, n, arr.width, arr.height)
    parts = [_clip_chain(i, c, boxes[min(i, len(boxes) - 1)], arr) for i, c in enumerate(clips)]
    parts += _wipe(arr) if arr.layout == "wipe" else _tiles(n, boxes, arr)
    vlast = "[st]"
    if arr.labels_ass:
        parts.append(f"{vlast}{ass.subtitles_filter(arr.labels_ass)}[lab]")
        vlast = "[lab]"
    parts.append(f"{vlast}trim=duration={total:.3f}[v]")
    audio = _audio(clips, arr, total)
    return ";".join(parts + audio), total, bool(audio)


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


def sbs_cmd(
    clips: Sequence[Clip],
    graph: str,
    venc: enc.VideoEncode,
    container: str,
    total: float,
    out: str,
    *,
    audio: bool,
    loop_to: float | None = None,
) -> list[str]:
    """The arrangement (*graph*: [v], and [a] with *audio*), *total* seconds;
    clips shorter than *loop_to* repeat."""
    inputs: list[str] = []
    for c in clips:
        if loop_to is not None and c.duration / c.speed < loop_to - 0.05:
            inputs += ["-stream_loop", "-1"]
        inputs += ["-i", c.path]
    return graph_encode_cmd(inputs, graph, venc, container, out, audio=audio, limit=total)
