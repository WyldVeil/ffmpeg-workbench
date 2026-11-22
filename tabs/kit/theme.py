"""
tabs/kit/theme.py  -  applying a colour theme (core/skins.py) to the window.

Live switching re-colours the sidebar, the pages, ttk styles and the shared
CLR palette; the Mystical theme adds a slow star field behind the window.
"""
from __future__ import annotations

import random
import sys
import tkinter as tk
from tkinter import ttk

from core import settings
from core.scaling import px
from core.skins import UI_FONT, get_skin, load_skin_name

# ── Module-level state for star animation ────────────────────────────────────
_star_canvas   = None
_star_toplevel = None      # click-through Toplevel overlay (Windows)
_star_data: list = []
_star_after_id = None
# Unique chroma-key colour - never used in any real skin palette
_STAR_CHROMA   = "#010203"
# Speed multiplier: 1.0 = default, <1 slower, >1 faster
_star_speed_mult: float = 1.0
# Star count (density)
_star_count: int = 55


def get_star_speed() -> float:
    """Return the current star speed multiplier."""
    return _star_speed_mult


def set_star_speed(mult: float) -> None:
    """Set the star speed multiplier live (no restart needed)."""
    global _star_speed_mult
    _star_speed_mult = float(mult)


def get_star_count() -> int:
    """Return the current star density count."""
    return _star_count


def set_star_count(count: int) -> None:
    """Set the number of stars. Takes effect on next skin apply."""
    global _star_count
    _star_count = max(10, int(count))


def stop_stars():
    """Kill any running star animation and destroy all overlay widgets."""
    global _star_canvas, _star_toplevel, _star_data, _star_after_id
    if _star_after_id is not None:
        try:
            if _star_canvas and _star_canvas.winfo_exists():
                _star_canvas.after_cancel(_star_after_id)
        except Exception:
            pass
        _star_after_id = None
    # Destroying the Toplevel also destroys the canvas inside it
    if _star_toplevel is not None:
        try:
            if _star_toplevel.winfo_exists():
                _star_toplevel.destroy()
        except Exception:
            pass
        _star_toplevel = None
        _star_canvas   = None
    elif _star_canvas is not None:
        try:
            if _star_canvas.winfo_exists():
                _star_canvas.destroy()
        except Exception:
            pass
        _star_canvas = None
    _star_data.clear()


