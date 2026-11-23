"""
tabs/kit/tktyped.py  -  The Tk calls whose type stubs are incomplete

Tk's type stubs leave a few methods untyped (``yview`` / ``xview``,
``curselection``, ``lift``, ``wm_attributes``), so using them makes a page
"partially unknown" to the type checker.  They are wrapped here, once, with
real types; pages call these instead.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Literal


def linked_scrollbar(
    parent: tk.Misc,
    view: tk.XView | tk.YView,
    orient: Literal["vertical", "horizontal"] = "vertical",
) -> ttk.Scrollbar:
    """A scrollbar in *parent* that scrolls *view* and follows it; the caller packs it."""
    bar = ttk.Scrollbar(parent, orient=orient)
    if orient == "vertical":
        assert isinstance(view, tk.YView)
        bar.configure(command=view.yview)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType] - stub gap
        view.configure(yscrollcommand=bar.set)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] - every YView widget has it
    else:
        assert isinstance(view, tk.XView)
        bar.configure(command=view.xview)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType] - stub gap
        view.configure(xscrollcommand=bar.set)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] - every XView widget has it
    return bar


def selected_rows(listbox: tk.Listbox) -> tuple[int, ...]:
    """The indices of *listbox*'s selected rows (typed: the stubs leave curselection untyped)."""
    return tuple(int(i) for i in listbox.curselection())  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType] - stub gap


def raise_above(widget: tk.Misc, other: tk.Misc) -> None:
    """Stack *widget* above *other* (so a widget packed into *other* shows)."""
    widget.lift(other)  # pyright: ignore[reportUnknownMemberType] - stub gap


def tool_window(window: tk.Toplevel) -> None:
    """Keep a small helper window (a tooltip) off the Windows taskbar."""
    window.wm_attributes("-toolwindow", True)  # pyright: ignore[reportUnknownMemberType] - stub gap


def set_scale(scale: tk.Scale | ttk.Scale, value: float) -> None:
    """Move a slider (``tk.Scale.set`` is untyped in the stubs)."""
    scale.set(value)  # pyright: ignore[reportUnknownMemberType] - stub gap


def draw_image(
    canvas: tk.Canvas,
    x: float,
    y: float,
    image: tk.PhotoImage,
    anchor: Literal["center", "nw"] = "center",
) -> int:
    """Put *image* on *canvas* at (*x*, *y*); returns the item id."""
    return canvas.create_image(x, y, image=image, anchor=anchor)  # pyright: ignore[reportUnknownMemberType] - stub gap


def on_write(var: tk.Variable, callback: Callable[[], object]) -> str:
    """Call *callback()* whenever *var* changes (Tk's trace passes three
    untyped names nobody uses); returns the trace id."""

    def fire(*_: object) -> None:
        callback()

    return var.trace_add("write", fire)


def add_pane(paned: tk.PanedWindow, child: tk.Widget, minsize: int) -> None:
    """Add *child* to *paned*, never narrower than *minsize*."""
    paned.add(child, minsize=minsize)  # pyright: ignore[reportUnknownMemberType] - stub gap


def current_tab(notebook: ttk.Notebook) -> int:
    """The index of *notebook*'s shown tab."""
    return int(notebook.index(notebook.select()))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType] - stub gap


def overlay(window: tk.Toplevel, alpha: float) -> None:
    """Make *window* a see-through layer over the whole screen, on top of everything."""
    window.attributes("-alpha", alpha, "-fullscreen", True, "-topmost", True)  # pyright: ignore[reportUnknownMemberType] - stub gap


def row_text(listbox: tk.Listbox, index: int) -> str:
    """The text of row *index* of *listbox*."""
    return str(listbox.get(index))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType] - stub gap
