"""
tab_allinone.py  ─  All-in-One Pipeline Builder

Select a video, enable any combination of processing steps, and the
tab assembles a single optimised FFmpeg command that does everything
in one pass - no intermediate files, no quality loss from multiple
re-encodes.

The generated command is shown in a live-updating editable box at the
bottom. You can tweak it by hand before rendering.

Sections (all optional, all combinable):
  ✂  Trim            - start/end timestamps
  📐  Scale           - resolution + algorithm
  🎬  Frame Rate      - target FPS
  🔄  Rotate & Flip   - transpose / hflip / vflip
  ⚡  Speed           - constant speed multiplier with audio pitch
  🎨  Color & Image   - eq, hue, lift/gain
  🌫  Denoise         - hqdn3d or nlmeans
  🔪  Sharpen         - unsharp mask
  🔊  Audio           - volume, normalise, fade in/out
  💬  Text Overlay    - burned-in watermark / caption
  🌅  Fade            - video and audio fade in/out
  ⚙   Encode          - codec, CRF, preset, audio bitrate
"""

from __future__ import annotations

import os
import shlex
import tkinter as tk
from collections.abc import Callable
from functools import partial
from tkinter import messagebox, ttk
from typing import Any, cast

from core import ffmpeg as ff
from core import tasks
from core.i18n import t
from core.ops import all_in_one as aio
from core.scaling import px
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab, make_header
from tabs.kit import dialogs
from tabs.kit.time_fields import RangePicker
from tabs.kit.tktyped import linked_scrollbar, on_write
from tabs.widgets import scroll_body

# ── Constants reused from other tabs ─────────────────────────────────────────

#: size presets: what the drop-down shows -> (width, height); None = type your own
_RES_PRESETS: dict[str, tuple[int, int] | None] = {
    "7680x4320 (8K)": (7680, 4320),
    "3840x2160 (4K UHD)": (3840, 2160),
    "2560x1440 (QHD/2K)": (2560, 1440),
    "1920x1080 (Full HD)": (1920, 1080),
    "1280x720 (HD)": (1280, 720),
    "854x480 (SD)": (854, 480),
    "640x360 (360p)": (640, 360),
    "Custom…": None,
}
_SCALE_ALGO = {
    "Lanczos (best downscale)": "lanczos",
    "Lanczos4 (best upscale)": "lanczos",  # Changed from lanczos4 for compatibility
    "Bicubic": "bicubic",
    "Bilinear (fastest)": "bilinear",
    "Nearest (pixel art)": "neighbor",
}

# Updated to a dictionary to translate friendly UI names to FFmpeg identifiers
_VCODECS = {
    "H.264 (CPU)": "libx264",
    "H.265 / HEVC (CPU)": "libx265",
    "H.264 (Nvidia HW)": "h264_nvenc",
    "H.265 / HEVC (Nvidia HW)": "hevc_nvenc",
    "H.264 (AMD HW)": "h264_amf",
    "H.265 / HEVC (AMD HW)": "hevc_amf",
    "H.264 (Intel HW)": "h264_qsv",
    "H.265 / HEVC (Intel HW)": "hevc_qsv",
    "H.264 (best GPU available)": "auto_h264",
    "H.265 / HEVC (best GPU available)": "auto_hevc",
    "AV1 (CPU, smallest files)": "libsvtav1",
    "VP9 (CPU)": "libvpx-vp9",
    "ProRes (CPU)": "prores_ks",
    "Copy (No re-encode)": "copy",
}

_ACODECS = ["aac", "libmp3lame", "libopus", "libvorbis", "copy", "none"]
_PRESETS = [
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
]
_ABITRATES = ["96k", "128k", "192k", "256k", "320k"]
_FPS_VALS = ["23.976", "24", "25", "29.97", "30", "48", "50", "59.94", "60", "120"]

_TEXT_POSITIONS = {  # (x, y) drawtext expressions; margins scale with the frame
    "Bottom Centre": ("(w-text_w)/2", "h-text_h-h*0.04"),
    "Top Centre": ("(w-text_w)/2", "h*0.04"),
    "Bottom Left": ("w*0.03", "h-text_h-h*0.04"),
    "Bottom Right": ("w-text_w-w*0.03", "h-text_h-h*0.04"),
    "Centre": ("(w-text_w)/2", "(h-text_h)/2"),
    "Top Left": ("w*0.03", "h*0.04"),
    "Top Right": ("w-text_w-w*0.03", "h*0.04"),
}


def _num(var: tk.StringVar, default: float) -> float:
    try:
        return float(var.get())
    except ValueError:
        return default


# ─────────────────────────────────────────────────────────────────────────────
#  Small UI helpers
# ─────────────────────────────────────────────────────────────────────────────


def _lbl(parent: tk.Misc, text: str, bold: bool = False, dim: bool = False) -> tk.Label:
    fg = CLR["fgdim"] if dim else CLR["fg"]
    font = (UI_FONT, 9, "bold") if bold else (UI_FONT, 9)
    return tk.Label(parent, text=text, bg=CLR["bg"], fg=fg, font=font)


def _entry(parent: tk.Misc, var: tk.StringVar, width: int = 10) -> tk.Entry:
    return tk.Entry(
        parent,
        textvariable=var,
        width=width,
        bg=CLR["panel"],
        fg=CLR["fg"],
        insertbackground=CLR["fg"],
        relief="flat",
        bd=px(4),
    )


def _combo(
    parent: tk.Misc, var: tk.StringVar, values: list[str], width: int = 18, state: str = "readonly"
) -> ttk.Combobox:
    cb = ttk.Combobox(parent, textvariable=var, values=values, state=state, width=width)
    return cb


