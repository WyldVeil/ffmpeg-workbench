"""
core/logs.py - Application logging and crash reporting.

* :func:`setup` configures the ``crossfader`` logger with a rotating log file
  in the per-user log folder (see core/paths.py) and console output.
* :func:`install_excepthooks` routes uncaught exceptions from the main thread,
  worker threads and Tk callbacks to the log, and to the presenter the window
  installs (:func:`set_presenter`): a friendly dialog with the details and a
  button to open the log folder.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import sys
import threading
import traceback
from collections.abc import Callable
from types import TracebackType
from typing import Any

logger = logging.getLogger("crossfader.logs")

LOGGER_NAME = "crossfader"
_configured = False


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
                h, logging.handlers.RotatingFileHandler
            ):
                h.setLevel(logging.DEBUG if debug else logging.WARNING)
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        fh = logging.handlers.RotatingFileHandler(
            log_file(), maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG if debug else logging.INFO)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    if sys.stderr is not None:  # pythonw / windowed builds have no stderr
        sh = logging.StreamHandler()
        sh.setLevel(logging.DEBUG if debug else logging.WARNING)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    logging.captureWarnings(True)
    _configured = True
    try:
        from core import APP_VERSION as version
    except Exception:
        logger.debug("setup: best-effort step failed", exc_info=True)
        version = "?"
    logger.info(
        "Started v%s on %s %s (Python %s)",
        version,
        platform.system(),
        platform.release(),
        platform.python_version(),
    )
    return logger


def get(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def _format(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> str:
    return "".join(traceback.format_exception(exc_type, exc, tb))


#: Shows a crash to the person: (title, summary, details).  The window installs
#: one (tabs/kit/crash.py); without a window, crashes are only logged.
Presenter = Callable[[str, str, str], object]
_presenter: Presenter | None = None


def set_presenter(presenter: Presenter | None) -> None:
    global _presenter
    _presenter = presenter


def report(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
    *,
    where: str = "",
    dialog: bool = True,
) -> None:
    details = _format(exc_type, exc, tb)
    get().error("Unhandled exception%s:\n%s", f" in {where}" if where else "", details)
    presenter = _presenter
    if dialog and presenter is not None:
        summary = (
            f"Something went wrong{(' in ' + where) if where else ''}:\n"
            f"{exc_type.__name__}: {exc}\n\nThe details were written to the log file."
        )
        from core import ui

        ui.call_soon(presenter, "Unexpected error", summary, details)


def install_excepthooks(tk_root: Any = None) -> None:
    def _sys_hook(
        exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        report(exc_type, exc, tb, where="the application")

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        name = getattr(args.thread, "name", "a background task")
        exc = args.exc_value if args.exc_value is not None else args.exc_type()
        report(args.exc_type, exc, args.exc_traceback, where=name)

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook
    if tk_root is not None:

        def _tk_hook(
            exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
        ) -> None:
            report(exc_type, exc, tb, where="a button or window action")

        tk_root.report_callback_exception = _tk_hook
