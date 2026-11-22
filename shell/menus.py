"""shell/menus.py - the menu bar.  Tool menus come from core/tools.py, so they
are always complete; every command calls back into the window."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core import APP_NAME
from core.skins import SKINS
from core.tools import Category, ToolId, sidebar
from tabs.kit import labels


@dataclass(frozen=True, slots=True)
class MenuActions:
    """What the menu items do (all provided by the window)."""

    show: Callable[[ToolId], object]
    open_files: Callable[[], object]
    open_output_folder: Callable[[], object]
    quit: Callable[[], object]
    set_theme: Callable[[str], object]
    scroll_sidebar_top: Callable[[], object]
    expand_sidebar: Callable[[bool], object]
    shortcuts: Callable[[], object]
    system_info: Callable[[], object]
    check_updates: Callable[[], object]
    open_logs: Callable[[], object]
    about: Callable[[], object]


@dataclass(slots=True)
class MenuBar:
    bar: tk.Menu
    recent: tk.Menu
    theme: tk.StringVar


def build(
    window: tk.Tk,
    skin: dict[str, Any],
    actions: MenuActions,
    *,
    debug_mode: tk.BooleanVar,
    theme: str,
) -> MenuBar:
    def menu(parent: tk.Menu) -> tk.Menu:
        return tk.Menu(
            parent,
            tearoff=0,
            bg=skin["panel"],
            fg=skin["fg"],
            activebackground=skin["accent"],
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
        )

    bar = tk.Menu(
        window,
        bg=skin["panel"],
        fg=skin["fg"],
        activebackground=skin["accent"],
        activeforeground="#FFFFFF",
        relief="flat",
        bd=0,
    )
    window.config(menu=bar)

    file = menu(bar)
    bar.add_cascade(label="File", menu=file)
    file.add_command(
        label="Start - what do you want to do?", command=lambda: actions.show(ToolId.START)
    )
    file.add_command(label="Home - all tools", command=lambda: actions.show(ToolId.HOME))
    file.add_separator()
    file.add_command(label="Open video file…", command=actions.open_files, accelerator="Ctrl+O")
    file.add_separator()
    recent = menu(file)
    file.add_cascade(label="Recent Files", menu=recent)
    file.add_separator()
    file.add_command(label="Open output folder…", command=actions.open_output_folder)
    file.add_separator()
    file.add_command(label="Exit", command=actions.quit, accelerator="Alt+F4")

    edit = menu(bar)
    bar.add_cascade(label="Edit", menu=edit)
    edit.add_command(label="Quick Settings", command=lambda: actions.show(ToolId.SETTINGS))
    edit.add_command(
        label="Advanced Settings", command=lambda: actions.show(ToolId.ADVANCED_SETTINGS)
    )
    edit.add_separator()
    edit.add_checkbutton(label="Debug mode", variable=debug_mode)

    for category in Category:
        if category in (Category.PINNED, Category.HIDDEN):
            continue
        tools = menu(bar)
        bar.add_cascade(label=labels.section(category).title(), menu=tools)
        for spec in sidebar(category):
            tools.add_command(label=labels.tool(spec.id), command=lambda t=spec.id: actions.show(t))

    view = menu(bar)
    bar.add_cascade(label="View", menu=view)
    themes = menu(view)
    view.add_cascade(label="Theme", menu=themes)
    theme_var = tk.StringVar(master=window, value=theme)
    for name in SKINS:
        themes.add_radiobutton(
            label=name, variable=theme_var, value=name, command=lambda n=name: actions.set_theme(n)
        )
    view.add_separator()
    view.add_command(label="Jump to top of sidebar", command=actions.scroll_sidebar_top)
    view.add_command(label="Expand all categories", command=lambda: actions.expand_sidebar(True))
    view.add_command(label="Collapse all categories", command=lambda: actions.expand_sidebar(False))

    help_ = menu(bar)
    bar.add_cascade(label="Help", menu=help_)
    help_.add_command(label="Keyboard Shortcuts", command=actions.shortcuts)
    help_.add_command(label="System Info", command=actions.system_info)
    help_.add_command(label="Check for tool updates", command=actions.check_updates)
    help_.add_command(label="Open log folder", command=actions.open_logs)
    help_.add_separator()
    help_.add_command(label=f"About {APP_NAME}", command=actions.about)
    return MenuBar(bar, recent, theme_var)
