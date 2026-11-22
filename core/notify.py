"""
core/notify.py - Tell the user when a long job finishes while they are away.

Only fires when the main window is not focused and the ``notify_on_complete``
setting is on.  Linux: ``notify-send`` (any freedesktop notification server);
macOS: ``osascript``; Windows: flashes the taskbar button.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys

from core import proc as _proc
from core import settings

logger = logging.getLogger("crossfader.notify")


def _enabled() -> bool:
    if os.environ.get("CROSSFADER_NO_NOTIFY") == "1":
        return False
    return settings.NOTIFY_ON_COMPLETE.get()


def _root():
    from core import ui

    return ui.tk_default_root()


def _window_focused(root) -> bool:
    try:
        return root is not None and root.focus_displayof() is not None
    except Exception:
        logger.debug("_window_focused: best-effort step failed", exc_info=True)
        return True


def desktop_notify(title: str, body: str, *, urgency: str = "normal") -> None:
    try:
        from core.paths import resource_path
        from core.procenv import child_env

        icon = resource_path("assets", "icons", "app_128.png")
        if sys.platform.startswith("linux") and shutil.which("notify-send"):
            _proc.spawn(
                [
                    "notify-send",
                    "--app-name=WyldVeil Media Editor",
                    f"--urgency={urgency}",
                    f"--icon={icon}",
                    title,
                    body,
                ],
                stdin=_proc.DEVNULL,
                stdout=_proc.DEVNULL,
                stderr=_proc.DEVNULL,
                env=child_env(),
            )
        elif sys.platform == "darwin":

            def esc(s):
                return s.replace("\\", "\\\\").replace('"', '\\"')

            _proc.spawn(
                [
                    "osascript",
                    "-e",
                    f'display notification "{esc(body)}" with title "{esc(title)}"',
                ],
                stdin=_proc.DEVNULL,
                stdout=_proc.DEVNULL,
                stderr=_proc.DEVNULL,
            )
        elif sys.platform.startswith("win"):
            root = _root()
            if root is not None:
                import ctypes
                from ctypes import wintypes

                class FLASHWINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.UINT),
                        ("hwnd", wintypes.HWND),
                        ("dwFlags", wintypes.DWORD),
                        ("uCount", wintypes.UINT),
                        ("dwTimeout", wintypes.DWORD),
                    ]

                hwnd = int(root.wm_frame(), 16)
                info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x3 | 0xC, 3, 0)
                ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))  # type: ignore[attr-defined]
    except Exception as exc:
        logger.debug("notification failed: %s", exc, exc_info=True)


def job_finished(task) -> None:
    """Called by the render queue (on a worker thread) when a task ends."""
    if not _enabled() or task.status == "cancelled":
        return
    root = _root()

    def _go():
        if _window_focused(root):
            return
        name = os.path.basename(task.output_path) if task.output_path else task.name
        if task.status == "done":
            desktop_notify("Render finished", f"{task.name}: {name}")
        else:
            desktop_notify(
                "Render failed",
                f"{task.name}: {(task.error or '').splitlines()[0][:120]}",
                urgency="critical",
            )

    try:
        from core import ui

        ui.call_soon(_go)
    except Exception:
        logger.debug("job_finished: best-effort step failed", exc_info=True)
