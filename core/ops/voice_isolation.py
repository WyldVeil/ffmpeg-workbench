"""
core/ops/voice_isolation.py - command builders for tabs/audio/voice_isolation.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""

from __future__ import annotations

from core import filters as fl
from core.paths import resource_path


def models_dir() -> str:
    return resource_path("assets", "models", "rnnoise")


def denoise_filter(
    method: str,
    *,
    model: str = "",
    blend: float = 0.0,
    floor_db: float = -30,
    reduction_db: float = 12,
    strength: float = 0.00015,
    rumble: bool = True,
) -> str:
    parts = ["highpass=f=80"] if rumble else []
    if method == "neural":
        parts.append(f"arnndn=model={fl.filter_path(model)}:mix={1.0 - blend:.2f}")
    elif method == "nlm":
        parts.append(f"anlmdn=s={strength:g}")
    else:
        parts.append(f"afftdn=nf={floor_db:g}:nr={reduction_db:g}:tn=1")
    return ",".join(parts)
