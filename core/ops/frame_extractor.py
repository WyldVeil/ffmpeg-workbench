"""
core/ops/frame_extractor.py - command builders for tabs/social/frame_extractor.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""
from __future__ import annotations


def quality_args(ext: str, quality: int) -> list:
    q = max(1, min(100, int(quality)))
    if ext == "jpg":
        return ["-c:v", "mjpeg", "-q:v", str(max(2, min(31, round(31 - (q - 1) * 29 / 99))))]
    if ext == "webp":
        return ["-c:v", "libwebp", "-quality", str(q), "-lossless", "0"]
    if ext == "tiff":
        return ["-c:v", "tiff", "-compression_algo", "deflate"]
    return ["-c:v", "png"]
