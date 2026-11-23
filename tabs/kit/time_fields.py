"""
tabs/kit/time_fields.py - choosing a moment, or a part, of a video.

    at = MomentPicker(row, value="0", source=lambda: self.src_row.path)
    part = RangePicker(row, end_hint="(empty = to the end)", here=lambda: self.compare.position)
    start, end = part.span()          # ValueError with a sentence when it can't be used

Each time field takes 12.5, 1:02.5 or 1:02:03.25.  Next to it: 🎞 opens a
frame picker on the source (when *source* is given) and ◉ takes the frame
the page's preview shows (when *here* is given).  Pages used to build these
rows by hand, each parsing and checking the times its own way.
"""

from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Callable

from core import ffmpeg as ff

Source = Callable[[], "str | None"]  # the video the times are in
Here = Callable[[], float]  # the time the page's preview shows


def seconds_text(t: float) -> str:
    """12.5 -> "12.5" (for fields that hold plain seconds)."""
    return f"{t:.3f}".rstrip("0").rstrip(".") or "0"


def frame_button(
    parent: tk.Misc, var: tk.StringVar, source: Source, fmt: Callable[[float], str] = ff.format_time
) -> tk.Button:
    """A 🎞 button opening a frame picker on *source()*; the chosen time goes into *var*."""
    from tabs.base_tab import add_tooltip

    def pick() -> None:
        from tabs.widgets import FramePicker

        path = source()
        if not path or not os.path.isfile(path):
            from tkinter import messagebox

            messagebox.showinfo("Pick a frame", "Choose a video first.", parent=parent)
            return
        FramePicker(parent, path, var, fmt)

    btn = tk.Button(parent, text="🎞", relief="flat", width=2, cursor="hand2", command=pick)
    add_tooltip(btn, "Find the moment by looking at the frames (, and . step one frame)")
    return btn


class MomentPicker(tk.Frame):
    """One time field (plus its 🎞 / ◉ buttons)."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        value: str = "",
        width: int = 9,
        source: Source | None = None,
        here: Here | None = None,
        on_change: Callable[[], object] | None = None,
        tip: str = "Use the frame shown in the preview",
    ) -> None:
        super().__init__(parent)
        self.var = tk.StringVar(value=value)
        self._source, self._here, self._on_change = source, here, on_change
        self.entry = tk.Entry(self, textvariable=self.var, width=width)
        self.entry.pack(side="left", padx=(2, 0))
        if source is not None:
            frame_button(self, self.var, source).pack(side="left")
        if here is not None:
            self._button("◉", self.use_here, tip)
        if on_change is not None:
            self.var.trace_add("write", self._changed)

    def _changed(self, _name: str, _index: str, _mode: str) -> None:
        if self._on_change is not None:
            self._on_change()

    def _button(self, text: str, command: Callable[[], object], tip: str) -> None:
        from tabs.base_tab import add_tooltip

        btn = tk.Button(self, text=text, relief="flat", width=2, cursor="hand2", command=command)
        btn.pack(side="left")
        add_tooltip(btn, tip)

    @property
    def text(self) -> str:
        return self.var.get().strip()

    def use_here(self) -> None:
        """Take the preview's position (to the millisecond: frame-exact after , and . steps)."""
        if self._here is not None:
            self.set(max(0.0, float(self._here())))

    def set(self, seconds: float | None) -> None:
        """Show *seconds* (None: empty)."""
        self.var.set("" if seconds is None else ff.format_time(seconds))

    def seconds(self, default: float | None = None, name: str = "Time") -> float | None:
        """The time in seconds; *default* when empty.  ValueError naming the field
        (*name*) when it isn't a time."""
        if not self.text:
            return default
        try:
            return ff.parse_time(self.text)
        except ValueError:
            raise ValueError(
                f"{name}: '{self.text}' isn't a time - use 12.5, 1:02.5 or 1:02:03."
            ) from None


class RangePicker(tk.Frame):
    """``[from] to [to]  (hint)``: part of a video.  An empty *to* means the end."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        start: str = "0",
        end: str = "",
        joiner: str = "to",
        end_hint: str = "",
        source: Source | None = None,
        here: Here | None = None,
        on_change: Callable[[], object] | None = None,
    ) -> None:
        super().__init__(parent)
        self.start = MomentPicker(
            self,
            value=start,
            source=source,
            here=here,
            on_change=on_change,
            tip="From the frame shown (i)",
        )
        self.start.pack(side="left")
        tk.Label(self, text=f" {joiner} ").pack(side="left")
        self.end = MomentPicker(
            self,
            value=end,
            source=source,
            here=here,
            on_change=on_change,
            tip="To the frame shown (o)",
        )
        self.end.pack(side="left")
        if end_hint:
            from tabs.base_tab import CLR

            tk.Label(self, text=f"  {end_hint}", fg=CLR["fgdim"]).pack(side="left")

    def span(self, duration: float | None = None) -> tuple[float, float | None]:
        """(start, end) in seconds - end None when empty (the end of the video),
        and never past *duration* when that's known.  ValueError when the
        times can't be used."""
        a = self.start.seconds(0.0, "From") or 0.0
        b = self.end.seconds(None, "To")
        if duration and b is not None:
            b = min(b, duration)
        if a < 0 or (b is not None and b <= a):
            raise ValueError("The end must be after the start.")
        return a, b

    def set(self, start: float | None, end: float | None) -> None:
        self.start.set(start)
        self.end.set(end)
