"""
core/ops/color_correct.py - the Colour Corrector (tabs/visuals/color_correct.py):
light, tone, colour and detail sliders (and ready-made looks) as one filter chain.
"""

from __future__ import annotations

from collections.abc import Mapping

# key: (label, low, high, default, resolution)
CONTROLS = {
    "exposure": ("Exposure (stops)", -2.0, 2.0, 0.0, 0.05),
    "contrast": ("Contrast", 0.5, 2.0, 1.0, 0.01),
    "brightness": ("Brightness", -0.3, 0.3, 0.0, 0.01),
    "gamma": ("Gamma (midtones)", 0.4, 2.5, 1.0, 0.01),
    "blacks": ("Blacks", -0.2, 0.2, 0.0, 0.01),
    "shadows": ("Shadows", -1.0, 1.0, 0.0, 0.05),
    "highlights": ("Highlights", -1.0, 1.0, 0.0, 0.05),
    "whites": ("Whites", -0.2, 0.2, 0.0, 0.01),
    "temperature": ("Temperature (cool / warm)", -100.0, 100.0, 0.0, 1.0),
    "tint": ("Tint (green / magenta)", -100.0, 100.0, 0.0, 1.0),
    "saturation": ("Saturation", 0.0, 2.5, 1.0, 0.01),
    "vibrance": ("Vibrance", -1.0, 1.0, 0.0, 0.05),
    "hue": ("Hue rotate (degrees)", -180.0, 180.0, 0.0, 1.0),
    "sharpen": ("Sharpen", 0.0, 2.0, 0.0, 0.05),
}
DEFAULTS = {k: v[3] for k, v in CONTROLS.items()}
LOOKS: dict[str, dict[str, float]] = {
    "Neutral (reset)": {},
    "Warm & sunny": {"temperature": 35, "saturation": 1.12, "vibrance": 0.2, "highlights": 0.15},
    "Cool & clean": {"temperature": -30, "contrast": 1.06, "whites": 0.03},
    "Punchy": {"contrast": 1.18, "vibrance": 0.45, "blacks": -0.03, "sharpen": 0.5},
    "Faded film": {
        "blacks": 0.08,
        "contrast": 0.9,
        "saturation": 0.8,
        "temperature": 12,
        "highlights": -0.2,
    },
    "Moody": {
        "exposure": -0.25,
        "contrast": 1.12,
        "saturation": 0.75,
        "temperature": -15,
        "shadows": -0.3,
    },
    "Black & white": {"saturation": 0.0, "contrast": 1.15},
    "Lift shadows": {"shadows": 0.45, "blacks": 0.02},
}


def _changed(values: Mapping[str, float], key: str) -> bool:
    return abs(float(values.get(key, DEFAULTS[key])) - DEFAULTS[key]) > 1e-6


def build_chain(values: Mapping[str, float]) -> str:
    """FFmpeg filter chain for the slider *values* ('' when nothing changes)."""
    v = {k: float(values.get(k, d)) for k, d in DEFAULTS.items()}
    parts: list[str] = []
    if _changed(v, "temperature"):
        kelvin = 6500 * 2 ** (-v["temperature"] / 100 * 0.6)
        parts.append(f"colortemperature=temperature={kelvin:.0f}:pl=1")
    if _changed(v, "tint"):
        parts.append(f"colorchannelmixer=gg={1 - v['tint'] / 100 * 0.15:.4f}")
    if _changed(v, "exposure"):
        gain = 2 ** v["exposure"]
        if gain > 1:
            m = f"{1 / gain:.4f}"
            parts.append(f"colorlevels=rimax={m}:gimax={m}:bimax={m}")
        else:
            m = f"{gain:.4f}"
            parts.append(f"colorlevels=romax={m}:gomax={m}:bomax={m}")
    eq = [
        f"{k}={v[k]:.3f}"
        for k in ("contrast", "brightness", "gamma", "saturation")
        if _changed(v, k)
    ]
    if eq:
        parts.append("eq=" + ":".join(eq))
    if any(_changed(v, k) for k in ("blacks", "shadows", "highlights", "whites")):
        b, w = v["blacks"], v["whites"]
        lo = f"0/{b:.3f}" if b >= 0 else f"{-b:.3f}/0"
        hi = f"1/{1 + w:.3f}" if w <= 0 else f"{1 - w:.3f}/1"
        ys = min(max(0.25 + 0.12 * v["shadows"], 0.02), 0.6)
        yh = min(max(0.75 + 0.12 * v["highlights"], 0.4), 0.98)
        parts.append(f"curves=all='{lo} 0.25/{ys:.3f} 0.75/{yh:.3f} {hi}':interp=pchip")
    if _changed(v, "vibrance"):
        parts.append(f"vibrance=intensity={v['vibrance']:.3f}")
    if _changed(v, "hue"):
        parts.append(f"hue=h={v['hue']:.1f}")
    if _changed(v, "sharpen"):
        parts.append(f"unsharp=5:5:{v['sharpen']:.2f}:5:5:0")
    return ",".join(parts)
