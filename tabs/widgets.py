"""
tabs/widgets.py - Reusable composite widgets for tool tabs.

FramePreview
    Shows the frame of a video at any time position inside the app (no
    external player window).  Frames are decoded by FFmpeg in a background
    thread; while the user drags a slider only the newest request is kept, so
    scrubbing stays responsive on long files.

CompareView
    Before/after FramePreviews of one video with a shared time slider; the
    "after" side shows a live FFmpeg filter (colour grades, LUTs, keys...).

FileRow
    Label + entry + Browse button bound to a StringVar - the entry is the
    single source of truth, so typed or pasted paths work too.

EncoderPanel
    Codec / quality / speed / hardware-acceleration controls backed by
    core/encoders.py.  ``panel.video_encode(container)`` returns a
    :class:`core.encoders.VideoEncode` with correct arguments for whichever
    encoder (software or GPU) is selected and actually works on this machine.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import os
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from core import encoders, tasks
from core import ffmpeg as ff
from core.i18n import t
from core.scaling import px
from core.skins import MONO_FONT, UI_FONT
from tabs import framestep

logger = logging.getLogger("crossfader.widgets")


def _clr():
    from tabs.base_tab import CLR

    return CLR


def _tr(key: str, fallback: str) -> str:
    text = t(key)
    return fallback if text == key else text


# ─────────────────────────────────────────────────────────────────────────────
#  FramePreview
# ─────────────────────────────────────────────────────────────────────────────


class FramePreview(tk.Frame):
    """In-app still-frame viewer for a video file."""

    def __init__(
        self,
        parent,
        width: int = 480,
        height: int = 270,
        show_time: bool = True,
        on_click: Callable[[], None] | None = None,
    ):
        clr = _clr()
        super().__init__(parent, bg=clr["bg"])
        self._pw, self._ph = px(width), px(height)  # design px -> screen px
        self._path: str | None = None
        self._duration = 0.0
        self._photo: tk.PhotoImage | None = None
        self._lock = threading.Lock()
        self._wanted: float | None = None  # newest requested time
        self._busy = False
        self._shown_t: float | None = None
        self._last_at = 0.0
        self._vf = ""  # optional preview filter
        self._keep_time = False
        self._gen = 0  # bumps on every load()
        self.after_draw: Callable[[], None] | None = None  # called after each new frame
        self.canvas = tk.Canvas(
            self,
            width=self._pw,
            height=self._ph,
            bg="#000000",
            highlightthickness=px(1),
            highlightbackground=clr["border"],
            cursor="hand2" if on_click else "",
        )
        self.canvas.pack()
        self._time_lbl = None
        if show_time:
            self._time_lbl = tk.Label(
                self, text="", bg=clr["bg"], fg=clr["fgdim"], font=(MONO_FONT, 8)
            )
            self._time_lbl.pack(anchor="e")
        if on_click:
            self.canvas.bind("<Button-1>", lambda _e: on_click())
        self._placeholder(_tr("preview.no_file", "No video loaded"))

    # ── public ───────────────────────────────────────────────────────────────

    def load(self, path: str | None, duration: float | None = None, at: float = 0.0) -> None:
        """Set the source file and show the frame at *at* seconds."""
        self._gen += 1
        self._path = path or None
        self._shown_t = None
        if not self._path:
            self._duration = 0.0
            self._placeholder(_tr("preview.no_file", "No video loaded"))
            return
        if duration is None:
            info = ff.try_probe(self._path)
            duration = info.duration if info else 0.0
            if info is not None and not info.has_video:
                self._placeholder(_tr("preview.audio_only", "Audio file — no picture"))
                return
        self._duration = max(0.0, float(duration or 0.0))
        self._placeholder(_tr("preview.loading", "Loading preview…"))
        self.show(at)

    def show(self, at: float) -> None:
        """Request the frame at *at* seconds (coalesced, non-blocking)."""
        if not self._path:
            return
        if self._duration:
            at = max(0.0, min(float(at), max(0.0, self._duration - 0.04)))
        self._last_at = at
        with self._lock:
            self._wanted = at
            if self._busy:
                return
            self._busy = True
        tasks.submit(self._worker, name="FramePreview", pool="preview")

    def clear(self) -> None:
        self.load(None)

    def set_filter(self, vf: str, keep_time: bool = False) -> None:
        """Show frames through FFmpeg filter chain *vf* ("" = untouched).
        *keep_time* is needed for time-based filters such as subtitles."""
        if (vf or "") == self._vf and keep_time == self._keep_time:
            return
        self._vf = vf or ""
        self._keep_time = keep_time
        if self._path:
            self.show(self._last_at)

    @property
    def position(self) -> float:
        return self._last_at

    @property
    def path(self) -> str | None:
        return self._path

    # ── internals ────────────────────────────────────────────────────────────

    def _placeholder(self, text: str) -> None:
        self.canvas.delete("all")
        self._photo = None
        self.canvas.create_text(
            self._pw // 2, self._ph // 2, text=text, fill="#777777", font=(UI_FONT, 10)
        )
        if self._time_lbl is not None:
            self._time_lbl.config(text="")

    def _worker(self) -> None:
        # Serves the newest request until none is left.  The request is read
        # together with the current file/filter generation: a reload while a
        # frame is being grabbed must be served too, never dropped (the view
        # would otherwise stay on "Loading preview..." for good).
        while True:
            with self._lock:
                at = self._wanted
                self._wanted = None
                if at is None:
                    self._busy = False
                    return
                gen, path, vf, keep = self._gen, self._path, self._vf, self._keep_time
            png = ff.grab_frame_png(
                path or "", at, width=self._pw, height=self._ph, vf=vf, keep_time=keep
            )
            self._deliver(gen, at, png)

    def _deliver(self, gen: int, at: float, png: bytes | None) -> None:
        from core import ui

        ui.call_soon(self._apply, gen, at, png)

    def _apply(self, gen: int, at: float, png: bytes | None) -> None:
        if gen != self._gen or not self.winfo_exists():
            return
        if png is None:
            self._placeholder(_tr("preview.failed", "Could not decode a frame here"))
            return
        try:
            photo = tk.PhotoImage(master=self, data=base64.b64encode(png).decode("ascii"))
        except tk.TclError:
            self._placeholder(_tr("preview.failed", "Could not decode a frame here"))
            return
        self._photo = photo
        self.canvas.delete("all")
        self.canvas.create_image(self._pw // 2, self._ph // 2, image=photo)
        self._shown_t = at
        if self._time_lbl is not None:
            self._time_lbl.config(text=ff.format_time(at))
        if self.after_draw is not None:
            self.after_draw()

    def image_box(self) -> tuple | None:
        """(x0, y0, width, height) of the frame on the canvas, None before one is shown."""
        if self._photo is None:
            return None
        w, h = self._photo.width(), self._photo.height()
        return (self._pw - w) // 2, (self._ph - h) // 2, w, h


# ─────────────────────────────────────────────────────────────────────────────
#  CompareView
# ─────────────────────────────────────────────────────────────────────────────


class CompareView(tk.Frame):
    """Before / after preview of one video with a shared position slider."""

    def __init__(
        self,
        parent,
        width: int = 360,
        height: int = 203,
        before_label: str = "",
        after_label: str = "",
    ):
        clr = _clr()
        super().__init__(parent, bg=clr["bg"])
        row = tk.Frame(self, bg=clr["bg"])
        row.pack()
        self.views = []
        for label in (
            before_label or _tr("preview.before", "Before"),
            after_label or _tr("preview.after", "After"),
        ):
            col = tk.Frame(row, bg=clr["bg"])
            col.pack(side="left", padx=px(4))
            tk.Label(
                col, text=label, bg=clr["bg"], fg=clr["fgdim"], font=(UI_FONT, 9, "bold")
            ).pack(anchor="w")
            view = FramePreview(col, width, height, show_time=False)
            view.pack()
            self.views.append(view)
        self.view_before, self.view_after = self.views
        bar = tk.Frame(self, bg=clr["bg"])
        bar.pack(fill="x", pady=px(4, 0))
        self._pos = tk.DoubleVar(value=0.0)
        self._scale = ttk.Scale(
            bar, from_=0.0, to=1.0, variable=self._pos, command=lambda _v: self._moved()
        )
        self._scale.pack(side="left", fill="x", expand=True, padx=px(4, 8))
        framestep.slider(self._scale, self.view_before)  # , and . step a frame
        self._time = tk.Label(
            bar, text="", width=12, bg=clr["bg"], fg=clr["fgdim"], font=(MONO_FONT, 9)
        )
        self._time.pack(side="right")
        self._duration = 0.0

    def load(self, path: str | None, at: float | None = None) -> None:
        info = ff.try_probe(path) if path else None
        self._duration = info.duration if info else 0.0
        pos = at if at is not None else self._duration * 0.25
        self._scale.configure(to=max(self._duration, 0.01))
        self._pos.set(pos)
        for view in self.views:
            view.load(path, self._duration, pos)
        self._time.config(text=ff.format_time(pos) if path else "")

    def set_filter(self, vf: str, keep_time: bool = False) -> None:
        self.view_after.set_filter(vf, keep_time)

    @property
    def position(self) -> float:
        return float(self._pos.get())

    def _moved(self) -> None:
        pos = self.position
        self._time.config(text=ff.format_time(pos))
        for view in self.views:
            view.show(pos)


# ─────────────────────────────────────────────────────────────────────────────
#  FileRow
# ─────────────────────────────────────────────────────────────────────────────


class FileRow(tk.Frame):
    """``Label [entry......] [Browse]`` bound to a StringVar.

    The entry is the single source of truth: :attr:`path` reads it at the
    moment it is needed, so typed and pasted paths work exactly like
    browsed ones.  *on_change(path)* fires (debounced) when the entry holds
    an existing file (any text for save rows)."""

    def __init__(
        self,
        parent,
        label: str,
        var: tk.StringVar | None = None,
        *,
        kind: str = "video",
        save: bool = False,
        ext: str = ".mp4",
        width: int = 56,
        label_width: int = 16,
        on_change: Callable[[str], object] | None = None,
        suggest: Callable[[], str] | None = None,
    ):
        clr = _clr()
        super().__init__(parent, bg=clr["bg"])
        self.var = var if var is not None else tk.StringVar()
        self._kind, self._save, self._ext = kind, save, ext
        self._on_change, self._suggest = on_change, suggest
        self._job = None
        tk.Label(
            self,
            text=label,
            width=label_width,
            anchor="w",
            bg=clr["bg"],
            fg=clr["fg"],
            font=(UI_FONT, 10, "bold"),
        ).pack(side="left")
        self.entry = tk.Entry(self, textvariable=self.var, width=width, relief="flat")
        self.entry.pack(side="left", fill="x", expand=True, padx=px(6))
        text = _tr("common.save_as", "Save as…") if save else _tr("btn.browse", "Browse…")
        tk.Button(self, text=text, command=self._browse, cursor="hand2", relief="flat").pack(
            side="left"
        )
        if on_change is not None:
            self.var.trace_add("write", self._changed)

    @property
    def path(self) -> str:
        return self.var.get().strip().strip('"').strip()

    def set(self, path: str) -> None:
        self.var.set(path)

    def _browse(self) -> None:
        from core import dialogs

        if self._save:
            current = self.path or (self._suggest() if self._suggest else "")
            chosen = dialogs.ask_save(
                current, kind=self._kind, defaultextension=self._ext, parent=self
            )
        else:
            chosen = dialogs.ask_open(self._kind, parent=self)
        if chosen:
            self.var.set(chosen)

    def _changed(self, *_a) -> None:
        if self._job is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._job)
        self._job = self.after(350, self._fire)

    def _fire(self) -> None:
        self._job = None
        path = self.path
        if self._on_change is not None and path and (self._save or os.path.isfile(path)):
            self._on_change(path)


# ─────────────────────────────────────────────────────────────────────────────
#  scroll_body
# ─────────────────────────────────────────────────────────────────────────────


def scroll_body(parent) -> tk.Frame:
    """Fill *parent* with a vertically scrolling area and return the inner
    frame to build into.  The mouse wheel works over any child (the app's
    global wheel handler scrolls the nearest canvas with a scroll command)."""
    clr = _clr()
    outer = tk.Frame(parent, bg=clr["bg"])
    outer.pack(fill="both", expand=True)
    canvas = tk.Canvas(outer, bg=clr["bg"], highlightthickness=0, yscrollincrement=px(20))
    vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=clr["bg"])
    win = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    return inner


# ─────────────────────────────────────────────────────────────────────────────
#  EncoderPanel
# ─────────────────────────────────────────────────────────────────────────────

_CODEC_LABELS = [
    ("h264", "H.264 / AVC (most compatible)"),
    ("hevc", "H.265 / HEVC (smaller files)"),
    ("av1", "AV1 (smallest, slower)"),
    ("vp9", "VP9 (WebM)"),
    ("prores", "ProRes 422 (editing)"),
    ("dnxhr", "DNxHR (editing)"),
    ("ffv1", "FFV1 (lossless archive)"),
]
_QUALITY_LABELS = [
    ("very_high", "Very High"),
    ("high", "High"),
    ("good", "Good"),
    ("low", "Low"),
    ("lowest", "Lowest"),
]
_SPEED_LABELS = [
    ("fastest", "Fastest"),
    ("fast", "Fast"),
    ("balanced", "Balanced"),
    ("slow", "Slow (smaller)"),
    ("slowest", "Slowest (smallest)"),
]


class EncoderPanel(tk.Frame):
    """Video encoder choice: codec, quality, speed, GPU, 10-bit."""

    def __init__(
        self,
        parent,
        *,
        codecs=("h264", "hevc", "av1", "vp9"),
        default_codec: str = "h264",
        default_quality: str = "high",
        default_speed: str = "balanced",
        show_ten_bit: bool = True,
        on_change: Callable[[], None] | None = None,
    ):
        clr = _clr()
        super().__init__(parent, bg=clr["bg"])
        self._on_change = on_change
        self._codecs = [c for c in _CODEC_LABELS if c[0] in codecs]
        self._codec_var = tk.StringVar(
            value=dict(self._codecs).get(default_codec, self._codecs[0][1])
        )
        self._quality_var = tk.StringVar(
            value=dict(_QUALITY_LABELS)[encoders.quality_key(default_quality)]
        )
        self._speed_var = tk.StringVar(value=dict(_SPEED_LABELS).get(default_speed, "Balanced"))
        self._hw_var = tk.StringVar(value=_tr("encoder.hw_auto", "Auto (use GPU if available)"))
        self._ten_bit = tk.BooleanVar(value=False)

        def lbl(text):
            return tk.Label(self, text=text, bg=clr["bg"], fg=clr["fg"], font=(UI_FONT, 9))

        lbl(_tr("encoder.codec", "Codec")).grid(
            row=0, column=0, sticky="w", padx=px(0, 6), pady=px(2)
        )
        cb = ttk.Combobox(
            self,
            textvariable=self._codec_var,
            state="readonly",
            width=30,
            values=[v for _, v in self._codecs],
        )
        cb.grid(row=0, column=1, sticky="w", pady=px(2))
        lbl(_tr("encoder.quality", "Quality")).grid(row=0, column=2, sticky="w", padx=px(14, 6))
        qb = ttk.Combobox(
            self,
            textvariable=self._quality_var,
            state="readonly",
            width=11,
            values=[v for _, v in _QUALITY_LABELS],
        )
        qb.grid(row=0, column=3, sticky="w")
        lbl(_tr("encoder.speed", "Speed")).grid(
            row=1, column=0, sticky="w", padx=px(0, 6), pady=px(2)
        )
        sb = ttk.Combobox(
            self,
            textvariable=self._speed_var,
            state="readonly",
            width=30,
            values=[v for _, v in _SPEED_LABELS],
        )
        sb.grid(row=1, column=1, sticky="w", pady=px(2))
        lbl(_tr("encoder.hardware", "Encoder")).grid(row=1, column=2, sticky="w", padx=px(14, 6))
        self._hw_box = ttk.Combobox(self, textvariable=self._hw_var, state="readonly", width=26)
        self._hw_box.grid(row=1, column=3, sticky="w")
        if show_ten_bit:
            tk.Checkbutton(
                self,
                text=_tr("encoder.ten_bit", "10-bit colour (HDR-safe, less compatible)"),
                variable=self._ten_bit,
                bg=clr["bg"],
                fg=clr["fg"],
                selectcolor=clr["input_bg"],
                activebackground=clr["bg"],
                command=self._changed,
            ).grid(row=2, column=1, columnspan=3, sticky="w")
        self._status = tk.Label(self, text="", bg=clr["bg"], fg=clr["fgdim"], font=(UI_FONT, 8))
        self._status.grid(row=3, column=0, columnspan=4, sticky="w", pady=px(2, 0))
        for w in (cb, qb, sb, self._hw_box):
            w.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self._hw_choices = [
            ("auto", self._hw_var.get()),
            ("off", _tr("encoder.hw_off", "Software (best quality per MB)")),
        ]
        self._hw_box.config(values=[v for _, v in self._hw_choices])
        tasks.submit(encoders.working_hw_kinds, on_done=self._hw_detected, name="EncoderPanelProbe")
        self._changed(notify=False)  # the owner is still being built: don't call back into it yet

    def _hw_detected(self, kinds) -> None:
        if not self.winfo_exists():
            return
        for k in kinds:
            self._hw_choices.append((k, encoders.HW_LABELS.get(k) or k))
        self._hw_box.config(values=[v for _, v in self._hw_choices])
        self._changed()

    def _key(self, var, pairs, default):
        rev = {v: k for k, v in pairs}
        return rev.get(var.get(), default)

    # ── public API ──────────────────────────────────────────────────────────

    @property
    def codec(self) -> str:
        return self._key(self._codec_var, self._codecs, self._codecs[0][0])

    @property
    def quality(self) -> str:
        return self._key(self._quality_var, _QUALITY_LABELS, "high")

    @property
    def speed(self) -> str:
        return self._key(self._speed_var, _SPEED_LABELS, "balanced")

    @property
    def hw(self) -> str:
        return self._key(self._hw_var, self._hw_choices, "auto")

    @property
    def ten_bit(self) -> bool:
        return bool(self._ten_bit.get())

    def set_codec(self, codec: str) -> None:
        label = dict(self._codecs).get(codec)
        if label:
            self._codec_var.set(label)
            self._changed()

    def video_encode(self, container: str = "mp4", **kwargs) -> encoders.VideoEncode:
        hw = self.hw
        if self.codec in ("prores", "dnxhr", "ffv1"):
            hw = "off"
        return encoders.video(
            self.codec,
            self.quality,
            speed=self.speed,
            hw=hw,
            ten_bit=self.ten_bit,
            container=container,
            **kwargs,
        )

    def _changed(self, notify: bool = True) -> None:
        try:
            ve = self.video_encode()
            self._status.config(text=_tr("encoder.using", "Will encode with") + f": {ve.label}")
        except Exception:
            logger.debug("_changed: best-effort step failed", exc_info=True)
        if notify and self._on_change:
            self._on_change()


# ─────────────────────────────────────────────────────────────────────────────
#  here_button
# ─────────────────────────────────────────────────────────────────────────────


def here_button(
    parent,
    var: tk.StringVar,
    get_pos: Callable[[], float],
    on_set: Callable[[], None] | None = None,
    tip: str = "Use the frame shown in the preview",
) -> tk.Button:
    """A small ◉ button that puts the preview's position (to the millisecond,
    so frame-exact after , and . stepping) into the time field *var*."""
    from tabs.base_tab import add_tooltip

    def use():
        var.set(ff.format_time(max(0.0, float(get_pos()))))
        if on_set is not None:
            on_set()

    btn = tk.Button(parent, text="◉", relief="flat", width=2, cursor="hand2", command=use)
    add_tooltip(btn, tip)
    return btn


# ─────────────────────────────────────────────────────────────────────────────
#  FramePicker - choose a time by looking at the frames (for tools without a preview)
# ─────────────────────────────────────────────────────────────────────────────


class FramePicker(tk.Toplevel):
    """A small window to find a moment: the frame, a slider, ◀ ▶ buttons and
    the , and . keys (Shift: a second).  "Use this frame" puts its time into
    *var*."""

    def __init__(
        self,
        parent,
        path: str,
        var: tk.StringVar,
        fmt: Callable[[float], str] = ff.format_time,
        title: str = "Pick a frame",
    ):
        super().__init__(parent)
        clr = _clr()
        self.configure(bg=clr["bg"])
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self._var, self._fmt = var, fmt
        info = ff.try_probe(path)
        self._fps = framestep.fps_of(path)
        dur = info.duration if info else 0.0
        self.preview = FramePreview(self, 480, 270, show_time=False)
        self.preview.pack(padx=px(12), pady=px(12, 4))
        self.pos = tk.DoubleVar(value=0.0)
        self.slider = ttk.Scale(
            self,
            from_=0.0,
            to=max(0.0, dur - 1.0 / self._fps),
            variable=self.pos,
            command=lambda _v: self._moved(),
        )
        self.slider.pack(fill="x", padx=px(12))
        framestep.slider(self.slider, lambda: self._fps)
        bar = tk.Frame(self, bg=clr["bg"])
        bar.pack(fill="x", padx=px(12), pady=px(6))
        for label, frames, seconds in (("◀◀", 0, -1), ("◀", -1, 0), ("▶", 1, 0), ("▶▶", 0, 1)):
            tk.Button(
                bar,
                text=label,
                relief="flat",
                width=3,
                command=lambda f=frames, s=seconds: framestep.step(self.slider, f, s),
            ).pack(side="left")
        self.time_lbl = tk.Label(
            bar, text="", bg=clr["bg"], fg=clr["accent"], font=(MONO_FONT, 10, "bold")
        )
        self.time_lbl.pack(side="left", padx=px(10))
        tk.Button(bar, text="Cancel", relief="flat", command=self.destroy).pack(side="right")
        tk.Button(
            bar,
            text="✓  Use this frame",
            bg=clr["green"],
            fg="white",
            relief="flat",
            command=self._use,
        ).pack(side="right", padx=px(6))
        tk.Label(
            self,
            text="Keys:  ,  and  .  one frame   ·   with Shift, one second   ·   Enter to use it",
            bg=clr["bg"],
            fg=clr["fgdim"],
            font=(UI_FONT, 8),
        ).pack(pady=px(0, 8))
        for key, frames, seconds in (
            ("<comma>", -1, 0),
            ("<period>", 1, 0),
            ("<less>", 0, -1),
            ("<semicolon>", 0, -1),
            ("<greater>", 0, 1),
            ("<colon>", 0, 1),
        ):
            self.bind(key, lambda _e, f=frames, s=seconds: framestep.step(self.slider, f, s))
        self.bind("<Return>", lambda _e: self._use())
        self.bind("<Escape>", lambda _e: self.destroy())
        try:
            start = ff.parse_time(var.get()) if var.get().strip() else 0.0
        except ValueError:
            start = 0.0
        start = min(float(self.slider.cget("to")), max(0.0, framestep.snap(start, self._fps)))
        self.preview.load(path, dur, start)
        self.pos.set(start)
        self._moved()
        self.slider.focus_set()
        with contextlib.suppress(tk.TclError):
            self.grab_set()

    def _moved(self):
        at = framestep.snap(float(self.pos.get()), self._fps)
        self.time_lbl.config(text=f"{ff.format_time(at)}   ·   frame {round(at * self._fps) + 1:,}")
        self.preview.show(at)

    def _use(self):
        self._var.set(self._fmt(framestep.snap(float(self.pos.get()), self._fps)))
        self.destroy()


def pick_button(
    parent,
    var: tk.StringVar,
    get_path: Callable[[], str | None],
    fmt: Callable[[float], str] = ff.format_time,
) -> tk.Button:
    """A 🎞 button that opens a :class:`FramePicker` for the time field *var*."""
    from tabs.base_tab import add_tooltip

    def open_picker():
        path = get_path()
        if not path or not os.path.isfile(path):
            from tkinter import messagebox

            messagebox.showinfo("Pick a frame", "Choose a video first.", parent=parent)
            return
        FramePicker(parent, path, var, fmt)

    btn = tk.Button(parent, text="🎞", relief="flat", width=2, cursor="hand2", command=open_picker)
    add_tooltip(btn, "Find the moment by looking at the frames (, and . step one frame)")
    return btn


def add_pickers(
    root,
    variables,
    get_path: Callable[[], str | None],
    fmt: Callable[[float], str] = ff.format_time,
) -> int:
    """Put a 🎞 button right after the entry of each time field in *variables*
    (looked up under *root*).  Returns how many were added."""
    names = {str(v): v for v in variables}
    added = 0
    todo = [root]
    while todo:
        w = todo.pop()
        todo.extend(w.winfo_children())
        try:
            tv = (
                str(w.cget("textvariable"))
                if w.winfo_class() in ("Entry", "Spinbox", "TEntry")
                else ""
            )
        except tk.TclError:
            tv = ""
        if tv in names and w.winfo_manager() == "pack":
            pick_button(w.master, names[tv], get_path, fmt).pack(side="left", after=w)
            added += 1
    return added
