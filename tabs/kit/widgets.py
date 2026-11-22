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

    def __init__(self, master: tk.Misc, labels: Mapping[E, str], value: E, *, width: int = 30,
                 on_change: Callable[[E], object] | None = None) -> None:
        if not labels:
            raise ValueError("a ChoiceBox needs at least one choice")
        self._labels: dict[E, str] = dict(labels)
        self._by_label: dict[str, E] = {text: member for member, text in self._labels.items()}
        if len(self._by_label) != len(self._labels):
            raise ValueError("two choices share a label")
        self._text = tk.StringVar(master=master)
        super().__init__(master, textvariable=self._text, values=list(self._labels.values()),
                         state="readonly", width=width)
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
