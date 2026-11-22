"""
shell/files.py - getting files into the app: File › Open, Recent Files,
drag-and-drop and the command line all end in :meth:`FileOpener.open`.
"""

from __future__ import annotations

import logging
import os
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox

from core import dialogs, settings
from core.tools import ToolId
from tabs.kit.contracts import AcceptsFiles

logger = logging.getLogger("crossfader.shell")
MAX_RECENT = 10

#: Pages that are not tools: a file dropped on them goes to the Media Inspector.
_NOT_TOOLS = frozenset({ToolId.HOME, ToolId.SETTINGS})


def remember(path: str) -> None:
    """Put *path* at the top of Recent Files."""
    path = os.path.normpath(path)
    with settings.transaction() as data:
        recent = [
            r
            for r in data.get("recent_files", [])
            if isinstance(r, str) and os.path.normpath(r) != path
        ]
        data["recent_files"] = [path, *recent][:MAX_RECENT]


class FileOpener:
    """*current()* is (the open tool, its page); *show(tool)* opens a tool and returns its page."""

    def __init__(
        self,
        window: tk.Tk,
        recent_menu: tk.Menu,
        *,
        current: Callable[[], tuple[ToolId | None, tk.Misc | None]],
        show: Callable[[ToolId], tk.Misc | None],
        loaded: Callable[[str], object],
    ) -> None:
        self._window = window
        self._menu = recent_menu
        self._current = current
        self._show = show
        self._loaded = loaded
        self.drag_and_drop = False
        self.rebuild_menu()

    def open(self, paths: list[str]) -> bool:
        """Load files into the open tool; one that can't take them hands over
        to the Media Inspector.  True if something took the first file."""
        files = [os.path.abspath(p) for p in paths if p and os.path.isfile(p)]
        if not files:
            return False
        for path in files:
            remember(path)
        self.rebuild_menu()
        tool, page = self._current()
        accepted = False
        if tool not in _NOT_TOOLS and isinstance(page, AcceptsFiles):
            for path in files:
                try:
                    accepted = bool(page.load_file(path)) or accepted
                except Exception:  # a page's bug: log it, fall back to the inspector
                    logger.exception("load_file failed in %s", tool)
        if not accepted:
            inspector = self._show(ToolId.MEDIA_INSPECTOR)
            if isinstance(inspector, AcceptsFiles):
                accepted = bool(inspector.load_file(files[0]))
        if accepted:
            self._loaded(files[0])
        return accepted

    def ask(self) -> None:
        """File › Open."""
        paths = dialogs.ask_open_many("media", title="Open media files", parent=self._window)
        if paths:
            self.open(list(paths))

    # ── Recent Files ──────────────────────────────────────────────────────────
    def rebuild_menu(self) -> None:
        menu = self._menu
        menu.delete(0, tk.END)
        recent = [p for p in settings.RECENT_FILES.get() if isinstance(p, str)]
        if not recent:
            menu.add_command(label="(no recent files)", state="disabled")
            return
        for path in recent:
            menu.add_command(
                label=os.path.basename(path), command=lambda p=path: self._open_recent(p)
            )
        menu.add_separator()
        menu.add_command(label="Clear Recent Files", command=self.clear_recent)

    def _open_recent(self, path: str) -> None:
        if not os.path.isfile(path):
            messagebox.showwarning(
                "File Not Found", f"File no longer exists:\n{path}", parent=self._window
            )
            return
        self.open([path])

    def clear_recent(self) -> None:
        settings.RECENT_FILES.set([])
        self.rebuild_menu()

    # ── Drag-and-drop (Windows, Linux X11/XWayland, macOS) ───────────────────
    def accept_drops(self, *targets: tk.Misc) -> None:
        try:
            from tkinterdnd2 import DND_FILES, TkinterDnD  # pyright: ignore[reportMissingImports] - optional

            TkinterDnD._require(self._window)  # noqa: SLF001 - tkinterdnd2's documented set-up call
            for widget in targets:
                widget.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
                widget.dnd_bind("<<Drop>>", self._dropped)  # type: ignore[attr-defined]
            self.drag_and_drop = True
        except Exception as exc:  # no tkdnd for this Tk: the app works without it
            logger.info("Drag-and-drop unavailable: %s", exc, exc_info=True)

    def _dropped(self, event: tk.Event) -> str:
        data = str(getattr(event, "data", ""))
        try:
            paths = list(self._window.tk.splitlist(data))
        except tk.TclError:
            paths = [data]
        paths = [p[7:] if p.startswith("file://") else p for p in paths]
        self._window.after(10, lambda: self.open(paths))
        return str(getattr(event, "action", "copy"))
