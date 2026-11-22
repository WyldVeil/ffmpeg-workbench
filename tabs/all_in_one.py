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

import os
import shlex
import tkinter as tk
from functools import partial
from tkinter import messagebox, ttk

from core import tasks
from core.i18n import t
from core.scaling import px
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit import dialogs

# ── Constants reused from other tabs ─────────────────────────────────────────

_RES_PRESETS = [
    "7680x4320 (8K)",
    "3840x2160 (4K UHD)",
    "2560x1440 (QHD/2K)",
    "1920x1080 (Full HD)",
    "1280x720 (HD)",
    "854x480 (SD)",
    "640x360 (360p)",
    "Custom…",
]
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
_NVENC_PRESET = {
    "ultrafast": "p1",
    "superfast": "p2",
    "veryfast": "p3",
    "faster": "p3",
    "fast": "p4",
    "medium": "p5",
    "slow": "p6",
    "slower": "p7",
    "veryslow": "p7",
}
_AMF_QUALITY = {
    "ultrafast": "speed",
    "superfast": "speed",
    "veryfast": "speed",
    "faster": "speed",
    "fast": "balanced",
    "medium": "balanced",
    "slow": "quality",
    "slower": "quality",
    "veryslow": "quality",
}
_SVT_PRESET = {
    "ultrafast": 12,
    "superfast": 11,
    "veryfast": 10,
    "faster": 9,
    "fast": 8,
    "medium": 7,
    "slow": 6,
    "slower": 5,
    "veryslow": 4,
}


