"""
core/procenv.py - Environment for child processes.

A PyInstaller-frozen Linux app runs with ``LD_LIBRARY_PATH`` pointing at its
own bundled libraries (the original value is saved in
``LD_LIBRARY_PATH_ORIG``).  System programs started from the app - xdg-open,
kdialog, zenity, notify-send, a system FFmpeg - would then load the bundle's
libraries instead of their own and can crash.  :func:`child_env` returns an
environment with the user's original values restored.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping

_RESTORE = ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONPATH", "PYTHONHOME",
            "GST_PLUGIN_PATH", "GIO_MODULE_DIR", "GDK_PIXBUF_MODULE_FILE",
            "QT_PLUGIN_PATH", "TCL_LIBRARY", "TK_LIBRARY", "SSL_CERT_FILE")


def child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    if getattr(sys, "frozen", False) or os.environ.get("APPIMAGE"):
        for key in _RESTORE:
            orig = env.pop(key + "_ORIG", None)
            if orig is not None:
                env[key] = orig
            elif key in ("LD_LIBRARY_PATH", "TCL_LIBRARY", "TK_LIBRARY", "PYTHONPATH", "PYTHONHOME"):
                env.pop(key, None)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra:
        env.update(extra)
    return env
