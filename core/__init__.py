"""
core - the engine of WyldVeil's Media Editor: everything that is not the window.

It never imports Tk (tests/test_architecture.py).  The modules to know first:
errors (the exception tree), proc (starting programs), tasks (background work),
settings, tools (every tool's id), ffmpeg (probing and running) and ops (the
command builders).  ARCHITECTURE.md has the map.
"""

# ── Application identity (single source of truth) ────────────────────────────
APP_VERSION = "1.2.5"
APP_NAME = "WyldVeil's Media Editor"
APP_ID = "io.github.wyldveil.MediaEditor"     # Linux desktop/AppStream id
APP_SLUG = "wyldveil-media-editor"            # Linux executable / folder names
APP_URL = "https://github.com/WyldVeil/wyldveils-media-editor"
