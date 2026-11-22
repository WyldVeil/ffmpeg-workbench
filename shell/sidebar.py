"""
shell/sidebar.py - the tool list down the left of the window.

The wordmark, the pinned All-in-One Builder, a search box, then one
collapsible section per category (core/tools.py), each tool a button.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import ttk
from typing import Any

from core import APP_VERSION
from core.i18n import t
from core.paths import resource_path
from core.scaling import px
from core.skins import UI_FONT
from core.tools import Category, ToolId, sidebar
from shell.wheel import yview
from tabs.kit import labels

WIDTH = 272  # design pixels
_SEARCH_DELAY_MS = 80


@dataclass(slots=True)
class _Section:
    header: tk.Frame
    items: tk.Frame
    arrow: tk.Label
    expanded: bool = True


class Sidebar(tk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        skin: dict[str, Any],
        *,
        on_select: Callable[[ToolId], object],
        debug_mode: tk.BooleanVar,
    ) -> None:
        super().__init__(master, bg=skin["sb_bg"], width=px(WIDTH))
        self.pack_propagate(False)
        self.skin = skin
        self._on_select = on_select
        self.buttons: dict[ToolId, tk.Button] = {}
        self.indicators: dict[ToolId, tk.Frame] = {}
        self._rows: list[tuple[str, tk.Frame]] = []  # (search text, row) per tool
        self._sections: dict[Category, _Section] = {}
        self._active: ToolId | None = None
        self._search_job: str | None = None
        self._build_header()
        self._build_list()
        self._build_footer(debug_mode)

    # ── Building ──────────────────────────────────────────────────────────────
    def _build_header(self) -> None:
        skin, bg, border = self.skin, self.skin["sb_bg"], self.skin.get("border", "#2C2C2C")
        top = tk.Frame(self, bg=bg)
        top.pack(side="top", fill="x")
        logo = tk.Frame(top, bg=bg)
        logo.pack(fill="x", padx=px(16), pady=px(20, 0))
        badge = tk.Canvas(logo, width=px(38), height=px(34), bg=bg, highlightthickness=0)
        badge.pack(side="left", padx=px(0, 12))
        try:
            size = min(
                (32, 48, 64, 128), key=lambda n: (abs(n - px(32)), n)
            )  # sharpest at this scale
            self._badge = tk.PhotoImage(
                master=self, file=resource_path("assets", "icons", f"app_{size}.png")
            )
            badge.create_image(px(19), px(17), image=self._badge)  # pyright: ignore[reportUnknownMemberType] - stub gap
        except tk.TclError:
            badge.create_rectangle(0, 0, px(38), px(34), fill=skin["accent"], outline="")
        names = tk.Frame(logo, bg=bg)
        names.pack(side="left", anchor="w")
        tk.Label(names, text="WYLDVEIL", font=(UI_FONT, 11, "bold"), bg=bg, fg="#CCCCCC").pack(
            anchor="w"
        )
        tk.Label(names, text=t("app.subtitle"), font=(UI_FONT, 8), bg=bg, fg="#424242").pack(
            anchor="w"
        )
        tk.Frame(top, bg=border, height=px(1)).pack(fill="x", padx=px(14), pady=px(16, 0))

        pinned = tk.Frame(top, bg=bg)
        pinned.pack(fill="x", padx=px(6), pady=px(8, 5))
        self._add_button(
            pinned,
            ToolId.ALL_IN_ONE_BUILDER,
            f"   ⚡  {t('tab.all_in_one')}",
            font=(UI_FONT, 10, "bold"),
            fg=skin["accent"],
            pady=px(5),
        )
        tk.Frame(top, bg=border, height=px(1)).pack(fill="x", padx=px(14), pady=px(4, 0))

        box = tk.Frame(top, bg=bg)
        box.pack(fill="x", padx=px(12), pady=px(10, 8))
        field_bg = skin.get("input_bg", "#1E1E1E")
        edge = tk.Frame(box, bg=border)
        edge.pack(fill="x")
        inner = tk.Frame(edge, bg=field_bg)
        inner.pack(fill="x", padx=px(1), pady=px(1))
        tk.Label(inner, text="⌕", bg=field_bg, fg="#484848", font=(UI_FONT, 11)).pack(
            side="left", padx=px(7, 0), pady=px(3)
        )
        self.search_var = tk.StringVar()
        self._search = tk.Entry(
            inner,
            textvariable=self.search_var,
            bg=field_bg,
            fg="#808080",
            insertbackground=skin["accent"],
            relief="flat",
            bd=0,
            font=(UI_FONT, 9),
            highlightthickness=0,
        )
        self._search.pack(side="left", fill="x", expand=True, padx=px(4, 6), pady=px(4))
        self._placeholder = t("app.search_placeholder")
        self._search.insert(0, self._placeholder)
        self._search.bind("<FocusIn>", self._search_focus)
        self._search.bind("<FocusOut>", self._search_blur)
        self.search_var.trace_add("write", self._search_changed)
        self._clear = tk.Label(
            inner, text="✕", bg=field_bg, fg="#484848", font=(UI_FONT, 8), cursor="hand2"
        )
        self._clear.bind("<Button-1>", lambda _e: self.clear_search())
        tk.Frame(top, bg=border, height=px(1)).pack(fill="x", padx=px(14))

    def _build_list(self) -> None:
        bg = self.skin["sb_bg"]
        holder = tk.Frame(self, bg=bg)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, bg=bg, highlightthickness=0, yscrollincrement=px(20))
        bar = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType] - stub gap
        self.inner = tk.Frame(self.canvas, bg=bg)
        window = self.canvas.create_window(
            (0, 0), window=self.inner, anchor="nw", width=px(WIDTH - 18)
        )
        # the list is exactly as wide as the visible canvas, whatever the scrollbar's width
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width), add=True
        )
        self.inner.bind("<Configure>", lambda _e: self._update_scrollregion())
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.unbind_class("Canvas", "<<FocusIn>>")
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        for category in Category:
            specs = sidebar(category)
            if specs and category not in (Category.PINNED, Category.HIDDEN):
                self._build_section(category, [s.id for s in specs])
        tk.Frame(self.inner, bg=bg, height=px(20)).pack()
        self.after_idle(self._update_scrollregion)  # once laid out: bbox() is empty before

    def _build_section(self, category: Category, tools: list[ToolId]) -> None:
        skin, bg = self.skin, self.skin["sb_bg"]
        header = tk.Frame(self.inner, bg=bg, cursor="hand2")
        header.pack(fill="x", pady=px(10, 0))
        arrow = tk.Label(header, text="▾", bg=bg, fg="#484848", font=(UI_FONT, 7), cursor="hand2")
        arrow.pack(side="left", padx=px(14, 0), pady=px(2))
        title = tk.Label(
            header,
            text=labels.section(category),
            font=(UI_FONT, 7, "bold"),
            bg=bg,
            fg=skin["sb_cat"],
            anchor="w",
            cursor="hand2",
        )
        title.pack(side="left", padx=px(5, 4), pady=px(2), fill="x", expand=True)
        tk.Label(
            header,
            text=str(len(tools)),
            bg=skin.get("input_bg", "#1E1E1E"),
            fg="#4A4A4A",
            font=(UI_FONT, 7),
            padx=px(5),
            pady=px(1),
        ).pack(side="right", padx=px(0, 12), pady=px(4))
        items = tk.Frame(self.inner, bg=bg)
        items.pack(fill="x")
        section = self._sections[category] = _Section(header, items, arrow)
        for w in (header, title, arrow):
            w.bind(
                "<Button-1>", lambda _e, c=category: self.expand(c, not self._sections[c].expanded)
            )
        for tool in tools:
            row = tk.Frame(section.items, bg=bg)
            row.pack(fill="x", padx=px(6), pady=0)
            label = labels.tool(tool)
            self._add_button(
                row, tool, f"    {label}", font=(UI_FONT, 9), fg=skin["sb_btn"], pady=px(3)
            )
            self._rows.append(
                (f"{tool.value} {labels.tool(tool).casefold()} {label.casefold()}", row)
            )

    def _add_button(
        self,
        row: tk.Frame,
        tool: ToolId,
        text: str,
        *,
        font: tuple[str, int] | tuple[str, int, str],
        fg: str,
        pady: int,
    ) -> None:
        skin, bg = self.skin, self.skin["sb_bg"]
        mark = tk.Frame(row, bg=bg, width=px(3))
        mark.pack(side="left", fill="y")
        mark.pack_propagate(False)
        btn = tk.Button(
            row,
            text=text,
            font=font,
            bg=bg,
            fg=fg,
            relief="flat",
            anchor="w",
            cursor="hand2",
            activebackground=skin["sb_hover"],
            activeforeground="#FFFFFF",
            command=lambda: self._on_select(tool),
            bd=0,
            padx=0,
            pady=pady,
        )
        btn.pack(side="left", fill="x", expand=True)
        btn.bind("<Enter>", lambda _e: btn.config(bg=self.skin["sb_hover"]))
        btn.bind(
            "<Leave>",
            lambda _e: btn.config(
                bg=self.skin["sb_active"] if self._active is tool else self.skin["sb_bg"]
            ),
        )
        self.buttons[tool] = btn
        self.indicators[tool] = mark

    def _build_footer(self, debug_mode: tk.BooleanVar) -> None:
        bg, border = self.skin["sb_bg"], self.skin.get("border", "#2C2C2C")
        footer = tk.Frame(self, bg=bg)
        footer.pack(side="bottom", fill="x")
        tk.Frame(footer, bg=border, height=px(1)).pack(fill="x")
        inner = tk.Frame(footer, bg=bg)
        inner.pack(fill="x", padx=px(14), pady=px(7))
        tk.Label(inner, text=f"v{APP_VERSION}", bg=bg, fg="#343434", font=(UI_FONT, 8)).pack(
            side="left"
        )
        tk.Checkbutton(
            inner,
            text="DEBUG",
            variable=debug_mode,
            bg=bg,
            fg="#343434",
            selectcolor=bg,
            activebackground=bg,
            font=(UI_FONT, 8),
            cursor="hand2",
        ).pack(side="right")

    # ── The active tool ───────────────────────────────────────────────────────
    def set_active(self, tool: ToolId | None) -> None:
        skin = self.skin
        previous = self._active
        if previous is not None and previous in self.buttons:
            self.buttons[previous].config(bg=skin["sb_bg"], fg=skin["sb_btn"], font=(UI_FONT, 9))
            self.indicators[previous].config(bg=skin["sb_bg"])
        self._active = tool
        if tool is not None and tool in self.buttons:
            self.buttons[tool].config(
                bg=skin["sb_active"], fg=skin["accent"], font=(UI_FONT, 9, "bold")
            )
            self.indicators[tool].config(bg=skin["accent"])
            self.scroll_to(tool)

    def scroll_to(self, tool: ToolId) -> None:
        """Bring *tool*'s button into view if it is scrolled out of sight."""
        btn = self.buttons.get(tool)
        box = self.canvas.bbox("all")
        if btn is None or not box or not box[3]:
            return
        self.canvas.update_idletasks()
        total = box[3]
        frac = (btn.winfo_rooty() - self.inner.winfo_rooty()) / max(total, 1)
        top, bottom = yview(self.canvas)
        if frac < top:
            self.canvas.yview_moveto(max(0.0, frac - 0.05))
        elif frac + 30 / total > bottom:
            self.canvas.yview_moveto(min(1.0, frac - self.canvas.winfo_height() / total + 0.05))

    def scroll_top(self) -> None:
        self.canvas.yview_moveto(0)

    # ── Sections ──────────────────────────────────────────────────────────────
    def expand(self, category: Category, expanded: bool) -> None:
        section = self._sections[category]
        section.expanded = expanded
        section.arrow.config(text="▾" if expanded else "▸")
        if expanded:
            section.items.pack(fill="x", after=section.header)
        else:
            section.items.pack_forget()
        self._update_scrollregion()

    def expand_all(self, expanded: bool) -> None:
        for category in self._sections:
            self.expand(category, expanded)

    def _update_scrollregion(self) -> None:
        self.inner.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    # ── Search ────────────────────────────────────────────────────────────────
    def _search_focus(self, _event: object) -> None:
        if self._search.get() == self._placeholder:
            self._search.delete(0, tk.END)
            self._search.config(fg=self.skin.get("input_fg", "#E2E2E2"))

    def _search_blur(self, _event: object) -> None:
        if not self._search.get():
            self._search.insert(0, self._placeholder)
            self._search.config(fg="#808080")
            self._clear.pack_forget()

    def clear_search(self) -> None:
        self._search.delete(0, tk.END)
        self._search_blur(None)
        self.filter()

    def _search_changed(self, _name: str, _index: str, _mode: str) -> None:
        self._search_soon()

    def _search_soon(self) -> None:
        if self._search_job is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(_SEARCH_DELAY_MS, self.filter)

    def filter(self) -> None:
        """Show only the tools whose names contain what was typed."""
        self._search_job = None
        query = self.search_var.get().strip().casefold()
        searching = bool(query) and query != self._placeholder.casefold()
        if searching:
            self._clear.pack(side="right", padx=px(0, 6))
        else:
            self._clear.pack_forget()
        shown: set[tk.Misc] = set()
        for text, row in self._rows:
            if not searching or query in text:
                row.pack(fill="x", padx=px(6), pady=0)
                shown.add(row.master)
            else:
                row.pack_forget()
        for section in self._sections.values():
            visible = section.items in shown if searching else True
            if visible:
                section.header.pack(fill="x", pady=px(10, 0))
            else:
                section.header.pack_forget()
            if visible and (searching or section.expanded):
                section.items.pack(fill="x", after=section.header)
            else:
                section.items.pack_forget()
        self._update_scrollregion()

    # ── Theme ─────────────────────────────────────────────────────────────────
    def recolour(self, skin: dict[str, Any]) -> None:
        self.skin = skin
        for tool, btn in self.buttons.items():
            btn.config(
                bg=skin["sb_bg"],
                fg=skin["accent"] if tool is ToolId.ALL_IN_ONE_BUILDER else skin["sb_btn"],
                activebackground=skin["sb_hover"],
                activeforeground="#FFFFFF",
            )
        for mark in self.indicators.values():
            mark.config(bg=skin["sb_bg"])
        self.set_active(self._active)
