"""
tabs/kit/crash.py - the window that tells a person something went wrong.

core/logs.py logs every uncaught exception and hands it to a presenter; the
window installs this one, so the engine itself never needs Tk.
"""

from __future__ import annotations

import logging
import os
import time
import tkinter as tk
from tkinter import ttk

from core.i18n import t
from core.logs import log_file
from core.scaling import px

logger = logging.getLogger("crossfader.crash")
_last_shown = 0.0


def show_crash(title: str, summary: str, details: str) -> None:
    """A non-modal error window with Copy / Open log folder buttons (at most one every 3 s)."""
    global _last_shown
    now = time.monotonic()
    if now - _last_shown < 3.0:  # a storm of errors gets one window
        return
    _last_shown = now
    from core import ui

    root = ui.tk_default_root()
    if root is None:
        return
    win = tk.Toplevel(root)
    win.title(title)
    win.geometry(f"{px(720)}x{px(420)}")
    ttk.Label(win, text=summary, wraplength=px(680), justify="left").pack(
        fill="x", padx=px(14), pady=px(12, 6)
    )
    txt = tk.Text(win, height=14, wrap="none")
    txt.insert("1.0", details)
    txt.configure(state="disabled")
    txt.pack(fill="both", expand=True, padx=px(14))
    row = ttk.Frame(win)
    row.pack(fill="x", padx=px(14), pady=px(10))

    def copy() -> None:
        win.clipboard_clear()
        win.clipboard_append(details)

    def open_folder() -> None:
        from core.hardware import open_in_explorer

        open_in_explorer(os.path.dirname(log_file()))

    ttk.Button(row, text=t("crash.copy_details"), command=copy).pack(side="left")
    ttk.Button(row, text=t("crash.open_log_folder"), command=open_folder).pack(
        side="left", padx=px(6)
    )
    ttk.Button(row, text=t("btn.close"), command=win.destroy).pack(side="right")