def start_stars(app: tk.Tk, skin: dict, inner_frame=None):
    """Spawn the Mystical falling-star particle overlay across the full window.

    On Windows: creates a borderless click-through Toplevel that sits over the
    entire app. The background is chroma-keyed transparent (-transparentcolor),
    so only the star glyphs are visible. WS_EX_TRANSPARENT lets all mouse events
    fall through to the main window beneath.

    On other platforms: falls back to a canvas on the app root (limited effect).
    The inner_frame parameter is accepted for API compatibility but not used.
    """
    global _star_canvas, _star_toplevel, _star_data, _star_after_id

    stop_stars()

    # Load persisted speed / density preferences
    set_star_speed(settings.STAR_SPEED.get())
    set_star_count(settings.STAR_COUNT.get())
    CHARS   = ["✦", "✧", "⋆", "·", "˚", "∘", "*"]
    COLOURS = ["#E1BEE7", "#CE93D8", "#BA68C8",
               "#AB47BC", "#F3E5F5", "#D1C4E9",
               "#B39DDB", "#EDE7F6"]

    if sys.platform == "win32":
        import ctypes

        _star_toplevel = tk.Toplevel(app)
        _star_toplevel.overrideredirect(True)
        _star_toplevel.configure(bg=_STAR_CHROMA)
        _star_toplevel.attributes("-transparentcolor", _STAR_CHROMA)
        # Not -topmost (that drew stars over every other program): owned by the
        # app window and kept just above it.
        _star_toplevel.transient(app)
        _star_toplevel.lift(app)

        # Apply WS_EX_TRANSPARENT | WS_EX_LAYERED so the overlay is
        # completely click-through - all mouse events reach the app below.
        def _make_clickthrough():
            try:
                _star_toplevel.update_idletasks()
                raw = _star_toplevel.winfo_id()
                hwnd = ctypes.windll.user32.GetParent(raw) or raw
                GWL_EXSTYLE     = -20
                WS_EX_LAYERED   = 0x00080000
                WS_EX_TRANSPARENT = 0x00000020
                ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                ctypes.windll.user32.SetWindowLongW(
                    hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED | WS_EX_TRANSPARENT)
            except Exception:
                pass
        app.after(80, _make_clickthrough)

        _star_canvas = tk.Canvas(
            _star_toplevel, bg=_STAR_CHROMA, highlightthickness=0, bd=0)
        _star_canvas.pack(fill="both", expand=True)

        # Keep overlay geometry in sync with main window (move / resize)
        def _sync():
            if _star_toplevel is None or not _star_toplevel.winfo_exists():
                return
            try:
                if app.state() == "iconic":
                    _star_toplevel.withdraw()
                else:
                    _star_toplevel.deiconify()
                    _star_toplevel.lift(app)
                    x = app.winfo_rootx()
                    y = app.winfo_rooty()
                    w = app.winfo_width()
                    h = app.winfo_height()
                    _star_toplevel.geometry(f"{w}x{h}+{x}+{y}")
            except Exception:
                pass
            _star_toplevel.after(500, _sync)

        # Bind to Configure for immediate response to resize/move
        def _on_configure(event):
            try:
                if _star_toplevel and _star_toplevel.winfo_exists():
                    x = app.winfo_rootx()
                    y = app.winfo_rooty()
                    w = app.winfo_width()
                    h = app.winfo_height()
                    _star_toplevel.geometry(f"{w}x{h}+{x}+{y}")
            except Exception:
                pass
        app.bind("<Configure>", _on_configure, add=True)
        app.after(50, _sync)

    else:
        # Non-Windows fallback - canvas on root, lower behind content
        _star_canvas = tk.Canvas(app, bg=skin["sb_bg"],
                                  highlightthickness=0, bd=0)
        _star_canvas.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        try:
            # Canvas.lower() reorders the canvas's *items*; the window-stacking
            # lower is Misc.lower - without it the canvas covered the whole UI.
            tk.Misc.lower(_star_canvas)
        except tk.TclError:
            pass

    # _star_data is populated lazily on the first tick.
    # We use the MAIN APP window dimensions (always reliable) rather than the
    # canvas/Toplevel dimensions, which lag behind until the geometry manager
    # has completed at least one layout pass after _sync fires.

    def _get_wh():
        """Return (W, H) using app dims - the overlay always matches the app."""
        W = app.winfo_width()
        H = app.winfo_height()
        if W < 100: W = 1440
        if H < 100: H = 900
        return W, H

    def _tick():
        global _star_after_id
        if _star_canvas is None or not _star_canvas.winfo_exists():
            return

        W, H = _get_wh()

        # Lazy init - runs once, using reliable app dimensions.
        if not _star_data:
            n = _star_count
            slot_w = W / n
            for i in range(n):
                _star_data.append({
                    "x":     slot_w * i + random.uniform(0, slot_w),
                    "y":     random.uniform(-px(120), H + px(200)),
                    "speed": random.uniform(0.5, 1.8),
                    "char":  random.choice(CHARS),
                    "col":   random.choice(COLOURS),
                    "size":  random.randint(8, 16),
                    "drift": random.uniform(-0.2, 0.2),
                    "id":    None,
                })

        for s in _star_data:
            if s["id"] is not None:
                try:
                    _star_canvas.delete(s["id"])
                except Exception:
                    pass
                s["id"] = None

            s["y"] += px(s["speed"] * _star_speed_mult)
            s["x"] += px(s["drift"] * _star_speed_mult)

            if s["y"] > H + px(20):
                # Re-enter from a random x across the full width
                s["y"] = random.uniform(-px(30), -px(5))
                s["x"] = random.uniform(0, W)
                s["speed"] = random.uniform(0.5, 1.8)
                s["char"]  = random.choice(CHARS)
                s["col"]   = random.choice(COLOURS)
                s["size"]  = random.randint(8, 16)
                s["drift"] = random.uniform(-0.2, 0.2)
            if s["x"] < 0: s["x"] = W
            if s["x"] > W: s["x"] = 0

            s["id"] = _star_canvas.create_text(
                int(s["x"]), int(s["y"]),
                text=s["char"],
                fill=s["col"],
                font=(UI_FONT, s["size"]))

        _star_after_id = _star_canvas.after(50, _tick)

    _star_canvas.after(50, _tick)


# ── Global tk option cascading ────────────────────────────────────────────────

