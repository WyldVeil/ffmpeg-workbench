"""
core/upscale.py  -  Better upscaling: GPU shaders through libplacebo

The Resolution Scaler can upscale with these (bundled under shaders/, all
MIT-licensed, see THIRD_PARTY_NOTICES.md):

* AMD FidelityFX Super Resolution 1.0 (FSR) - sharp, works on any content;
  the automatic choice for upscaling.
* NVIDIA Image Scaling (NIS) - similar, a little softer.
* ArtCNN - a small neural network made for animation and artwork.
* Anime4K - a neural network made for anime.

They work on the brightness plane only.  libplacebo's own colour path goes
through RGB and clips the most saturated colours (measurably changing
them), so only the brightness plane goes through libplacebo - marked full
range in and out, so its values are left alone - while the colour planes
are scaled by FFmpeg and the frame's colour tags are put back afterwards.
FSRCNNX was left out: it is GPL-3.0 licensed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

from core import filters as fl

if TYPE_CHECKING:
    from core import ffmpeg as ff

#: key: (label, shader file, what it's for)
SHADERS = {
    "fsr": ("FSR 1.0 (AMD) - sharp, any video", "FSR.glsl", "AMD FidelityFX Super Resolution 1.0"),
    "nis": ("NIS (NVIDIA) - sharp, any video", "NVScaler.glsl", "NVIDIA Image Scaling 1.0.3"),
    "artcnn": (
        "ArtCNN - AI, animation & artwork",
        "ArtCNN_C4F16.glsl",
        "ArtCNN C4F16 luma doubler",
    ),
    "artcnn_best": (
        "ArtCNN best - AI, animation (slower)",
        "ArtCNN_C4F32.glsl",
        "ArtCNN C4F32 luma doubler",
    ),
    "anime4k": ("Anime4K - AI, anime", "Anime4K_Upscale_CNN_x2_M.glsl", "Anime4K Upscale CNN x2 M"),
}


def shader_dirs() -> list[str]:
    """Bundled shaders (bin/<platform>/shaders, installed by scripts/fetch_binaries.py)."""
    from core.hardware import APP_ROOT, bundled_bin_dirs

    return [os.path.join(d, "shaders") for d in bundled_bin_dirs()] + [
        os.path.join(APP_ROOT, "shaders")
    ]


def shader_path(key: str) -> str | None:
    spec = SHADERS.get(key)
    if not spec:
        return None
    for folder in shader_dirs():
        path = os.path.join(folder, spec[1])
        if os.path.isfile(path):
            return path
    return None


def available() -> list[str]:
    """Shader keys whose files are present (the GPU check is filters.vulkan_ok())."""
    return [k for k in SHADERS if shader_path(k)]


def _tag(value: str, default: str = "unknown") -> str:
    return value if value and value not in ("unknown", "reserved") else default


def plane_graph(
    size: tuple[int, int],
    *,
    shader: str | None = None,
    deep: bool = False,
    tags: Mapping[str, str] | None = None,
) -> str:
    """A single-input/single-output filter graph (usable as -vf) scaling to
    *size*: brightness through libplacebo (with *shader*), colour through
    FFmpeg, then the source's colour *tags* (range, primaries, trc, space)
    restored.  Needs ``-init_hw_device vulkan`` before the input."""
    w, h = max(2, int(size[0]) // 2 * 2), max(2, int(size[1]) // 2 * 2)
    fmt, gray = ("yuv420p10le", "gray10le") if deep else ("yuv420p", "gray")
    path = shader_path(shader) if shader else None
    if shader and not path:
        raise ValueError(f"The {SHADERS[shader][2]} shader isn't installed.")
    sh = f":custom_shader_path={fl.filter_path(path)}" if path else ""
    tags = tags or {}
    restore = (
        f"setparams=range={_tag(tags.get('range', ''))}:"
        f"color_primaries={_tag(tags.get('primaries', ''))}:color_trc={_tag(tags.get('trc', ''))}:"
        f"colorspace={_tag(tags.get('space', ''))}"
    )
    return (
        f"format={fmt},extractplanes=y+u+v[y][u][v];"
        f"[y]setparams=range=pc,libplacebo=w={w}:h={h}:upscaler=ewa_lanczossharp:downscaler=ewa_lanczos"
        f"{sh}:range=pc:format={gray}[Y];"
        f"[u]scale={w // 2}:{h // 2}:flags=lanczos[U];[v]scale={w // 2}:{h // 2}:flags=lanczos[V];"
        f"[Y][U][V]mergeplanes=0x001020:{fmt},{restore}"
    )


def tags_of(video: ff.Stream) -> dict[str, str]:
    """Colour tags of a probed video stream, for :func:`plane_graph`."""
    return {
        "range": video.color_range or "",
        "primaries": video.color_primaries or "",
        "trc": video.color_transfer or "",
        "space": video.color_space or "",
    }
