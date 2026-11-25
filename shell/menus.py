"""shell/menus.py - the menu bar.  Tool menus come from core/tools.py, so they
are always complete; every command calls back into the window."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass

from core import APP_NAME
from core.i18n import t
from core.skins import SKINS, Skin
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


def _menu(parent: tk.Misc, skin: Skin) -> tk.Menu:
    """A menu (or the menu bar) in the skin's colours."""
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


def _file_menu(bar: tk.Menu, skin: Skin, actions: MenuActions) -> tk.Menu:
    """File (returns its Recent Files menu, filled later)."""
    file = _menu(bar, skin)
    bar.add_cascade(label=t("menu.file"), menu=file)
    file.add_command(
        label=t("menu.start_what_do_you_want_to_do"), command=lambda: actions.show(ToolId.START)
    )
    file.add_command(label=t("menu.home_all_tools"), command=lambda: actions.show(ToolId.HOME))
    file.add_separator()
    file.add_command(
        label=t("menu.open_video_file"), command=actions.open_files, accelerator="Ctrl+O"
    )
    file.add_separator()
    recent = _menu(file, skin)
    file.add_cascade(label=t("menu.recent_files"), menu=recent)
    file.add_separator()
    file.add_command(label=t("menu.open_output_folder"), command=actions.open_output_folder)
    file.add_separator()
    file.add_command(label=t("menu.exit"), command=actions.quit, accelerator="Alt+F4")
    return recent


def _edit_menu(bar: tk.Menu, skin: Skin, actions: MenuActions, debug_mode: tk.BooleanVar) -> None:
    edit = _menu(bar, skin)
    bar.add_cascade(label=t("menu.edit"), menu=edit)
    edit.add_command(label=t("menu.quick_settings"), command=lambda: actions.show(ToolId.SETTINGS))
    edit.add_command(
        label=t("tab.advanced_settings"), command=lambda: actions.show(ToolId.ADVANCED_SETTINGS)
    )
    edit.add_separator()
    edit.add_checkbutton(label=t("menu.debug_mode"), variable=debug_mode)


def _tool_menus(bar: tk.Menu, skin: Skin, actions: MenuActions) -> None:
    """One menu per sidebar category, listing its tools."""
    for category in Category:
        if category in (Category.PINNED, Category.HIDDEN):
            continue
        tools = _menu(bar, skin)
        bar.add_cascade(label=labels.menu_section(category), menu=tools)
        for spec in sidebar(category):
            tools.add_command(label=labels.tool(spec.id), command=lambda t=spec.id: actions.show(t))


def _view_menu(
    bar: tk.Menu, window: tk.Tk, skin: Skin, actions: MenuActions, theme: str
) -> tk.StringVar:
    """View (returns the chosen theme's variable)."""
    view = _menu(bar, skin)
    bar.add_cascade(label=t("menu.view"), menu=view)
    themes = _menu(view, skin)
    view.add_cascade(label=t("menu.theme"), menu=themes)
    theme_var = tk.StringVar(master=window, value=theme)
    for name in SKINS:
        themes.add_radiobutton(
            label=name, variable=theme_var, value=name, command=lambda n=name: actions.set_theme(n)
        )
    view.add_separator()
    view.add_command(label=t("menu.jump_to_top_of_sidebar"), command=actions.scroll_sidebar_top)
    view.add_command(
        label=t("menu.expand_all_categories"), command=lambda: actions.expand_sidebar(True)
    )
    view.add_command(
        label=t("menu.collapse_all_categories"), command=lambda: actions.expand_sidebar(False)
    )
    return theme_var


def _help_menu(bar: tk.Menu, skin: Skin, actions: MenuActions) -> None:
    help_ = _menu(bar, skin)
    bar.add_cascade(label=t("menu.help"), menu=help_)
    help_.add_command(label=t("shell_app.keyboard_shortcuts"), command=actions.shortcuts)
    help_.add_command(label=t("adv.tab.system"), command=actions.system_info)
    help_.add_command(label=t("menu.check_for_tool_updates"), command=actions.check_updates)
    help_.add_command(label=t("crash.open_log_folder"), command=actions.open_logs)
    help_.add_separator()
    help_.add_command(label=t("shell_app.about_app_name", app_name=APP_NAME), command=actions.about)


def build(
    window: tk.Tk,
    skin: Skin,
    actions: MenuActions,
    *,
    debug_mode: tk.BooleanVar,
    theme: str,
) -> MenuBar:
    bar = _menu(window, skin)
    window.config(menu=bar)
    recent = _file_menu(bar, skin, actions)
    _edit_menu(bar, skin, actions, debug_mode)
    _tool_menus(bar, skin, actions)
    theme_var = _view_menu(bar, window, skin, actions, theme)
    _help_menu(bar, skin, actions)
    return MenuBar(bar, recent, theme_var)
