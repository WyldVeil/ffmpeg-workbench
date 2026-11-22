"""
core/ops/reverse.py - the Reverser (tabs/cutting/reverser.py): a clip, a section,
a boomerang or a ping-pong loop played backwards, reversed in slices small enough
for the reverse filter to hold in memory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from core import encoders as enc
from core import filters as fl
from core.ops.shared import join_pieces_cmd, piece_cmd
from core.queue_manager import TaskContext

Piece = tuple[str, float, float]  # ("fwd" | "rev", start, end)

MEMORY_BUDGET = 1.2e9  # bytes of decoded frames the reverse filter may hold


def chunk_seconds(width: int, height: int, fps: float, budget: float | None = None) -> float:
    """Longest slice whose decoded frames fit in *budget* bytes (MEMORY_BUDGET when None)."""
    frame_bytes = max(1, width * height) * 1.5 * 1.3  # yuv420p + filter overhead
    return max(1.0, (MEMORY_BUDGET if budget is None else budget) / frame_bytes / max(fps, 1.0))


def reverse_chunks(start: float, end: float, chunk: float) -> list[tuple[float, float]]:
    """[(a, b)] slices of [start, end] in the order they must be played once
    each slice is reversed (latest slice first)."""
    out: list[tuple[float, float]] = []
    b = end
    while b - start > 1e-6:
        a = max(start, b - chunk)
        out.append((a, b))
        b = a
    return out


def plan(mode: str, dur: float, start: float, end: float, repeats: int) -> list[Piece]:
    """[('fwd'|'rev', a, b)] pieces of the output in order."""
    if mode == "full":
        return [("rev", 0.0, dur)]
    if mode == "range":
        pieces: list[Piece] = []
        if start > 0.01:
            pieces.append(("fwd", 0.0, start))
        pieces.append(("rev", start, end))
        if end < dur - 0.01:
            pieces.append(("fwd", end, dur))
        return pieces
    if mode == "boomerang":
        return [("fwd", start, end), ("rev", start, end)]
    return [("fwd", start, end), ("rev", start, end)] * max(1, repeats)


def normalise(w: int, h: int, venc: enc.VideoEncode) -> str:
    """Every piece the same size and pixel format, so they join without re-encoding."""
    return f"scale={w}:{h},setsar=1" + ("" if venc.pix_fmt.endswith("10le") else ",format=yuv420p")


@dataclass(frozen=True, kw_only=True)
class ReverseJob:
    """Reversed parts are made in chunks small enough to reverse in memory
    (each chunk reversed, the chunks in reverse order); then all are joined."""

    src: str
    pieces: list[Piece]
    chunk: float
    norm: str
    venc: enc.VideoEncode
    container: str
    audio: bool
    total: float

    def _slice_cmd(self, kind: str, sa: float, sb: float, seg: str) -> list[str]:
        rev = kind == "rev"
        vf = ("reverse," if rev else "") + self.norm
        af = ("areverse" if rev else "anull") if self.audio else None
        return piece_cmd(self.src, sa, sb - sa, seg, self.venc, vf=vf, af=af)

    def run(self, _p: object, _c: object, ctx: TaskContext) -> int:
        tmp = ctx.tempdir()
        files: list[str] = []
        cache: dict[tuple[str, float, float], list[str]] = {}
        done, n = 0.0, 0
        for kind, a, b in self.pieces:
            key = (kind, a, b)
            if key in cache:  # ping-pong reuses rendered pieces
                files += cache[key]
                done += b - a
                continue
            made: list[str] = []
            for sa, sb in reverse_chunks(a, b, self.chunk) if kind == "rev" else [(a, b)]:
                n += 1
                seg = os.path.join(tmp, f"piece{n:05d}.mkv")
                ctx.span(0.95 * done / self.total, 0.95 * (done + sb - sa) / self.total).run(
                    self._slice_cmd(kind, sa, sb, seg), duration=sb - sa
                )
                done += sb - sa
                made.append(seg)
            cache[key] = made
            files += made
        lst = fl.write_concat_list(files, os.path.join(tmp, "list.txt"))
        join = join_pieces_cmd(lst, ctx.output(), self.venc, self.container, audio=self.audio)
        ctx.span(0.95, 1.0).run(join, duration=self.total)
        return 0
