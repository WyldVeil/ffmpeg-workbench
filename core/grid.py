"""
core/grid.py  -  Lay out any number of clips on one canvas

``best_layout(aspects, W, H, gap=..., mode=...)`` returns a rectangle per
clip (x, y, w, h - even integers), chosen to show as much picture as
possible:

* "fit" (nothing cropped): compares every uniform grid (short last row
  centred), "justified rows" - like a photo gallery, each row as tall as
  needed for its clips to fill the width exactly at their own shapes - and
  the same in columns for tall canvases; keeps whichever covers the most of
  the canvas.
* "fill" (edges cropped, no empty space): the uniform grid whose cells crop
  the clips least.

Clips stay in their order (left to right, top to bottom).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from core import encoders as enc
from core import ffmpeg as ff

Rect = tuple[int, int, int, int]  # x, y, w, h
Size = tuple[int, int]
LayoutKey = tuple[int, ...] | str  # the clip indices a layout is for, or "all"


@dataclass
class Layout:
    rects: list[Rect]  # per clip, in clip order
    kind: str  # "grid CxR", "rows a-b-c", "columns a-b"
    coverage: float  # share of the canvas showing picture (0..1)
    crop: list[Size | None] | None = None  # per clip: (cw, ch) to crop to after scaling (fill mode)
    balance: float = 0.0  # log of the geometric mean of the visible clip areas
    rows: int = 1
    cols: int = 1

    def score(self) -> tuple[float, float, int]:
        """Bigger is better: every clip as large as possible *and* similar in
        size (geometric mean), then frame coverage, then the first clip's size."""
        first = self.rects[0][2] * self.rects[0][3] if self.rects else 0
        return (round(self.balance, 6), round(self.coverage, 6), first)

    def describe(self) -> str:
        return f"{self.kind} - {self.coverage * 100:.0f}% of the frame used"


def _even(v: float) -> int:
    return max(2, round(v / 2) * 2)


def _fit_in(cell_w: float, cell_h: float, aspect: float) -> tuple[float, float]:
    if cell_w / cell_h > aspect:
        return cell_h * aspect, cell_h
    return cell_w, cell_w / aspect


def uniform(
    aspects: Sequence[float], W: int, H: int, cols: int, gap: int = 0, fill: bool = False
) -> Layout:
    n = len(aspects)
    rows = math.ceil(n / cols)
    cw = (W - gap * (cols + 1)) / cols
    ch = (H - gap * (rows + 1)) / rows
    rects: list[Rect] = []
    crops: list[Size | None] = []
    area = logs = 0.0
    for i, a in enumerate(aspects):
        r, c = divmod(i, cols)
        in_row = min(cols, n - r * cols)
        x0 = (W - (in_row * cw + (in_row - 1) * gap)) / 2 + c * (
            cw + gap
        )  # centre a short last row
        y0 = gap + r * (ch + gap)
        if fill:
            rects.append((_even(x0), _even(y0), _even(cw), _even(ch)))
            crops.append((_even(cw), _even(ch)))
            visible = cw * ch * min(cw / ch / a, a / (cw / ch))  # share of the clip still shown
            area += visible
            logs += math.log(max(visible, 1.0))
        else:
            w, h = _fit_in(cw, ch, a)
            rects.append((_even(x0 + (cw - w) / 2), _even(y0 + (ch - h) / 2), _even(w), _even(h)))
            crops.append(None)
            area += w * h
            logs += math.log(max(w * h, 1.0))
    return Layout(
        rects, f"grid {cols}×{rows}", area / (W * H), crops if fill else None, logs / n, rows, cols
    )


