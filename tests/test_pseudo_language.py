"""Every page built in another language: every drop-down's choices come from
the language files, the choice it shows is one of its own (a default left in
English isn't), and picking each choice works.

The "language" is English with every string wrapped in «...», so text that
skipped the language files stands out."""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk

import pytest

from core import i18n

pytestmark = pytest.mark.gui

#: Choices that stay as they are in every language: names of fonts, codecs,
#: formats, models, sizes and other technical values.
_TECHNICAL = re.compile(
    r"^(?:[\w.+\-/×x:%()# ]*\d[\w.+\-/×x:%()# ]*"  # anything with a number in it
    r"|[a-z0-9_.+\-/]+"  # lower-case ids: libx264, yuv420p, hable, tiny...
    r"|(?:[A-Z][a-z]+ ?){1,3}(?:Sans|Serif|Mono|Bold|Light|Regular|Display|Pro)?"  # font names
    r"|[A-Z0-9]{2,}(?:[ /+-][A-Z0-9]+)*)$"  # AAC, MP3, H.264, PNG...
)


#: a list of languages (each in its own name, or with its ISO code)
_LANGUAGE_NAME = re.compile(r"中文|\([a-z]{3}\)$")


def _combos(widget: tk.Misc) -> list[ttk.Combobox]:
    found = [widget] if isinstance(widget, ttk.Combobox) else []
    for child in widget.winfo_children():
        found += _combos(child)
    return found


def _english(text: str) -> bool:
    """Words that should have come from the language files but didn't."""
    return " " in text.strip() and "«" not in text and not _TECHNICAL.match(text.strip())


@pytest.fixture
def pseudo(monkeypatch):
    if not i18n._fallback:
        i18n.load_language("en_US")
    wrapped = {k: f"«{v}»" for k, v in i18n._fallback.items()}
    monkeypatch.setattr(i18n, "_strings", wrapped)
    return wrapped


def _check(tool: str, box: ttk.Combobox) -> list[str]:
    """What's wrong with one drop-down; then each of its choices is picked."""
    problems: list[str] = []
    values = [str(v) for v in box.cget("values")]
    shown = box.get()
    if any(_LANGUAGE_NAME.search(v) for v in values):
        values = []  # languages, shown by their own names / ISO codes: not translated
    untranslated = [v for v in values if _english(v)]
    if untranslated:
        problems.append(f"{tool}: choices not translated: {untranslated[:3]}")
    prompt = not shown or "«" in shown  # an empty or translated "choose..." prompt
    if values and str(box.cget("state")) == "readonly" and shown not in values and not prompt:
        problems.append(f"{tool}: shows {shown!r}, not one of its choices")
    for v in values:
        box.set(v)
        box.event_generate("<<ComboboxSelected>>")
    return problems


def _reload_pages() -> None:
    """Run the page modules again, so text they translate when loaded is read
    in the language now in force."""
    import importlib
    import sys

    from tabs.registry import PAGES

    for lazy in PAGES.values():
        module = sys.modules.get(lazy.module)
        if module is not None:
            importlib.reload(module)
        lazy._cls = None


def test_drop_downs_in_another_language(ui, pseudo):
    from tabs.registry import PAGES

    english = dict(i18n._fallback)
    _reload_pages()
    host = tk.Toplevel(ui.app)
    host.withdraw()
    problems: list[str] = []
    try:
        for tool, lazy in PAGES.items():
            frame = tk.Frame(host)
            frame.pack()
            try:
                page = lazy(frame)
                page.pack()
                ui.app.update_idletasks()
            except Exception as exc:
                problems.append(f"{tool}: page failed to build: {exc!r}")
                frame.destroy()
                continue
            for box in _combos(page):
                problems += _check(str(tool), box)
                ui.app.update_idletasks()
            frame.destroy()
    finally:
        host.destroy()
        i18n._strings = english
        _reload_pages()
    ui.pump(1.0)  # let background checks the pages started finish
    # a page's background check landing after the page was thrown away (here,
    # pages are built and dropped in quick succession) isn't what this tests
    errors = [e for e in ui.callback_errors if "invalid command name" not in e]
    ui.callback_errors.clear()
    assert not problems and not errors, "\n".join(problems + errors)