def _video_codec_args(vcodec: str, crf: str, preset: str, container: str) -> list:
    """Encoder arguments per encoder family (x264 preset names and CRF are
    translated - NVENC/AMF/QSV reject x264 names)."""
    q = int(crf) if str(crf).isdigit() else 18
    if vcodec in ("libx264", "libx265"):
        args = ["-c:v", vcodec, "-crf", str(q), "-preset", preset, "-pix_fmt", "yuv420p"]
        if vcodec == "libx265":
            args += ["-x265-params", "log-level=error"]
    elif vcodec.endswith("_nvenc"):
        args = [
            "-c:v",
            vcodec,
            "-preset",
            _NVENC_PRESET.get(preset, "p5"),
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            str(q),
            "-b:v",
            "0",
            "-pix_fmt",
            "yuv420p",
        ]
    elif vcodec.endswith("_amf"):
        args = [
            "-c:v",
            vcodec,
            "-quality",
            _AMF_QUALITY.get(preset, "balanced"),
            "-rc",
            "cqp",
            "-qp_i",
            str(q),
            "-qp_p",
            str(q + 2),
            "-pix_fmt",
            "yuv420p",
        ]
    elif vcodec.endswith("_qsv"):
        qsv_preset = (
            preset
            if preset in ("veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
            else "medium"
        )
        args = [
            "-c:v",
            vcodec,
            "-preset",
            qsv_preset,
            "-global_quality",
            str(q),
            "-pix_fmt",
            "nv12",
        ]
    elif vcodec == "libsvtav1":
        args = [
            "-c:v",
            "libsvtav1",
            "-crf",
            str(min(63, round(q * 1.6))),
            "-preset",
            str(_SVT_PRESET.get(preset, 8)),
            "-pix_fmt",
            "yuv420p",
        ]
    elif vcodec in ("libvpx-vp9", "libvpx"):
        args = [
            "-c:v",
            vcodec,
            "-b:v",
            "0",
            "-crf",
            str(min(63, round(q * 1.65))),
            "-row-mt",
            "1",
            "-deadline",
            "good",
            "-cpu-used",
            "2" if preset in ("slow", "slower", "veryslow") else "4",
            "-pix_fmt",
            "yuv420p",
        ]
    elif vcodec == "prores_ks":
        args = [
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            "-vendor",
            "apl0",
            "-pix_fmt",
            "yuv422p10le",
        ]
    else:
        args = ["-c:v", vcodec]
    if container in ("mp4", "mov", "m4v") and ("265" in vcodec or "hevc" in vcodec):
        args += ["-tag:v", "hvc1"]
    return args


def _resolve_auto(vcodec: str) -> str:
    """'auto_h264' / 'auto_hevc' -> the best working encoder on this machine."""
    if not vcodec.startswith("auto_"):
        return vcodec
    from core import encoders

    enc, _hw = encoders.choose_encoder(vcodec[5:], "auto")
    return enc


def _codec_container(vcodec: str, out: str) -> str:
    """Output path with an extension the codec can live in."""
    base, ext = os.path.splitext(out)
    ext = ext.lower()
    if vcodec == "prores_ks" and ext not in (".mov", ".mkv"):
        return base + ".mov"
    if vcodec in ("libvpx-vp9", "libvpx") and ext not in (".webm", ".mkv"):
        return base + ".webm"
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Small UI helpers
# ─────────────────────────────────────────────────────────────────────────────


def _lbl(parent, text, bold=False, dim=False):
    fg = CLR["fgdim"] if dim else CLR["fg"]
    font = (UI_FONT, 9, "bold") if bold else (UI_FONT, 9)
    return tk.Label(parent, text=text, bg=CLR["bg"], fg=fg, font=font)


def _entry(parent, var, width=10):
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


def _combo(parent, var, values, width=18, state="readonly"):
    cb = ttk.Combobox(parent, textvariable=var, values=values, state=state, width=width)
    return cb


def _spin(parent, var, from_, to, inc=0.5, width=7):
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


def _row(parent, pady=3):
    f = tk.Frame(parent, bg=CLR["bg"])
    f.pack(fill="x", pady=px(pady))
    return f


def _pack_lr(*widgets, padx=6):
    """Pack a sequence of widgets left-to-right."""
    for w in widgets:
        w.pack(side="left", padx=px(padx))


# ─────────────────────────────────────────────────────────────────────────────
#  Section container
# ─────────────────────────────────────────────────────────────────────────────


class _Section(tk.Frame):
    """
    A collapsible section with a checkbox header.
    When the checkbox is off all interior widgets are disabled (greyed out).
    Calls *on_toggle* whenever the enabled state changes.
    """

    def __init__(self, parent, title, on_toggle, **kw):
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

    def get(self):
        return self.enabled.get()

    def _toggled(self):
        self._set_body_state("normal" if self.enabled.get() else "disabled")
        self._cb()

    def set_enabled(self, on: bool) -> None:
        """Tick (or untick) the section without notifying anyone."""
        self.enabled.set(on)
        self._set_body_state("normal" if on else "disabled")

    def _set_body_state(self, state):
        fg = CLR["fg"] if state == "normal" else CLR["fgdim"]
        self._walk(self.body, state, fg)

    def _walk(self, widget, state, fg):
        cls = widget.winfo_class()
        try:
            if cls in ("Entry", "Spinbox", "Button", "Checkbutton", "Radiobutton"):
                widget.config(state=state)
            if cls in ("Label",):
                widget.config(fg=fg)
            if cls == "TCombobox":
                widget.config(state="readonly" if state == "normal" else "disabled")
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._walk(child, state, fg)


# ─────────────────────────────────────────────────────────────────────────────
#  Main tab
# ─────────────────────────────────────────────────────────────────────────────


class AllInOneTab(BaseTab):
    """Pipeline builder - combine any processing steps into one FFmpeg pass."""

    def __init__(self, parent):
        super().__init__(parent)
        self._probed_path: str | None = None
        self._duration = 0.0  # loaded video duration - needed for fade-out math
        self._last_cmd = []  # most recently built command list
        self._build_ui()
        from tabs.widgets import add_pickers

        add_pickers(
            self,
            [self._trim_start, self._trim_end, self._text_start, self._text_end],
            lambda: self._src_var.get().strip(),
        )

    # ─────────────────────────────────────────────────────────────────────
    #  Top-level layout
    # ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Fixed header ──────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=CLR["panel"])
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text="🎛  " + t("tab.all_in_one"),
            font=(UI_FONT, 16, "bold"),
            bg=CLR["panel"],
            fg=CLR["accent"],
        ).pack(side="left", padx=px(20), pady=px(12))
        tk.Label(hdr, text=t("all_in_one.subtitle"), bg=CLR["panel"], fg=CLR["fgdim"]).pack(
            side="left"
        )

        # ── Source / output (always visible, above scroll) ─────────────────
        io_frame = tk.Frame(self, bg=CLR["bg"], padx=px(12), pady=px(8))
        io_frame.pack(fill="x")
        self._build_io(io_frame)

        # ── Scrollable section area ────────────────────────────────────────
        self._canvas = tk.Canvas(self, highlightthickness=0, bg=CLR["bg"])
        _sb = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._scroll_inner = tk.Frame(self._canvas, bg=CLR["bg"])
        self._canvas.create_window((0, 0), window=self._scroll_inner, anchor="nw")
        self._scroll_inner.bind(
            "<Configure>", lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        )
        self._canvas.configure(yscrollcommand=_sb.set)
        _sb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._build_sections(self._scroll_inner)

        # ── Fixed bottom: command display + render ─────────────────────────
        self._build_bottom()

    # ─────────────────────────────────────────────────────────────────────
    #  Source / Output
    # ─────────────────────────────────────────────────────────────────────

    def _build_io(self, parent):
        self._src_var = tk.StringVar()
        self._out_var = tk.StringVar()

        r1 = _row(parent, pady=2)
        _lbl(r1, t("common.source_video"), bold=True).pack(side="left")
        _entry(r1, self._src_var, width=62).pack(side="left", padx=px(8))
        tk.Button(
            r1, text=t("btn.browse"), command=self._browse_src, bg=CLR["panel"], fg=CLR["fg"]
        ).pack(side="left")
        self._dur_lbl = tk.Label(r1, text="", bg=CLR["bg"], fg=CLR["accent"], font=(MONO_FONT, 9))
        self._dur_lbl.pack(side="left", padx=px(10))

        self._src_job = None
        self._src_var.trace_add("write", lambda *_a: self._src_typed())

        r2 = _row(parent, pady=2)
        _lbl(r2, t("common.output_file"), bold=True).pack(side="left")
        _entry(r2, self._out_var, width=62).pack(side="left", padx=px(8))
        tk.Button(
            r2, text=t("common.save_as"), command=self._browse_out, bg=CLR["panel"], fg=CLR["fg"]
        ).pack(side="left")

    def _browse_src(self):
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

    def _src_typed(self):
        """Typed / pasted / dropped paths are probed too (debounced)."""
        if self._src_job is not None:
            self.after_cancel(self._src_job)

        def go():
            self._src_job = None
            p = self._src_var.get().strip()
            if p and os.path.isfile(p) and p != self._probed_path:
                self._probed_path = p
                if not self._out_var.get():
                    self._out_var.set(self.suggest_output(p, "pipeline"))
                tasks.submit(partial(self._load_duration, p))

        self._src_job = self.after(400, go)

    def _load_duration(self, path):
        from core import ffmpeg as _ff

        info = _ff.try_probe(path)
        dur = info.duration if info else 0.0
        self._duration = dur
        self._sample_rate = (
            info.require_audio().sample_rate if info is not None and info.audio else 0
        )
        self.after(0, lambda: self._dur_lbl.config(text=f"{dur:.2f}s" if dur else ""))
        self.after(0, self._rebuild_cmd)

    def _browse_out(self):
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

    def _build_sections(self, parent):
        """Build all optional processing sections inside the scrollable area."""

        def sep():
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

    def _build_trim(self, parent):
        self._sec_trim = _Section(parent, "✂  Trim", self._rebuild_cmd)
        self._sec_trim.pack(fill="x", pady=px(1))
        b = self._sec_trim.body

        self._trim_start = tk.StringVar(value="0.0")
        self._trim_end = tk.StringVar(value="")
        self._trim_start.trace_add("write", lambda *_: self._rebuild_cmd())
        self._trim_end.trace_add("write", lambda *_: self._rebuild_cmd())

        r = _row(b)
        _lbl(r, "Start (s):").pack(side="left")
        _entry(r, self._trim_start, 8).pack(side="left", padx=px(6))
        _lbl(r, "  End (s):").pack(side="left")
        _entry(r, self._trim_end, 8).pack(side="left", padx=px(6))
        _lbl(r, "(leave End blank to go to end of video)", dim=True).pack(side="left", padx=px(6))

    # ── 📐 Scale ──────────────────────────────────────────────────────────

    def _build_scale(self, parent):
        self._sec_scale = _Section(parent, "📐  Scale / Resolution", self._rebuild_cmd)
        self._sec_scale.pack(fill="x", pady=px(1))
        b = self._sec_scale.body

        self._scale_preset = tk.StringVar(value=_RES_PRESETS[3])
        self._scale_w = tk.StringVar(value="1920")
        self._scale_h = tk.StringVar(value="1080")
        self._scale_algo = tk.StringVar(value=next(iter(_SCALE_ALGO.keys())))
        self._scale_pad = tk.BooleanVar(value=False)

        for v in (self._scale_preset, self._scale_w, self._scale_h, self._scale_algo):
            v.trace_add("write", lambda *_: self._rebuild_cmd())
        self._scale_pad.trace_add("write", lambda *_: self._rebuild_cmd())

        r1 = _row(b)
        _lbl(r1, "Preset:").pack(side="left")
        cb = _combo(r1, self._scale_preset, _RES_PRESETS, width=24)
        cb.pack(side="left", padx=px(6))
        cb.bind("<<ComboboxSelected>>", self._on_res_preset)
        _lbl(r1, "  W:").pack(side="left")
        self._scale_w_entry = _entry(r1, self._scale_w, 6)
        self._scale_w_entry.pack(side="left", padx=px(3))
        _lbl(r1, "H:").pack(side="left")
        self._scale_h_entry = _entry(r1, self._scale_h, 6)
        self._scale_h_entry.pack(side="left", padx=px(3))
        _lbl(r1, "(-1 = auto-aspect)", dim=True).pack(side="left", padx=px(6))

        r2 = _row(b)
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

    def _on_res_preset(self, *_):
        val = self._scale_preset.get()
        if "Custom" not in val:
            wh = val.split(" ")[0].split("x")
            if len(wh) == 2:
                self._scale_w.set(wh[0])
                self._scale_h.set(wh[1])
        self._rebuild_cmd()

    # ── 🎬 Frame Rate ─────────────────────────────────────────────────────

    def _build_fps(self, parent):
        self._sec_fps = _Section(parent, "🎬  Frame Rate", self._rebuild_cmd)
        self._sec_fps.pack(fill="x", pady=px(1))
        b = self._sec_fps.body

        self._fps_val = tk.StringVar(value="30")
        self._fps_val.trace_add("write", lambda *_: self._rebuild_cmd())

        r = _row(b)
        _lbl(r, "Target FPS:").pack(side="left")
        cb = _combo(r, self._fps_val, _FPS_VALS, width=8, state="normal")
        cb.pack(side="left", padx=px(6))
        _lbl(r, "(or type any value)", dim=True).pack(side="left", padx=px(4))

    # ── 🔄 Rotate & Flip ─────────────────────────────────────────────────

    def _build_rotate(self, parent):
        self._sec_rotate = _Section(parent, "🔄  Rotate & Flip", self._rebuild_cmd)
        self._sec_rotate.pack(fill="x", pady=px(1))
        b = self._sec_rotate.body

        self._rotate_val = tk.StringVar(value="0")
        self._flip_h = tk.BooleanVar(value=False)
        self._flip_v = tk.BooleanVar(value=False)

        for v in (self._rotate_val, self._flip_h, self._flip_v):
            v.trace_add("write", lambda *_: self._rebuild_cmd())

        r = _row(b)
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

        r2 = _row(b)
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

    def _build_speed(self, parent):
        self._sec_speed = _Section(parent, "⚡  Speed", self._rebuild_cmd)
        self._sec_speed.pack(fill="x", pady=px(1))
        b = self._sec_speed.body

        self._speed_val = tk.StringVar(value="2.0")
        self._speed_pitch = tk.BooleanVar(value=True)
        for v in (self._speed_val, self._speed_pitch):
            v.trace_add("write", lambda *_: self._rebuild_cmd())

        r = _row(b)
        _lbl(r, "Speed multiplier:").pack(side="left")
        _spin(r, self._speed_val, 0.05, 16, 0.25, width=6).pack(side="left", padx=px(6))
        _lbl(r, "(0.5 = half speed · 2.0 = double)", dim=True).pack(side="left", padx=px(6))

        r2 = _row(b)
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

    def _build_color(self, parent):
        self._sec_color = _Section(parent, "🎨  Color & Image", self._rebuild_cmd)
        self._sec_color.pack(fill="x", pady=px(1))
        b = self._sec_color.body

        def _sv(default):
            v = tk.StringVar(value=str(default))
            v.trace_add("write", lambda *_: self._rebuild_cmd())
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
            r = _row(b, pady=2)
            _lbl(r, label, bold=True).pack(side="left")
            sc = tk.Scale(
                r,
                variable=var,
                from_=lo,
                to=hi,
                resolution=inc,
                orient="horizontal",
                length=px(280),
                bg=CLR["bg"],
                fg=CLR["fg"],
                highlightthickness=0,
                command=lambda v, var=var: (var.set(f"{float(v):.2f}"), self._rebuild_cmd()),
            )
            sc.pack(side="left", padx=px(6))
            _lbl(r, "", dim=True).pack(side="left")  # spacer

        r5 = _row(b, pady=2)
        tk.Button(
            r5,
            text="Reset all to defaults",
            bg=CLR["panel"],
            fg=CLR["fg"],
            command=self._reset_color,
        ).pack(side="left")

    def _reset_color(self):
        self._bri.set("0.00")
        self._con.set("1.00")
        self._sat.set("1.00")
        self._gam.set("1.00")
        self._hue.set("0")
        self._rebuild_cmd()

    # ── 🌫 Denoise ────────────────────────────────────────────────────────

    def _build_denoise(self, parent):
        self._sec_denoise = _Section(parent, "🌫  Denoise", self._rebuild_cmd)
        self._sec_denoise.pack(fill="x", pady=px(1))
        b = self._sec_denoise.body

        self._denoise_algo = tk.StringVar(value="hqdn3d")
        self._denoise_str = tk.StringVar(value="Medium")
        for v in (self._denoise_algo, self._denoise_str):
            v.trace_add("write", lambda *_: self._rebuild_cmd())

        r = _row(b)
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

        r2 = _row(b)
        _lbl(r2, "Strength:").pack(side="left")
        _combo(r2, self._denoise_str, ["Light", "Medium", "Strong", "Aggressive"], width=12).pack(
            side="left", padx=px(6)
        )

    # ── 🔪 Sharpen ────────────────────────────────────────────────────────

    def _build_sharpen(self, parent):
        self._sec_sharpen = _Section(parent, "🔪  Sharpen", self._rebuild_cmd)
        self._sec_sharpen.pack(fill="x", pady=px(1))
        b = self._sec_sharpen.body

        self._sharp_lx = tk.StringVar(value="5")
        self._sharp_la = tk.StringVar(value="1.0")
        for v in (self._sharp_lx, self._sharp_la):
            v.trace_add("write", lambda *_: self._rebuild_cmd())

        r = _row(b)
        _lbl(r, "Kernel size (odd):").pack(side="left")
        _combo(r, self._sharp_lx, ["3", "5", "7"], width=4).pack(side="left", padx=px(6))
        _lbl(r, "  Amount (luma):").pack(side="left")
        _spin(r, self._sharp_la, 0.1, 5.0, 0.1, width=5).pack(side="left", padx=px(6))
        _lbl(r, "(0.5 subtle · 1.0 standard · 2.5 aggressive)", dim=True).pack(
            side="left", padx=px(6)
        )

    # ── 🔊 Audio ──────────────────────────────────────────────────────────

    def _build_audio(self, parent):
        self._sec_audio = _Section(parent, "🔊  Audio Processing", self._rebuild_cmd)
        self._sec_audio.pack(fill="x", pady=px(1))
        b = self._sec_audio.body

        self._vol_en = tk.BooleanVar(value=False)
        self._vol_val = tk.StringVar(value="1.5")
        self._norm_en = tk.BooleanVar(value=False)
        self._norm_lufs = tk.StringVar(value="-23")

        for v in (self._vol_en, self._vol_val, self._norm_en, self._norm_lufs):
            v.trace_add("write", lambda *_: self._rebuild_cmd())

        r1 = _row(b)
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

        r2 = _row(b)
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

    def _build_text(self, parent):
        self._sec_text = _Section(parent, "💬  Text / Watermark Overlay", self._rebuild_cmd)
        self._sec_text.pack(fill="x", pady=px(1))
        b = self._sec_text.body

        self._text_val = tk.StringVar(value="@YourChannel")
        self._text_size = tk.StringVar(value="42")
        self._text_color = tk.StringVar(value="white")
        self._text_pos = tk.StringVar(value="Bottom Right")
        self._text_start = tk.StringVar(value="0")
        self._text_end = tk.StringVar(value="")

        for v in (
            self._text_val,
            self._text_size,
            self._text_color,
            self._text_pos,
            self._text_start,
            self._text_end,
        ):
            v.trace_add("write", lambda *_: self._rebuild_cmd())

        r1 = _row(b)
        _lbl(r1, "Text:").pack(side="left")
        _entry(r1, self._text_val, 34).pack(side="left", padx=px(6))
        _lbl(r1, "  Size:").pack(side="left")
        _spin(r1, self._text_size, 8, 200, 2, width=5).pack(side="left", padx=px(4))
        _lbl(r1, "  Colour:").pack(side="left")
        _combo(
            r1, self._text_color, ["white", "yellow", "red", "cyan", "black", "orange"], width=8
        ).pack(side="left", padx=px(4))

        r2 = _row(b)
        _lbl(r2, "Position:").pack(side="left")
        _combo(r2, self._text_pos, list(_TEXT_POSITIONS.keys()), width=14).pack(
            side="left", padx=px(6)
        )
        _lbl(r2, "  Show from (s):").pack(side="left")
        _entry(r2, self._text_start, 6).pack(side="left", padx=px(4))
        _lbl(r2, "  to (s, blank = end):").pack(side="left")
        _entry(r2, self._text_end, 6).pack(side="left", padx=px(4))

    # ── 🌅 Fade In / Out ─────────────────────────────────────────────────

    def _build_fade(self, parent):
        self._sec_fade = _Section(parent, "🌅  Fade In / Fade Out", self._rebuild_cmd)
        self._sec_fade.pack(fill="x", pady=px(1))
        b = self._sec_fade.body

        self._fi_en = tk.BooleanVar(value=False)
        self._fi_dur = tk.StringVar(value="1.5")
        self._fo_en = tk.BooleanVar(value=False)
        self._fo_dur = tk.StringVar(value="2.0")
        self._fade_audio = tk.BooleanVar(value=True)

        for v in (self._fi_en, self._fi_dur, self._fo_en, self._fo_dur, self._fade_audio):
            v.trace_add("write", lambda *_: self._rebuild_cmd())

        r1 = _row(b)
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

        r2 = _row(b)
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

        r3 = _row(b)
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

    def _build_encode(self, parent):
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
            v.trace_add("write", lambda *_: self._rebuild_cmd())

        r1 = _row(b)
        _lbl(r1, "Video codec:").pack(side="left")
        _combo(r1, self._vcodec, list(_VCODECS.keys()), width=24).pack(side="left", padx=px(6))
        _lbl(r1, "  Quality (CRF/CQ):").pack(side="left")
        _combo(r1, self._crf, [str(x) for x in range(12, 32)], width=4).pack(
            side="left", padx=px(4)
        )
        _lbl(r1, "  Preset:").pack(side="left")
        _combo(r1, self._preset, _PRESETS, width=10).pack(side="left", padx=px(4))

        r2 = _row(b)
        _lbl(r2, "Audio codec:").pack(side="left")
        _combo(r2, self._acodec, _ACODECS, width=12).pack(side="left", padx=px(6))
        _lbl(r2, "  Bitrate:").pack(side="left")
        _combo(r2, self._abitrate, _ABITRATES, width=7).pack(side="left", padx=px(4))
        _lbl(r2, "(ignored when codec = copy / none)", dim=True).pack(side="left", padx=px(6))

    # ─────────────────────────────────────────────────────────────────────
    #  Fixed bottom bar: command display + render
    # ─────────────────────────────────────────────────────────────────────

    def _build_bottom(self):
        bottom = tk.Frame(self, bg=CLR["panel"])
        bottom.pack(side="bottom", fill="x")

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
        cmd_sb_x = ttk.Scrollbar(cmd_frame, orient="horizontal", command=self._cmd_text.xview)
        cmd_sb_y = ttk.Scrollbar(cmd_frame, orient="vertical", command=self._cmd_text.yview)
        self._cmd_text.configure(xscrollcommand=cmd_sb_x.set, yscrollcommand=cmd_sb_y.set)
        cmd_sb_y.pack(side="right", fill="y")
        self._cmd_text.pack(side="left", fill="x", expand=True)
        self._cmd_text.bind("<<Modified>>", self._on_cmd_modified)
        cmd_sb_x.pack(side="bottom", fill="x")

        # ── Action buttons ────────────────────────────────────────────────
        btn_row = tk.Frame(bottom, bg=CLR["panel"])
        btn_row.pack(fill="x", padx=px(12), pady=px(4, 10))

        self.btn_render = tk.Button(
            btn_row,
            text="▶  RENDER",
            font=(UI_FONT, 12, "bold"),
            bg=CLR["green"],
            fg="black",
            height=2,
            width=20,
            cursor="hand2",
            command=self._render,
        )
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
        con_frame = tk.Frame(bottom, bg=CLR["panel"])
        con_frame.pack(fill="x", padx=px(12), pady=px(0, 8))
        self.console, csb = self.make_console(con_frame, height=5)
        self.console.pack(side="left", fill="x", expand=True)
        csb.pack(side="right", fill="y")

        # Initial command render
        self._rebuild_cmd()

    # ─────────────────────────────────────────────────────────────────────
    #  Command builder  ← the heart of the tab
    # ─────────────────────────────────────────────────────────────────────

    def _rebuild_cmd(self, *_):
        """Assemble one FFmpeg command from every enabled section and show it.

        Order matters: trim on the INPUT side (source time), then rotate,
        scale, speed, frame rate, colour, denoise, sharpen, text and fades
        (fade times are in OUTPUT time, i.e. after the speed change)."""
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
        from core import ffmpeg as _ff
        from core import filters as _fl

        parts = [_ff.ffmpeg_path(), "-y"]
        vf, af, notes = [], [], []
        dur = float(getattr(self, "_duration", 0.0) or 0.0)

        # ── Trim (input side, source time) ───────────────────────────────
        start, end = 0.0, None
        if self._sec_trim.get():
            try:
                start = max(0.0, _ff.parse_time(self._trim_start.get() or "0"))
            except ValueError:
                notes.append("trim start is not a time")
            try:
                if self._trim_end.get().strip():
                    end = _ff.parse_time(self._trim_end.get())
            except ValueError:
                notes.append("trim end is not a time")
            if end is not None and end <= start:
                notes.append("trim end must be after the start")
                end = None
            if start > 0:
                parts += ["-ss", _ff.ts(start)]
            if end is not None:
                parts += ["-to", _ff.ts(end)]
        parts += ["-i", src]
        src_len = ((end if end is not None else dur) - start) if dur or end else 0.0

        # ── Speed factor (needed for output-time fades) ───────────────────
        speed = 1.0
        if self._sec_speed.get():
            try:
                speed = max(0.05, min(float(self._speed_val.get()), 64.0))
            except ValueError:
                notes.append("speed is not a number")
        out_len = src_len / speed if src_len else 0.0

        # ── Rotate & flip ──────────────────────────────────────────────────
        if self._sec_rotate.get():
            rot = self._rotate_val.get()
            vf += {
                "90": ["transpose=clock"],
                "180": ["hflip", "vflip"],
                "270": ["transpose=cclock"],
            }.get(rot, [])
            if self._flip_h.get():
                vf.append("hflip")
            if self._flip_v.get():
                vf.append("vflip")

        # ── Scale ─────────────────────────────────────────────────────────
        if self._sec_scale.get():
            w = (self._scale_w.get() or "1920").strip()
            h = (self._scale_h.get() or "1080").strip()
            w, h = ("-2" if w in ("-1", "") else w), ("-2" if h in ("-1", "") else h)
            alg = _SCALE_ALGO.get(self._scale_algo.get(), "lanczos")
            if self._scale_pad.get() and "-2" not in (w, h):
                vf.append(
                    f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags={alg},"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
                )
            else:
                if self._scale_pad.get():
                    notes.append("letterboxing needs both width and height")
                vf.append(f"scale={w}:{h}:flags={alg}")
            vf.append("setsar=1")

        # ── Speed (video) then frame rate ─────────────────────────────────
        if abs(speed - 1.0) > 1e-3:
            vf.append(f"setpts=(PTS-STARTPTS)/{speed:.4f}")
        if self._sec_fps.get():
            vf.append(f"fps={(self._fps_val.get().strip() or '30')}")

        # ── Colour ────────────────────────────────────────────────────────
        if self._sec_color.get():
            try:
                bri, con, sat, gam, hue = (
                    float(v.get()) for v in (self._bri, self._con, self._sat, self._gam, self._hue)
                )
            except ValueError:
                bri, con, sat, gam, hue = 0, 1, 1, 1, 0
            eq = [
                f"{k}={v:.3f}"
                for k, v, d in (
                    ("brightness", bri, 0),
                    ("contrast", con, 1),
                    ("saturation", sat, 1),
                    ("gamma", gam, 1),
                )
                if abs(v - d) > 1e-3
            ]
            if eq:
                vf.append("eq=" + ":".join(eq))
            if abs(hue) > 0.1:
                vf.append(f"hue=h={hue:.1f}")

        # ── Denoise / sharpen ─────────────────────────────────────────────
        if self._sec_denoise.get():
            p = {
                "Light": (2, 2, 3, 3),
                "Medium": (4, 4, 6, 6),
                "Strong": (8, 8, 12, 12),
                "Aggressive": (14, 14, 20, 20),
            }.get(self._denoise_str.get(), (4, 4, 6, 6))
            vf.append(
                "hqdn3d={}:{}:{}:{}".format(*p)
                if self._denoise_algo.get() == "hqdn3d"
                else f"nlmeans=s={p[0]}"
            )
        if self._sec_sharpen.get():
            try:
                lx, la = int(self._sharp_lx.get()) | 1, float(self._sharp_la.get())
            except ValueError:
                lx, la = 5, 1.0
            vf.append(f"unsharp={lx}:{lx}:{la}:{lx}:{lx}:0")

        # ── Text (drawtext from a UTF-8 file: any characters are safe) ─────
        if self._sec_text.get() and self._text_val.get().strip():
            if getattr(self, "_aio_tmp", None) is None:
                from core.paths import temp_dir

                self._aio_tmp = temp_dir("allinone_")
            textfile = os.path.join(self._aio_tmp, "caption.txt")
            with open(textfile, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(self._text_val.get())
            x, y = _TEXT_POSITIONS.get(self._text_pos.get(), _TEXT_POSITIONS["Bottom Right"])
            enable = None
            try:
                st = _ff.parse_time(self._text_start.get().strip() or "0")
                if self._text_end.get().strip():
                    enable = f"between(t,{st:.3f},{_ff.parse_time(self._text_end.get()):.3f})"
                elif st > 0:
                    enable = f"gte(t,{st:.3f})"
            except ValueError:
                notes.append("text times are not valid times")
            vf.append(
                _fl.DrawText(
                    textfile=textfile,
                    font="sans bold",
                    fontsize=(self._text_size.get() or "42").strip(),
                    fontcolor=self._text_color.get() or "white",
                    borderw=3,
                    x=x,
                    y=y,
                    enable=enable,
                ).filter()
            )

        # ── Fades (output time) ───────────────────────────────────────────
        def _num(var, default):
            try:
                return float(var.get())
            except ValueError:
                return default

        if self._sec_fade.get():
            if self._fi_en.get():
                vf.append(f"fade=t=in:st=0:d={_num(self._fi_dur, 1.5):.2f}")
            if self._fo_en.get() and out_len > 0:
                fd = _num(self._fo_dur, 2.0)
                vf.append(f"fade=t=out:st={max(0.0, out_len - fd):.3f}:d={fd:.2f}")

        # ── Audio filters ─────────────────────────────────────────────────
        if abs(speed - 1.0) > 1e-3:
            if self._speed_pitch.get():
                rem, chain = speed, []
                while rem > 2.0:
                    chain.append("atempo=2.0")
                    rem /= 2.0
                while rem < 0.5:
                    chain.append("atempo=0.5")
                    rem *= 2.0
                af += [*chain, f"atempo={rem:.4f}"]
            else:
                sr = int(getattr(self, "_sample_rate", 0) or 48000)
                af += [f"asetrate={round(sr * speed)}", f"aresample={sr}"]
        if self._sec_audio.get():
            if self._vol_en.get():
                af.append(f"volume={_num(self._vol_val, 1.0):.2f}")
            if self._norm_en.get():
                af += [
                    f"loudnorm=I={self._norm_lufs.get() or '-23'}:TP=-1:LRA=11",
                    "aresample=48000",
                    "asetnsamples=n=4096:p=0",
                ]
        if self._sec_fade.get() and self._fade_audio.get():
            if self._fi_en.get():
                af.append(f"afade=t=in:st=0:d={_num(self._fi_dur, 1.5):.2f}")
            if self._fo_en.get() and out_len > 0:
                fd = _num(self._fo_dur, 2.0)
                af.append(f"afade=t=out:st={max(0.0, out_len - fd):.3f}:d={fd:.2f}")

        # ── Codecs ────────────────────────────────────────────────────────
        enc_on = self._sec_encode.get()
        vcodec = _resolve_auto(_VCODECS.get(self._vcodec.get(), "libx264")) if enc_on else "libx264"
        acodec = self._acodec.get() if enc_on else "aac"
        if vcodec == "copy" and vf:
            notes.append("'Copy' can't apply video changes - encoding with H.264")
            vcodec = "libx264"
        if not enc_on and not vf:
            vcodec = "copy"
        fixed = _codec_container(vcodec, out)
        if fixed != out:
            notes.append(f"{os.path.splitext(fixed)[1]} is required for this codec")
            out = fixed
        container = os.path.splitext(out)[1].lower().lstrip(".")
        if acodec == "copy" and af:
            notes.append("'copy' can't apply audio changes - encoding audio")
            acodec = "libopus" if container == "webm" else "aac"
        if container == "webm" and acodec in ("aac", "libmp3lame"):
            acodec = "libopus"

        parts += ["-map", "0:v:0"]
        if acodec != "none":
            parts += ["-map", "0:a?"]
        if container in ("mkv",):
            parts += ["-map", "0:s?", "-c:s", "copy"]
        if vf:
            parts += ["-vf", ",".join(vf)]
        if af and acodec != "none":
            parts += ["-af", ",".join(af)]
        if vcodec == "copy":
            parts += ["-c:v", "copy"]
        else:
            parts += _video_codec_args(
                vcodec,
                self._crf.get() if enc_on else "18",
                self._preset.get() if enc_on else "fast",
                container,
            )
        if acodec == "none":
            parts += ["-an"]
        elif acodec == "copy":
            parts += ["-c:a", "copy"]
        else:
            parts += ["-c:a", acodec]
            if acodec in ("aac", "libmp3lame", "libopus", "libvorbis"):
                parts += ["-b:a", self._abitrate.get() if enc_on else "192k"]
        parts += ["-map_metadata", "0", "-map_chapters", "0"]
        if container in ("mp4", "mov", "m4v"):
            parts += ["-movflags", "+faststart"]
        parts.append(out)

        self._last_cmd = parts
        self._expected_len = out_len or None
        self._set_cmd_text(self._format_cmd(parts))
        self._edit_lbl.config(text=("⚠ " + "; ".join(notes)) if notes else "")

    # ─────────────────────────────────────────────────────────────────────
    #  Command display helpers
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _format_cmd(parts):
        """
        Format the command list as a readable multi-line string using
        backslash line continuations.  Groups args into logical pairs.
        """
        if not parts:
            return ""

        lines = [shlex.quote(parts[0])]
        i = 1
        # Flags that take a following value
        takes_val = {
            "-ss",
            "-t",
            "-to",
            "-i",
            "-vf",
            "-af",
            "-c:v",
            "-c:a",
            "-crf",
            "-preset",
            "-b:v",
            "-b:a",
            "-movflags",
            "-r",
            "-s",
            "-map",
            "-f",
        }

        while i < len(parts):
            p = parts[i]
            if p.startswith("-") and i + 1 < len(parts) and parts[i + 1] not in takes_val:
                # flag + value on same continuation line
                lines.append(f"  {shlex.quote(p)} {shlex.quote(parts[i + 1])}")
                i += 2
            else:
                lines.append(f"  {shlex.quote(p)}")
                i += 1

        return " \\\n".join(lines)

    def _set_cmd_text(self, text):
        self._setting_text = True
        self._cmd_text.config(state="normal")
        self._cmd_text.delete("1.0", "end")
        self._cmd_text.insert("1.0", text)
        self._cmd_text.edit_modified(False)
        self._setting_text = False

    def _on_cmd_modified(self, _event=None):
        if self._cmd_text.edit_modified() and not getattr(self, "_setting_text", False):
            self._cmd_dirty = True
            self._edit_lbl.config(
                text="✎ Edited by hand - your edits are used; 'Reset command' regenerates it"
            )
        self._cmd_text.edit_modified(False)

    def _reset_cmd(self):
        self._cmd_dirty = False
        self._rebuild_cmd()

    def _get_cmd_from_text(self):
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

    def _render(self):
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
        def _on_done_wrapper(rc):
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

    def _stop_render(self):
        """Cancel this tab's queued/running jobs (kills only their own FFmpeg)."""
        self.cancel_render()

        self.log(self.console, t("log.all_in_one.render_forcibly_stopped_by_user"))
        self._btn_stop.config(state="disabled")
        self.btn_render.config(state="normal", text="▶  RENDER")

    def _copy_cmd(self):
        raw = self._cmd_text.get("1.0", "end").strip()
        self.clipboard_clear()
        self.clipboard_append(raw)
        self.log(self.console, t("log.all_in_one.command_copied_to_clipboard"))

    def _save_bat(self):
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

    def _save_sh(self):
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
