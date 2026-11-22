"""
base_tab.py  ─  Shared utilities for all Studio Pro tabs.

Provides:
  • CLR - live skin color dict
  • BaseTab - base class for all tool tabs
  • make_header, make_console, make_file_row, make_section,
    make_render_btn, make_labeled_entry - UI factory helpers
"""
from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import Any, Literal

from core import dialogs, logs, outputs, proc, settings, ui
from core import ffmpeg as ff
from core.hardware import open_in_explorer, reveal_file
from core.i18n import t
from core.scaling import px
from core.skins import MONO_FONT, UI_FONT
from core.timecode import Timecode
from core.tools import ToolId
from tabs.kit.contracts import Shell, Transport


def _load_clr():
    try:
        from core.skins import get_skin, load_skin_name
        skin = get_skin(load_skin_name())
        return {
            "bg":         skin["content"],
            "panel":      skin["panel"],
            "accent":     skin["accent"],
            "fg":         skin["fg"],
            "fgdim":      skin["fgdim"],
            "green":      skin["green"],
            "red":        skin["red"],
            "orange":     skin["orange"],
            "pink":       skin["pink"],
            "console_bg": skin["console_bg"],
            "console_fg": skin["console_fg"],
            "border":     skin.get("border", "#2C2C2C"),
            "input_bg":   skin.get("input_bg", "#1E1E1E"),
            "input_fg":   skin.get("input_fg", "#E2E2E2"),
        }
    except Exception:
        return {
            "bg": "#1A1A1A", "panel": "#212121", "accent": "#3A8EE6",
            "fg": "#E2E2E2", "fgdim": "#7A7A7A", "green": "#2ECC71",
            "red": "#E74C3C", "orange": "#F39C12", "pink": "#C0392B",
            "console_bg": "#0A0A0A", "console_fg": "#00E676",
            "border": "#2C2C2C", "input_bg": "#1E1E1E", "input_fg": "#E2E2E2",
        }


CLR = _load_clr()


# ─────────────────────────────────────────────────────────────────────────────
#  UI factory helpers (module-level so tabs can use them directly)
# ─────────────────────────────────────────────────────────────────────────────

def make_header(parent, title, subtitle="", icon=""):
    """
    Professional two-row tab header:
      ┌───────────────────────────────────────────┐
      │  ICON  Title                              │
      │        Subtitle description               │
      └───────────────────────────────────────────┘
    Returns the outer header frame.
    """
    hdr = tk.Frame(parent, bg=CLR["panel"])
    hdr.pack(fill="x")

    inner = tk.Frame(hdr, bg=CLR["panel"])
    inner.pack(fill="x", padx=px(22), pady=px(14, 12))

    left = tk.Frame(inner, bg=CLR["panel"])
    left.pack(side="left", fill="both", expand=True)

    title_text = f"{icon}  {title}".strip() if icon else title
    tk.Label(left, text=title_text,
             font=(UI_FONT, 14, "bold"),
             bg=CLR["panel"], fg=CLR["accent"],
             anchor="w").pack(side="top", fill="x")

    if subtitle:
        tk.Label(left, text=subtitle,
                 font=(UI_FONT, 9),
                 bg=CLR["panel"], fg=CLR["fgdim"],
                 anchor="w").pack(side="top", fill="x", pady=px(2, 0))

    # Bottom border
    tk.Frame(parent, bg=CLR["border"], height=px(1)).pack(fill="x")
    return hdr


def make_section(parent, title="", padx=20, pady=(8, 4)):
    """
    Return a styled LabelFrame for grouping related controls.
    Matches the professional card appearance.
    """
    lf = tk.LabelFrame(
        parent,
        text=f"  {title}  " if title else "",
        bg=CLR["bg"],
        fg=CLR["fgdim"],
        font=(UI_FONT, 8, "bold"),
        bd=px(1),
        relief="solid",
        padx=px(14), pady=px(8),
        highlightthickness=0,
    )
    # LabelFrame borders are drawn in the widget's fg - set border via relief
    lf.pack(fill="x", padx=px(padx), pady=px(pady))
    return lf


