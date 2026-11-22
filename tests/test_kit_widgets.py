"""tabs/kit/widgets.py: typed widgets."""

from __future__ import annotations

from enum import StrEnum

import pytest

from tabs.kit.widgets import ChoiceBox


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
