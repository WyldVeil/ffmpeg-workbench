"""
core/timecode.py - a point in time on a timeline, as a value.

Before this the app had a dozen small "seconds -> text" functions, each
slightly different (and some printed 59.999 s as "00:60.00").  A Timecode is
one value type for all of them - Fluent Python ch. 11 (a "Pythonic object":
``__format__``, ``__float__``, ordering, hashing) and Robust Python ch. 9-10
(a frozen dataclass whose invariant - never negative, whole milliseconds -
holds from construction on, so float drift never reaches a cut point).

    tc = Timecode.parse("1:02:03.5")
    f"{tc}"          -> "01:02:03.500"     the app's usual form
    f"{tc:.2}"       -> "01:02:03.50"      two decimals (rounded, carried)
    f"{tc:H.0}"      -> "01:02:03"         always show hours
    f"{tc:m.1}"      -> "62:03.5"          minutes not padded, no hours
    f"{tc:ass}"      -> "1:02:03.50"       ASS subtitles
    f"{tc:ff}"       -> "3723.500"         an FFmpeg -ss/-t argument
    tc.snap(25)      -> the nearest frame at 25 fps
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final, SupportsFloat

_TEXT: Final = re.compile(r"^\s*(?:(\d+):)?(?:(\d+):)?(\d+(?:[.,]\d*)?)\s*$")
_SPEC: Final = re.compile(r"^(?P<lead>[Hm]?)(?:\.(?P<digits>[0-3]))?$")


@dataclass(frozen=True, slots=True, order=True)
class Timecode:
    """Whole milliseconds from the start; never negative."""

    ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.ms, int) or isinstance(self.ms, bool):  # pyright: ignore[reportUnnecessaryIsInstance] - checked at run time too
            raise TypeError(f"Timecode needs whole milliseconds, not {self.ms!r}")
        if self.ms < 0:
            raise ValueError(f"a timecode can't be negative: {self.ms} ms")

    # ── Making one ────────────────────────────────────────────────────────────
    @classmethod
    def of(cls, seconds: SupportsFloat | None) -> Timecode:
        """From seconds; None, NaN and negative values become 0 (display-safe)."""
        s = 0.0 if seconds is None else float(seconds)
        if math.isnan(s) or s < 0:
            s = 0.0
        if math.isinf(s):
            raise ValueError("a timecode can't be infinite")
        return cls(round(s * 1000))

    @classmethod
    def parse(cls, text: str | SupportsFloat) -> Timecode:
        """``"1:02:03.5"``, ``"02:03"``, ``"75.5"``, ``"75,5"`` or a number.
        Raises ValueError for anything else - never quietly 0."""
        if not isinstance(text, str):
            value = float(text)
            if value < 0 or math.isnan(value):
                raise ValueError(f"not a time: {text!r}")
            return cls.of(value)
        m = _TEXT.match(text)
        if not m:
            raise ValueError(f"not a time: {text!r}")
        a, b, c = m.groups()
        secs = float(c.replace(",", "."))
        if a is not None and b is not None:  # H:M:S
            secs += int(a) * 3600 + int(b) * 60
        elif a is not None:  # M:S
            secs += int(a) * 60
        return cls.of(secs)

    # ── Reading it ────────────────────────────────────────────────────────────
    @property
    def seconds(self) -> float:
        return self.ms / 1000

    def __float__(self) -> float:
        return self.seconds

    def __bool__(self) -> bool:
        return self.ms != 0

    def __add__(self, other: object) -> Timecode:
        if isinstance(other, Timecode):
            return Timecode(self.ms + other.ms)
        if isinstance(other, (int, float)):
            return Timecode.of(self.seconds + other)
        return NotImplemented

    def __sub__(self, other: object) -> Timecode:
        """Clamped at zero: a timeline has no negative times."""
        if isinstance(other, Timecode):
            return Timecode(max(0, self.ms - other.ms))
        if isinstance(other, (int, float)):
            return Timecode.of(max(0.0, self.seconds - other))
        return NotImplemented

    def snap(self, fps: float) -> Timecode:
        """The nearest frame boundary at *fps* (unchanged if fps is unknown)."""
        if not fps or fps <= 0:
            return self
        frame = round(self.seconds * fps)
        return Timecode.of(frame / fps)

    def frame(self, fps: float) -> int:
        """Which frame this is at *fps*."""
        return round(self.seconds * fps) if fps and fps > 0 else 0

    # ── Writing it ────────────────────────────────────────────────────────────
    def __repr__(self) -> str:
        return f"Timecode({self})"

    def __str__(self) -> str:
        return format(self, "")

    def __format__(self, spec: str) -> str:
        if spec == "ff":
            return f"{self.ms // 1000}.{self.ms % 1000:03d}"
        if spec == "ass":
            cs = round(self.ms / 10)
            h, rem = divmod(cs, 360_000)
            m, rem = divmod(rem, 6000)
            s, c = divmod(rem, 100)
            return f"{h}:{m:02d}:{s:02d}.{c:02d}"
        found = _SPEC.match(spec)
        if not found:
            raise ValueError(f"unknown Timecode format {spec!r}")
        lead, digits = found["lead"], int(found["digits"] or 3)
        unit = 10 ** (3 - digits)  # round once, at the shown precision,
        ticks = round(self.ms / unit)  # so 59.999 s at 2 digits is 01:00.00
        whole, frac = divmod(ticks, 10**digits)
        h, rem = divmod(whole, 3600)
        m, s = divmod(rem, 60)
        tail = f".{frac:0{digits}d}" if digits else ""
        if lead == "m":
            return f"{h * 60 + m}:{s:02d}{tail}"
        if h or lead == "H":
            return f"{h:02d}:{m:02d}:{s:02d}{tail}"
        return f"{m:02d}:{s:02d}{tail}"


ZERO: Final = Timecode(0)
