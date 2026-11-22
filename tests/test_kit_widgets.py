"""tabs/kit/widgets.py: typed widgets."""

from __future__ import annotations

from enum import StrEnum

import pytest

from tabs.kit.widgets import ChoiceBox, ColourButton, normal_colour


class Speed(StrEnum):
    SLOW = "slow"
    FAST = "fast"


@pytest.mark.gui
def test_choice_box_stores_members_not_labels(ui):
    seen: list[Speed] = []
    box = ChoiceBox(
        ui.app, {Speed.SLOW: "Gentle", Speed.FAST: "Quick!"}, Speed.SLOW, on_change=seen.append
    )
    try:
        assert box.value is Speed.SLOW and box.get() == "Gentle"
        box.value = Speed.FAST
        assert box.get() == "Quick!" and box.value is Speed.FAST
        box.event_generate("<<ComboboxSelected>>")
        ui.pump(0.05)
        assert seen == [Speed.FAST]
        assert box.choices == (Speed.SLOW, Speed.FAST)
    finally:
        box.destroy()


@pytest.mark.gui
def test_choice_box_refuses_ambiguous_labels(ui):
    with pytest.raises(ValueError):
        ChoiceBox(ui.app, {Speed.SLOW: "Same", Speed.FAST: "Same"}, Speed.SLOW)


@pytest.mark.gui
def test_choice_box_can_relabel_a_choice(ui):
    box = ChoiceBox(ui.app, {Speed.SLOW: "Source", Speed.FAST: "Quick"}, Speed.SLOW)
    try:
        box.relabel(Speed.SLOW, "Source (yuv420p10le)")
        assert box.get() == "Source (yuv420p10le)" and box.value is Speed.SLOW
        box.value = Speed.FAST
        box.relabel(Speed.SLOW, "Source (gbrp)")
        assert box.value is Speed.FAST and "Source (gbrp)" in box.cget("values")
        with pytest.raises(ValueError):
            box.relabel(Speed.SLOW, "Quick")
    finally:
        box.destroy()


def test_colours_are_normalised():
    assert normal_colour("#abc") == "#AABBCC"
    assert normal_colour("1e1e2e") == "#1E1E2E"
    with pytest.raises(ValueError):
        normal_colour("#12345")
    with pytest.raises(ValueError):
        normal_colour("purple")


@pytest.mark.gui
def test_colour_button_picks_paints_and_reports(ui, monkeypatch):
    from tkinter import colorchooser

    seen: list[str] = []
    btn = ColourButton(ui.app, "#101010", show_hex=True, on_change=seen.append)
    assert (btn.colour, btn.rgb, btn.cget("fg")) == ("#101010", "101010", "white")
    assert "#101010" in btn.cget("text")
    monkeypatch.setattr(colorchooser, "askcolor", lambda *_a, **_k: ((255, 238, 0), "#ffee00"))
    btn.invoke()
    assert (btn.colour, seen, btn.cget("fg")) == ("#FFEE00", ["#FFEE00"], "black")
    monkeypatch.setattr(colorchooser, "askcolor", lambda *_a, **_k: (None, None))
    btn.invoke()  # cancelled: nothing changes
    assert (btn.colour, seen) == ("#FFEE00", ["#FFEE00"])
    btn.set("00b140")  # set by the page: no report
    assert (btn.colour, seen) == ("#00B140", ["#FFEE00"])
    btn.destroy()
