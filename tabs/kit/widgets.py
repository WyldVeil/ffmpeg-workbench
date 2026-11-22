"""
tabs/kit/widgets.py - small typed widgets the pages share.

:class:`ChoiceBox` is the fix for a whole class of bugs Robust Python ch. 8 and
Fluent Python ch. 5 warn about: a drop-down whose *displayed text* was the
value the code compared against ("if var.get() == 'Home - all tools'") breaks
the moment the text is reworded or translated.  A ChoiceBox shows labels but
its value is an enum member, so the code never sees the text.

    box = ChoiceBox(parent, {StartPage.START: "Start page", StartPage.HOME: "Home"}, StartPage.START)
    box.value                 # StartPage.HOME - typed, whatever the label says
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping
from enum import Enum
from tkinter import ttk
from typing import Generic, TypeVar

E = TypeVar("E", bound=Enum)


class ChoiceBox(ttk.Combobox, Generic[E]):
    """A read-only drop-down of enum members, each shown by its label."""

    def __init__(
        self,
        master: tk.Misc,
        labels: Mapping[E, str],
        value: E,
        *,
        width: int = 30,
        on_change: Callable[[E], object] | None = None,
    ) -> None:
        if not labels:
            raise ValueError("a ChoiceBox needs at least one choice")
        self._labels: dict[E, str] = dict(labels)
        self._by_label: dict[str, E] = {text: member for member, text in self._labels.items()}
        if len(self._by_label) != len(self._labels):
            raise ValueError("two choices share a label")
        self._text = tk.StringVar(master=master)
        super().__init__(
            master,
            textvariable=self._text,
            values=list(self._labels.values()),
            state="readonly",
            width=width,
        )
        self.value = value
        if on_change is not None:
            self.bind("<<ComboboxSelected>>", lambda _e: on_change(self.value))

    @property
    def value(self) -> E:
        return self._by_label[self._text.get()]

    @value.setter
    def value(self, member: E) -> None:
        self._text.set(self._labels[member])

    @property
    def choices(self) -> tuple[E, ...]:
        return tuple(self._labels)

    def relabel(self, member: E, text: str) -> None:
        """Show *member* as *text* from now on (say, with what was detected:
        "Source (yuv420p10le)"); the choice made stays."""
        if self._by_label.get(text, member) is not member:
            raise ValueError("two choices share a label")
        chosen = self.value
        del self._by_label[self._labels[member]]
        self._labels[member] = text
        self._by_label[text] = member
        self.configure(values=list(self._labels.values()))
        self.value = chosen


def _readable_on(colour: str) -> str:
    """Black or white text, whichever reads on *colour* ('#RRGGBB')."""
    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return "black" if r * 0.3 + g * 0.59 + b * 0.11 >= 128 else "white"


def normal_colour(colour: str) -> str:
    """'#RRGGBB' (upper case) from '#rrggbb', 'RRGGBB' or '#RGB'."""
    c = colour.strip().lstrip("#").upper()
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6 or any(ch not in "0123456789ABCDEF" for ch in c):
        raise ValueError(f"not a colour: {colour!r}")
    return "#" + c


class ColourButton(tk.Button):
    """A button filled with a colour; clicking it opens the colour picker.

    :attr:`colour` is '#RRGGBB', :attr:`rgb` the same without '#' (the form
    ASS and FFmpeg take); *show_hex* writes the colour on the button;
    *on_change(colour)* runs after each pick."""

    def __init__(
        self,
        master: tk.Misc,
        colour: str = "#FFFFFF",
        *,
        text: str = "",
        width: int = 6,
        show_hex: bool = False,
        on_change: Callable[[str], object] | None = None,
    ) -> None:
        super().__init__(
            master, text=text, width=width, relief="flat", cursor="hand2", command=self._pick
        )
        self._on_change = on_change
        self._show_hex = show_hex
        self._colour = normal_colour(colour)
        self._paint()

    @property
    def colour(self) -> str:
        return self._colour

    @property
    def rgb(self) -> str:
        return self._colour[1:]

    def set(self, colour: str) -> None:
        """Show *colour* (no on_change: the caller already knows)."""
        self._colour = normal_colour(colour)
        self._paint()

    def _paint(self) -> None:
        fg = _readable_on(self._colour)
        self.configure(bg=self._colour, activebackground=self._colour, fg=fg, activeforeground=fg)
        if self._show_hex:
            self.configure(text=f"  {self._colour}  ")

    def _pick(self) -> None:
        from tkinter import colorchooser

        chosen = colorchooser.askcolor(self._colour, parent=self)[1]
        if chosen:
            self.set(chosen)
            if self._on_change is not None:
                self._on_change(self._colour)