def apply_default_options(app: tk.Tk, skin: dict):
    """
    Set tk option_add defaults so EVERY tk widget (in all 67 tabs) inherits
    dark-theme colors without touching individual tab files.
    Called at startup BEFORE tabs are created, and again on skin switch.
    """
    try:
        app.option_clear()
        P = "widgetDefault"  # lowest priority - explicit widget settings override
        c = skin["content"]
        fg = skin["fg"]
        i_bg = skin.get("input_bg", "#222222")
        i_fg = skin.get("input_fg", "#E2E2E2")
        bdr  = skin.get("border",   "#2C2C2C")
        pnl  = skin["panel"]
        acc  = skin["accent"]

        # Frame / generic background
        app.option_add("*Background",                  c,     P)
        app.option_add("*Foreground",                  fg,    P)
        app.option_add("*Font",                        f"{{{UI_FONT}}} 10", P)
        app.option_add("*activeBackground",            pnl,   P)
        app.option_add("*activeForeground",            fg,    P)
        app.option_add("*disabledForeground",          bdr,   P)

        # Label
        app.option_add("*Label.Background",            c,     P)
        app.option_add("*Label.Foreground",            fg,    P)
        app.option_add("*Label.BorderWidth",           "0",   P)

        # LabelFrame
        app.option_add("*LabelFrame.Background",       c,     P)
        app.option_add("*LabelFrame.Foreground",       skin["fgdim"], P)
        app.option_add("*LabelFrame.Relief",           "solid", P)
        app.option_add("*LabelFrame.BorderWidth",      px(1),  P)
        app.option_add("*LabelFrame.Font",
                       f"{{{UI_FONT}}} 9 bold", P)

        # Entry
        app.option_add("*Entry.Background",            i_bg,  P)
        app.option_add("*Entry.Foreground",            i_fg,  P)
        app.option_add("*Entry.insertBackground",      i_fg,  P)
        app.option_add("*Entry.selectBackground",      acc,   P)
        app.option_add("*Entry.selectForeground",      "#FFFFFF", P)
        app.option_add("*Entry.Relief",                "flat", P)
        app.option_add("*Entry.BorderWidth",           px(1),  P)
        app.option_add("*Entry.HighlightThickness",    "0",   P)

        # Spinbox
        app.option_add("*Spinbox.Background",          i_bg,  P)
        app.option_add("*Spinbox.Foreground",          i_fg,  P)
        app.option_add("*Spinbox.insertBackground",    i_fg,  P)
        app.option_add("*Spinbox.selectBackground",    acc,   P)
        app.option_add("*Spinbox.Relief",              "flat", P)
        app.option_add("*Spinbox.buttonBackground",    pnl,   P)
        app.option_add("*Spinbox.HighlightThickness",  "0",   P)

        # Button
        app.option_add("*Button.Background",           pnl,   P)
        app.option_add("*Button.Foreground",           fg,    P)
        app.option_add("*Button.activeBackground",     skin["sb_hover"], P)
        app.option_add("*Button.activeForeground",     "#FFFFFF", P)
        app.option_add("*Button.Relief",               "flat", P)
        app.option_add("*Button.BorderWidth",          "0",   P)
        app.option_add("*Button.Cursor",               "hand2", P)
        app.option_add("*Button.HighlightThickness",   "0",   P)

        # Checkbutton
        app.option_add("*Checkbutton.Background",      c,     P)
        app.option_add("*Checkbutton.Foreground",      fg,    P)
        app.option_add("*Checkbutton.selectColor",     pnl,   P)
        app.option_add("*Checkbutton.activeBackground",c,     P)
        app.option_add("*Checkbutton.activeForeground",acc,   P)
        app.option_add("*Checkbutton.HighlightThickness","0", P)

        # Radiobutton
        app.option_add("*Radiobutton.Background",      c,     P)
        app.option_add("*Radiobutton.Foreground",      fg,    P)
        app.option_add("*Radiobutton.selectColor",     acc,   P)
        app.option_add("*Radiobutton.activeBackground",c,     P)
        app.option_add("*Radiobutton.activeForeground",acc,   P)
        app.option_add("*Radiobutton.HighlightThickness","0", P)

        # Scale (slider).  Tk draws the slider in the background colour, so the
        # trough must be clearly lighter for the slider to show at all.
        app.option_add("*Scale.Background",            c,     P)
        app.option_add("*Scale.Foreground",            fg,    P)
        # option *names* are case-sensitive ("troughColor"); its class is
        # "Background", so without this the trough took the background colour
        app.option_add("*Scale.troughColor",           _blend(bdr, fg, 0.35), P)
        app.option_add("*Scale.activeBackground",      acc,   P)
        app.option_add("*Scale.SliderRelief",          "flat", P)
        app.option_add("*Scale.HighlightThickness",    "0",   P)
        app.option_add("*Scale.BorderWidth",           "0",   P)
        # Tk's own default sizes are in pixels: grow them on HiDPI screens
        app.option_add("*Scale.Width",                 px(15), P)
        app.option_add("*Scale.SliderLength",          px(30), P)
        app.option_add("*Scale.Length",                px(100), P)
        app.option_add("*Scrollbar.Width",             px(11), P)
        for cls in ("Entry", "Spinbox", "Text"):
            app.option_add(f"*{cls}.InsertWidth",       px(2), P)

        # Listbox
        app.option_add("*Listbox.Background",          i_bg,  P)
        app.option_add("*Listbox.Foreground",          i_fg,  P)
        app.option_add("*Listbox.selectBackground",    acc,   P)
        app.option_add("*Listbox.selectForeground",    "#FFFFFF", P)
        app.option_add("*Listbox.Relief",              "flat", P)
        app.option_add("*Listbox.BorderWidth",         "0",   P)
        app.option_add("*Listbox.HighlightThickness",  "0",   P)

        # Text (used for console log areas)
        app.option_add("*Text.Background",             skin["console_bg"], P)
        app.option_add("*Text.Foreground",             skin["console_fg"], P)
        app.option_add("*Text.insertBackground",       skin["console_fg"], P)
        app.option_add("*Text.selectBackground",       acc,   P)
        app.option_add("*Text.Relief",                 "flat", P)
        app.option_add("*Text.BorderWidth",            "0",   P)
        app.option_add("*Text.HighlightThickness",     "0",   P)

        # Canvas
        app.option_add("*Canvas.Background",           c,     P)
        app.option_add("*Canvas.HighlightThickness",   "0",   P)

        # Menu
        app.option_add("*Menu.Background",             pnl,   P)
        app.option_add("*Menu.Foreground",             fg,    P)
        app.option_add("*Menu.activeBackground",       acc,   P)
        app.option_add("*Menu.activeForeground",       "#FFFFFF", P)
        app.option_add("*Menu.Relief",                 "flat", P)
        app.option_add("*Menu.BorderWidth",            px(1),  P)
        app.option_add("*Menu.TearOff",                "0",   P)

    except Exception:
        pass


