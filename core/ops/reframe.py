"""
core/ops/reframe.py - Smart Reframe (tabs/cutting/smart_reframe.py): a virtual
camera that follows the motion, a fixed centre crop, or the whole picture over a
blurred copy of itself.

Motion is measured on a small grey grid (frame differences), its centre followed
with a dead zone and a pull to the middle, smoothed forwards and backwards (no
lag) within each scene, and played back through sendcmd moving a crop window.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from core import encoders as enc
from core import ffmpeg as ff
from core import proc as _proc
from core.queue_manager import TaskContext

GRID_W, GRID_H, RATE = 64, 36, 8  # analysis grid and samples per second

Point = tuple[float, float]  # (x, y), 0..1 of the frame


@dataclass(frozen=True, slots=True)
class Tuning:
    alpha: float  # EMA weight per analysis sample
    dead_zone: float  # ignore target moves smaller than this (fraction of frame)
    centre_pull: float  # bias of the target towards the centre


TUNING = {"follow": Tuning(0.22, 0.03, 0.05), "smooth": Tuning(0.07, 0.07, 0.25)}


def crop_box(w: int, h: int, rw: int, rh: int) -> tuple[int, int]:
    """Largest rw:rh window inside w x h (even sizes)."""
    if w / h > rw / rh:
        cw, ch = round(h * rw / rh), h
    else:
        cw, ch = w, round(w * rh / rw)
    return min(cw, w) // 2 * 2, min(ch, h) // 2 * 2


def fit_chain(ow: int, oh: int) -> str:
    """The whole picture, fitted, over a blurred and darkened fill of itself."""
    small = f"{ow // 8}:{oh // 8}"
    return (
        f"split[a][b];[a]scale={small}:force_original_aspect_ratio=increase,"
        f"crop={small},gblur=sigma=4,eq=brightness=-0.1,scale={ow}:{oh}[bg];"
        f"[b]scale={ow}:{oh}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )


def _targets(
    frames: Sequence[bytes], tuning: Tuning, cut_factor: float
) -> tuple[list[Point | None], list[bool]]:
    """Raw target for each grid (None until there's motion to trust) and where the scenes cut."""
    targets: list[Point | None] = []
    cuts: list[bool] = []
    recent: list[int] = []
    target: Point | None = None
    for grid in frames:
        rows = [sum(grid[y * GRID_W : (y + 1) * GRID_W]) for y in range(GRID_H)]
        cols = [sum(grid[x::GRID_W]) for x in range(GRID_W)]
        total = sum(rows)
        mean = sum(recent) / len(recent) if recent else 0
        cuts.append(bool(recent) and total > cut_factor * max(mean, 50))
        recent = [*recent, total][-24:]
        if total > GRID_W * GRID_H * 1.5:  # enough motion to trust
            cx = sum(c * (i + 0.5) for i, c in enumerate(cols)) / total / GRID_W
            cy = sum(r * (i + 0.5) for i, r in enumerate(rows)) / total / GRID_H
            raw = (cx + (0.5 - cx) * tuning.centre_pull, cy + (0.5 - cy) * tuning.centre_pull)
            if target is None or cuts[-1]:
                target = raw
            else:
                tx = raw[0] if abs(raw[0] - target[0]) > tuning.dead_zone else target[0]
                ty = raw[1] if abs(raw[1] - target[1]) > tuning.dead_zone else target[1]
                target = (tx, ty)
        targets.append(target)
    return targets, cuts


def _ease(a: Point, b: Point, alpha: float) -> Point:
    return (a[0] + (b[0] - a[0]) * alpha, a[1] + (b[1] - a[1]) * alpha)


def track(frames: Sequence[bytes], tuning: Tuning, cut_factor: float = 6.0) -> list[Point]:
    """Smoothed centre for each motion-energy grid (bytes, GRID_W x GRID_H).

    Raw targets are smoothed forwards and backwards - zero-phase, so the virtual
    camera does not lag behind the action - separately within each scene."""
    targets, cuts = _targets(frames, tuning, cut_factor)
    last: Point = next((tg for tg in targets if tg is not None), (0.5, 0.5))
    filled: list[Point] = []
    for tg in targets:
        last = tg if tg is not None else last
        filled.append(last)
    out = list(filled)
    starts = [0] + [i for i, c in enumerate(cuts) if c and i > 0] + [len(filled)]
    for a0, b0 in itertools.pairwise(starts):
        seg = filled[a0:b0]
        if not seg:
            continue
        fwd: list[Point] = []
        cur = seg[0]
        for p in seg:
            cur = _ease(cur, p, tuning.alpha)
            fwd.append(cur)
        cur = fwd[-1]
        for i in range(len(fwd) - 1, -1, -1):
            cur = _ease(cur, fwd[i], tuning.alpha)
            out[a0 + i] = cur
    return out


def crop_commands(
    path: Sequence[Point],
    size: tuple[int, int],
    window: tuple[int, int],
    fps: float,
    duration: float,
) -> tuple[str, tuple[int, int]]:
    """sendcmd script placing the crop window on every output frame + start x/y."""
    (w, h), (cw, ch) = size, window

    def at(tm: float) -> Point:
        if not path:
            return 0.5, 0.5
        i = tm * RATE
        a = int(min(max(i, 0), len(path) - 1))
        b = min(a + 1, len(path) - 1)
        return _ease(path[a], path[b], min(max(i - a, 0.0), 1.0))

    def xy(tm: float) -> tuple[int, int]:
        cx, cy = at(tm)
        x = int(min(max(cx * w - cw / 2, 0), w - cw)) // 2 * 2
        y = int(min(max(cy * h - ch / 2, 0), h - ch)) // 2 * 2
        return x, y

    lines: list[str] = []
    last: tuple[int, int] | None = None
    for k in range(int(duration * fps) + 2):
        tm = k / fps
        pos = xy(tm)
        if pos != last:
            lines.append(f"{tm:.4f} crop@rf x {pos[0]}, crop@rf y {pos[1]};")
            last = pos
    return "\n".join(lines) + "\n", xy(0.0)


def motion_cmd(src: str) -> list[str]:
    """Raw grey motion-energy grids (GRID_W x GRID_H bytes, RATE a second) to stdout."""
    grid = (
        f"fps={RATE},scale={GRID_W * 2}:{GRID_H * 2}:flags=area,format=gray,"
        f"tblend=all_mode=difference,scale={GRID_W}:{GRID_H}:flags=area"
    )
    head = [
        ff.ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        src,
        "-an",
        "-sn",
    ]
    return [*head, "-vf", grid, "-f", "rawvideo", "-pix_fmt", "gray", "-"]


def analyse(
    src: str, duration: float, tuning: Tuning, cancelled: Callable[[], bool], ctx: TaskContext
) -> list[Point]:
    """The smoothed path of the action, RATE samples a second."""
    proc = _proc.spawn(motion_cmd(src), stdout=_proc.PIPE, stderr=_proc.DEVNULL)
    out = proc.stdout
    assert out is not None  # started with stdout=PIPE
    size = GRID_W * GRID_H
    frames: list[bytes] = []
    buf = b""
    expected = max(1, int(duration * RATE))
    try:
        while True:
            if cancelled():
                ff.kill_tree(proc)
                raise ff.CancelledError("cancelled")
            chunk = out.read(size * 16)
            if not chunk:
                break
            buf += chunk
            while len(buf) >= size:
                frames.append(buf[:size])
                buf = buf[size:]
            ctx.progress(0.35 * min(1.0, len(frames) / expected))
    finally:
        proc.wait()
    # first sample has no predecessor in tblend: repeat it so samples line up with time
    path = track(frames, tuning)
    return path[:1] + path if path else path


@dataclass(frozen=True, slots=True, kw_only=True)
class ReframeJob:
    """Crop a *window* (moving with the action when *tuning* is set) out of
    *source*, scaled to *out_size*."""

    src: str
    source: tuple[int, int]
    window: tuple[int, int]
    out_size: tuple[int, int]
    fps: float
    rate: str  # the output frame rate, as FFmpeg reads it
    duration: float
    tuning: Tuning | None
    venc: enc.VideoEncode
    audio: Sequence[str]

    def run(self, _p: object, cancelled: Callable[[], bool], ctx: TaskContext) -> int:
        (w, h), (cw, ch), (ow, oh) = self.source, self.window, self.out_size
        path: list[Point] = []
        if self.tuning is not None and (cw < w or ch < h):
            ctx.log("Analysing motion…")
            path = analyse(self.src, self.duration, self.tuning, cancelled, ctx)
            moves = max((abs(a[0] - b[0]) for a, b in itertools.pairwise(path)), default=0)
            ctx.log(f"Tracked {len(path)} samples (largest step {moves * 100:.1f}% of the frame).")
        tmp = ctx.tempdir()
        script, (x0, y0) = crop_commands(path, self.source, self.window, self.fps, self.duration)
        with open(os.path.join(tmp, "crop.cmd"), "w", encoding="ascii") as fh:
            fh.write(script)
        graph = (
            f"[0:v]sendcmd=f=crop.cmd,crop@rf=w={cw}:h={ch}:x={x0}:y={y0},"
            f"scale={ow}:{oh}:flags=lanczos,setsar=1,fps={self.rate}[v]"
        )
        vmap = "[v]"
        if self.venc.filter_suffix:
            graph += f";[v]{self.venc.filter_suffix}[venc]"
            vmap = "[venc]"
        v = self.venc
        cmd = [
            ff.ffmpeg_path(),
            "-y",
            *v.pre_input,
            "-i",
            self.src,
            "-filter_complex",
            graph,
            "-map",
            vmap,
        ]
        cmd += [*self.audio, *v.args, ctx.output()]
        ctx.span(0.35 if path else 0.0, 1.0).run(cmd, duration=self.duration, cwd=tmp)
        return 0
