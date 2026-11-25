"""Switching theme without a restart repaints everything (colours of the old
theme used to stay on buttons, entries, menus and cards), and the Mystical
theme's stars float over the window on Linux without taking the mouse."""

from __future__ import annotations

import sys
import tkinter as tk

import pytest

from core.skins import get_skin
from tabs.kit import theme

_OPTIONS = ("background", "foreground", "activebackground", "highlightbackground", "selectcolor")


def _colours(widget: tk.Misc) -> set[str]:
    found: set[str] = set()
    for option in _OPTIONS:
        try:
            found.add(str(widget.cget(option)).lower())
        except (tk.TclError, ValueError):
            pass
    for child in widget.winfo_children():
        found |= _colours(child)
    return found


def _only_in(old: str, new: str) -> set[str]:
    a, b = get_skin(old), get_skin(new)
    was = {v.lower() for v in a.values() if isinstance(v, str) and v.startswith("#")}
    now = {v.lower() for v in b.values() if isinstance(v, str) and v.startswith("#")}
    return was - now


def test_a_colour_follows_its_palette_entry():
    old = {"content": "#111111", "panel": "#222222", "sb_bg": "#111111"}
    new = {"content": "#AAAAAA", "panel": "#BBBBBB", "sb_bg": "#CCCCCC"}
    assert theme.colour_map(old, new, theme._CONTENT_FIRST)["#111111"] == "#AAAAAA"
    assert theme.colour_map(old, new, theme._SIDEBAR_FIRST)["#111111"] == "#CCCCCC"
    assert theme.colour_map(old, new, theme._CONTENT_FIRST)["#222222"] == "#BBBBBB"


@pytest.mark.gui
def test_switching_theme_leaves_no_old_colour(ui):
    app = ui.app
    for name in ("Start", "Video Downloader", "Quick Trimmer", "Audio Mixer", "Settings"):
        ui.open(name)  # build some pages in the current theme first
    try:
        for old, new in (("Classic", "Low Blue"), ("Low Blue", "Mystical")):
            app.apply_theme(old)
            ui.pump(0.3)
            app.apply_theme(new)
            ui.pump(0.3)
            left = _colours(app) & _only_in(old, new)
            assert not left, f"{old} -> {new}: still painted {sorted(left)}"
    finally:
        app.apply_theme("Classic")
        ui.pump(0.3)


@pytest.mark.gui
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the X SHAPE overlay is Linux's")
def test_mystical_stars_float_over_the_window_and_let_clicks_through(ui):
    from tabs.kit import stars

    ui.app.apply_theme("Mystical")
    try:
        ui.pump(0.5)
        field = stars.running()
        assert field is not None and field.canvas.winfo_toplevel() is not ui.app
        if field._shaper is None:
            pytest.skip("no X SHAPE extension on this display")
        assert field._shaper.input_rectangles() == 0  # the mouse goes through
        assert field.canvas.find_withtag("star")  # sparkles are drawn
    finally:
        ui.app.apply_theme("Classic")
        ui.pump(0.3)


def test_a_sparkle_is_a_few_small_rectangles():
    from tabs.kit.stars import sparkle

    assert len(sparkle(100, 100, "·", 12)) == 1
    bars = sparkle(100, 100, "✦", 12)
    assert len(bars) == 3 and all(w > 0 and h > 0 for _x, _y, w, h in bars)