# ── Main entry point ─────────────────────────────────────────────────────────

def _blend(a: str, b: str, amount: float) -> str:
    """Colour *a* moved *amount* (0..1) of the way towards *b* (#rrggbb)."""
    try:
        ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    except (ValueError, IndexError, TypeError):
        return a
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(ca, cb))


def apply_skin(app: tk.Tk, *, sidebar: tk.Frame, content: tk.Frame, stars_behind: tk.Misc | None = None) -> dict:
    """Apply the saved theme to the window (safe to repeat: live switching).
    Returns the palette; the shell's own widgets recolour themselves from it."""
    skin = get_skin(load_skin_name())
    apply_default_options(app, skin)              # options for every widget made from now on
    import tabs.base_tab as bt  # the palette pages read when they build
    bt.CLR.update({
        "bg": skin["content"], "panel": skin["panel"], "accent": skin["accent"], "fg": skin["fg"],
        "fgdim": skin["fgdim"], "green": skin["green"], "red": skin["red"], "orange": skin["orange"],
        "pink": skin["pink"], "console_bg": skin["console_bg"], "console_fg": skin["console_fg"],
        "border": skin.get("border", "#2C2C2C"), "input_bg": skin.get("input_bg", "#1E1E1E"),
        "input_fg": skin.get("input_fg", "#E2E2E2"),
    })
    _recolour_frame(sidebar, skin["sb_bg"])
    content.config(bg=skin["content"])
    _recolour_widget_tree(content, skin)
    apply_ttk_styles(skin)
    if skin.get("stars"):
        start_stars(app, skin, stars_behind)
    else:
        stop_stars()
    return skin


