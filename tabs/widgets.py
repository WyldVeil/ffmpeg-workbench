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
import os
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from core import encoders
from core import ffmpeg as ff
from core.i18n import t
from core.skins import MONO_FONT, UI_FONT


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

    def __init__(self, parent, width: int = 480, height: int = 270,
                 show_time: bool = True, on_click: Optional[Callable[[], None]] = None):
        clr = _clr()
        super().__init__(parent, bg=clr["bg"])
        self._pw, self._ph = width, height
        self._path: Optional[str] = None
        self._duration = 0.0
        self._photo: Optional[tk.PhotoImage] = None
        self._lock = threading.Lock()
        self._wanted: Optional[float] = None       # newest requested time
        self._busy = False
        self._shown_t: Optional[float] = None
        self._last_at = 0.0
        self._vf = ""                               # optional preview filter
        self._keep_time = False
        self._gen = 0                               # bumps on every load()
        self.after_draw: Optional[Callable[[], None]] = None   # called after each new frame
        self.canvas = tk.Canvas(self, width=width, height=height, bg="#000000",
                                highlightthickness=1, highlightbackground=clr["border"],
                                cursor="hand2" if on_click else "")
        self.canvas.pack()
        self._time_lbl = None
        if show_time:
            self._time_lbl = tk.Label(self, text="", bg=clr["bg"], fg=clr["fgdim"],
                                      font=(MONO_FONT, 8))
            self._time_lbl.pack(anchor="e")
        if on_click:
            self.canvas.bind("<Button-1>", lambda _e: on_click())
        self._placeholder(_tr("preview.no_file", "No video loaded"))

    # ── public ───────────────────────────────────────────────────────────────

    def load(self, path: Optional[str], duration: Optional[float] = None,
             at: float = 0.0) -> None:
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
        threading.Thread(target=self._worker, daemon=True, name="FramePreview").start()

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
    def path(self) -> Optional[str]:
        return self._path

    # ── internals ────────────────────────────────────────────────────────────

    def _placeholder(self, text: str) -> None:
        self.canvas.delete("all")
        self._photo = None
        self.canvas.create_text(self._pw // 2, self._ph // 2, text=text,
                                fill="#777777", font=(UI_FONT, 10))
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
            png = ff.grab_frame_png(path or "", at, width=self._pw, height=self._ph, vf=vf, keep_time=keep)
            self._deliver(gen, at, png)

    def _deliver(self, gen: int, at: float, png: Optional[bytes]) -> None:
        from core import ui
        ui.call_soon(self._apply, gen, at, png)

    def _apply(self, gen: int, at: float, png: Optional[bytes]) -> None:
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

    def image_box(self) -> Optional[tuple]:
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

    def __init__(self, parent, width: int = 360, height: int = 203,
                 before_label: str = "", after_label: str = ""):
        clr = _clr()
        super().__init__(parent, bg=clr["bg"])
        row = tk.Frame(self, bg=clr["bg"])
        row.pack()
        self.views = []
        for label in (before_label or _tr("preview.before", "Before"),
                      after_label or _tr("preview.after", "After")):
            col = tk.Frame(row, bg=clr["bg"])
            col.pack(side="left", padx=4)
            tk.Label(col, text=label, bg=clr["bg"], fg=clr["fgdim"],
                     font=(UI_FONT, 9, "bold")).pack(anchor="w")
            view = FramePreview(col, width, height, show_time=False)
            view.pack()
            self.views.append(view)
        self.view_before, self.view_after = self.views
        bar = tk.Frame(self, bg=clr["bg"])
        bar.pack(fill="x", pady=(4, 0))
        self._pos = tk.DoubleVar(value=0.0)
        self._scale = ttk.Scale(bar, from_=0.0, to=1.0, variable=self._pos,
                                command=lambda _v: self._moved())
        self._scale.pack(side="left", fill="x", expand=True, padx=(4, 8))
        self._time = tk.Label(bar, text="", width=12, bg=clr["bg"], fg=clr["fgdim"],
                              font=(MONO_FONT, 9))
        self._time.pack(side="right")
        self._duration = 0.0

    def load(self, path: Optional[str], at: Optional[float] = None) -> None:
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

    def __init__(self, parent, label: str, var: Optional[tk.StringVar] = None, *,
                 kind: str = "video", save: bool = False, ext: str = ".mp4",
                 width: int = 56, label_width: int = 16,
                 on_change: Optional[Callable[[str], None]] = None,
                 suggest: Optional[Callable[[], str]] = None):
        clr = _clr()
        super().__init__(parent, bg=clr["bg"])
        self.var = var if var is not None else tk.StringVar()
        self._kind, self._save, self._ext = kind, save, ext
        self._on_change, self._suggest = on_change, suggest
        self._job = None
        tk.Label(self, text=label, width=label_width, anchor="w", bg=clr["bg"], fg=clr["fg"],
                 font=(UI_FONT, 10, "bold")).pack(side="left")
        self.entry = tk.Entry(self, textvariable=self.var, width=width, relief="flat")
        self.entry.pack(side="left", fill="x", expand=True, padx=6)
        text = (_tr("common.save_as", "Save as…") if save else _tr("btn.browse", "Browse…"))
        tk.Button(self, text=text, command=self._browse, cursor="hand2",
                  relief="flat").pack(side="left")
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
            chosen = dialogs.ask_save(current, kind=self._kind, defaultextension=self._ext,
                                      parent=self)
        else:
            chosen = dialogs.ask_open(self._kind, parent=self)
        if chosen:
            self.var.set(chosen)

    def _changed(self, *_a) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
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
    canvas = tk.Canvas(outer, bg=clr["bg"], highlightthickness=0, yscrollincrement=20)
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
_QUALITY_LABELS = [("very_high", "Very High"), ("high", "High"), ("good", "Good"),
                   ("low", "Low"), ("lowest", "Lowest")]