def make_console(parent, height=8):
    """
    Styled console text widget + scrollbar.
    Returns (Text, Scrollbar) - caller packs them.
    """
    c = tk.Text(
        parent,
        height=height,
        bg=CLR["console_bg"],
        fg=CLR["console_fg"],
        font=(MONO_FONT, 9),
        wrap="none",
        relief="flat",
        bd=0,
        insertbackground=CLR["console_fg"],
        selectbackground=CLR["accent"],
        selectforeground="#FFFFFF",
        padx=px(12), pady=px(8),
        highlightthickness=0,
    )
    # Tag for green "Done" lines
    c.tag_configure("success", foreground=CLR["green"])
    c.tag_configure("error",   foreground=CLR["red"])
    c.tag_configure("warn",    foreground=CLR["orange"])
    c.tag_configure("info",    foreground=CLR["accent"])
    c.tag_configure("dim",     foreground=CLR["fgdim"])

    sb = ttk.Scrollbar(parent, command=c.yview)
    c.configure(yscrollcommand=sb.set)
    return c, sb


def make_file_row(parent, label, var, browse_cmd, width=52, label_width=18):
    """
    Standard source/output file row with consistent label alignment.
    Returns the row Frame.
    """
    f = tk.Frame(parent, bg=CLR["bg"])
    tk.Label(f, text=label,
             font=(UI_FONT, 9, "bold"),
             width=label_width, anchor="e",
             bg=CLR["bg"], fg=CLR["fgdim"]).pack(side="left")
    tk.Entry(f, textvariable=var, width=width,
             relief="flat",
             bg=CLR["input_bg"], fg=CLR["input_fg"],
             insertbackground=CLR["accent"],
             highlightthickness=px(1),
             highlightbackground=CLR["border"],
             highlightcolor=CLR["accent"],
             font=(UI_FONT, 9)).pack(side="left", padx=px(8))
    btn = tk.Button(f, text=t("btn.browse"),
                    font=(UI_FONT, 9),
                    bg=CLR["panel"], fg=CLR["fg"],
                    activebackground=CLR["accent"],
                    activeforeground="#FFFFFF",
                    relief="flat", cursor="hand2",
                    padx=px(10), pady=px(3),
                    command=browse_cmd)
    btn.pack(side="left")
    return f


