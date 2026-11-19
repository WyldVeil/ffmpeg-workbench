"""
core/logs.py - Application logging and crash reporting.

* :func:`setup` configures the ``crossfader`` logger with a rotating log file
  in the per-user log folder (see core/paths.py) and console output.
* :func:`install_excepthooks` routes uncaught exceptions from the main thread,
  worker threads and Tk callbacks to the log and shows a friendly dialog with
  the details and a button to open the log folder - instead of the old
  behaviour of failing silently.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import sys
import threading
import time
import traceback

LOGGER_NAME = "crossfader"
_configured = False
_last_dialog = 0.0


def log_file() -> str:
    from core.paths import log_dir
    return os.path.join(log_dir(), "app.log")


def setup(debug: bool = False) -> logging.Logger:
    """Configure logging once; later calls only adjust the console level."""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        for h in logger.handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(
                    h, logging.handlers.RotatingFileHandler):
                h.setLevel(logging.DEBUG if debug else logging.WARNING)
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        fh = logging.handlers.RotatingFileHandler(
            log_file(), maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        fh.setLevel(logging.DEBUG if debug else logging.INFO)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    if sys.stderr is not None:   # pythonw / windowed builds have no stderr
        sh = logging.StreamHandler()
        sh.setLevel(logging.DEBUG if debug else logging.WARNING)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    logging.captureWarnings(True)
    _configured = True
    try:
        from core import APP_VERSION
    except Exception:
        APP_VERSION = "?"
    logger.info("Started v%s on %s %s (Python %s)", APP_VERSION, platform.system(),
                platform.release(), platform.python_version())
    return logger


def get(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def _format(exc_type, exc, tb) -> str:
    return "".join(traceback.format_exception(exc_type, exc, tb))


def _show_dialog(title: str, summary: str, details: str) -> None:
    """Modal-less error window with Copy / Open log folder buttons."""
    global _last_dialog
    now = time.monotonic()
    if now - _last_dialog < 3.0:      # avoid a storm of dialogs
        return
    _last_dialog = now
    try:
        import tkinter as tk
        from tkinter import ttk
        root = tk._default_root  # type: ignore[attr-defined]
        if root is None:
            return
        win = tk.Toplevel(root)
        win.title(title)
        win.geometry("720x420")
        ttk.Label(win, text=summary, wraplength=680, justify="left").pack(
            fill="x", padx=14, pady=(12, 6))
        txt = tk.Text(win, height=14, wrap="none")
        txt.insert("1.0", details)
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=14)
        row = ttk.Frame(win)
        row.pack(fill="x", padx=14, pady=10)

        def _copy():
            win.clipboard_clear()
            win.clipboard_append(details)

        def _open():
            from core.hardware import open_in_explorer
            open_in_explorer(os.path.dirname(log_file()))

        ttk.Button(row, text="Copy details", command=_copy).pack(side="left")
        ttk.Button(row, text="Open log folder", command=_open).pack(side="left", padx=6)
        ttk.Button(row, text="Close", command=win.destroy).pack(side="right")
    except Exception:
        pass


def report(exc_type, exc, tb, *, where: str = "", dialog: bool = True) -> None:
    details = _format(exc_type, exc, tb)
    get().error("Unhandled exception%s:\n%s", f" in {where}" if where else "", details)
    if dialog:
        summary = (f"Something went wrong{(' in ' + where) if where else ''}:\n"
                   f"{exc_type.__name__}: {exc}\n\nThe details were written to the log file.")
        try:
            from core import ui
            ui.call_soon(_show_dialog, "Unexpected error", summary, details)
        except Exception:
            pass


def install_excepthooks(tk_root=None) -> None:
    def _sys_hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        report(exc_type, exc, tb, where="the application")

    def _thread_hook(args):
        if args.exc_type is SystemExit:
            return
        name = getattr(args.thread, "name", "a background task")
        report(args.exc_type, args.exc_value, args.exc_traceback, where=name)

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook
    if tk_root is not None:
        def _tk_hook(exc_type, exc, tb):
            report(exc_type, exc, tb, where="a button or window action")
        tk_root.report_callback_exception = _tk_hook
