"""
shell/pages.py - the tool pages: built when first opened, one on show.

A page is built from tabs/registry.py the first time it is opened, so start-up
stays fast and a tool that fails to load shows an error page instead of
taking the app down (it is retried the next time it is opened).
"""
from __future__ import annotations

import logging
import tkinter as tk
from collections.abc import Callable, Iterator
from tkinter import ttk

from core.i18n import t
from core.scaling import px
from core.skins import UI_FONT
from core.tools import ToolId, spec
from tabs.kit.contracts import Navigating, Showable
from tabs.registry import PAGES

_log = logging.getLogger("crossfader.shell")


class ErrorPage(ttk.Frame):
    """Stands in for a tool whose module or class failed to load."""

    def __init__(self, parent: tk.Misc, title: str, error: str = "") -> None:
        super().__init__(parent)
        tk.Label(self, text=f"⚠  {title}", font=(UI_FONT, 20, "bold"), fg="#C0392B").pack(pady=px(120, 10))
        msg = (f"This tool could not be loaded:\n{error}\n\nThe full details were written to the log file "
               "(Help › Open log folder).") if error else t("app.under_construction")
        tk.Label(self, text=msg, font=(UI_FONT, 11), fg="#808080", wraplength=px(700), justify="center").pack()


class PageHost:
    """Owns the pages inside *area*: builds them on demand and shows one."""

    def __init__(self, area: tk.Frame, navigate: Callable[[ToolId], object]) -> None:
        self._area = area
        self._navigate = navigate
        self._pages: dict[ToolId, tk.Widget] = {}
        self.current: ToolId | None = None

    def __contains__(self, tool: object) -> bool:
        return tool in self._pages

    def get(self, tool: ToolId) -> tk.Widget | None:
        """The page if it has been built (never builds one)."""
        return self._pages.get(tool)

    def built(self) -> Iterator[tk.Widget]:
        return iter(list(self._pages.values()))

    def build(self, tool: ToolId) -> tk.Widget:
        existing = self._pages.get(tool)
        if isinstance(existing, ErrorPage) and tool != self.current:
            existing.destroy()                          # try loading the tool again
            existing = None
        if existing is not None:
            return existing
        page: tk.Widget
        try:
            page = PAGES[tool](self._area)
        except Exception as exc:                        # a broken tool must not take the app down
            _log.exception("Could not load tool %r", tool.value)
            page = ErrorPage(self._area, spec(tool).name, f"{type(exc).__name__}: {exc}")
        if isinstance(page, Navigating):
            page.set_navigator(self._navigate)
        self._pages[tool] = page
        return page

    def show(self, tool: ToolId) -> tk.Widget:
        page = self.build(tool)
        previous = self._pages.get(self.current) if self.current is not None else None
        if previous is not None and previous is not page:
            previous.pack_forget()
        page.pack(fill="both", expand=True)
        self.current = tool
        if isinstance(page, Showable):
            try:
                page.on_show()
            except Exception:                           # a page's refresh failing is its own bug
                _log.exception("on_show failed in %s", tool.value)
        return page
