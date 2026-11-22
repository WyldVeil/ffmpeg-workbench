"""
core/ops/sharpen.py - command builders for tabs/visuals/sharpen.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""
from __future__ import annotations


def sharpen_chain(method: str, amount: float, radius: int = 5) -> str:
    """*amount* 0..1, *radius* odd 3..13 (unsharp only)."""
    a = max(0.0, min(1.0, amount))
    if method == "unsharp":
        r = max(3, min(13, int(radius) | 1))
        return f"unsharp={r}:{r}:{a * 2.5:.2f}:{r}:{r}:0"
    if method == "clarity":
        return f"unsharp=13:13:{a * 1.2:.2f}:5:5:0"
    return f"cas=strength={a:.2f}"
