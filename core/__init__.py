"""
core  -  Quintessential Video Editor internal utilities

Submodules
----------
  hardware  - binary resolution, GPU detection, ffprobe/ffplay helpers
  settings  - centralised settings.json I/O (single source of truth)
  deps      - vendored dependency manager (auto-install into libs/)
  skins     - theming engine (skin palette, live switching, star particles)
"""

# ── Application identity (single source of truth) ────────────────────────────
APP_VERSION = "1.2.2"
APP_NAME = "WyldVeil's Media Editor"
APP_ID = "io.github.wyldveil.MediaEditor"     # Linux desktop/AppStream id
APP_SLUG = "wyldveil-media-editor"            # Linux executable / folder names
APP_URL = "https://github.com/WyldVeil/wyldveils-media-editor"
