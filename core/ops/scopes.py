"""
core/ops/scopes.py - Video Scopes (tabs/visuals/scopes.py): waveform, RGB parade,
histogram, vectorscope and false colour, beside or below the picture (or all four
in a grid), as a video or one snapshot.
"""

from __future__ import annotations

from core import ffmpeg as ff
from core.filters import even

# key: (label, filter chain, display aspect (w/h) or None = same as video, help)
SCOPES: dict[str, tuple[str, str, float | None, str]] = {
    "waveform": (
        "Waveform (luma)",
        "waveform=m=column:c=1:d=overlay:s=ire:g=green:fl=numbers+dots:i=0.08",
        16 / 9,
        "Brightness across the frame. IRE scale: 0 = black, 100 = white; "
        "anything flat at the top or bottom is clipped.",
    ),
    "parade": (
        "RGB parade",
        "format=gbrp,waveform=m=column:c=7:d=parade:s=ire:g=green:fl=numbers+dots:i=0.08",
        16 / 9,
        "Red, green and blue waveforms side by side - level the three for a neutral white balance.",
    ),
    "histogram": (
        "Histogram (RGB)",
        "format=gbrp,histogram=display_mode=stack:levels_mode=linear",
        4 / 3,
        "How many pixels sit at each level. Piled against an edge = clipping.",
    ),
    "vectorscope": (
        "Vectorscope",
        "vectorscope=m=color2:g=color:f=name:c=auto",
        1.0,
        "Hue (angle) and saturation (distance from centre); the targets mark "
        "the 75% colour bars. Skin tones sit near the line to the top left.",
    ),
    "falsecolor": (
        "False colour (exposure)",
        "pseudocolor=p=turbo",
        None,
        "Exposure map: blue = shadows, green = mid-tones, red = highlights.",
    ),
}
GRID = ("waveform", "parade", "vectorscope", "histogram")


def scope_box(key: str, width: int, height: int, *, fit: str = "height") -> tuple[int, int]:
    """Size for scope *key* next to a WxH picture (fit='height') or under it ('width')."""
    aspect = SCOPES[key][2] or (width / height)
    if fit == "width":
        return even(width), even(width / aspect)
    return even(height * aspect), even(height)


def export_graph(key: str, layout: str, width: int, height: int) -> str:
    """filter_complex graph (output [v]) for a scope video."""

    def chain(k: str, bw: int, bh: int) -> str:
        return f"{SCOPES[k][1]},scale={bw}:{bh},setsar=1,format=yuv420p"

    if layout == "grid":
        hw, hh = even(width / 2), even(height / 2)
        parts = ["[0:v]setsar=1,format=yuv420p,split=5[src][s0][s1][s2][s3]"]
        for i, k in enumerate(GRID):
            parts.append(f"[s{i}]{chain(k, hw, hh)}[g{i}]")
        parts.append("[g0][g1][g2][g3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[grid]")
        parts.append(f"[src]scale={2 * hw}:{2 * hh}[pic]")
        parts.append("[pic][grid]hstack=inputs=2[v]")
        return ";".join(parts)
    if layout == "only":
        bw, bh = scope_box(key, width, height)
        return f"[0:v]{chain(key, bw, bh)}[v]"
    fit = "width" if layout == "below" else "height"
    bw, bh = scope_box(key, width, height, fit=fit)
    stack = "vstack" if layout == "below" else "hstack"
    return (
        f"[0:v]setsar=1,format=yuv420p,split[src][s];[s]{chain(key, bw, bh)}[sc];"
        f"[src][sc]{stack}=inputs=2[v]"
    )


def snapshot_cmd(src: str, at: float, graph: str, out: str) -> list[str]:
    """One picture of the scopes (*graph*, output [v]) at *at* seconds."""
    head = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    return [
        *head,
        "-ss",
        ff.ts(at),
        "-i",
        src,
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-frames:v",
        "1",
        "-update",
        "1",
        out,
    ]
