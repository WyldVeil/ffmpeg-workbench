"""
core/ops/image_watermark.py - command builders for tabs/social/image_watermark.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

POSITIONS = {
    "tl": ("Top left", "M", "M"),
    "tc": ("Top centre", "(W-w)/2", "M"),
    "tr": ("Top right", "W-w-M", "M"),
    "ml": ("Middle left", "M", "(H-h)/2"),
    "c": ("Centre", "(W-w)/2", "(H-h)/2"),
    "mr": ("Middle right", "W-w-M", "(H-h)/2"),
    "bl": ("Bottom left", "M", "H-h-M"),
    "bc": ("Bottom centre", "(W-w)/2", "H-h-M"),
    "br": ("Bottom right", "W-w-M", "H-h-M"),
    "custom": ("Custom spot (%)", "", ""),
}


def logo_graph(
    video_w: int,
    video_h: int,
    *,
    pct: float,
    opacity: float,
    place: str,
    margin_pct: float = 3.0,
    custom: tuple[float, float] = (50.0, 50.0),
    window: tuple[float, float] | None = None,
    fade: float = 0.0,
    logo_input: str = "[1:v]",
) -> str:
    """filter_complex: [0:v] + logo -> [v]."""
    lw = max(2, int(video_w * pct / 100) // 2 * 2)
    m = int(min(video_w, video_h) * margin_pct / 100)
    _label, x, y = POSITIONS.get(place, POSITIONS["br"])
    if place == "custom":
        x = f"(W-w)*{custom[0] / 100:.4f}"
        y = f"(H-h)*{custom[1] / 100:.4f}"
    x, y = x.replace("M", str(m)), y.replace("M", str(m))
    logo = f"{logo_input}scale={lw}:-1:flags=lanczos,format=rgba"
    if opacity < 0.999:
        logo += f",colorchannelmixer=aa={opacity:.3f}"
    if window and fade > 0:
        a, b = window
        logo += (
            f",fade=t=in:st={a:.3f}:d={fade:.2f}:alpha=1,"
            f"fade=t=out:st={max(a, b - fade):.3f}:d={fade:.2f}:alpha=1"
        )
    enable = f":enable='between(t,{window[0]:.3f},{window[1]:.3f})'" if window else ""
    return (
        f"{logo}[logo];[0:v][logo]overlay=x='{x}':y='{y}':shortest=1:format=auto{enable},"
        f"format=yuv420p[v]"
    )