def _justified(
    aspects: Sequence[float], W: int, H: int, groups: Sequence[int], gap: int, columns: bool
) -> Layout:
    """Rows (or columns) of clips that exactly fill the width (height)."""
    if columns:  # transpose: work on the rotated problem
        lay = _justified([1 / a for a in aspects], H, W, groups, gap, False)
        rects = [(y, x, h, w) for (x, y, w, h) in lay.rects]
        return Layout(
            rects,
            lay.kind.replace("rows", "columns"),
            lay.coverage,
            None,
            lay.balance,
            lay.cols,
            lay.rows,
        )
    heights: list[float] = []
    idx = 0
    for size in groups:
        sum_a = sum(aspects[idx : idx + size])
        heights.append((W - gap * (size + 1)) / sum_a)
        idx += size
    total = sum(heights)
    avail_h = H - gap * (len(groups) + 1)
    scale = min(1.0, avail_h / total) if total > 0 else 1.0
    used_h = total * scale + gap * (len(groups) + 1)
    y = (H - used_h) / 2 + gap
    rects: list[Rect] = []
    area, idx, logs = 0.0, 0, 0.0
    for size, h0 in zip(groups, heights, strict=False):
        h = h0 * scale
        widths = [aspects[idx + k] * h for k in range(size)]
        row_w = sum(widths) + gap * (size - 1)
        x = (W - row_w) / 2
        for w in widths:
            rects.append((_even(x), _even(y), _even(w), _even(h)))
            area += w * h
            logs += math.log(max(w * h, 1.0))
            x += w + gap
        y += h + gap
        idx += size
    return Layout(
        rects,
        "rows " + "-".join(str(g) for g in groups),
        area / (W * H),
        None,
        logs / len(aspects),
        len(groups),
        max(groups),
    )


def _partitions(n: int, limit_exhaustive: int = 14) -> Iterator[list[int]]:
    """Ways to split n clips (in order) into rows."""
    if n <= limit_exhaustive:
        for cuts in itertools.product((0, 1), repeat=n - 1):
            groups: list[int] = []
            size = 1
            for c in cuts:
                if c:
                    groups.append(size)
                    size = 1
                else:
                    size += 1
            groups.append(size)
            yield groups
        return
    for rows in range(1, n + 1):  # big counts: balanced rows only
        base, extra = divmod(n, rows)
        for where in ("front", "back", "middle"):
            sizes = [base] * rows
            order = list(range(rows))
            if where == "back":
                order.reverse()
            elif where == "middle":
                order = sorted(order, key=lambda r: abs(r - (rows - 1) / 2))
            for r in order[:extra]:
                sizes[r] += 1
            yield sizes


def best_layout(
    aspects: Sequence[float], W: int, H: int, *, gap: int = 0, mode: str = "fit"
) -> Layout:
    """Best arrangement of clips with display aspect ratios *aspects* on W×H."""
    if not aspects:
        raise ValueError("No clips to arrange.")
    aspects = [max(0.05, min(20.0, float(a))) for a in aspects]
    n = len(aspects)
    fill = mode == "fill"
    landscape = W >= H

    def key(lay: Layout) -> tuple[float, float, int, int]:
        # ties: follow the canvas - side by side on wide canvases, stacked on tall ones
        b, c, first = lay.score()
        return (b, c, -(lay.rows if landscape else lay.cols), first)

    best: Layout | None = None
    for cols in range(1, n + 1):
        lay = uniform(aspects, W, H, cols, gap, fill)
        if best is None or key(lay) > key(best):
            best = lay
    if not fill:
        seen: set[tuple[int, ...]] = set()
        for groups in _partitions(n):
            if tuple(groups) in seen:
                continue
            seen.add(tuple(groups))
            for columns in (False, True):
                lay = _justified(aspects, W, H, groups, gap, columns)
                if best is None or key(lay) > key(best):
                    best = lay
    assert best is not None
    return best


# ── Rendering: one FFmpeg graph for a whole collage ──────────────────────────


@dataclass
class Clip:
    index: int  # FFmpeg input number
    aspect: float  # display aspect ratio
    duration: float  # seconds of video
    has_audio: bool = False


def _place(label: str, rect: Rect, crop: Size | None, fps: str) -> str:
    """Scale (and crop for 'fill') one clip branch to its rectangle."""
    _x, _y, w, h = rect
    if crop:
        cw, ch = crop
        return (
            f"{label}scale={cw}:{ch}:force_original_aspect_ratio=increase:force_divisible_by=2,"
            f"crop={cw}:{ch},setsar=1,fps={fps},format=yuv420p"
        )
    return f"{label}scale={w}:{h},setsar=1,fps={fps},format=yuv420p"


@dataclass(frozen=True, slots=True)
class Canvas:
    """The frame the clips are arranged on."""

    width: int
    height: int
    fps: str = "30"
    gap: int = 0
    mode: str = "fit"  # "fit": whole clips (letterboxed); "fill": cropped to their cells
    background: str = "black"


@dataclass(frozen=True, slots=True)
class Timing:
    """How long the collage runs, and what its clips do meanwhile.

    *end* (when not looping): 'hold' (last frame stays), 'background', or
    'reflow' (the remaining clips are re-arranged to fill the frame).
    *audio*: 'first', 'mix' or 'none'."""

    total: float
    loop: bool = True
    end: str = "hold"
    audio: str = "first"


