"""
core - the engine of FFmpeg Workbench: everything that is not the window.

It never imports Tk (tests/test_architecture.py).  The modules to know first:
errors (the exception tree), proc (starting programs), tasks (background work),
settings, tools (every tool's id), ffmpeg (probing and running) and ops (the
command builders).  ARCHITECTURE.md has the map.
"""

# ── Application identity (single source of truth) ────────────────────────────
APP_VERSION = "1.2.6"
APP_NAME = "FFmpeg Workbench"
APP_ID = "io.github.ffmpeg_workbench.FFmpegWorkbench"  # Linux desktop/AppStream id
APP_SLUG = "ffmpeg-workbench"  # Linux executable / folder names
APP_URL = "https://github.com/WyldVeil/ffmpeg-workbench"
#: who the Linux packages name as maintainer (a "name <address>")
MAINTAINER = "FFmpeg Workbench <50123619+WyldVeil@users.noreply.github.com>"
