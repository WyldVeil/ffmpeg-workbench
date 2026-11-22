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
from typing import cast

from core.scaling import px
from core.skins import UI_FONT, Skin, get_skin, load_skin_name
from tabs.kit.stars import start_stars, stop_stars

logger = logging.getLogger("crossfader.kit.theme")

# ── Global tk option cascading ────────────────────────────────────────────────


def apply_default_options(app: tk.Tk, skin: Skin) -> None:
    """
    Set tk option_add defaults so EVERY tk widget (in all 67 tabs) inherits
    dark-theme colors without touching individual tab files.
    Called at startup BEFORE tabs are created, and again on skin switch.
    """
    try:
        app.option_clear()
        P = "widgetDefault"  # lowest priority - explicit widget settings override
        # option_add's stub leaves the value untyped: give it a typed name once
        option_add = app.option_add  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        add = cast("Callable[[str, object, str], None]", option_add)
        c = skin["content"]
        fg = skin["fg"]
        i_bg = skin.get("input_bg", "#222222")
        i_fg = skin.get("input_fg", "#E2E2E2")
        bdr = skin.get("border", "#2C2C2C")
        pnl = skin["panel"]
        acc = skin["accent"]

        # Frame / generic background
        add("*Background", c, P)
        add("*Foreground", fg, P)
        add("*Font", f"{{{UI_FONT}}} 10", P)
        add("*activeBackground", pnl, P)
        add("*activeForeground", fg, P)
        add("*disabledForeground", bdr, P)

        # Label
        add("*Label.Background", c, P)
        add("*Label.Foreground", fg, P)
        add("*Label.BorderWidth", "0", P)

        # LabelFrame
        add("*LabelFrame.Background", c, P)
        add("*LabelFrame.Foreground", skin["fgdim"], P)
        add("*LabelFrame.Relief", "solid", P)
        add("*LabelFrame.BorderWidth", px(1), P)
        add("*LabelFrame.Font", f"{{{UI_FONT}}} 9 bold", P)

        # Entry
        add("*Entry.Background", i_bg, P)
        add("*Entry.Foreground", i_fg, P)
        add("*Entry.insertBackground", i_fg, P)
        add("*Entry.selectBackground", acc, P)
        add("*Entry.selectForeground", "#FFFFFF", P)
        add("*Entry.Relief", "flat", P)
        add("*Entry.BorderWidth", px(1), P)
        add("*Entry.HighlightThickness", "0", P)

        # Spinbox
        add("*Spinbox.Background", i_bg, P)
        add("*Spinbox.Foreground", i_fg, P)
        add("*Spinbox.insertBackground", i_fg, P)
        add("*Spinbox.selectBackground", acc, P)
        add("*Spinbox.Relief", "flat", P)
        add("*Spinbox.buttonBackground", pnl, P)
        add("*Spinbox.HighlightThickness", "0", P)

        # Button
        add("*Button.Background", pnl, P)
        add("*Button.Foreground", fg, P)
        add("*Button.activeBackground", skin["sb_hover"], P)
        add("*Button.activeForeground", "#FFFFFF", P)
        add("*Button.Relief", "flat", P)
        add("*Button.BorderWidth", "0", P)
        add("*Button.Cursor", "hand2", P)
        add("*Button.HighlightThickness", "0", P)

        # Checkbutton
        add("*Checkbutton.Background", c, P)
        add("*Checkbutton.Foreground", fg, P)
        add("*Checkbutton.selectColor", pnl, P)
        add("*Checkbutton.activeBackground", c, P)
        add("*Checkbutton.activeForeground", acc, P)
        add("*Checkbutton.HighlightThickness", "0", P)

        # Radiobutton
        add("*Radiobutton.Background", c, P)
        add("*Radiobutton.Foreground", fg, P)
        add("*Radiobutton.selectColor", acc, P)
        add("*Radiobutton.activeBackground", c, P)
        add("*Radiobutton.activeForeground", acc, P)
        add("*Radiobutton.HighlightThickness", "0", P)

        # Scale (slider).  Tk draws the slider in the background colour, so the
        # trough must be clearly lighter for the slider to show at all.
        add("*Scale.Background", c, P)
        add("*Scale.Foreground", fg, P)
        # option *names* are case-sensitive ("troughColor"); its class is
        # "Background", so without this the trough took the background colour
        add("*Scale.troughColor", _blend(bdr, fg, 0.35), P)
        add("*Scale.activeBackground", acc, P)
        add("*Scale.SliderRelief", "flat", P)
        add("*Scale.HighlightThickness", "0", P)
        add("*Scale.BorderWidth", "0", P)
        # Tk's own default sizes are in pixels: grow them on HiDPI screens
        add("*Scale.Width", px(15), P)
        add("*Scale.SliderLength", px(30), P)
        add("*Scale.Length", px(100), P)
        add("*Scrollbar.Width", px(11), P)
        for cls in ("Entry", "Spinbox", "Text"):
            add(f"*{cls}.InsertWidth", px(2), P)

        # Listbox
        add("*Listbox.Background", i_bg, P)
        add("*Listbox.Foreground", i_fg, P)
        add("*Listbox.selectBackground", acc, P)
        add("*Listbox.selectForeground", "#FFFFFF", P)
        add("*Listbox.Relief", "flat", P)
        add("*Listbox.BorderWidth", "0", P)
        add("*Listbox.HighlightThickness", "0", P)

        # Text (used for console log areas)
        add("*Text.Background", skin["console_bg"], P)
        add("*Text.Foreground", skin["console_fg"], P)
        add("*Text.insertBackground", skin["console_fg"], P)
        add("*Text.selectBackground", acc, P)
        add("*Text.Relief", "flat", P)
        add("*Text.BorderWidth", "0", P)
        add("*Text.HighlightThickness", "0", P)

        # Canvas
        add("*Canvas.Background", c, P)
        add("*Canvas.HighlightThickness", "0", P)

        # Menu
        add("*Menu.Background", pnl, P)
        add("*Menu.Foreground", fg, P)
        add("*Menu.activeBackground", acc, P)
        add("*Menu.activeForeground", "#FFFFFF", P)
        add("*Menu.Relief", "flat", P)
        add("*Menu.BorderWidth", px(1), P)
        add("*Menu.TearOff", "0", P)

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
        c = skin["content"]
        pnl = skin["panel"]
        acc = skin["accent"]
        fg = skin["fg"]
        fdm = skin["fgdim"]
        bdr = skin.get("border", "#2C2C2C")
        i_bg = skin.get("input_bg", "#1E1E1E")
        i_fg = skin.get("input_fg", "#E2E2E2")

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
        style.configure("TFrame", background=c)
        style.configure("TLabel", background=c, foreground=fg, font=(UI_FONT, 10))
        style.configure("TCheckbutton", background=c, foreground=fg, font=(UI_FONT, 10))
        style.configure("TRadiobutton", background=c, foreground=fg, font=(UI_FONT, 10))

        # LabelFrame - clear bordered panel
        style.configure(
            "TLabelframe", background=c, bordercolor=bdr, relief="solid", borderwidth=px(1)
        )
        style.configure(
            "TLabelframe.Label", background=c, foreground=fdm, font=(UI_FONT, 9, "bold")
        )

        # Scrollbar
        style.configure(
            "TScrollbar",
            background=pnl,
            troughcolor=skin["sb_bg"],
            arrowcolor=fdm,
            borderwidth=0,
            relief="flat",
            gripcount=0,
        )
        style.map("TScrollbar", background=[("active", bdr), ("!active", pnl)])

        # Notebook
        style.configure("TNotebook", background=pnl, tabmargins=px(0, 2, 0, 0))
        style.configure("TNotebook.Tab", padding=px(14, 8), font=(UI_FONT, 9))
        style.map(
            "TNotebook.Tab",
            background=[("selected", skin["sb_active"]), ("!selected", skin["sb_bg"])],
            foreground=[("selected", acc), ("!selected", skin["sb_btn"])],
        )

        # Entry
        style.configure(
            "TEntry",
            fieldbackground=i_bg,
            foreground=i_fg,
            insertcolor=i_fg,
            borderwidth=px(1),
            relief="flat",
            font=(UI_FONT, 10),
            padding=px(4),
        )

        # Combobox
        style.configure(
            "TCombobox",
            fieldbackground=i_bg,
            foreground=i_fg,
            selectbackground=acc,
            selectforeground="#FFFFFF",
            arrowcolor=fdm,
            borderwidth=px(1),
            relief="flat",
            font=(UI_FONT, 10),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", i_bg)],
            foreground=[("readonly", i_fg)],
            selectbackground=[("readonly", acc)],
        )

        # Tree lists (queue, batch lists, metadata...)
        style.configure(
            "Treeview",
            background=i_bg,
            fieldbackground=i_bg,
            foreground=i_fg,
            borderwidth=0,
            font=(UI_FONT, 10),
            rowheight=px(24),
        )
        style.map("Treeview", background=[("selected", acc)], foreground=[("selected", "#FFFFFF")])
        style.configure(
            "Treeview.Heading",
            background=pnl,
            foreground=fg,
            relief="flat",
            font=(UI_FONT, 9, "bold"),
        )
        style.map("Treeview.Heading", background=[("active", bdr)])

        # Progressbar
        style.configure(
            "TProgressbar", background=acc, troughcolor=bdr, borderwidth=0, thickness=px(4)
        )

        # Checkbutton + Radiobutton maps
        style.map("TCheckbutton", background=[("active", c)], foreground=[("active", acc)])
        style.map("TRadiobutton", background=[("active", c)], foreground=[("active", acc)])

    except Exception:
        logger.debug("apply_ttk_styles: best-effort step failed", exc_info=True)


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
