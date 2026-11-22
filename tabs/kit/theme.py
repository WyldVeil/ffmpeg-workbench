"""
tabs/kit/theme.py  -  applying a colour theme (core/skins.py) to the window.

Live switching re-colours the sidebar, the pages, ttk styles and the shared
CLR palette; the Mystical theme adds a slow star field behind the window.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import NamedTuple, cast

from core.scaling import px
from core.skins import UI_FONT, Skin, get_skin, load_skin_name
from tabs.kit.stars import start_stars, stop_stars

logger = logging.getLogger("crossfader.kit.theme")

# ── Global tk option cascading ────────────────────────────────────────────────


class _Colours(NamedTuple):
    c: str  # content background
    fg: str  # text
    i_bg: str  # input background
    i_fg: str  # input text
    bdr: str  # borders
    pnl: str  # panels
    acc: str  # accent
    fdm: str = ""  # dim text


def _window_options(skin: Skin, k: _Colours) -> dict[str, object]:
    return {
        # Frame
        "*Background": k.c,
        "*Foreground": k.fg,
        "*Font": f"{{{UI_FONT}}} 10",
        "*activeBackground": k.pnl,
        "*activeForeground": k.fg,
        "*disabledForeground": k.bdr,
        # Label
        "*Label.Background": k.c,
        "*Label.Foreground": k.fg,
        "*Label.BorderWidth": "0",
        # LabelFrame
        "*LabelFrame.Background": k.c,
        "*LabelFrame.Foreground": skin["fgdim"],
        "*LabelFrame.Relief": "solid",
        "*LabelFrame.BorderWidth": px(1),
        "*LabelFrame.Font": f"{{{UI_FONT}}} 9 bold",
        # Canvas
        "*Canvas.Background": k.c,
        "*Canvas.HighlightThickness": "0",
        # Menu
        "*Menu.Background": k.pnl,
        "*Menu.Foreground": k.fg,
        "*Menu.activeBackground": k.acc,
        "*Menu.activeForeground": "#FFFFFF",
        "*Menu.Relief": "flat",
        "*Menu.BorderWidth": px(1),
        "*Menu.TearOff": "0",
    }


def _inputs_options(skin: Skin, k: _Colours) -> dict[str, object]:
    return {
        # Entry
        "*Entry.Background": k.i_bg,
        "*Entry.Foreground": k.i_fg,
        "*Entry.insertBackground": k.i_fg,
        "*Entry.selectBackground": k.acc,
        "*Entry.selectForeground": "#FFFFFF",
        "*Entry.Relief": "flat",
        "*Entry.BorderWidth": px(1),
        "*Entry.HighlightThickness": "0",
        # Spinbox
        "*Spinbox.Background": k.i_bg,
        "*Spinbox.Foreground": k.i_fg,
        "*Spinbox.insertBackground": k.i_fg,
        "*Spinbox.selectBackground": k.acc,
        "*Spinbox.Relief": "flat",
        "*Spinbox.buttonBackground": k.pnl,
        "*Spinbox.HighlightThickness": "0",
        # Listbox
        "*Listbox.Background": k.i_bg,
        "*Listbox.Foreground": k.i_fg,
        "*Listbox.selectBackground": k.acc,
        "*Listbox.selectForeground": "#FFFFFF",
        "*Listbox.Relief": "flat",
        "*Listbox.BorderWidth": "0",
        "*Listbox.HighlightThickness": "0",
        # Text
        "*Text.Background": skin["console_bg"],
        "*Text.Foreground": skin["console_fg"],
        "*Text.insertBackground": skin["console_fg"],
        "*Text.selectBackground": k.acc,
        "*Text.Relief": "flat",
        "*Text.BorderWidth": "0",
        "*Text.HighlightThickness": "0",
    }


def _buttons_options(skin: Skin, k: _Colours) -> dict[str, object]:
    return {
        # Button
        "*Button.Background": k.pnl,
        "*Button.Foreground": k.fg,
        "*Button.activeBackground": skin["sb_hover"],
        "*Button.activeForeground": "#FFFFFF",
        "*Button.Relief": "flat",
        "*Button.BorderWidth": "0",
        "*Button.Cursor": "hand2",
        "*Button.HighlightThickness": "0",
        # Checkbutton
        "*Checkbutton.Background": k.c,
        "*Checkbutton.Foreground": k.fg,
        "*Checkbutton.selectColor": k.pnl,
        "*Checkbutton.activeBackground": k.c,
        "*Checkbutton.activeForeground": k.acc,
        "*Checkbutton.HighlightThickness": "0",
        # Radiobutton
        "*Radiobutton.Background": k.c,
        "*Radiobutton.Foreground": k.fg,
        "*Radiobutton.selectColor": k.acc,
        "*Radiobutton.activeBackground": k.c,
        "*Radiobutton.activeForeground": k.acc,
        "*Radiobutton.HighlightThickness": "0",
    }


def _sliders_options(skin: Skin, k: _Colours) -> dict[str, object]:
    return {
        # Scale
        # trough must be clearly lighter for the slider to show at all.
        "*Scale.Background": k.c,
        "*Scale.Foreground": k.fg,
        # option *names* are case-sensitive ("troughColor"); its class is
        # "Background", so without this the trough took the background colour
        "*Scale.troughColor": _blend(k.bdr, k.fg, 0.35),
        "*Scale.activeBackground": k.acc,
        "*Scale.SliderRelief": "flat",
        "*Scale.HighlightThickness": "0",
        "*Scale.BorderWidth": "0",
        # Tk's own default sizes are in pixels: grow them on HiDPI screens
        "*Scale.Width": px(15),
        "*Scale.SliderLength": px(30),
        "*Scale.Length": px(100),
        "*Scrollbar.Width": px(11),
        **{f"*{cls}.InsertWidth": px(2) for cls in ("Entry", "Spinbox", "Text")},
    }


def option_defaults(skin: Skin) -> dict[str, object]:
    """Tk option-database defaults that give every classic Tk widget the skin's
    colours (explicit widget settings still win)."""
    colours = _Colours(
        skin["content"],
        skin["fg"],
        skin.get("input_bg", "#222222"),
        skin.get("input_fg", "#E2E2E2"),
        skin.get("border", "#2C2C2C"),
        skin["panel"],
        skin["accent"],
    )
    return {
        **_window_options(skin, colours),
        **_inputs_options(skin, colours),
        **_buttons_options(skin, colours),
        **_sliders_options(skin, colours),
    }


def apply_default_options(app: tk.Tk, skin: Skin) -> None:
    """
    Set tk option_add defaults so EVERY tk widget (in all the tools) inherits
    the skin's colours without touching individual tab files.
    Called at startup BEFORE tabs are created, and again on skin switch.
    """
    try:
        app.option_clear()
        # option_add's stub leaves the value untyped: give it a typed name once
        option_add = app.option_add  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        add = cast("Callable[[str, object, str], None]", option_add)
        for pattern, value in option_defaults(skin).items():
            add(pattern, value, "widgetDefault")  # lowest priority
    except Exception:
        logger.debug("apply_default_options: best-effort step failed", exc_info=True)


# ── Main entry point ─────────────────────────────────────────────────────────


def _blend(a: str, b: str, amount: float) -> str:
    """Colour *a* moved *amount* (0..1) of the way towards *b* (#rrggbb)."""
    try:
        ca = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
        cb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    except (ValueError, IndexError, TypeError):
        return a
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(ca, cb, strict=False))


