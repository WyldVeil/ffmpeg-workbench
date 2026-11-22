"""
tabs/system/home.py  ─  Home / launch screen

A landing page that shows every tab as a clickable card, organised by the
same category structure used in the sidebar, plus a Favourites section
populated from the user's saved favourites in settings.json.

Each card has:
  • a clickable body that navigates to that tab
  • a star toggle (☆ / ★) on the right that adds/removes the tab from
    the user's Favourites list (persisted to settings.json under
    "favourite_tabs")

The tab itself is registered in HIDDEN so it never appears in the
sidebar - it's reachable as the launch page and via the persistent
"🏠 Home" button on the content header bar (see main.py).
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable, Sequence

from core import settings as _settings
from core import tools
from core.scaling import px
from core.tools import Category, ToolId
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.kit import labels

logger = logging.getLogger("crossfader.system.home")

#: Shown at the top, one click from the launch screen: (tool, card text).
_QUICK: tuple[tuple[ToolId, str], ...] = (
    (ToolId.ALL_IN_ONE_BUILDER, "⚡ " + tools.spec(ToolId.ALL_IN_ONE_BUILDER).name),
    (ToolId.ADVANCED_SETTINGS, "⚙  Advanced Settings"),
)
Card = ToolId | tuple[ToolId, str]


class HomeTab(BaseTab):
    """Launch / overview screen with category grids and favourites."""

    # Card layout constants
    _CARD_WIDTH = 200
    _CARD_HEIGHT = 56
    _GRID_PAD_X = 8
    _GRID_PAD_Y = 8
    _MIN_COLS = 2
    _MAX_COLS = 6

    def __init__(self, parent):
        super().__init__(parent)
        self._navigate: Callable[[ToolId], object] | None = None
        self._sections_frame = None
        self._fav_section_frame = None
        self._build_ui()
        # Re-render when window is resized so the grid reflows.
        self.bind("<Configure>", self._on_resize, add=True)

    # ── Public hook used by main.py ──────────────────────────────────────────
    def set_navigator(self, navigate: Callable[[ToolId], object]) -> None:
        """*navigate(tool)* opens a tool (set by the window)."""
        self._navigate = navigate

    def _go(self, tool: ToolId) -> None:
        if self._navigate is not None:
            self._navigate(tool)

    # ── Favourites (stored as tool ids; names from older versions still read) ─
    def _favourites(self) -> list[ToolId]:
        return tools.parse_all(_settings.FAVOURITE_TABS.get())

    def _is_fav(self, tool: ToolId) -> bool:
        return tool in self._favourites()

    def _toggle_fav(self, tool: ToolId) -> None:
        favs = self._favourites()
        favs = [f for f in favs if f is not tool] if tool in favs else [*favs, tool]
        _settings.FAVOURITE_TABS.set([f.value for f in favs])
        self._refresh()

    # ── UI build ─────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=CLR["panel"])
        hdr.pack(fill="x")
        inner = tk.Frame(hdr, bg=CLR["panel"])
        inner.pack(fill="x", padx=px(22), pady=px(16, 14))
        tk.Label(
            inner,
            text="🏠  Home",
            font=(UI_FONT, 16, "bold"),
            bg=CLR["panel"],
            fg=CLR["accent"],
            anchor="w",
        ).pack(side="top", anchor="w")
        tk.Label(
            inner,
            text="Pick a tool to get started. Star the ones you "
            "use most to pin them to your Favourites.",
            font=(UI_FONT, 9),
            bg=CLR["panel"],
            fg=CLR["fgdim"],
            anchor="w",
        ).pack(side="top", anchor="w", pady=px(2, 0))
        back = tk.Label(
            inner,
            text="🧭  Not sure which tool? Start: What do you want to do?  ›",
            font=(UI_FONT, 10, "bold"),
            bg=CLR["panel"],
            fg=CLR["accent"],
            anchor="w",
            cursor="hand2",
        )
        back.pack(side="top", anchor="w", pady=px(8, 0))
        back.bind("<Button-1>", lambda _e: self._go(ToolId.START))
        tk.Frame(self, bg=CLR["border"], height=px(1)).pack(fill="x")

        # Scrollable content
        outer = tk.Frame(self, bg=CLR["bg"])
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=CLR["bg"], highlightthickness=0)
        from tkinter import ttk

        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body = tk.Frame(canvas, bg=CLR["bg"])
        body_win = canvas.create_window((0, 0), window=body, anchor="nw")
        self._scroll_canvas = canvas
        self._scroll_body = body

        def _on_body_configure(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))

        body.bind("<Configure>", _on_body_configure)

        def _on_canvas_configure(e):
            canvas.itemconfig(body_win, width=e.width)
            self._render_sections()

        canvas.bind("<Configure>", _on_canvas_configure)

        self._sections_frame = body
        self._render_sections()

    # ── Render / refresh ─────────────────────────────────────────────────────
    def _refresh(self):
        self._render_sections()

    def _on_resize(self, _e):
        # Debounce-ish: re-render on configure events. Cheap to redraw.
        self._render_sections()

    def _render_sections(self):
        body = self._sections_frame
        if body is None or not body.winfo_exists():
            return
        # Clear existing children
        for w in body.winfo_children():
            with contextlib.suppress(tk.TclError):
                w.destroy()

        # 1. Favourites
        favs = self._favourites()
        if favs:
            self._render_section_header(body, "⭐  Favourites", accent=CLR["orange"])
            self._render_grid(body, favs)
            tk.Frame(body, bg=CLR["bg"], height=px(10)).pack(fill="x")

        # 2. Quick tools: the All-in-One Builder and Advanced Settings
        self._render_section_header(body, "⚡  Quick Tools", accent=CLR["accent"])
        self._render_grid(body, list(_QUICK))
        tk.Frame(body, bg=CLR["bg"], height=px(10)).pack(fill="x")

        # 3. Each sidebar section (without what is already shown above)
        quick = {tool for tool, _text in _QUICK}
        for category in Category:
            if category in (Category.PINNED, Category.HIDDEN):
                continue
            visible = [s.id for s in tools.sidebar(category) if s.id not in quick]
            if not visible:
                continue
            self._render_section_header(body, category.value)
            self._render_grid(body, visible)
            tk.Frame(body, bg=CLR["bg"], height=px(10)).pack(fill="x")

    def _render_section_header(self, parent, label, accent=None):
        if accent is None:
            accent = CLR["accent"]
        row = tk.Frame(parent, bg=CLR["bg"])
        row.pack(fill="x", padx=px(20), pady=px(14, 6))
        tk.Label(
            row,
            text=label,
            font=(UI_FONT, 11, "bold"),
            bg=CLR["bg"],
            fg=accent,
            anchor="w",
        ).pack(side="left", anchor="w")
        # Thin horizontal rule under header
        rule = tk.Frame(parent, bg=CLR["border"], height=px(1))
        rule.pack(fill="x", padx=px(20))

    def _render_grid(self, parent, items: Sequence[Card]) -> None:
        """Cards for *items*: tools, or (tool, card text) pairs."""
        if not items:
            return
        grid = tk.Frame(parent, bg=CLR["bg"])
        grid.pack(fill="x", padx=px(20), pady=px(8, 0))

        # Column count based on parent width.
        try:
            avail = self._scroll_body.winfo_width() or self.winfo_width() or px(1000)
        except Exception:
            logger.debug("_render_grid: best-effort step failed", exc_info=True)
            avail = 1000
        col_w = px(self._CARD_WIDTH + 2 * self._GRID_PAD_X)
        cols = max(self._MIN_COLS, min(self._MAX_COLS, max(1, avail // col_w)))

        for i, item in enumerate(items):
            tool, display = item if isinstance(item, tuple) else (item, labels.tool(item))
            r, c = divmod(i, cols)
            self._build_card(grid, tool, display).grid(
                row=r,
                column=c,
                padx=px(self._GRID_PAD_X),
                pady=px(self._GRID_PAD_Y),
                sticky="nsew",
            )
        for c in range(cols):
            grid.grid_columnconfigure(c, weight=1, uniform="cards")

    def _build_card(self, parent: tk.Misc, tool: ToolId, display: str) -> tk.Frame:
        """A single tool card: clickable body + star toggle on the right."""
        is_fav = self._is_fav(tool)
        card = tk.Frame(
            parent,
            bg=CLR["panel"],
            highlightthickness=px(1),
            highlightbackground=CLR["border"],
            cursor="hand2",
        )
        card.config(width=px(self._CARD_WIDTH), height=px(self._CARD_HEIGHT))
        card.pack_propagate(False)

        # Tab name (left, fills space)
        name_lbl = tk.Label(
            card,
            text=display,
            font=(UI_FONT, 10, "bold"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            anchor="w",
            padx=px(12),
            cursor="hand2",
        )
        name_lbl.pack(side="left", fill="both", expand=True)

        # Star toggle (right)
        star_text = "★" if is_fav else "☆"
        star_color = CLR["orange"] if is_fav else CLR["fgdim"]
        star_lbl = tk.Label(
            card,
            text=star_text,
            font=(UI_FONT, 14),
            bg=CLR["panel"],
            fg=star_color,
            padx=px(12),
            cursor="hand2",
        )
        star_lbl.pack(side="right", fill="y")

        # Click handlers - star toggles favourite (no navigation), the rest
        # of the card body navigates.
        def on_navigate(_e: object = None) -> None:
            self._go(tool)

        def on_toggle(_e: object = None) -> str:
            self._toggle_fav(tool)
            return "break"  # prevent the click from also bubbling to body

        for w in (card, name_lbl):
            w.bind("<Button-1>", on_navigate)
        star_lbl.bind("<Button-1>", on_toggle)

        # Hover effect
        hover_bg = CLR.get("sb_hover") or "#2A2A2A"
        normal_bg = CLR["panel"]

        def on_enter(_e):
            try:
                card.config(bg=hover_bg, highlightbackground=CLR["accent"])
                name_lbl.config(bg=hover_bg)
                star_lbl.config(bg=hover_bg)
            except tk.TclError:
                pass

        def on_leave(_e):
            try:
                card.config(bg=normal_bg, highlightbackground=CLR["border"])
                name_lbl.config(bg=normal_bg)
                star_lbl.config(bg=normal_bg)
            except tk.TclError:
                pass

        for w in (card, name_lbl, star_lbl):
            w.bind("<Enter>", on_enter, add=True)
            w.bind("<Leave>", on_leave, add=True)

        return card