def _reflow_video(
    clips: Sequence[Clip], canvas: Canvas, total: float, layouts: Mapping[LayoutKey, Layout]
) -> list[str]:
    """Clips that end leave the frame and the others re-arrange: one segment per
    set of clips still playing, joined -> [v]."""
    W, H, fps = canvas.width, canvas.height, canvas.fps
    cuts = sorted({0.0, total, *[c.duration for c in clips if 0 < c.duration < total]})
    segments = [(a, b) for a, b in itertools.pairwise(cuts) if b - a > 0.02]
    active = [[c for c in clips if c.duration > a + 1e-3] for a, _b in segments]
    parts: list[str] = []
    branch: dict[int, list[str]] = {}
    for c in clips:  # each clip split into as many branches as segments it plays in
        n = sum(1 for group in active if c in group)
        if n:
            labels = [f"[s{c.index}_{k}]" for k in range(n)]
            parts.append(
                f"[{c.index}:v]setpts=PTS-STARTPTS"
                + (f",split={n}{''.join(labels)}" if n > 1 else labels[0])
            )
            branch[c.index] = labels
    for k, ((a, b), group) in enumerate(zip(segments, active, strict=False)):
        parts.append(
            f"color=c={canvas.background}:s={W}x{H}:r={fps}:d={b - a:.3f},format=yuv420p[bg{k}]"
        )
        cur = f"[bg{k}]"
        if group:
            lay = layouts.get(tuple(c.index for c in group)) or best_layout(
                [c.aspect for c in group], W, H, gap=canvas.gap, mode=canvas.mode
            )
            for j, c in enumerate(group):
                src, rect = branch[c.index].pop(0), lay.rects[j]
                crop = lay.crop[j] if lay.crop else None
                trim = f"{src}trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,"
                parts.append(_place(trim, rect, crop, fps) + f"[p{k}_{j}]")
                parts.append(
                    f"{cur}[p{k}_{j}]overlay={rect[0]}:{rect[1]}:eof_action=pass[o{k}_{j}]"
                )
                cur = f"[o{k}_{j}]"
        parts.append(f"{cur}null[seg{k}]")
    joined = "".join(f"[seg{k}]" for k in range(len(segments)))
    return [*parts, joined + f"concat=n={len(segments)}:v=1:a=0,format=yuv420p[v]"]


def _fixed_video(
    clips: Sequence[Clip], canvas: Canvas, timing: Timing, layouts: Mapping[LayoutKey, Layout]
) -> list[str]:
    """One layout for the whole collage -> [v]; a clip that ends holds its last
    frame, or (end="background" without looping) leaves its cell empty."""
    W, H, fps = canvas.width, canvas.height, canvas.fps
    lay = layouts.get("all") or best_layout(
        [c.aspect for c in clips], W, H, gap=canvas.gap, mode=canvas.mode
    )
    parts = [
        f"color=c={canvas.background}:s={W}x{H}:r={fps}:d={timing.total:.3f},format=yuv420p[bg]"
    ]
    cur = "[bg]"
    eof = "pass" if (not timing.loop and timing.end == "background") else "repeat"
    for j, c in enumerate(clips):
        crop = lay.crop[j] if lay.crop else None
        parts.append(
            _place(f"[{c.index}:v]setpts=PTS-STARTPTS,", lay.rects[j], crop, fps) + f"[p{j}]"
        )
        x, y = lay.rects[j][0], lay.rects[j][1]
        parts.append(f"{cur}[p{j}]overlay={x}:{y}:eof_action={eof}[o{j}]")
        cur = f"[o{j}]"
    return [*parts, f"{cur}format=yuv420p[v]"]


def _collage_audio(clips: Sequence[Clip], audio: str, total: float) -> list[str]:
    """[a]: the first clip's sound, or every clip's mixed ("mix"); [] for none."""
    with_audio = [c for c in clips if c.has_audio]
    if audio == "none" or not with_audio:
        return []
    st = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    if audio == "first":
        c = with_audio[0]
        return [f"[{c.index}:a:0]{st},asetpts=PTS-STARTPTS,apad,atrim=duration={total:.3f}[a]"]
    parts = [
        f"[{c.index}:a:0]{st},asetpts=PTS-STARTPTS,apad,atrim=duration={total:.3f}[a{c.index}]"
        for c in with_audio
    ]
    labels = "".join(f"[a{c.index}]" for c in with_audio)
    if len(with_audio) > 1:
        return [
            *parts,
            f"{labels}amix=inputs={len(with_audio)}:duration=longest:normalize=0,alimiter=limit=0.95[a]",
        ]
    return [*parts, f"{labels}anull[a]"]


