"""Property-based tests (Effective Python item 108; Robust Python ch. 23):
instead of a few hand-picked examples, Hypothesis tries hundreds of inputs -
including the awkward ones (0, huge values, 59.999...) - and shrinks any
failure to the smallest case that shows it."""

from __future__ import annotations

import json
import os
import string
import tempfile

from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from core import settings as app_settings
from core import tools
from core.timecode import Timecode

millis = st.integers(min_value=0, max_value=1000 * 3600 * 200)  # up to 200 hours
seconds = st.floats(min_value=0, max_value=3600 * 200, allow_nan=False, allow_infinity=False)


@given(millis)
def test_timecode_text_round_trips(ms):
    tc = Timecode(ms)
    assert Timecode.parse(str(tc)) == tc
    assert Timecode.parse(f"{tc:H}") == tc
    assert Timecode.parse(f"{tc:m.3}") == tc


@given(millis, st.sampled_from(["", ".0", ".1", ".2", "H", "H.0", "m.1", "m.2"]))
def test_timecode_text_is_well_formed(ms, spec):
    text = format(Timecode(ms), spec)
    *head, last = text.split(":")
    assert all(part.isdigit() for part in head)
    secs = float(last)
    assert 0 <= secs < 60  # never "00:60.00", whatever the rounding
    if not spec.startswith("m") and len(head) >= 1:
        assert int(head[-1]) < 60


@given(millis, st.sampled_from([0, 1, 2, 3]))
def test_rounding_is_within_half_a_unit(ms, digits):
    shown = Timecode.parse(format(Timecode(ms), f".{digits}"))
    assert abs(shown.ms - ms) <= 10 ** (3 - digits) / 2


@given(seconds)
def test_seconds_survive_to_the_millisecond(s):
    assert abs(Timecode.of(s).seconds - s) <= 0.0005 + 1e-9


@given(millis, millis)
def test_ordering_and_arithmetic_agree_with_milliseconds(a, b):
    ta, tb = Timecode(a), Timecode(b)
    assert (ta < tb) == (a < b) and (ta == tb) == (a == b)
    assert (ta + tb).ms == a + b
    assert (ta - tb).ms == max(0, a - b)


@given(millis, st.sampled_from([23.976, 24, 25, 29.97, 30, 50, 59.94, 60]))
def test_snapping_lands_on_a_frame_and_moves_less_than_half_a_frame(ms, fps):
    tc = Timecode(ms)
    snapped = tc.snap(fps)
    assert abs(snapped.seconds - tc.seconds) <= 0.5 / fps + 0.001
    assert snapped.snap(fps) == snapped  # idempotent


@given(st.text(alphabet=string.printable, max_size=12))
@settings(max_examples=300)
def test_parse_accepts_or_refuses_but_never_crashes(text):
    try:
        tc = Timecode.parse(text)
    except ValueError:
        return
    assert tc.ms >= 0


@given(
    st.lists(
        st.sampled_from(
            [
                *[t.value for t in tools.ToolId],
                *[s.name for s in tools.SPECS],
                "gone",
                "",
                "Not A Tool",
            ]
        ),
        max_size=20,
    )
)
def test_stored_tool_lists_parse_to_unique_known_ids(values):
    ids = tools.parse_all(values)
    assert len(ids) == len(set(ids))
    assert all(isinstance(t, tools.ToolId) for t in ids)
    known = [tools.parse(v) for v in values if tools.parse(v) is not None]
    assert ids == list(dict.fromkeys(known))  # order kept, first wins


# ── Settings: a stateful test (Robust Python ch. 23) ──────────────────────────
# Hypothesis runs random sequences of writes and reads against the real
# settings store (on a scratch file) and a plain dict model of it: after every
# step, what the store says must match the model.


_KEYS = {
    "auto_open_output": st.booleans(),
    "max_parallel_jobs": st.sampled_from([1, 2, 3, 4]),
    "star_speed": st.floats(min_value=0.1, max_value=4, allow_nan=False),
    "default_crf": st.sampled_from(["18", "23", "28"]),
    "theme": st.sampled_from(["Classic", "Mystical"]),
}


class SettingsStore(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.folder = tempfile.mkdtemp(prefix="settings_prop_")
        self.saved = (app_settings.SETTINGS_FILE, app_settings._cache, app_settings._cache_sig)
        app_settings.SETTINGS_FILE = os.path.join(self.folder, "settings.json")
        app_settings._cache = app_settings._cache_sig = None
        self.model: dict[str, object] = {}

    @rule(data=st.data(), key=st.sampled_from(sorted(_KEYS)))
    def set_one(self, data, key):
        value = data.draw(_KEYS[key])
        assert app_settings.REGISTRY[key].set(value)
        self.model[key] = value

    @rule(data=st.data())
    def update_many(self, data):
        keys = data.draw(
            st.lists(st.sampled_from(sorted(_KEYS)), min_size=1, max_size=3, unique=True)
        )
        changes = {k: data.draw(_KEYS[k]) for k in keys}
        assert app_settings.update(changes)
        self.model.update(changes)

    @rule()
    def hand_edit_garbage(self):
        """Someone breaks the file by hand: reads fall back to the backup (always a good
        copy - a broken file is never backed up), or to the defaults."""
        with open(app_settings.SETTINGS_FILE, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        bak = app_settings.SETTINGS_FILE + ".bak"
        if os.path.exists(bak):
            with open(bak, encoding="utf-8") as fh:
                self.model = dict(json.load(fh))
        else:
            self.model = {}

    @invariant()
    def reads_match_the_model(self):
        for key, setting in ((k, app_settings.REGISTRY[k]) for k in _KEYS):
            expected = self.model.get(key, setting.default)
            assert setting.get() == setting.coerce(expected), key

    def teardown(self) -> None:
        app_settings.SETTINGS_FILE, app_settings._cache, app_settings._cache_sig = self.saved


TestSettingsStore = SettingsStore.TestCase
TestSettingsStore.settings = settings(max_examples=40, stateful_step_count=12, deadline=None)
