"""
core/skins.py  -  the colour themes, as data.

Each skin is a palette (a dict of named colours); which one is in use is the
``theme`` setting.  Applying a palette to the window is GUI work and lives in
tabs/kit/theme.py - the engine never imports tkinter.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger("crossfader.skins")
SKIN_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skin.json")


# ── Platform-aware font selection ─────────────────────────────────────────────
def _detect_ui_font():
    if sys.platform == "win32":
        return "Segoe UI"
    elif sys.platform == "darwin":
        return "SF Pro Display"
    return "Ubuntu"


def _detect_mono_font():
    if sys.platform == "win32":
        return "Consolas"
    elif sys.platform == "darwin":
        return "Menlo"
    return "Ubuntu Mono"


UI_FONT = _detect_ui_font()
MONO_FONT = _detect_mono_font()

# ── Default colors for the Custom skin (based on Classic) ────────────────────
_CUSTOM_DEFAULTS: dict[str, Any] = {
    "sb_bg": "#111111",
    "sb_cat": "#424242",
    "sb_btn": "#B0B0B0",
    "sb_hover": "#1C1C1C",
    "sb_active": "#212121",
    "accent": "#3A8EE6",
    "content": "#1A1A1A",
    "panel": "#212121",
    "fg": "#E2E2E2",
    "fgdim": "#7A7A7A",
    "green": "#2ECC71",
    "red": "#E74C3C",
    "orange": "#F39C12",
    "pink": "#C0392B",
    "console_bg": "#0A0A0A",
    "console_fg": "#00E676",
    "border": "#2C2C2C",
    "input_bg": "#1E1E1E",
    "input_fg": "#E2E2E2",
    "stars": False,
    "desc": "Your personal color scheme.",
}

SKINS: dict[str, dict[str, Any]] = {
    "Classic": {
        # Professional dark - all surfaces dark, blue-cyan accent
        "sb_bg": "#111111",
        "sb_cat": "#424242",
        "sb_btn": "#B0B0B0",
        "sb_hover": "#1C1C1C",
        "sb_active": "#212121",
        "accent": "#3A8EE6",
        "content": "#1A1A1A",
        "panel": "#212121",
        "fg": "#E2E2E2",
        "fgdim": "#7A7A7A",
        "green": "#2ECC71",
        "red": "#E74C3C",
        "orange": "#F39C12",
        "pink": "#C0392B",
        "console_bg": "#0A0A0A",
        "console_fg": "#00E676",
        "border": "#2C2C2C",
        "input_bg": "#1E1E1E",
        "input_fg": "#E2E2E2",
        "stars": False,
        "desc": "Professional dark. Premiere Pro inspired.",
    },
    "Space": {
        "sb_bg": "#080812",
        "sb_cat": "#24244A",
        "sb_btn": "#00E890",
        "sb_hover": "#10102A",
        "sb_active": "#18183A",
        "accent": "#00E890",
        "content": "#0B0B18",
        "panel": "#10102A",
        "fg": "#00E890",
        "fgdim": "#2E6644",
        "green": "#00CC77",
        "red": "#FF2255",
        "orange": "#FF9900",
        "pink": "#FF22AA",
        "console_bg": "#040410",
        "console_fg": "#00FFCC",
        "border": "#1E1E3A",
        "input_bg": "#141428",
        "input_fg": "#00E890",
        "stars": False,
        "desc": "Deep space: dark navy with electric neon green.",
    },
    "Night": {
        "sb_bg": "#0B1C2A",
        "sb_cat": "#284660",
        "sb_btn": "#A8DFE8",
        "sb_hover": "#162E40",
        "sb_active": "#1B3650",
        "accent": "#22BDD0",
        "content": "#0D1E2E",
        "panel": "#112232",
        "fg": "#A8DFE8",
        "fgdim": "#3E5A6C",
        "green": "#1E9E92",
        "red": "#EF4444",
        "orange": "#F59E0B",
        "pink": "#E8407A",
        "console_bg": "#080F18",
        "console_fg": "#4DD0E1",
        "border": "#1A3040",
        "input_bg": "#112232",
        "input_fg": "#A8DFE8",
        "stars": False,
        "desc": "Deep teal night mode. Easy on the eyes.",
    },
    "Dark Mode": {
        "sb_bg": "#1A1A1C",
        "sb_cat": "#363638",
        "sb_btn": "#E2E2E6",
        "sb_hover": "#2A2A2C",
        "sb_active": "#383838",
        "accent": "#0A84FF",
        "content": "#1A1A1C",
        "panel": "#2A2A2C",
        "fg": "#E2E2E6",
        "fgdim": "#8A8A90",
        "green": "#28C952",
        "red": "#FF3A30",
        "orange": "#FF9F05",
        "pink": "#FF3058",
        "console_bg": "#000000",
        "console_fg": "#30D14A",
        "border": "#363638",
        "input_bg": "#2A2A2C",
        "input_fg": "#E2E2E6",
        "stars": False,
        "desc": "System dark mode. Matches macOS / Windows dark UI.",
    },
    "Low Blue": {
        "sb_bg": "#181000",
        "sb_cat": "#3A2C00",
        "sb_btn": "#FFD060",
        "sb_hover": "#281C00",
        "sb_active": "#302200",
        "accent": "#FFB000",
        "content": "#181000",
        "panel": "#201600",
        "fg": "#FFD060",
        "fgdim": "#725A2A",
        "green": "#88BB40",
        "red": "#F44336",
        "orange": "#FF8800",
        "pink": "#F06090",
        "console_bg": "#100C00",
        "console_fg": "#FF9800",
        "border": "#2A2000",
        "input_bg": "#201600",
        "input_fg": "#FFD060",
        "stars": False,
        "desc": "Warm amber tones. Minimal blue light for late-night editing.",
    },
    "Mystical": {
        "sb_bg": "#0C0818",
        "sb_cat": "#28184A",
        "sb_btn": "#CC88D8",
        "sb_hover": "#180828",
        "sb_active": "#201040",
        "accent": "#A840BC",
        "content": "#0C0818",
        "panel": "#180828",
        "fg": "#DDB8E8",
        "fgdim": "#60407A",
        "green": "#7B1FA2",
        "red": "#E8186A",
        "orange": "#FF6800",
        "pink": "#F080B0",
        "console_bg": "#070518",
        "console_fg": "#CC88D8",
        "border": "#28184A",
        "input_bg": "#180828",
        "input_fg": "#DDB8E8",
        "stars": True,
        "desc": "Fantasy purple with falling star particles ✨",
    },
    "Custom": {
        **_CUSTOM_DEFAULTS,
    },
}


def load_skin_name() -> str:
    """The chosen theme (settings.THEME; a skin.json from older versions is
    moved into it the first time)."""
    from core import settings

    if os.path.exists(SKIN_FILE):
        try:
            with open(SKIN_FILE, encoding="utf-8") as f:
                old = json.load(f).get("name")
            if isinstance(old, str) and old in SKINS:
                settings.THEME.set(old)
            os.remove(SKIN_FILE)
        except (OSError, ValueError, AttributeError):
            logger.warning(
                "could not move the theme from %s into the settings", SKIN_FILE, exc_info=True
            )
    name = settings.THEME.get()
    return name if name in SKINS else "Classic"


def save_skin_name(name: str) -> None:
    from core import settings

    settings.THEME.set(name)


def get_skin(name: str) -> dict[str, Any]:
    """Return the skin dict for *name*.
    For "Custom", overlay saved user colours from settings.json on top of defaults.
    """
    if name == "Custom":
        skin = dict(_CUSTOM_DEFAULTS)
        from core import settings

        prefix = "custom_color_"
        for key, val in settings.load_settings().items():
            if key.startswith(prefix) and key[len(prefix) :] in skin and isinstance(val, str):
                skin[key[len(prefix) :]] = val
        return skin
    return SKINS.get(name, SKINS["Classic"])


def save_custom_color(color_key: str, value: str) -> None:
    """Persist a single custom-skin color to settings.json."""
    from core import settings

    settings.update({f"custom_color_{color_key}": value})