def collage_filter(
    clips: Sequence[Clip],
    canvas: Canvas,
    timing: Timing,
    layouts: Mapping[LayoutKey, Layout] | None = None,
) -> tuple[str, bool]:
    """filter_complex for the collage -> (graph, has_audio)."""
    fixed = layouts or {}
    if not timing.loop and timing.end == "reflow":
        parts = _reflow_video(clips, canvas, timing.total, fixed)
    else:
        parts = _fixed_video(clips, canvas, timing, fixed)
    audio = _collage_audio(clips, timing.audio, timing.total)
    return ";".join(parts + audio), bool(audio)


def pip_filter(
    has_audio: Sequence[bool], canvas: Canvas, *, shortest: bool, audio: str
) -> tuple[str, bool]:
    """Picture-in-picture: input 0 fills the frame, the others sit in its
    corners (a quarter of its size) -> (filter_complex, has_audio_out).
    *audio*: "first", "mix" or "none"; *canvas.gap* is the corner margin."""
    total_w, total_h = canvas.width // 2 * 2, canvas.height // 2 * 2
    bg, fps, pad = canvas.background, canvas.fps, canvas.gap
    sw, sh = (total_w // 4) // 2 * 2, (total_h // 4) // 2 * 2
    m = max(pad, total_h // 30)
    parts = [
        f"[0:v]scale={total_w}:{total_h}:force_original_aspect_ratio=decrease,"
        f"pad={total_w}:{total_h}:(ow-iw)/2:(oh-ih)/2:color={bg},setsar=1,fps={fps},"
        f"format=yuv420p[base]"
    ]
    last = "[base]"
    corners = [(f"W-w-{m}", f"H-h-{m}"), (f"W-w-{m}", f"{m}")]
    for i in range(1, len(has_audio)):
        parts.append(
            f"[{i}:v]scale={sw}:{sh}:force_original_aspect_ratio=decrease,"
            f"pad={sw}:{sh}:(ow-iw)/2:(oh-ih)/2:color={bg},setsar=1,fps={fps},"
            f"format=yuv420p[p{i}]"
        )
        x, y = corners[(i - 1) % len(corners)]
        parts.append(
            f"{last}[p{i}]overlay=x={x}:y={y}:shortest={1 if shortest else 0}:"
            f"eof_action=repeat[o{i}]"
        )
        last = f"[o{i}]"
    parts.append(f"{last}null[v]")
    srcs = [i for i, a in enumerate(has_audio) if a]
    if audio == "none" or not srcs:
        return ";".join(parts), False
    if audio == "first" or len(srcs) == 1:
        parts.append(f"[{srcs[0]}:a:0]aresample=48000[a]")
    else:
        parts.append(
            "".join(f"[{i}:a:0]" for i in srcs)
            + f"amix=inputs={len(srcs)}:duration={'shortest' if shortest else 'longest'}:"
            f"normalize=0,aresample=48000[a]"
        )
    return ";".join(parts), True


def collage_cmd(
    paths: Sequence[str],
    graph: str,
    venc: enc.VideoEncode,
    container: str,
    total: float,
    out: str,
    *,
    audio: bool,
    loop: bool,
) -> list[str]:
    """The collage (*graph*: [v], and [a] with *audio*) of *paths*, *total* seconds;
    each input repeated to fill the time when *loop*."""
    cmd = [ff.ffmpeg_path(), "-y", *venc.pre_input]
    for p in paths:
        cmd += [*(["-stream_loop", "-1"] if loop else []), "-i", p]
    vmap = "[v]"
    if venc.filter_suffix:
        graph += f";[v]{venc.filter_suffix}[venc]"
        vmap = "[venc]"
    cmd += ["-filter_complex", graph, "-map", vmap]
    cmd += (
        ["-map", "[a]", *enc.audio(enc.default_audio_for(container), container=container)]
        if audio
        else ["-an"]
    )
    return [*cmd, *venc.args, "-t", f"{total:.3f}", out]