def make_labeled_entry(parent, label, var, width=8, tooltip=""):
    """Inline label + entry, optionally with a dim tooltip/hint."""
    f = tk.Frame(parent, bg=CLR["bg"])
    tk.Label(f, text=label,
             font=(UI_FONT, 9),
             bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
    tk.Entry(f, textvariable=var, width=width,
             relief="flat",
             bg=CLR["input_bg"], fg=CLR["input_fg"],
             insertbackground=CLR["accent"],
             highlightthickness=0,
             font=(UI_FONT, 9)).pack(side="left", padx=px(4))
    if tooltip:
        tk.Label(f, text=tooltip,
                 fg=CLR["fgdim"],
                 bg=CLR["bg"],
                 font=(UI_FONT, 8)).pack(side="left", padx=px(2, 0))
    return f


def make_render_btn(parent, text, command, color=None, width=24):
    """
    Large, prominent render/action button.
    Uses accent color by default; pass color= to override (green/orange/red).
    """
    bg = color or CLR["accent"]
    # Compute a darker active state
    btn = tk.Button(
        parent,
        text=text,
        font=(UI_FONT, 11, "bold"),
        bg=bg,
        fg="#FFFFFF",
        activebackground=CLR["fgdim"],
        activeforeground="#FFFFFF",
        relief="flat",
        cursor="hand2",
        height=2,
        width=width,
        bd=0,
        highlightthickness=0,
        command=command,
    )
    return btn


# ─────────────────────────────────────────────────────────────────────────────
#  ToolTip - hover tooltip for any widget
# ─────────────────────────────────────────────────────────────────────────────

class ToolTip:
    """
    Hover tooltip that appears after a short delay.

    Usage::

        btn = tk.Button(parent, text="Render")
        ToolTip(btn, "Start rendering the output file (Ctrl+S)")

    Or via the helper::

        add_tooltip(btn, "Start rendering the output file")
    """

    _DELAY = 500      # ms before showing
    _WRAP  = 280      # max tooltip width in pixels

    def __init__(self, widget, text):
        self._widget = widget
        self._text   = text
        self._tip    = None
        self._after  = None
        widget.bind("<Enter>",  self._on_enter, add=True)
        widget.bind("<Leave>",  self._on_leave, add=True)
        widget.bind("<Button>", self._on_leave, add=True)

    def _on_enter(self, event=None):
        self._cancel()
        self._after = self._widget.after(self._DELAY, self._show)

    def _on_leave(self, event=None):
        self._cancel()
        self._hide()

    def _cancel(self):
        if self._after:
            self._widget.after_cancel(self._after)
            self._after = None

    def _show(self):
        if self._tip or not self._text:
            return
        x = self._widget.winfo_rootx() + px(20)
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + px(4)
        self._tip = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        # Attempt to remove from taskbar on Windows
        try:
            tw.wm_attributes("-toolwindow", True)
        except Exception:
            pass
        lbl = tk.Label(tw, text=self._text,
                       bg="#2A2A2A", fg="#E0E0E0",
                       font=(UI_FONT, 8),
                       relief="solid", bd=px(1),
                       wraplength=px(self._WRAP),
                       justify="left",
                       padx=px(8), pady=px(5))
        lbl.pack()

    def _hide(self):
        if self._tip:
            self._tip.destroy()
            self._tip = None


def add_tooltip(widget, text):
    """Convenience function to attach a tooltip to any widget."""
    return ToolTip(widget, text)


# ─────────────────────────────────────────────────────────────────────────────
#  VideoTimeline - reusable canvas timeline with dual handles + playhead
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_tl(seconds):
    """Format seconds for timeline display."""
    return f"{Timecode.of(seconds):.2}"


class VideoTimeline(tk.Frame):
    """
    Reusable dual-handle timeline with playhead scrubber.

    Drop-in widget for any tab that needs a visual time range selector.
    Provides start/end handles (draggable), a playhead, tick marks,
    and optional preview integration via callback.

    Usage::

        tl = VideoTimeline(parent, on_change=my_callback)
        tl.pack(fill="x", padx=20, pady=4)
        tl.set_duration(120.5)            # set total duration in seconds
        tl.set_range(10.0, 90.0)          # set start/end programmatically
        s, e = tl.get_range()             # read current start/end
        t = tl.get_playhead()             # read playhead position
    """

    _PAD     = 28      # horizontal padding
    _HR      = 8       # handle radius
    _HEIGHT  = 100     # default canvas height

    def __init__(self, parent, on_change=None, height=None, show_handles=True):
        super().__init__(parent, bg=CLR["bg"])
        self._duration    = 0.0
        self._start       = 0.0
        self._end         = 0.0
        self._playhead    = 0.0
        self._dragging    = None
        self._last_moved  = "playhead"   # what , and . move: the handle used last
        self.fps          = 30.0         # set by the tab that loads the video
        self._on_change   = on_change    # callback(start, end, playhead)
        self._show_handles = show_handles
        ch = height or self._HEIGHT

        # Canvas
        self._canvas = tk.Canvas(self, bg="#0D0D0D", height=px(ch),
                                  highlightthickness=0, bd=0)
        self._canvas.pack(fill="x", padx=px(4), pady=px(4))
        self._canvas.bind("<Configure>",      lambda _: self._draw())
        self._canvas.bind("<ButtonPress-1>",   self._on_press)
        self._canvas.bind("<B1-Motion>",       self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", lambda _: self._on_release())
        from tabs import framestep
        framestep.register(self._canvas, self.step_frames, lambda: self.fps)
        ToolTip(self._canvas, "Drag the handles, or click one and press , and . to move it a frame "
                              "(with Shift, a second). i / o set in and out at the playhead.")

        # Info strip below canvas
        info = tk.Frame(self, bg=CLR["bg"])
        info.pack(fill="x", padx=px(8))
        self._pos_lbl = tk.Label(info, text="00:00.00",
                                  fg=CLR["accent"], bg=CLR["bg"],
                                  font=(MONO_FONT, 9, "bold"))
        self._pos_lbl.pack(side="left")
        self._range_lbl = tk.Label(info, text="",
                                    fg=CLR["fgdim"], bg=CLR["bg"],
                                    font=(MONO_FONT, 8))
        self._range_lbl.pack(side="right")

    # ── Public API ────────────────────────────────────────────────────────

    def set_duration(self, dur, fps=None):
        if fps:
            self.fps = float(fps)
        self._duration = max(0.0, dur)
        self._end = self._duration
        self._start = 0.0
        self._playhead = 0.0
        self._draw()

    def set_range(self, start, end):
        self._start = max(0.0, min(start, self._duration))
        self._end   = max(self._start, min(end, self._duration))
        self._draw()
        self._fire_change()

    def get_range(self):
        return (self._start, self._end)

    def get_playhead(self):
        return self._playhead

    def set_playhead(self, t):
        self._playhead = max(0.0, min(t, self._duration))
        self._draw()

    # ── Transport, MarksIn and MarksOut (tabs/kit/contracts.py) ────────────────────
    @property
    def position(self) -> float:
        return self._playhead

    @property
    def duration(self) -> float:
        return self._duration

    def seek(self, seconds: float) -> None:
        self.set_playhead(seconds)

    def mark_in(self) -> None:
        """Start of the range at the playhead (only a timeline with handles has a range)."""
        if self._show_handles:
            self._start = min(self._playhead, self._end)
            self._draw()
            self._fire_change()

    def mark_out(self) -> None:
        if self._show_handles:
            self._end = max(self._playhead, self._start)
            self._draw()
            self._fire_change()

    # ── Coordinate helpers ────────────────────────────────────────────────

    def _t2x(self, t, w):
        p = px(self._PAD)
        if self._duration <= 0:
            return p
        return p + int((t / self._duration) * (w - 2 * p))

    def _x2t(self, x, w):
        p = px(self._PAD)
        if self._duration <= 0:
            return 0.0
        return max(0.0, min(1.0, (x - p) / max(1, w - 2 * p))) * self._duration

    # ── Drawing ───────────────────────────────────────────────────────────

    def _draw(self):
        c = self._canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w < px(20):
            return
        c.delete("all")
        c.create_rectangle(0, 0, w, h, fill="#0D0D0D", outline="")
        pad, hr = px(self._PAD), px(self._HR)

        if self._duration <= 0:
            c.create_text(w // 2, h // 2,
                          text="Load a file to see the timeline",
                          fill=CLR["fgdim"], font=(UI_FONT, 10))
            return

        cy = h // 2

        # Track background
        c.create_rectangle(pad, cy - px(5), w - pad, cy + px(5),
                           fill="#222222", outline="")

        if self._show_handles:
            # Selection highlight
            xs = self._t2x(self._start, w)
            xe = self._t2x(self._end, w)
            c.create_rectangle(xs, cy - px(5), xe, cy + px(5),
                               fill="#1A5A98", outline="")

        # Tick marks + time labels
        num_ticks = min(20, max(5, w // px(80)))
        for i in range(num_ticks + 1):
            frac = i / num_ticks
            tx = self._t2x(self._duration * frac, w)
            c.create_line(tx, cy + px(7), tx, cy + px(14), fill="#444444", width=px(1))
            tick_t = self._duration * frac
            c.create_text(tx, cy + px(23), text=_fmt_tl(tick_t),
                          fill="#555555", font=(UI_FONT, 7))

        # Playhead
        xp = self._t2x(self._playhead, w)
        c.create_line(xp, cy - px(20), xp, cy + px(20), fill="#FFEB3B", width=px(2))
        c.create_polygon(xp - px(6), cy - px(20), xp + px(6), cy - px(20), xp, cy - px(11),
                         fill="#FFEB3B", outline="#000000", width=px(1))

        if self._show_handles:
            # Start handle (green)
            c.create_oval(xs - hr, cy - hr, xs + hr, cy + hr,
                          fill="#4CAF50", outline="white", width=px(2))
            c.create_text(xs, cy - px(20),
                          text=f"IN {_fmt_tl(self._start)}",
                          fill="#4CAF50", font=(MONO_FONT, 8, "bold"))

            # End handle (red)
            c.create_oval(xe - hr, cy - hr, xe + hr, cy + hr,
                          fill="#F44336", outline="white", width=px(2))
            c.create_text(xe, cy + px(34),
                          text=f"OUT {_fmt_tl(self._end)}",
                          fill="#F44336", font=(MONO_FONT, 8, "bold"))

            # Selection duration
            sel = max(0.0, self._end - self._start)
            c.create_text((xs + xe) // 2, px(12),
                          text=f"Selection: {_fmt_tl(sel)}",
                          fill="white", font=(UI_FONT, 9, "bold"))

        # Update info labels
        self._pos_lbl.config(text=_fmt_tl(self._playhead))
        if self._show_handles:
            sel = max(0.0, self._end - self._start)
            self._range_lbl.config(
                text=f"{_fmt_tl(self._start)} \u2192 {_fmt_tl(self._end)}  "
                     f"({_fmt_tl(sel)})")

    # ── Interaction ───────────────────────────────────────────────────────

    def _on_press(self, ev):
        w = self._canvas.winfo_width()
        if self._duration <= 0:
            return
        def near(a, b):
            return abs(a - b) < px(14)

        if self._show_handles:
            xs = self._t2x(self._start, w)
            xe = self._t2x(self._end, w)
            if near(ev.x, xs):
                self._dragging = "start"
                return
            if near(ev.x, xe):
                self._dragging = "end"
                return

        # Click anywhere else = move playhead
        self._dragging = "playhead"
        self._playhead = self._x2t(ev.x, w)
        self._draw()
        self._fire_change()

    def _on_drag(self, ev):
        if not self._dragging:
            return
        w = self._canvas.winfo_width()
        t = self._x2t(ev.x, w)

        if self._dragging == "start":
            self._start = max(0.0, min(t, self._end - 0.1))
        elif self._dragging == "end":
            self._end = max(self._start + 0.1, min(t, self._duration))
        elif self._dragging == "playhead":
            self._playhead = max(0.0, min(t, self._duration))

        self._draw()
        self._fire_change()

    def _on_release(self):
        if self._dragging:
            self._last_moved = self._dragging
        self._dragging = None

    def step_frames(self, n):
        """Move the handle used last (in, out, or the playhead) *n* frames."""
        if self._duration <= 0:
            return
        from tabs import framestep
        fps = self.fps or 30.0
        last = max(0.0, self._duration - 1.0 / fps)
        which = self._last_moved if self._show_handles else "playhead"
        if which == "start":
            self._start = min(max(0.0, framestep.snap(self._start, fps, n)), max(0.0, self._end - 1.0 / fps))
        elif which == "end":
            self._end = max(min(self._duration, framestep.snap(self._end, fps, n)), self._start + 1.0 / fps)
        else:
            self._playhead = min(last, max(0.0, framestep.snap(self._playhead, fps, n)))
        self._draw()
        self._fire_change()

    def _fire_change(self):
        if self._on_change:
            self._on_change(self._start, self._end, self._playhead)


# ─────────────────────────────────────────────────────────────────────────────
#  BaseTab
# ─────────────────────────────────────────────────────────────────────────────

class BaseTab(ttk.Frame):
    """
    Common base class for all tool tabs.

    Rendering
      run_ffmpeg(cmd, console, on_done, btn, btn_label, *, duration, output, inputs)
          Queue an FFmpeg command.  While it runs, *btn* shows the progress
          and becomes a Cancel button; *on_done(rc)* gets 0 on success.
      enqueue_render(...)        lower-level queue access (multi-step jobs)
      show_result(rc, out_path)  success / cancelled / error feedback with
                                 FFmpeg's actual error message
    Files
      load_file(path)            standard entry point (drag-and-drop, File >
                                 Open, Open Recent); override or implement
                                 on_source_loaded(path)
      pick_source(kind) / pick_output(...) / suggest_output(...) /
      confirm_output(out, inputs) / probe(path)
    Logging (thread-safe)
      log(widget, msg) / log_tagged(widget, msg, tag)
    """

    make_header        = staticmethod(make_header)
    make_section       = staticmethod(make_section)
    make_console       = staticmethod(make_console)
    make_file_row      = staticmethod(make_file_row)
    make_labeled_entry = staticmethod(make_labeled_entry)
    make_render_btn    = staticmethod(make_render_btn)
    VideoTimeline      = VideoTimeline
    ToolTip            = ToolTip
    add_tooltip        = staticmethod(add_tooltip)

    #: Maximum lines kept in a console widget (older lines are trimmed).
    CONSOLE_MAX_LINES = 3000
    #: Kinds of file this tab accepts by drag-and-drop (see core.dialogs).
    accepted_kinds = ("media",)

    #: The page's main button (Render, Export...), set by the page: Ctrl+S presses it.
    btn_render: tk.Button

    def __init__(self, parent):
        super().__init__(parent)
        self._render_thread = None      # last task id (kept for compatibility)
        self.preview_proc: proc.Popen[bytes] | None = None
        self._active_tasks: set = set()

    def shell(self) -> Shell | None:
        """The window this page lives in, when it is the app's (not in some tests)."""
        top = self.winfo_toplevel()
        return top if isinstance(top, Shell) else None

    # ── What the window may ask of a page (tabs/kit/contracts.py) ────────────
    def transport(self) -> Transport | None:
        """The page's playhead, if it has one (override)."""
        return None

    def render(self) -> None:
        """Ctrl+S: press the main button, when it can be pressed."""
        btn: tk.Button | None = getattr(self, "btn_render", None)          # pages without one: nothing to press
        if btn is not None and btn.winfo_exists() and str(btn.cget("state")) != "disabled":
            btn.invoke()

    def stop_preview(self) -> None:
        """Stop the preview player this page started (leaving the page, Escape)."""
        player, self.preview_proc = self.preview_proc, None
        if player is not None:
            proc.kill_tree(player)

    def on_app_closing(self) -> None:
        """Finish anything that must not be cut off when the app quits (override)."""

    # ── Logging ───────────────────────────────────────────────────────────────

    def _append(self, widget, msg, tag=""):
        try:
            if not widget.winfo_exists():
                return
            start = widget.index("end-1c")
            widget.insert(tk.END, f"{msg}\n")
            if tag:
                widget.tag_add(tag, f"{start} linestart", "end-1c")
            lines = int(widget.index("end-1c").split(".")[0])
            if lines > self.CONSOLE_MAX_LINES:
                widget.delete("1.0", f"{lines - self.CONSOLE_MAX_LINES}.0")
            widget.see(tk.END)
        except tk.TclError:
            pass

    def log(self, widget, msg):
        """Thread-safe append to a console Text widget."""
        if widget is None:
            return
        ui.call_soon(self._append, widget, msg)

    def log_tagged(self, widget, msg, tag=""):
        """Append with a colour tag (success, error, warn, info, dim)."""
        if widget is None:
            return
        ui.call_soon(self._append, widget, msg, tag)

    def log_debug(self, msg: str) -> None:
        logs.get("tab").debug(msg)
        shell = self.shell()
        if shell is not None:
            shell.log_debug(msg)

    # ── Thread helpers ────────────────────────────────────────────────────────

    def call_ui(self, fn, *args, **kwargs):
        """Schedule *fn* on the Tk thread (safe from worker threads)."""
        ui.call_soon(fn, *args, **kwargs)

    def after(self, ms: int | Literal["idle"], func: Callable[..., object] | None = None,  # type: ignore[override]
              *args: Any) -> str:
        """Thread-safe ``after``: calls from worker threads (a common pattern in
        older tabs) are handed to the Tk thread instead of touching Tk directly,
        which fails before/around mainloop and can dead-lock."""
        if func is not None and not ui.on_main_thread():
            if not ms:
                ui.call_soon(func, *args)
            else:
                ui.call_soon(lambda: tk.Misc.after(self, ms, func, *args))
            return ""                                  # no id: it was handed to the UI thread
        return super().after(ms, func, *args)

    # ── Files ────────────────────────────────────────────────────────────────

    def probe(self, path):
        """Cached :class:`core.ffmpeg.MediaInfo` for *path*, or None."""
        return ff.try_probe(path) if path else None

    def pick_source(self, kind="media", title=None):
        return dialogs.ask_open(kind, title=title or t("btn.browse"), parent=self)

    def pick_sources(self, kind="media", title=None):
        return dialogs.ask_open_many(kind, title=title or t("btn.browse"), parent=self)

    def pick_output(self, default_name="", kind="video", ext=".mp4", title=None):
        return dialogs.ask_save(default_name, kind=kind, defaultextension=ext,
                                title=title or t("common.save_as"), parent=self)

    def suggest_output(self, src, suffix="edited", ext=None):
        return outputs.suggest_output(src, suffix, ext)

    def confirm_output(self, out, inputs=(), default_ext=".mp4"):
        """Validate *out* before rendering.  Returns the final path (possibly
        renamed to avoid overwriting) or None if the user cancelled - in
        which case a message has already been shown."""
        policy = "overwrite" if dialogs.was_confirmed(out) else None
        try:
            return outputs.resolve_output(out, [i for i in inputs if i], default_ext=default_ext,
                                          policy=policy, parent=self)
        except outputs.OutputRejected as exc:
            if str(exc):
                messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return None

    def load_file(self, path):
        """Load *path* as this tab's source.  Returns True if accepted.

        Tabs override this, or implement ``on_source_loaded(path)``; the
        default fills the usual ``_src_var``/``src_var`` and ``file_path``."""
        loaded = False
        for attr in ("_src_var", "src_var", "source_var", "input_var", "_input_var"):
            var = getattr(self, attr, None)
            if isinstance(var, tk.Variable):
                var.set(path)
                loaded = True
                break
        if hasattr(self, "file_path"):
            self.file_path = path
            loaded = True
        hook = getattr(self, "on_source_loaded", None)
        if callable(hook):
            hook(path)
            loaded = True
        return loaded

    def goto_tool(self, tool: ToolId | str) -> tk.Misc | None:
        """Switch the window to *tool* (an id, or its English name) and return its page."""
        shell = self.shell()
        return shell.show_page(tool) if shell is not None else None

    # ── Queue bridge ──────────────────────────────────────────────────────────

    def enqueue_render(self, task_name, output_path="", *, cmd=None, cmds=None,
                       worker_fn=None, on_start=None, on_progress=None, on_complete=None,
                       expected_duration=None, inputs=(), atomic_output=True):
        """Submit a job to the global render queue and return its task id.

        Provide exactly one of *cmd*, *cmds* (run sequentially) or
        *worker_fn* (``fn(progress_cb, cancel_fn[, ctx])`` returning an int).
        Callbacks run on the Tk thread; *on_complete(task_id, rc)* always
        fires (rc 0 = success, -1 = cancelled).
        """
        from core.queue_manager import RenderQueueManager

        def _done(task_id, rc):
            self._active_tasks.discard(task_id)
            if on_complete:
                on_complete(task_id, rc)

        task_id = RenderQueueManager.get_instance().enqueue(
            name=task_name, output_path=output_path, cmd=cmd, cmds=cmds,
            worker_fn=worker_fn, on_start=on_start, on_progress=on_progress,
            on_complete=_done, expected_duration=expected_duration,
            inputs=inputs, atomic_output=atomic_output)
        self._active_tasks.add(task_id)
        self._render_thread = task_id
        return task_id

    def cancel_render(self, task_id=None):
        """Cancel *task_id*, or every job this tab started."""
        from core.queue_manager import RenderQueueManager
        q = RenderQueueManager.get_instance()
        for tid in ([task_id] if task_id else list(self._active_tasks)):
            q.cancel(tid)

    def task(self, task_id):
        from core.queue_manager import RenderQueueManager
        return RenderQueueManager.get_instance().get_task(task_id)

    # ── FFmpeg runner ─────────────────────────────────────────────────────────

    def run_ffmpeg(self, cmd, console=None, on_done=None, btn=None, btn_label="▶  Run",
                   *, duration=None, output=None, inputs=(), name=None, cmds=None):
        """Queue FFmpeg *cmd* (or a list *cmds* run in order).

        *duration* is the expected output length in seconds (enables an
        accurate percentage; guessed from the command when omitted).
        *output* is the final file (written atomically; defaults to the last
        argument of the last command).  While the job runs, *btn* shows the
        progress and acts as a Cancel button.
        """
        shell = self.shell()
        cmds = [list(map(str, c)) for c in cmds] if cmds else [list(map(str, cmd))]
        if output is None and cmds and cmds[-1] and ff.is_ffmpeg(cmds[-1][0]):
            last = cmds[-1][-1]
            output = last if last not in ("-", "pipe:", "pipe:1") and not last.startswith("-") else ""
        self.log_debug("Queuing: " + " | ".join(ff.format_cmd(c) for c in cmds))
        state = {"task": None, "orig_cmd": None, "t0": time.time(), "clicked": time.monotonic()}

        def _cancel_click():
            # Ignore the second half of an accidental double-click on Render.
            if time.monotonic() - state["clicked"] < 0.8:
                return
            if state["task"]:
                self.cancel_render(state["task"])

        if btn is not None:
            try:
                state["orig_cmd"] = btn.cget("command")
                btn.config(state="normal", text="⏳ " + t("app.status.queued_btn"),
                           command=_cancel_click)
            except tk.TclError:
                btn = None
        if shell is not None:
            shell.set_status(t("app.status.queued"))

        def _on_start(task_id):
            state["t0"] = time.time()
            if btn is not None:
                try:
                    btn.config(text="✖ " + _cancel_label())
                except tk.TclError:
                    pass
            if shell is not None:
                shell.set_status(t("app.status.processing"))

        def _cancel_label(pct=None):
            label = t("btn.cancel") if t("btn.cancel") != "btn.cancel" else "Cancel"
            return f"{label}  {pct:.0f}%" if pct is not None else label

        def _on_progress(task_id, line):
            if console is not None:
                self.log(console, line)
            tk_task = self.task(task_id)
            if btn is not None and tk_task is not None and tk_task.fraction is not None:
                try:
                    btn.config(text="✖ " + _cancel_label(tk_task.fraction * 100))
                except tk.TclError:
                    pass

        def _on_complete(task_id, returncode):
            elapsed = time.time() - state["t0"]
            elapsed_str = (f"{elapsed:.1f}s" if elapsed < 60
                           else f"{int(elapsed // 60)}m {int(elapsed % 60)}s")
            if btn is not None:
                try:
                    btn.config(state="normal", text=btn_label, command=state["orig_cmd"] or "")
                except tk.TclError:
                    pass
            tk_task = self.task(task_id)
            status = tk_task.status if tk_task else ("done" if returncode == 0 else "failed")
            if shell is not None:
                if status == "done":
                    shell.set_status(t("app.status.done", elapsed=elapsed_str))
                elif status == "cancelled":
                    shell.set_status(t("common.cancelled") if t("common.cancelled") != "common.cancelled" else "Cancelled")
                else:
                    shell.set_status(t("app.status.error_code", code=returncode), color=CLR["red"])
            if console is not None and tk_task is not None and status == "failed" and tk_task.error:
                self.log_tagged(console, tk_task.error, "error")
            self._last_task = tk_task
            if on_done:
                on_done(0 if status == "done" else (returncode if returncode else 1))

        state["task"] = self.enqueue_render(
            name or getattr(self, "TOOL_NAME", "") or "Render",
            output or "", cmds=cmds, on_start=_on_start, on_progress=_on_progress,
            on_complete=_on_complete, expected_duration=duration, inputs=inputs)
        return state["task"]

    # ── Result display ────────────────────────────────────────────────────────

    def show_result(self, returncode, out_path=""):
        """Success / cancelled / error feedback after a render."""
        task = getattr(self, "_last_task", None)
        status = task.status if task is not None else ("done" if returncode == 0 else "failed")
        if status == "cancelled":
            return
        if returncode == 0 and status == "done":
            msg = "{}\n\n{}".format(t("common.complete"), out_path) if out_path else t("common.complete")
            messagebox.showinfo(t("common.done"), msg, parent=self)
            if out_path:
                if settings.AUTO_OPEN_OUTPUT.get():
                    reveal_file(out_path)
            return
        detail = (task.error if task is not None and task.error else
                  t("common.render_error_msg", code=returncode))
        messagebox.showerror(t("common.render_error_title"), detail, parent=self)

    # ── Convenience ───────────────────────────────────────────────────────────

    @staticmethod
    def open_path(path):
        return open_in_explorer(path)

    @staticmethod
    def reveal(path):
        return reveal_file(path)