def _spin(
    parent: tk.Misc, var: tk.StringVar, from_: float, to: float, inc: float = 0.5, width: int = 7
) -> tk.Spinbox:
    return tk.Spinbox(
        parent,
        from_=from_,
        to=to,
        increment=inc,
        textvariable=var,
        width=width,
        bg=CLR["panel"],
        fg=CLR["fg"],
        buttonbackground=CLR["panel"],
        insertbackground=CLR["fg"],
        relief="flat",
    )


def _line(parent: tk.Misc, pady: int = 3) -> tk.Frame:
    """A strip for controls laid out like a sentence ("Width [ ] Height [ ]")."""
    f = tk.Frame(parent, bg=CLR["bg"])
    f.pack(fill="x", pady=px(pady))
    return f


# ─────────────────────────────────────────────────────────────────────────────
#  Section container
# ─────────────────────────────────────────────────────────────────────────────


class _Section(tk.Frame):
    """
    A collapsible section with a checkbox header.
    When the checkbox is off all interior widgets are disabled (greyed out).
    Calls *on_toggle* whenever the enabled state changes.
    """

    def __init__(
        self, parent: tk.Misc, title: str, on_toggle: Callable[[], object], **kw: Any
    ) -> None:
        super().__init__(parent, bg=CLR["bg"], **kw)
        self.enabled = tk.BooleanVar(value=False)
        self._cb = on_toggle

        # ── Header row ────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=CLR["panel"])
        hdr.pack(fill="x")
        tk.Checkbutton(
            hdr,
            text=title,
            variable=self.enabled,
            font=(UI_FONT, 10, "bold"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            selectcolor=CLR.get("sb_active", "#333333"),
            activebackground=CLR["panel"],
            command=self._toggled,
        ).pack(side="left", padx=px(10), pady=px(6))

        # ── Body ──────────────────────────────────────────────────────────
        self.body = tk.Frame(self, bg=CLR["bg"], padx=px(16), pady=px(6))
        self.body.pack(fill="x")
        self._set_body_state("disabled")

    def get(self) -> bool:
        return self.enabled.get()

    def _toggled(self) -> None:
        self._set_body_state("normal" if self.enabled.get() else "disabled")
        self._cb()

    def set_enabled(self, on: bool) -> None:
        """Tick (or untick) the section without notifying anyone."""
        self.enabled.set(on)
        self._set_body_state("normal" if on else "disabled")

    def _set_body_state(self, state: str) -> None:
        fg = CLR["fg"] if state == "normal" else CLR["fgdim"]
        self._walk(self.body, state, fg)

    def _walk(self, widget: tk.Misc, state: str, fg: str) -> None:
        cls = widget.winfo_class()
        try:
            if cls in ("Entry", "Spinbox", "Button", "Checkbutton", "Radiobutton"):
                widget["state"] = state
            if cls in ("Label",):
                widget["fg"] = fg
            if cls == "TCombobox":
                widget["state"] = "readonly" if state == "normal" else "disabled"
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._walk(child, state, fg)


# ─────────────────────────────────────────────────────────────────────────────
#  Main tab
# ─────────────────────────────────────────────────────────────────────────────


class AllInOneTab(BaseTab):
    """Pipeline builder - combine any processing steps into one FFmpeg pass."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self._probed_path: str | None = None
        self._duration = 0.0  # loaded video duration - needed for fade-out math
        self._last_cmd: list[str] = []  # most recently built command list
        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────
    #  Top-level layout
    # ─────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # ── Fixed header ──────────────────────────────────────────────────
        make_header(self, t("tab.all_in_one"), t("all_in_one.subtitle"), icon="🎛")

        # ── Source / output (always visible, above scroll) ─────────────────
        io_frame = tk.Frame(self, bg=CLR["bg"], padx=px(12), pady=px(8))
        io_frame.pack(fill="x")
        self._build_io(io_frame)

        # ── Scrollable section area ────────────────────────────────────────
        self._scroll_inner = scroll_body(self)

        self._build_sections(self._scroll_inner)

        # ── Fixed bottom: command display + render ─────────────────────────
        self._build_bottom()

    # ─────────────────────────────────────────────────────────────────────
    #  Source / Output
    # ─────────────────────────────────────────────────────────────────────

    def _build_io(self, parent: tk.Misc) -> None:
        self._src_var = tk.StringVar()
        self._out_var = tk.StringVar()

        r1 = _line(parent, pady=2)
        _lbl(r1, t("common.source_video"), bold=True).pack(side="left")
        _entry(r1, self._src_var, width=62).pack(side="left", padx=px(8))
        tk.Button(
            r1, text=t("btn.browse"), command=self._browse_src, bg=CLR["panel"], fg=CLR["fg"]
        ).pack(side="left")
        self._dur_lbl = tk.Label(r1, text="", bg=CLR["bg"], fg=CLR["accent"], font=(MONO_FONT, 9))
        self._dur_lbl.pack(side="left", padx=px(10))

        self._src_job: str | None = None
        on_write(self._src_var, lambda: self._src_typed())

        r2 = _line(parent, pady=2)
        _lbl(r2, t("common.output_file"), bold=True).pack(side="left")
        _entry(r2, self._out_var, width=62).pack(side="left", padx=px(8))
        tk.Button(
            r2, text=t("common.save_as"), command=self._browse_out, bg=CLR["panel"], fg=CLR["fg"]
        ).pack(side="left")

    def _browse_src(self) -> None:
        p = dialogs.askopenfilename(
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm *.flv *.m4v"), ("All", "*.*")]
        )
        if not p:
            return
        self._src_var.set(p)
        base = os.path.splitext(p)[0]
        if not self._out_var.get():
            self._out_var.set(base + "_pipeline.mp4")
        # Load duration in background
        tasks.submit(partial(self._load_duration, p))
        self._rebuild_cmd()

    def _src_typed(self) -> None:
        """Typed / pasted / dropped paths are probed too (debounced)."""
        if self._src_job is not None:
            self.after_cancel(self._src_job)

        def go() -> None:
            self._src_job = None
            p = self._src_var.get().strip()
            if p and os.path.isfile(p) and p != self._probed_path:
                self._probed_path = p
                if not self._out_var.get():
                    self._out_var.set(self.suggest_output(p, "pipeline"))
                tasks.submit(partial(self._load_duration, p))

        self._src_job = self.after(400, go)

    def _load_duration(self, path: str) -> None:
        from core import ffmpeg as _ff

        info = _ff.try_probe(path)
        dur = info.duration if info else 0.0
        self._duration = dur
        self._sample_rate = (
            info.require_audio().sample_rate if info is not None and info.audio else 0
        )
        self.after(0, lambda: self._dur_lbl.config(text=f"{dur:.2f}s" if dur else ""))
        self.after(0, self._rebuild_cmd)

    def _browse_out(self) -> None:
        p = dialogs.asksaveasfilename(
            defaultextension=".mp4",
            filetypes=[("MP4", "*.mp4"), ("MKV", "*.mkv"), ("MOV", "*.mov"), ("All", "*.*")],
        )
        if p:
            self._out_var.set(p)
            self._rebuild_cmd()

    # ─────────────────────────────────────────────────────────────────────
    #  Sections
    # ─────────────────────────────────────────────────────────────────────

    def _build_sections(self, parent: tk.Misc) -> None:
        """Build all optional processing sections inside the scrollable area."""

        def sep() -> None:
            tk.Frame(parent, bg=CLR["panel"], height=px(2)).pack(fill="x", pady=px(2))

        self._build_trim(parent)
        sep()
        self._build_scale(parent)
        sep()
        self._build_fps(parent)
        sep()
        self._build_rotate(parent)
        sep()
        self._build_speed(parent)
        sep()
        self._build_color(parent)
        sep()
        self._build_denoise(parent)
        sep()
        self._build_sharpen(parent)
        sep()
        self._build_audio(parent)
        sep()
        self._build_text(parent)
        sep()
        self._build_fade(parent)
        sep()
        self._build_encode(parent)

    # ── ✂ Trim ────────────────────────────────────────────────────────────

    def _build_trim(self, parent: tk.Misc) -> None:
        self._sec_trim = _Section(parent, "✂  Trim", self._rebuild_cmd)
        self._sec_trim.pack(fill="x", pady=px(1))
        b = self._sec_trim.body

        r = _line(b)
        _lbl(r, "From:").pack(side="left")
        trim = RangePicker(
            r,
            start="0.0",
            end_hint="(blank = to the end of the video)",
            source=self._source,
            on_change=self._rebuild_cmd,
        )
        trim.pack(side="left", padx=px(6))
        self._trim_range = trim

    # ── 📐 Scale ──────────────────────────────────────────────────────────

    def _build_scale(self, parent: tk.Misc) -> None:
        self._sec_scale = _Section(parent, "📐  Scale / Resolution", self._rebuild_cmd)
        self._sec_scale.pack(fill="x", pady=px(1))
        b = self._sec_scale.body

        self._scale_preset = tk.StringVar(value="1920x1080 (Full HD)")
        self._scale_w = tk.StringVar(value="1920")
        self._scale_h = tk.StringVar(value="1080")
        self._scale_algo = tk.StringVar(value=next(iter(_SCALE_ALGO.keys())))
        self._scale_pad = tk.BooleanVar(value=False)

        for v in (self._scale_preset, self._scale_w, self._scale_h, self._scale_algo):
            on_write(v, lambda: self._rebuild_cmd())
        on_write(self._scale_pad, lambda: self._rebuild_cmd())

        r1 = _line(b)
        _lbl(r1, "Preset:").pack(side="left")
        cb = _combo(r1, self._scale_preset, list(_RES_PRESETS), width=24)
        cb.pack(side="left", padx=px(6))
        cb.bind("<<ComboboxSelected>>", self._on_res_preset)
        _lbl(r1, "  W:").pack(side="left")
        self._scale_w_entry = _entry(r1, self._scale_w, 6)
        self._scale_w_entry.pack(side="left", padx=px(3))
        _lbl(r1, "H:").pack(side="left")
        self._scale_h_entry = _entry(r1, self._scale_h, 6)
        self._scale_h_entry.pack(side="left", padx=px(3))
        _lbl(r1, "(-1 = auto-aspect)", dim=True).pack(side="left", padx=px(6))

        r2 = _line(b)
        _lbl(r2, "Algorithm:").pack(side="left")
        _combo(r2, self._scale_algo, list(_SCALE_ALGO.keys()), width=26).pack(
            side="left", padx=px(6)
        )
        tk.Checkbutton(
            r2,
            text="Pad to exact size (adds black bars)",
            variable=self._scale_pad,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["panel"],
            activebackground=CLR["bg"],
            command=self._rebuild_cmd,
        ).pack(side="left", padx=px(10))

    def _on_res_preset(self, *_: object) -> None:
        size = _RES_PRESETS.get(self._scale_preset.get())
        if size is not None:
            self._scale_w.set(str(size[0]))
            self._scale_h.set(str(size[1]))
        self._rebuild_cmd()

    # ── 🎬 Frame Rate ─────────────────────────────────────────────────────

    def _build_fps(self, parent: tk.Misc) -> None:
        self._sec_fps = _Section(parent, "🎬  Frame Rate", self._rebuild_cmd)
        self._sec_fps.pack(fill="x", pady=px(1))
        b = self._sec_fps.body

        self._fps_val = tk.StringVar(value="30")
        on_write(self._fps_val, lambda: self._rebuild_cmd())

        r = _line(b)
        _lbl(r, "Target FPS:").pack(side="left")
        cb = _combo(r, self._fps_val, _FPS_VALS, width=8, state="normal")
        cb.pack(side="left", padx=px(6))
        _lbl(r, "(or type any value)", dim=True).pack(side="left", padx=px(4))

    # ── 🔄 Rotate & Flip ─────────────────────────────────────────────────

    def _build_rotate(self, parent: tk.Misc) -> None:
        self._sec_rotate = _Section(parent, "🔄  Rotate & Flip", self._rebuild_cmd)
        self._sec_rotate.pack(fill="x", pady=px(1))
        b = self._sec_rotate.body

        self._rotate_val = tk.StringVar(value="0")
        self._flip_h = tk.BooleanVar(value=False)
        self._flip_v = tk.BooleanVar(value=False)

        for v in (self._rotate_val, self._flip_h, self._flip_v):
            on_write(v, lambda: self._rebuild_cmd())

        r = _line(b)
        _lbl(r, "Rotation:").pack(side="left")
        for val, label in [("0", "None"), ("90", "90° CW"), ("180", "180°"), ("270", "90° CCW")]:
            tk.Radiobutton(
                r,
                text=label,
                variable=self._rotate_val,
                value=val,
                bg=CLR["bg"],
                fg=CLR["fg"],
                selectcolor=CLR["panel"],
                activebackground=CLR["bg"],
                command=self._rebuild_cmd,
            ).pack(side="left", padx=px(8))

        r2 = _line(b)
        tk.Checkbutton(
            r2,
            text="Flip Horizontal (Mirror)",
            variable=self._flip_h,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["panel"],
            activebackground=CLR["bg"],
            command=self._rebuild_cmd,
        ).pack(side="left", padx=px(4))
        tk.Checkbutton(
            r2,
            text="Flip Vertical",
            variable=self._flip_v,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["panel"],
            activebackground=CLR["bg"],
            command=self._rebuild_cmd,
        ).pack(side="left", padx=px(20))

    # ── ⚡ Speed ──────────────────────────────────────────────────────────

    def _build_speed(self, parent: tk.Misc) -> None:
        self._sec_speed = _Section(parent, "⚡  Speed", self._rebuild_cmd)
        self._sec_speed.pack(fill="x", pady=px(1))
        b = self._sec_speed.body

        self._speed_val = tk.StringVar(value="2.0")
        self._speed_pitch = tk.BooleanVar(value=True)
        for v in (self._speed_val, self._speed_pitch):
            on_write(v, lambda: self._rebuild_cmd())

        r = _line(b)
        _lbl(r, "Speed multiplier:").pack(side="left")
        _spin(r, self._speed_val, 0.05, 16, 0.25, width=6).pack(side="left", padx=px(6))
        _lbl(r, "(0.5 = half speed · 2.0 = double)", dim=True).pack(side="left", padx=px(6))

        r2 = _line(b)
        tk.Checkbutton(
            r2,
            text="Preserve audio pitch (atempo)",
            variable=self._speed_pitch,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["panel"],
            activebackground=CLR["bg"],
            command=self._rebuild_cmd,
        ).pack(side="left", padx=px(4))
        _lbl(r2, "(when off, audio speeds up / slows down raw)", dim=True).pack(
            side="left", padx=px(6)
        )

    # ── 🎨 Color & Image ──────────────────────────────────────────────────

    def _slider_moved(self, var: tk.StringVar, value: str) -> None:
        var.set(f"{float(value):.2f}")
        self._rebuild_cmd()

    def _build_color(self, parent: tk.Misc) -> None:
        self._sec_color = _Section(parent, "🎨  Color & Image", self._rebuild_cmd)
        self._sec_color.pack(fill="x", pady=px(1))
        b = self._sec_color.body

        def _sv(default: str) -> tk.StringVar:
            v = tk.StringVar(value=str(default))
            on_write(v, lambda: self._rebuild_cmd())
            return v

        self._bri = _sv("0.00")
        self._con = _sv("1.00")
        self._sat = _sv("1.00")
        self._gam = _sv("1.00")
        self._hue = _sv("0")

        sliders = [
            ("Brightness:", self._bri, -1.0, 1.0, 0.05),
            ("Contrast:", self._con, 0.1, 3.0, 0.05),
            ("Saturation:", self._sat, 0.0, 3.0, 0.05),
            ("Gamma:", self._gam, 0.1, 5.0, 0.05),
            ("Hue shift°:", self._hue, -180, 180, 5.0),
        ]
        for label, var, lo, hi, inc in sliders:
            r = _line(b, pady=2)
            _lbl(r, label, bold=True).pack(side="left")
            sc = tk.Scale(
                r,
                variable=cast("tk.DoubleVar", var),  # Tk keeps the number as text either way
                from_=lo,
                to=hi,
                resolution=inc,
                orient="horizontal",
                length=px(280),
                bg=CLR["bg"],
                fg=CLR["fg"],
                highlightthickness=0,
                command=partial(self._slider_moved, var),
            )
            sc.pack(side="left", padx=px(6))
            _lbl(r, "", dim=True).pack(side="left")  # spacer

        r5 = _line(b, pady=2)
        tk.Button(
            r5,
            text="Reset all to defaults",
            bg=CLR["panel"],
            fg=CLR["fg"],
            command=self._reset_color,
        ).pack(side="left")

    def _reset_color(self) -> None:
        self._bri.set("0.00")
        self._con.set("1.00")
        self._sat.set("1.00")
        self._gam.set("1.00")
        self._hue.set("0")
        self._rebuild_cmd()

    # ── 🌫 Denoise ────────────────────────────────────────────────────────

    def _build_denoise(self, parent: tk.Misc) -> None:
        self._sec_denoise = _Section(parent, "🌫  Denoise", self._rebuild_cmd)
        self._sec_denoise.pack(fill="x", pady=px(1))
        b = self._sec_denoise.body

        self._denoise_algo = tk.StringVar(value="hqdn3d")
        self._denoise_str = tk.StringVar(value="Medium")
        for v in (self._denoise_algo, self._denoise_str):
            on_write(v, lambda: self._rebuild_cmd())

        r = _line(b)
        _lbl(r, "Algorithm:").pack(side="left")
        for val, lbl in [("hqdn3d", "HQDN3D (fast)"), ("nlmeans", "NLMeans (quality)")]:
            tk.Radiobutton(
                r,
                text=lbl,
                variable=self._denoise_algo,
                value=val,
                bg=CLR["bg"],
                fg=CLR["fg"],
                selectcolor=CLR["panel"],
                activebackground=CLR["bg"],
                command=self._rebuild_cmd,
            ).pack(side="left", padx=px(10))

        r2 = _line(b)
        _lbl(r2, "Strength:").pack(side="left")
        _combo(r2, self._denoise_str, ["Light", "Medium", "Strong", "Aggressive"], width=12).pack(
            side="left", padx=px(6)
        )

    # ── 🔪 Sharpen ────────────────────────────────────────────────────────

    def _build_sharpen(self, parent: tk.Misc) -> None:
        self._sec_sharpen = _Section(parent, "🔪  Sharpen", self._rebuild_cmd)
        self._sec_sharpen.pack(fill="x", pady=px(1))
        b = self._sec_sharpen.body

        self._sharp_lx = tk.StringVar(value="5")
        self._sharp_la = tk.StringVar(value="1.0")
        for v in (self._sharp_lx, self._sharp_la):
            on_write(v, lambda: self._rebuild_cmd())

        r = _line(b)
        _lbl(r, "Kernel size (odd):").pack(side="left")
        _combo(r, self._sharp_lx, ["3", "5", "7"], width=4).pack(side="left", padx=px(6))
        _lbl(r, "  Amount (luma):").pack(side="left")
        _spin(r, self._sharp_la, 0.1, 5.0, 0.1, width=5).pack(side="left", padx=px(6))
        _lbl(r, "(0.5 subtle · 1.0 standard · 2.5 aggressive)", dim=True).pack(
            side="left", padx=px(6)
        )

    # ── 🔊 Audio ──────────────────────────────────────────────────────────

    def _build_audio(self, parent: tk.Misc) -> None:
        self._sec_audio = _Section(parent, "🔊  Audio Processing", self._rebuild_cmd)
        self._sec_audio.pack(fill="x", pady=px(1))
        b = self._sec_audio.body

        self._vol_en = tk.BooleanVar(value=False)
        self._vol_val = tk.StringVar(value="1.5")
        self._norm_en = tk.BooleanVar(value=False)
        self._norm_lufs = tk.StringVar(value="-23")

        for v in (self._vol_en, self._vol_val, self._norm_en, self._norm_lufs):
            on_write(v, lambda: self._rebuild_cmd())

        r1 = _line(b)
        tk.Checkbutton(
            r1,
            text="Adjust volume ×",
            variable=self._vol_en,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["panel"],
            activebackground=CLR["bg"],
            command=self._rebuild_cmd,
        ).pack(side="left")
        _spin(r1, self._vol_val, 0.05, 10, 0.05, width=5).pack(side="left", padx=px(6))
        _lbl(r1, "(1.0 = no change · 2.0 = double)", dim=True).pack(side="left", padx=px(4))

        r2 = _line(b)
        tk.Checkbutton(
            r2,
            text="Loudness normalise to",
            variable=self._norm_en,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["panel"],
            activebackground=CLR["bg"],
            command=self._rebuild_cmd,
        ).pack(side="left")
        _combo(
            r2, self._norm_lufs, ["-14", "-16", "-18", "-23", "-24"], width=5, state="normal"
        ).pack(side="left", padx=px(4))
        _lbl(r2, "LUFS  (YouTube = -14 · broadcast = -23)", dim=True).pack(side="left", padx=px(4))

    # ── 💬 Text Overlay ───────────────────────────────────────────────────

    def _build_text(self, parent: tk.Misc) -> None:
        self._sec_text = _Section(parent, "💬  Text / Watermark Overlay", self._rebuild_cmd)
        self._sec_text.pack(fill="x", pady=px(1))
        b = self._sec_text.body

        self._text_val = tk.StringVar(value="@YourChannel")
        self._text_size = tk.StringVar(value="42")
        self._text_color = tk.StringVar(value="white")
        self._text_pos = tk.StringVar(value="Bottom Right")
        for v in (self._text_val, self._text_size, self._text_color, self._text_pos):
            on_write(v, lambda: self._rebuild_cmd())

        r1 = _line(b)
        _lbl(r1, "Text:").pack(side="left")
        _entry(r1, self._text_val, 34).pack(side="left", padx=px(6))
        _lbl(r1, "  Size:").pack(side="left")
        _spin(r1, self._text_size, 8, 200, 2, width=5).pack(side="left", padx=px(4))
        _lbl(r1, "  Colour:").pack(side="left")
        _combo(
            r1, self._text_color, ["white", "yellow", "red", "cyan", "black", "orange"], width=8
        ).pack(side="left", padx=px(4))

        r2 = _line(b)
        _lbl(r2, "Position:").pack(side="left")
        _combo(r2, self._text_pos, list(_TEXT_POSITIONS.keys()), width=14).pack(
            side="left", padx=px(6)
        )
        _lbl(r2, "  Show from:").pack(side="left")
        shown = RangePicker(
            r2, end_hint="(blank = to the end)", source=self._source, on_change=self._rebuild_cmd
        )
        shown.pack(side="left", padx=px(4))
        self._text_shown = shown

    # ── 🌅 Fade In / Out ─────────────────────────────────────────────────

    def _build_fade(self, parent: tk.Misc) -> None:
        self._sec_fade = _Section(parent, "🌅  Fade In / Fade Out", self._rebuild_cmd)
        self._sec_fade.pack(fill="x", pady=px(1))
        b = self._sec_fade.body

        self._fi_en = tk.BooleanVar(value=False)
        self._fi_dur = tk.StringVar(value="1.5")
        self._fo_en = tk.BooleanVar(value=False)
        self._fo_dur = tk.StringVar(value="2.0")
        self._fade_audio = tk.BooleanVar(value=True)

        for v in (self._fi_en, self._fi_dur, self._fo_en, self._fo_dur, self._fade_audio):
            on_write(v, lambda: self._rebuild_cmd())

        r1 = _line(b)
        tk.Checkbutton(
            r1,
            text="Fade IN duration (s):",
            variable=self._fi_en,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["panel"],
            activebackground=CLR["bg"],
            command=self._rebuild_cmd,
        ).pack(side="left")
        _spin(r1, self._fi_dur, 0.2, 10, 0.5, width=5).pack(side="left", padx=px(6))

        r2 = _line(b)
        tk.Checkbutton(
            r2,
            text="Fade OUT duration (s):",
            variable=self._fo_en,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["panel"],
            activebackground=CLR["bg"],
            command=self._rebuild_cmd,
        ).pack(side="left")
        _spin(r2, self._fo_dur, 0.2, 10, 0.5, width=5).pack(side="left", padx=px(6))
        _lbl(r2, "(requires source duration to be loaded)", dim=True).pack(side="left", padx=px(6))

        r3 = _line(b)
        tk.Checkbutton(
            r3,
            text="Also fade audio in/out",
            variable=self._fade_audio,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["panel"],
            activebackground=CLR["bg"],
            command=self._rebuild_cmd,
        ).pack(side="left")

    # ── ⚙ Encode Settings ─────────────────────────────────────────────────

    def _build_encode(self, parent: tk.Misc) -> None:
        self._sec_encode = _Section(parent, "⚙  Encode Settings", self._rebuild_cmd)
        self._sec_encode.pack(fill="x", pady=px(1))
        # Default ON - you almost always want to set these
        self._sec_encode.set_enabled(True)
        b = self._sec_encode.body

        self._vcodec = tk.StringVar(value="H.264 (CPU)")
        self._crf = tk.StringVar(value="18")
        self._preset = tk.StringVar(value="fast")
        self._acodec = tk.StringVar(value="aac")
        self._abitrate = tk.StringVar(value="192k")

        for v in (self._vcodec, self._crf, self._preset, self._acodec, self._abitrate):
            on_write(v, lambda: self._rebuild_cmd())

        r1 = _line(b)
        _lbl(r1, "Video codec:").pack(side="left")
        _combo(r1, self._vcodec, list(_VCODECS.keys()), width=24).pack(side="left", padx=px(6))
        _lbl(r1, "  Quality (CRF/CQ):").pack(side="left")
        _combo(r1, self._crf, [str(x) for x in range(12, 32)], width=4).pack(
            side="left", padx=px(4)
        )
        _lbl(r1, "  Preset:").pack(side="left")
        _combo(r1, self._preset, _PRESETS, width=10).pack(side="left", padx=px(4))

        r2 = _line(b)
        _lbl(r2, "Audio codec:").pack(side="left")
        _combo(r2, self._acodec, _ACODECS, width=12).pack(side="left", padx=px(6))
        _lbl(r2, "  Bitrate:").pack(side="left")
        _combo(r2, self._abitrate, _ABITRATES, width=7).pack(side="left", padx=px(4))
        _lbl(r2, "(ignored when codec = copy / none)", dim=True).pack(side="left", padx=px(6))

    # ─────────────────────────────────────────────────────────────────────
    #  Fixed bottom bar: command display + render
    # ─────────────────────────────────────────────────────────────────────

    def _build_bottom(self) -> None:
        bottom = tk.Frame(self, bg=CLR["panel"])
        bottom.pack(side="bottom", fill="x")
        self._build_command_view(bottom)
        self._build_actions(bottom)
        self._rebuild_cmd()  # the first command

    def _build_command_view(self, bottom: tk.Frame) -> None:
        """The FFmpeg command: its heading, Copy and Reset, and the editable text."""
        # ── Command label + copy button ────────────────────────────────────
        cmd_hdr = tk.Frame(bottom, bg=CLR["panel"])
        cmd_hdr.pack(fill="x", padx=px(12), pady=px(8, 2))
        tk.Label(
            cmd_hdr,
            text="📋  Generated FFmpeg Command  (editable; your changes are preserved on render)",
            font=(UI_FONT, 9, "bold"),
            bg=CLR["panel"],
            fg=CLR["accent"],
        ).pack(side="left")
        tk.Button(
            cmd_hdr, text="Copy to clipboard", bg=CLR["panel"], fg=CLR["fg"], command=self._copy_cmd
        ).pack(side="right", padx=px(4))
        tk.Button(
            cmd_hdr, text="Reset command", bg=CLR["panel"], fg=CLR["fg"], command=self._reset_cmd
        ).pack(side="right", padx=px(4))
        self._edit_lbl = tk.Label(
            cmd_hdr, text="", bg=CLR["panel"], fg=CLR["orange"], font=(UI_FONT, 8)
        )
        self._edit_lbl.pack(side="right", padx=px(8))

        # ── Command text widget (editable) ────────────────────────────────
        cmd_frame = tk.Frame(bottom, bg=CLR["panel"])
        cmd_frame.pack(fill="x", padx=px(12), pady=px(0, 4))
        self._cmd_text = tk.Text(
            cmd_frame,
            height=5,
            bg="#0A0A0A",
            fg="#00FF88",
            font=(MONO_FONT, 9),
            wrap="none",
            insertbackground="#00FF88",
            relief="flat",
            bd=px(4),
        )
        cmd_sb_x = linked_scrollbar(cmd_frame, self._cmd_text, "horizontal")
        cmd_sb_y = linked_scrollbar(cmd_frame, self._cmd_text)
        cmd_sb_y.pack(side="right", fill="y")
        self._cmd_text.pack(side="left", fill="x", expand=True)
        self._cmd_text.bind("<<Modified>>", self._on_cmd_modified)
        cmd_sb_x.pack(side="bottom", fill="x")

    def _build_actions(self, bottom: tk.Frame) -> None:
        """Render, Stop and the other buttons, and the log."""
        # ── Action buttons ────────────────────────────────────────────────
        btn_row = tk.Frame(bottom, bg=CLR["panel"])
        btn_row.pack(fill="x", padx=px(12), pady=px(4, 10))

        self.btn_render = self.make_render_btn(btn_row, "▶  RENDER", self._render, width=20)
        self.btn_render.pack(side="left", padx=px(8))

        self._btn_stop = tk.Button(
            btn_row,
            text="⏹  STOP",
            font=(UI_FONT, 12, "bold"),
            bg="#D32F2F",
            fg="white",
            height=2,
            width=10,
            cursor="hand2",
            state="disabled",
            command=self._stop_render,
        )
        self._btn_stop.pack(side="left", padx=px(4))

        tk.Button(
            btn_row,
            text="💾  Save Command as .bat",
            bg=CLR["panel"],
            fg=CLR["fg"],
            height=2,
            command=self._save_bat,
        ).pack(side="left", padx=px(4))

        tk.Button(
            btn_row,
            text="💾  Save Command as .sh",
            bg=CLR["panel"],
            fg=CLR["fg"],
            height=2,
            command=self._save_sh,
        ).pack(side="left", padx=px(4))

        # Console
        self.console = self.make_console_block(
            bottom, height=5, bg=CLR["panel"], fill="x", expand=False, padx=px(12), pady=px(0, 8)
        )

    # ─────────────────────────────────────────────────────────────────────
    #  Command builder  ← the heart of the tab
    # ─────────────────────────────────────────────────────────────────────

    def _source(self) -> str:
        return self._src_var.get().strip()

    def _rebuild_cmd(self, *_: object) -> None:
        """Show the FFmpeg command for every enabled section (core/ops/all_in_one.py)."""
        if not hasattr(self, "_cmd_text"):
            return
        if getattr(self, "_cmd_dirty", False):
            self._edit_lbl.config(text="✎ Edited by hand - press 'Reset command' to regenerate")
            return
        src = self._src_var.get().strip()
        out = self._out_var.get().strip()
        if not src or not out:
            self._last_cmd = []
            self._set_cmd_text(
                "# Select a source video and output file above\n# to generate the FFmpeg command."
            )
            return
        notes: list[str] = []
        built = aio.build(self._recipe(src, out, notes))
        notes += built.notes
        self._last_cmd = built.argv
        self._expected_len = built.length or None
        self._set_cmd_text(ff.format_cmd_lines(built.argv))
        self._edit_lbl.config(text=("⚠ " + "; ".join(notes)) if notes else "")

    # ── reading the sections (each None while switched off) ─────────────────

    def _recipe(self, src: str, out: str, notes: list[str]) -> aio.Recipe:
        start, end = self._trim(notes)
        rotate = self._sec_rotate.get()
        audio = self._sec_audio.get()
        return aio.Recipe(
            src=src,
            out=out,
            duration=float(getattr(self, "_duration", 0.0) or 0.0),
            sample_rate=int(getattr(self, "_sample_rate", 0) or 48000),
            start=start,
            end=end,
            speed=self._speed(notes),
            keep_pitch=self._speed_pitch.get(),
            rotate=self._rotate_val.get() if rotate else "0",
            flip_h=rotate and self._flip_h.get(),
            flip_v=rotate and self._flip_v.get(),
            scale=self._scale(),
            fps=self._fps_val.get() if self._sec_fps.get() else None,
            colour=self._colour(),
            denoise=(self._denoise_algo.get(), self._denoise_str.get())
            if self._sec_denoise.get()
            else None,
            sharpen=self._sharpen(),
            caption=self._caption(notes),
            fades=self._fades(),
            volume=_num(self._vol_val, 1.0) if audio and self._vol_en.get() else None,
            loudness=self._norm_lufs.get() if audio and self._norm_en.get() else None,
            encode=self._encode(),
        )

    def _trim(self, notes: list[str]) -> tuple[float, float | None]:
        if not self._sec_trim.get():
            return 0.0, None
        start, end = 0.0, None
        try:
            start = max(0.0, self._trim_range.start.seconds(0.0) or 0.0)
        except ValueError:
            notes.append("trim start is not a time")
        try:
            end = self._trim_range.end.seconds()
        except ValueError:
            notes.append("trim end is not a time")
        if end is not None and end <= start:
            notes.append("trim end must be after the start")
            end = None
        return start, end

    def _speed(self, notes: list[str]) -> float:
        if not self._sec_speed.get():
            return 1.0
        try:
            return max(0.05, min(float(self._speed_val.get()), 64.0))
        except ValueError:
            notes.append("speed is not a number")
            return 1.0

    def _scale(self) -> aio.Scale | None:
        if not self._sec_scale.get():
            return None
        return aio.Scale(
            self._scale_w.get() or "1920",
            self._scale_h.get() or "1080",
            _SCALE_ALGO.get(self._scale_algo.get(), "lanczos"),
            self._scale_pad.get(),
        )

    def _colour(self) -> aio.Colour | None:
        if not self._sec_color.get():
            return None
        try:
            return aio.Colour(
                *(float(v.get()) for v in (self._bri, self._con, self._sat, self._gam, self._hue))
            )
        except ValueError:
            return aio.Colour()

    def _sharpen(self) -> tuple[int, float] | None:
        if not self._sec_sharpen.get():
            return None
        try:
            return int(self._sharp_lx.get()) | 1, float(self._sharp_la.get())
        except ValueError:
            return 5, 1.0

    def _caption(self, notes: list[str]) -> aio.Caption | None:
        if not self._sec_text.get() or not self._text_val.get().strip():
            return None
        if getattr(self, "_aio_tmp", None) is None:
            from core.paths import temp_dir

            self._aio_tmp = temp_dir("allinone_")
        textfile = os.path.join(self._aio_tmp, "caption.txt")
        with open(textfile, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self._text_val.get())
        enable = None
        try:
            st = self._text_shown.start.seconds(0.0) or 0.0
            until = self._text_shown.end.seconds()
            if until is not None:
                enable = f"between(t,{st:.3f},{until:.3f})"
            elif st > 0:
                enable = f"gte(t,{st:.3f})"
        except ValueError:
            notes.append("text times are not valid times")
        return aio.Caption(
            textfile,
            _TEXT_POSITIONS.get(self._text_pos.get(), _TEXT_POSITIONS["Bottom Right"]),
            (self._text_size.get() or "42").strip(),
            self._text_color.get() or "white",
            enable,
        )

    def _fades(self) -> aio.Fades | None:
        if not self._sec_fade.get():
            return None
        return aio.Fades(
            _num(self._fi_dur, 1.5) if self._fi_en.get() else None,
            _num(self._fo_dur, 2.0) if self._fo_en.get() else None,
            self._fade_audio.get(),
        )

    def _encode(self) -> aio.Encode:
        return aio.Encode(
            self._sec_encode.get(),
            _VCODECS.get(self._vcodec.get(), "libx264"),
            self._acodec.get(),
            self._crf.get(),
            self._preset.get(),
            self._abitrate.get(),
        )

    # ─────────────────────────────────────────────────────────────────────
    #  Command display helpers
    # ─────────────────────────────────────────────────────────────────────

    def _set_cmd_text(self, text: str) -> None:
        self._setting_text = True
        self._cmd_text.config(state="normal")
        self._cmd_text.delete("1.0", "end")
        self._cmd_text.insert("1.0", text)
        self._cmd_text.edit_modified(False)
        self._setting_text = False

    def _on_cmd_modified(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if self._cmd_text.edit_modified() and not getattr(self, "_setting_text", False):
            self._cmd_dirty = True
            self._edit_lbl.config(
                text="✎ Edited by hand - your edits are used; 'Reset command' regenerates it"
            )
        self._cmd_text.edit_modified(False)

    def _reset_cmd(self) -> None:
        self._cmd_dirty = False
        self._rebuild_cmd()

    def _get_cmd_from_text(self) -> list[str]:
        """The exact argv list, or the user's edited text parsed back."""
        if not getattr(self, "_cmd_dirty", False):
            return list(getattr(self, "_last_cmd", []) or [])
        import re

        raw = self._cmd_text.get("1.0", "end").strip()
        raw = re.sub(r"\\\r?\n", " ", raw)  # only line continuations
        try:
            return shlex.split(raw)
        except ValueError as exc:
            messagebox.showerror(
                t("msg.command_parse_error_title"),
                t("msg.command_parse_error", cmd=str(exc)),
                parent=self,
            )
            return []

    # ─────────────────────────────────────────────────────────────────────
    #  Actions
    # ─────────────────────────────────────────────────────────────────────

    def _render(self) -> None:
        cmd = self._get_cmd_from_text()
        if not cmd:
            return

        # Basic sanity checks
        src = self._src_var.get().strip()
        out = self._out_var.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), "Source file not found.")
            return
        if not out:
            messagebox.showwarning(t("common.warning"), "Set an output file path.")
            return

        self.log(self.console, t("log.all_in_one.running_pipeline"))
        self.log(self.console, "  " + " ".join(shlex.quote(c) for c in cmd[:6]) + " …")

        # Turn on the STOP button
        self._btn_stop.config(state="normal")

        # A little wrapper to re-disable the STOP button when FFmpeg finishes
        def _on_done_wrapper(rc: int) -> None:
            self._btn_stop.config(state="disabled")
            self.show_result(rc, out)

        final = cmd[-1]
        checked = self.confirm_output(
            final, [src], default_ext=os.path.splitext(final)[1] or ".mp4"
        )
        if not checked:
            self._btn_stop.config(state="disabled")
            return
        cmd[-1] = checked
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=_on_done_wrapper,
            btn=self.btn_render,
            duration=getattr(self, "_expected_len", None),
            output=checked,
            inputs=[src],
            name="All-in-One",
        )

    def _stop_render(self) -> None:
        """Cancel this tab's queued/running jobs (kills only their own FFmpeg)."""
        self.cancel_render()

        self.log(self.console, t("log.all_in_one.render_forcibly_stopped_by_user"))
        self._btn_stop.config(state="disabled")
        self.btn_render.config(state="normal", text="▶  RENDER")

    def _copy_cmd(self) -> None:
        raw = self._cmd_text.get("1.0", "end").strip()
        self.clipboard_clear()
        self.clipboard_append(raw)
        self.log(self.console, t("log.all_in_one.command_copied_to_clipboard"))

    def _save_bat(self) -> None:
        argv = self._get_cmd_from_text()
        if not argv:
            return
        p = dialogs.ask_save("render.bat", kind="any", defaultextension=".bat", parent=self)
        if not p:
            return
        from core import proc as _proc

        try:
            with open(p, "w", encoding="utf-8", newline="\r\n") as f:
                f.write("@echo off\nchcp 65001 >nul\n")
                f.write(_proc.list2cmdline(argv).replace("%", "%%") + "\n")
                f.write("pause\n")
            self.log(self.console, f"✅  Saved: {p}")
        except OSError as exc:
            messagebox.showerror(t("common.error"), str(exc), parent=self)

    def _save_sh(self) -> None:
        argv = self._get_cmd_from_text()
        if not argv:
            return
        p = dialogs.ask_save("render.sh", kind="any", defaultextension=".sh", parent=self)
        if not p:
            return
        try:
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write("#!/usr/bin/env bash\nset -euo pipefail\n")
                f.write(shlex.join(argv) + "\n")
            os.chmod(p, 0o755)
            self.log(self.console, f"✅  Saved: {p}")
        except OSError as exc:
            messagebox.showerror(t("common.error"), str(exc), parent=self)
