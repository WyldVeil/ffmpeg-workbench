"""
core/ops/shortifier.py - command builders for tabs/social/shortifier.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""
from __future__ import annotations

# id: (name, width, height, max seconds or 0)
PLATFORMS = {
    "shorts": ("YouTube Shorts", 1080, 1920, 180),
    "shorts4k": ("YouTube Shorts 4K", 2160, 3840, 180),
    "tiktok": ("TikTok", 1080, 1920, 600),
    "reels": ("Instagram Reels", 1080, 1920, 180),
    "story": ("Instagram / Facebook Story", 1080, 1920, 60),
    "ig_square": ("Instagram post (square)", 1080, 1080, 60),
    "ig_portrait": ("Instagram post (portrait 4:5)", 1080, 1350, 60),
    "x": ("X / Twitter", 1920, 1080, 140),
    "youtube": ("YouTube (16:9)", 1920, 1080, 0),
    "youtube4k": ("YouTube 4K", 3840, 2160, 0),
    "linkedin": ("LinkedIn (square)", 1080, 1080, 600),
    "snapchat": ("Snapchat Spotlight", 1080, 1920, 60),
    "custom": ("Custom size", 0, 0, 0),
}


ANCHORS = {"center": ("Centre", 0.5, 0.5), "top": ("Top", 0.5, 0.0), "bottom": ("Bottom", 0.5, 1.0),
           "left": ("Left", 0.0, 0.5), "right": ("Right", 1.0, 0.5)}


def reframe_graph(tw: int, th: int, mode: str, anchor: str = "center", colour: str = "000000") -> str:
    """filter_complex from [0:v] to [v] at tw x th."""
    sq = "scale=iw*sar:ih,setsar=1"          # anamorphic sources -> square pixels first
    if mode == "crop":
        _l, ax, ay = ANCHORS.get(anchor, ANCHORS["center"])
        return (f"[0:v]{sq},scale={tw}:{th}:force_original_aspect_ratio=increase,"
                f"crop={tw}:{th}:x=(iw-ow)*{ax}:y=(ih-oh)*{ay},setsar=1,format=yuv420p[v]")
    fit = f"scale={tw}:{th}:force_original_aspect_ratio=decrease:force_divisible_by=2"
    if mode in ("blur", "mirror"):
        bg = (f"scale={tw // 4}:{th // 4}:force_original_aspect_ratio=increase,crop={tw // 4}:{th // 4},"
              + ("hflip,vflip," if mode == "mirror" else "")
              + f"gblur=sigma={12 if mode == 'blur' else 2},eq=brightness=-0.06,scale={tw}:{th},setsar=1")
        return (f"[0:v]{sq},split[a][b];[a]{bg}[bg];[b]{fit},setsar=1[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]")
    col = {"black": "black", "white": "white"}.get(mode, f"0x{colour}")
    return f"[0:v]{sq},{fit},pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color={col},setsar=1,format=yuv420p[v]"