_SPEED_LABELS = [("fastest", "Fastest"), ("fast", "Fast"), ("balanced", "Balanced"),
                 ("slow", "Slow (smaller)"), ("slowest", "Slowest (smallest)")]


class EncoderPanel(tk.Frame):
    """Video encoder choice: codec, quality, speed, GPU, 10-bit."""

    def __init__(self, parent, *, codecs=("h264", "hevc", "av1", "vp9"),
                 default_codec: str = "h264", default_quality: str = "high",
                 default_speed: str = "balanced", show_ten_bit: bool = True,
                 on_change: Optional[Callable[[], None]] = None):
        clr = _clr()
        super().__init__(parent, bg=clr["bg"])
        self._on_change = on_change
        self._codecs = [c for c in _CODEC_LABELS if c[0] in codecs]
        self._codec_var = tk.StringVar(value=dict(self._codecs).get(default_codec, self._codecs[0][1]))
        self._quality_var = tk.StringVar(value=dict(_QUALITY_LABELS)[encoders.quality_key(default_quality)])
        self._speed_var = tk.StringVar(value=dict(_SPEED_LABELS).get(default_speed, "Balanced"))
        self._hw_var = tk.StringVar(value=_tr("encoder.hw_auto", "Auto (use GPU if available)"))
        self._ten_bit = tk.BooleanVar(value=False)

        def lbl(text):
            return tk.Label(self, text=text, bg=clr["bg"], fg=clr["fg"], font=(UI_FONT, 9))

        lbl(_tr("encoder.codec", "Codec")).grid(row=0, column=0, sticky="w", padx=(0, 6), pady=2)
        cb = ttk.Combobox(self, textvariable=self._codec_var, state="readonly", width=30,
                          values=[v for _, v in self._codecs])
        cb.grid(row=0, column=1, sticky="w", pady=2)
        lbl(_tr("encoder.quality", "Quality")).grid(row=0, column=2, sticky="w", padx=(14, 6))
        qb = ttk.Combobox(self, textvariable=self._quality_var, state="readonly", width=11,
                          values=[v for _, v in _QUALITY_LABELS])
        qb.grid(row=0, column=3, sticky="w")
        lbl(_tr("encoder.speed", "Speed")).grid(row=1, column=0, sticky="w", padx=(0, 6), pady=2)
        sb = ttk.Combobox(self, textvariable=self._speed_var, state="readonly", width=30,
                          values=[v for _, v in _SPEED_LABELS])
        sb.grid(row=1, column=1, sticky="w", pady=2)
        lbl(_tr("encoder.hardware", "Encoder")).grid(row=1, column=2, sticky="w", padx=(14, 6))
        self._hw_box = ttk.Combobox(self, textvariable=self._hw_var, state="readonly", width=26)
        self._hw_box.grid(row=1, column=3, sticky="w")
        if show_ten_bit:
            tk.Checkbutton(self, text=_tr("encoder.ten_bit", "10-bit colour (HDR-safe, less compatible)"),
                           variable=self._ten_bit, bg=clr["bg"], fg=clr["fg"],
                           selectcolor=clr["input_bg"], activebackground=clr["bg"],
                           command=self._changed).grid(row=2, column=1, columnspan=3, sticky="w")
        self._status = tk.Label(self, text="", bg=clr["bg"], fg=clr["fgdim"], font=(UI_FONT, 8))
        self._status.grid(row=3, column=0, columnspan=4, sticky="w", pady=(2, 0))
        for w in (cb, qb, sb, self._hw_box):
            w.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self._hw_choices = [("auto", self._hw_var.get()),
                            ("off", _tr("encoder.hw_off", "Software (best quality per MB)"))]
        self._hw_box.config(values=[v for _, v in self._hw_choices])
        from core import ui
        ui.run_in_thread(encoders.working_hw_kinds, on_done=self._hw_detected, name="EncoderPanelProbe")
        self._changed(notify=False)        # the owner is still being built: don't call back into it yet

    def _hw_detected(self, kinds) -> None:
        if not self.winfo_exists():
            return
        for k in kinds:
            self._hw_choices.append((k, encoders.HW_LABELS.get(k, k)))
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
        return encoders.video(self.codec, self.quality, speed=self.speed, hw=hw,
                              ten_bit=self.ten_bit, container=container, **kwargs)

    def _changed(self, notify: bool = True) -> None:
        try:
            ve = self.video_encode()
            self._status.config(text=_tr("encoder.using", "Will encode with") + f": {ve.label}")
        except Exception:
            pass
        if notify and self._on_change:
            self._on_change()