def apply_skin(app: tk.Tk, *, sidebar: tk.Frame, content: tk.Frame) -> Skin:
    """Apply the saved theme to the window (safe to repeat: live switching).
    Returns the palette; the shell's own widgets recolour themselves from it."""
    skin = get_skin(load_skin_name())
    apply_default_options(app, skin)  # options for every widget made from now on
    import tabs.base_tab as bt  # the palette pages read when they build

    bt.CLR.update(
        {
            "bg": skin["content"],
            "panel": skin["panel"],
            "accent": skin["accent"],
            "fg": skin["fg"],
            "fgdim": skin["fgdim"],
            "green": skin["green"],
            "red": skin["red"],
            "orange": skin["orange"],
            "pink": skin["pink"],
            "console_bg": skin["console_bg"],
            "console_fg": skin["console_fg"],
            "border": skin.get("border", "#2C2C2C"),
            "input_bg": skin.get("input_bg", "#1E1E1E"),
            "input_fg": skin.get("input_fg", "#E2E2E2"),
        }
    )
    _recolour_frame(sidebar, skin["sb_bg"])
    content.config(bg=skin["content"])
    _recolour_widget_tree(content, skin)
    apply_ttk_styles(skin)
    if skin.get("stars"):
        start_stars(app, skin)
    else:
        stop_stars()
    return skin


def apply_ttk_styles(skin: Skin) -> None:
    """Apply comprehensive ttk styling."""
    try:
        style = ttk.Style()
        k = _Colours(
            c=skin["content"],
            fg=skin["fg"],
            i_bg=skin.get("input_bg", "#1E1E1E"),
            i_fg=skin.get("input_fg", "#E2E2E2"),
            bdr=skin.get("border", "#2C2C2C"),
            pnl=skin["panel"],
            acc=skin["accent"],
            fdm=skin["fgdim"],
        )
        if tk.TkVersion < 9:
            # Tk 8.6's clam theme sizes its arrows, sliders and check marks in
            # fixed pixels (Tk 9 already gives them in points, so they scale).
            style.configure(
                ".",
                arrowsize=px(14),
                sliderlength=px(30),
                indicatorsize=px(10),
                indicatormargin=px(1, 1, 4, 1),
            )
        _style_surfaces(style, skin, k)
        _style_inputs(style, k)
        _style_lists(style, k)
    except Exception:
        logger.debug("apply_ttk_styles: best-effort step failed", exc_info=True)


