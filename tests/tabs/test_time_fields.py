"""tabs/kit/time_fields.py: one way to choose a moment or a part of a video."""

import tkinter as tk

import pytest

from tabs.kit.time_fields import MomentPicker, RangePicker, seconds_text


@pytest.fixture(scope="module")
def ui():
    from tests.gui_harness import Harness

    return Harness.instance()


@pytest.mark.gui
def test_moment_reads_sets_and_names_bad_input(ui):
    changes = []
    at = MomentPicker(ui.app, value="", here=lambda: 61.25, on_change=lambda: changes.append(1))
    assert at.seconds() is None and at.seconds(2.0) == 2.0
    at.var.set("1:02.5")
    assert at.seconds() == pytest.approx(62.5)
    at.use_here()
    assert at.seconds() == pytest.approx(61.25) and changes
    at.var.set("soon")
    with pytest.raises(ValueError, match="Start: 'soon'"):
        at.seconds(name="Start")
    at.destroy()


@pytest.mark.gui
def test_range_span_checks_and_clamps(ui):
    part = RangePicker(ui.app, end_hint="(empty = end)")
    assert part.span() == (0.0, None)
    part.set(5, 12)
    assert part.span() == (5.0, 12.0)
    assert part.span(duration=10) == (5.0, 10.0)  # never past the end
    part.set(8, 3)
    with pytest.raises(ValueError, match="after the start"):
        part.span()
    part.start.var.set("")
    part.end.var.set("")
    assert part.span() == (0.0, None)  # empty from = the start, empty to = the end
    part.destroy()


@pytest.mark.gui
def test_buttons_only_when_they_can_work(ui):
    plain = RangePicker(ui.app)
    both = RangePicker(ui.app, source=lambda: "/x.mp4", here=lambda: 0.0)
    texts = lambda w: [c.cget("text") for c in w.winfo_children() if isinstance(c, tk.Button)]  # noqa: E731
    assert texts(plain.start) == [] and texts(both.start) == ["🎞", "◉"]
    plain.destroy()
    both.destroy()


def test_seconds_text():
    assert seconds_text(12.5) == "12.5" and seconds_text(3.0) == "3" and seconds_text(0) == "0"
