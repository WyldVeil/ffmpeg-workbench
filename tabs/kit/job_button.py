"""
tabs/kit/job_button.py - a Render button while its job is queued and running.

It says "Queued", then "Cancel  42%"; a click cancels the job (the second
half of an accidental double-click on Render is ignored); afterwards the
button gets its own text and command back.  Used by every page's render.
"""

from __future__ import annotations

import contextlib
import time
import tkinter as tk
from collections.abc import Callable

from core.i18n import t


def cancel_label(percent: float | None = None) -> str:
    label = t("btn.cancel") if t("btn.cancel") != "btn.cancel" else "Cancel"
    return f"{label}  {percent:.0f}%" if percent is not None else label


class JobButton:
    """Drives *button* (may be None: then nothing is shown) for one job;
    *cancel()* stops the job."""

    def __init__(self, button: tk.Button | None, cancel: Callable[[], object]):
        self._button = button
        self._cancel = cancel
        self._clicked = time.monotonic()
        self._text = ""
        self._command = ""  # Tk gives a button's command back as the name of a Tcl command
        if button is None:
            return
        try:
            self._text, self._command = str(button.cget("text")), str(button.cget("command"))
            button.config(
                state="normal", text="⏳ " + t("app.status.queued_btn"), command=self._click
            )
        except tk.TclError:
            self._button = None

    def _click(self) -> None:
        if time.monotonic() - self._clicked >= 0.8:
            self._cancel()

    def running(self, fraction: float | None = None) -> None:
        """The job started (or moved on to *fraction* done)."""
        if self._button is not None:
            with contextlib.suppress(tk.TclError):
                self._button.config(
                    text="✖ " + cancel_label(fraction * 100 if fraction is not None else None)
                )

    def restore(self) -> None:
        if self._button is not None:
            with contextlib.suppress(tk.TclError):
                self._button.config(state="normal", text=self._text, command=self._command)