def apply_ttk_styles(skin: dict):
    """Apply comprehensive ttk styling."""
    try:
        style = ttk.Style()
        c   = skin["content"]
        pnl = skin["panel"]
        acc = skin["accent"]
        fg  = skin["fg"]
        fdm = skin["fgdim"]
        bdr = skin.get("border", "#2C2C2C")
        i_bg = skin.get("input_bg", "#1E1E1E")
        i_fg = skin.get("input_fg", "#E2E2E2")

        if tk.TkVersion < 9:
            # Tk 8.6's clam theme sizes its arrows, sliders and check marks in
            # fixed pixels (Tk 9 already gives them in points, so they scale).
            style.configure(".", arrowsize=px(14), sliderlength=px(30), indicatorsize=px(10),
                            indicatormargin=px(1, 1, 4, 1))
        style.configure("TFrame",        background=c)
        style.configure("TLabel",        background=c, foreground=fg,
                        font=(UI_FONT, 10))
        style.configure("TCheckbutton",  background=c, foreground=fg,
                        font=(UI_FONT, 10))
        style.configure("TRadiobutton",  background=c, foreground=fg,
                        font=(UI_FONT, 10))

        # LabelFrame - clear bordered panel
        style.configure("TLabelframe",   background=c,
                        bordercolor=bdr, relief="solid", borderwidth=px(1))
        style.configure("TLabelframe.Label",
                        background=c, foreground=fdm,
                        font=(UI_FONT, 9, "bold"))

        # Scrollbar
        style.configure("TScrollbar",
                        background=pnl, troughcolor=skin["sb_bg"],
                        arrowcolor=fdm, borderwidth=0, relief="flat",
                        gripcount=0)
        style.map("TScrollbar",
                  background=[("active", bdr), ("!active", pnl)])

        # Notebook
        style.configure("TNotebook",     background=pnl, tabmargins=px(0, 2, 0, 0))
        style.configure("TNotebook.Tab", padding=px(14, 8), font=(UI_FONT, 9))
        style.map("TNotebook.Tab",
                  background=[("selected",  skin["sb_active"]),
                               ("!selected", skin["sb_bg"])],
                  foreground=[("selected",  acc),
                               ("!selected", skin["sb_btn"])])

        # Entry
        style.configure("TEntry",
                        fieldbackground=i_bg, foreground=i_fg,
                        insertcolor=i_fg, borderwidth=px(1), relief="flat",
                        font=(UI_FONT, 10), padding=px(4))

        # Combobox
        style.configure("TCombobox",
                        fieldbackground=i_bg, foreground=i_fg,
                        selectbackground=acc, selectforeground="#FFFFFF",
                        arrowcolor=fdm, borderwidth=px(1), relief="flat",
                        font=(UI_FONT, 10))
        style.map("TCombobox",
                  fieldbackground=[("readonly", i_bg)],
                  foreground=[("readonly", i_fg)],
                  selectbackground=[("readonly", acc)])

        # Tree lists (queue, batch lists, metadata...)
        style.configure("Treeview", background=i_bg, fieldbackground=i_bg, foreground=i_fg,
                        borderwidth=0, font=(UI_FONT, 10), rowheight=px(24))
        style.map("Treeview", background=[("selected", acc)], foreground=[("selected", "#FFFFFF")])
        style.configure("Treeview.Heading", background=pnl, foreground=fg, relief="flat",
                        font=(UI_FONT, 9, "bold"))
        style.map("Treeview.Heading", background=[("active", bdr)])

        # Progressbar
        style.configure("TProgressbar",
                        background=acc, troughcolor=bdr,
                        borderwidth=0, thickness=px(4))

        # Checkbutton + Radiobutton maps
        style.map("TCheckbutton",
                  background=[("active", c)],
                  foreground=[("active", acc)])
        style.map("TRadiobutton",
                  background=[("active", c)],
                  foreground=[("active", acc)])

    except Exception:
        pass


def _recolour_frame(widget, bg_col: str):
    """Recursively set bg on Frame/Label/Checkbutton sidebar children."""
    try:
        cls = widget.winfo_class()
        if cls in ("Frame", "Label", "Checkbutton"):
            widget.config(bg=bg_col)
    except Exception:
        pass
    try:
        for child in widget.winfo_children():
            _recolour_frame(child, bg_col)
    except Exception:
        pass


def _recolour_widget_tree(widget, skin: dict):
    """
    Recursively update background colors in the content area for live skin switch.
    Only updates widgets that don't have explicit color overrides (checks for
    common surface color patterns to avoid incorrectly recoloring accent buttons).
    """
    try:
        cls = widget.winfo_class()
        c   = skin["content"]

        if cls == "Frame":
            try: widget.config(bg=c)
            except Exception: pass
        elif cls == "Label":
            try: widget.config(bg=c, fg=skin["fg"])
            except Exception: pass
        elif cls == "LabelFrame":
            try: widget.config(bg=c, fg=skin["fgdim"])
            except Exception: pass

        for child in widget.winfo_children():
            _recolour_widget_tree(child, skin)
    except Exception:
        pass
