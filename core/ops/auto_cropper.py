"""
core/ops/auto_cropper.py - command builders for tabs/social/auto_cropper.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""
from __future__ import annotations

import re

from core import ffmpeg as ff


def detect_bars(path: str, duration: float, samples: int = 6) -> tuple | None:
    """(x, y, w, h) of the picture area, or None when there are no bars."""
    boxes = []
    for i in range(samples):
        at = duration * (i + 0.5) / samples
        r = ff.capture([ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-ss", ff.ts(at), "-t", "1.5",
                        "-i", path, "-an", "-sn", "-vf", "cropdetect=limit=0.094:round=2:reset=0",
                        "-f", "null", "-"], timeout=60)
        found = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr or "")
        if found:
            w, h, x, y = (int(v) for v in found[-1])
            boxes.append((x, y, x + w, y + h))
    if not boxes:
        return None
    # union of what each sample considered picture: never cut into a bright scene
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    return x0 // 2 * 2, y0 // 2 * 2, (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2