def _style_surfaces(style: ttk.Style, skin: Skin, k: _Colours) -> None:
    """Frames, labels, group boxes, scrollbars and notebook tabs."""
    style.configure("TFrame", background=k.c)
    style.configure("TLabel", background=k.c, foreground=k.fg, font=(UI_FONT, 10))
    style.configure("TCheckbutton", background=k.c, foreground=k.fg, font=(UI_FONT, 10))
    style.configure("TRadiobutton", background=k.c, foreground=k.fg, font=(UI_FONT, 10))

    # LabelFrame - clear bordered panel
    style.configure(
        "TLabelframe", background=k.c, bordercolor=k.bdr, relief="solid", borderwidth=px(1)
    )
    style.configure(
        "TLabelframe.Label", background=k.c, foreground=k.fdm, font=(UI_FONT, 9, "bold")
    )

    # Scrollbar
    style.configure(
        "TScrollbar",
        background=k.pnl,
        troughcolor=skin["sb_bg"],
        arrowcolor=k.fdm,
        borderwidth=0,
        relief="flat",
        gripcount=0,
    )
    style.map("TScrollbar", background=[("active", k.bdr), ("!active", k.pnl)])

    # Notebook
    style.configure("TNotebook", background=k.pnl, tabmargins=px(0, 2, 0, 0))
    style.configure("TNotebook.Tab", padding=px(14, 8), font=(UI_FONT, 9))
    style.map(
        "TNotebook.Tab",
        background=[("selected", skin["sb_active"]), ("!selected", skin["sb_bg"])],
        foreground=[("selected", k.acc), ("!selected", skin["sb_btn"])],
    )


def _style_inputs(style: ttk.Style, k: _Colours) -> None:
    """Entries and drop-downs."""
    # Entry
    style.configure(
        "TEntry",
        fieldbackground=k.i_bg,
        foreground=k.i_fg,
        insertcolor=k.i_fg,
        borderwidth=px(1),
        relief="flat",
        font=(UI_FONT, 10),
        padding=px(4),
    )

    # Combobox
    style.configure(
        "TCombobox",
        fieldbackground=k.i_bg,
        foreground=k.i_fg,
        selectbackground=k.acc,
        selectforeground="#FFFFFF",
        arrowcolor=k.fdm,
        borderwidth=px(1),
        relief="flat",
        font=(UI_FONT, 10),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", k.i_bg)],
        foreground=[("readonly", k.i_fg)],
        selectbackground=[("readonly", k.acc)],
    )


def _style_lists(style: ttk.Style, k: _Colours) -> None:
    """Tree lists, progress bars, and check/radio buttons under the mouse."""
    # Tree lists (queue, batch lists, metadata...)
    style.configure(
        "Treeview",
        background=k.i_bg,
        fieldbackground=k.i_bg,
        foreground=k.i_fg,
        borderwidth=0,
        font=(UI_FONT, 10),
        rowheight=px(24),
    )
    style.map("Treeview", background=[("selected", k.acc)], foreground=[("selected", "#FFFFFF")])
    style.configure(
        "Treeview.Heading",
        background=k.pnl,
        foreground=k.fg,
        relief="flat",
        font=(UI_FONT, 9, "bold"),
    )
    style.map("Treeview.Heading", background=[("active", k.bdr)])

    # Progressbar
    style.configure(
        "TProgressbar", background=k.acc, troughcolor=k.bdr, borderwidth=0, thickness=px(4)
    )

    # Checkbutton + Radiobutton maps
    style.map("TCheckbutton", background=[("active", k.c)], foreground=[("active", k.acc)])
    style.map("TRadiobutton", background=[("active", k.c)], foreground=[("active", k.acc)])


def _configure(widget: tk.Misc, **options: str) -> None:
    """``widget.configure(**options)`` for any Tk widget class (Misc doesn't declare the options)."""
    widget.tk.call(str(widget), "configure", *[x for k, v in options.items() for x in (f"-{k}", v)])


def _recolour_frame(widget: tk.Misc, bg_col: str) -> None:
    """Recursively set bg on Frame/Label/Checkbutton sidebar children."""
    try:
        cls = widget.winfo_class()
        if cls in ("Frame", "Label", "Checkbutton"):
            _configure(widget, bg=bg_col)
    except Exception:
        logger.debug("_recolour_frame: best-effort step failed", exc_info=True)
    try:
        for child in widget.winfo_children():
            _recolour_frame(child, bg_col)
    except Exception:
        logger.debug("_recolour_frame: best-effort step failed", exc_info=True)


def _recolour_widget_tree(widget: tk.Misc, skin: Skin) -> None:
    """
    Recursively update background colors in the content area for live skin switch.
    Only updates widgets that don't have explicit color overrides (checks for
    common surface color patterns to avoid incorrectly recoloring accent buttons).
    """
    try:
        cls = widget.winfo_class()
        c = skin["content"]

        if cls == "Frame":
            with contextlib.suppress(tk.TclError):
                _configure(widget, bg=c)
        elif cls == "Label":
            with contextlib.suppress(tk.TclError):
                _configure(widget, bg=c, fg=skin["fg"])
        elif cls == "LabelFrame":
            with contextlib.suppress(tk.TclError):
                _configure(widget, bg=c, fg=skin["fgdim"])

        for child in widget.winfo_children():
            _recolour_widget_tree(child, skin)
    except Exception:
        logger.debug("_recolour_widget_tree: best-effort step failed", exc_info=True)
