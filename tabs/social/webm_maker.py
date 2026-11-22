"""
tab_webmmaker.py  ─  WebM Maker
Produces the highest quality WebM possible within a user-defined file-size
budget, using proper two-pass bitrate targeting.

Quality presets map to cpu-used + deadline settings for VP8/VP9.
Resolution sliders lock to original aspect ratio if desired.
"""

import contextlib
import logging
import os
import tkinter as tk
from enum import StrEnum
from functools import partial
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import tasks
from core.i18n import t
from core.ops import webm
from core.queue_manager import Hooks
from core.scaling import px
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit.time_fields import RangePicker
from tabs.kit.widgets import ChoiceBox
from tabs.widgets import FileRow, save_section, scroll_body

logger = logging.getLogger("crossfader.social.webm_maker")

# ─── Quality preset definitions ──────────────────────────────────────────────
# Each entry: (label, vp9_cpu_used, vp9_deadline, vp8_cpu_used, crf_hint)
QUALITY_PRESETS = [
    (t("webm_maker.best_quality_very_very_slow"), 0, "best", 0, 10),
    (t("webm_maker.brilliant_quality_very_slow"), 1, "best", 1, 18),
    (t("webm_maker.great_quality_slow"), 2, "good", 2, 24),
    (t("webm_maker.good_quality_balanced"), 3, "good", 3, 31),
    (t("webm_maker.low_quality_fast"), 4, "good", 4, 40),
    (t("webm_maker.bad_quality_very_fast"), 5, "realtime", 5, 50),
    (t("webm_maker.terrible_quality_very_very_fast"), 8, "realtime", 8, 63),
]
QUALITY_LABELS = [p[0] for p in QUALITY_PRESETS]

# Audio codec options
AUDIO_OPTIONS = {
    t("webm_maker.no_audio_option"): None,
    "Opus  (recommended)": "libopus",
    "Vorbis": "libvorbis",
}

AUDIO_BITRATES = ["48k", "64k", "96k", "128k", "160k", "192k", "256k", "320k"]


# ─── Pure helpers (no Tkinter dependency — testable) ────────────────────────
def _snap_even(n: int) -> int:
    """Round an integer up to the next even number if it's odd.

    Used for width/height because video codecs reject odd dimensions.
    """
    return n if n % 2 == 0 else n + 1


# Pixel formats supported by libvpx-vp9 / libvpx (VP8). Source pix_fmts
# outside these sets fall back to yuv420p with a console warning.


class PixFmt(StrEnum):
    SOURCE = "source"  # what the file has, when the codec can keep it
    YUV420P = "yuv420p"
    YUVA420P = "yuva420p"
    YUV444P = "yuv444p"
    GBRP = "gbrp"


PIX_FORMATS = {
    PixFmt.SOURCE: "Source (auto-detect)",
    PixFmt.YUV420P: "yuv420p",
    PixFmt.YUVA420P: "yuva420p (transparency)",
    PixFmt.YUV444P: "yuv444p",
    PixFmt.GBRP: "gbrp",
}


class WebMTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        # Source metadata (populated when a file is chosen, typed, dropped…)
        self._src_path = ""
        self._src_w = 0
        self._src_h = 0
        self._src_dur = 0.0
        self._src_fps = 0.0
        self._src_pixfmt = ""
        self._src_has_audio = False
        self._aspect = 1.0
        self._lock_updating = False  # prevents slider feedback loops
        self._task_id = None
        self._build_ui()

    # ═════════════════════════════════════════════════════════════════════════
    #  UI
    # ═════════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        self.make_header(self, t("tab.webm_maker"), t("webm_maker.subtitle"), icon="🌐")
        outer = scroll_body(self)
        self._build_source(outer)
        self._build_settings(outer)
        self._build_output(outer)

    def _build_source(self, outer):
        """The source file and what it is."""
        src_lf = tk.LabelFrame(outer, text=t("section.source_file"), padx=px(14), pady=px(8))
        src_lf.pack(fill="x", padx=px(18), pady=px(10, 4))
        self.src_var = tk.StringVar()
        self.src_row = FileRow(
            src_lf, t("webm_maker.input"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x")
        self.src_info_lbl = tk.Label(
            src_lf, text=t("common.no_file_loaded"), fg=CLR["fgdim"], font=(MONO_FONT, 9)
        )
        self.src_info_lbl.pack(anchor="w", pady=px(4, 0))

    def _build_settings(self, outer):
        """Simple and Advanced settings, in tabs."""
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="x", padx=px(18), pady=px(4))
        simple = tk.Frame(self.notebook)
        advanced = tk.Frame(self.notebook)
        self.notebook.add(simple, text="Simple")
        self.notebook.add(advanced, text="Advanced")
        self._build_simple_tab(simple)
        self._build_advanced_tab(advanced)
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._tab_changed())

    def _build_output(self, outer):
        """Where the result goes, the buttons, progress and the log."""
        self.out_row = save_section(
            outer, t("webm_maker.save_as"), ext=".webm", suggest=self._suggest_out
        )
        self.out_var = self.out_row.var

        render_row = tk.Frame(outer)
        render_row.pack(pady=px(8))
        self.btn_render = self.make_render_btn(
            render_row, "▶ ENCODE (Simple)", self._start_render, width=26
        )
        self.btn_render.pack(side="left", padx=px(8))
        self.btn_cancel = tk.Button(
            render_row,
            text=t("webm_maker.cancel"),
            font=(UI_FONT, 10),
            bg=CLR["red"],
            fg="white",
            height=2,
            width=10,
            state="disabled",
            command=self._cancel,
        )
        self.btn_cancel.pack(side="left", padx=px(4))
        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(
            outer, textvariable=self.status_var, fg=CLR["accent"], font=(UI_FONT, 10, "bold")
        ).pack()
        self.progress = ttk.Progressbar(outer, mode="determinate", length=px(600), maximum=100)
        self.progress.pack(pady=px(2, 6))
        self.console = self.make_console_block(outer, height=9, padx=px(18), pady=px(0, 8))
        self._on_use_source_res_toggle()
        self._update_simple_estimate()

    def _build_simple_tab(self, parent):
        prof = tk.LabelFrame(
            parent, text="  Profile (size · audio · max duration)  ", padx=px(14), pady=px(8)
        )
        prof.pack(fill="x", padx=px(8), pady=px(8, 4))
        self.simple_profile_var = tk.StringVar(value="6mb_audio")
        for key, label in webm.PROFILE_LABELS.items():
            tk.Radiobutton(
                prof,
                text=label,
                value=key,
                variable=self.simple_profile_var,
                command=self._update_simple_estimate,
            ).pack(anchor="w")
        self.simple_music_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            parent,
            text="Prioritize audio quality (music mode)",
            variable=self.simple_music_var,
            command=self._update_simple_estimate,
        ).pack(anchor="w", padx=px(14))
        tk.Label(
            parent,
            text="     Boosts audio bitrate; video quality drops.",
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w", padx=px(14))
        trim = tk.LabelFrame(parent, text="  Trim (optional)  ", padx=px(14), pady=px(6))
        trim.pack(fill="x", padx=px(8), pady=px(4))
        row = tk.Frame(trim)
        row.pack(fill="x")
        tk.Label(row, text="From").pack(side="left")
        self.simple_trim = RangePicker(
            row,
            end_hint="(empty = to the end)",
            source=lambda: self.src_row.path,
            on_change=self._update_simple_estimate,
        )
        self.simple_trim.pack(side="left", padx=px(4))
        self.simple_est_lbl = tk.Label(
            parent, text="", fg=CLR["accent"], font=(MONO_FONT, 10), justify="left"
        )
        self.simple_est_lbl.pack(anchor="w", padx=px(14), pady=px(4, 8))

    def _build_advanced_tab(self, parent):
        left, right = self._build_advanced_columns(parent)
        crf_row = self._build_size_limit(left)
        self._build_advanced_quality_slider(crf_row)
        self._build_quality_preset(left)
        self._build_codec(left)
        self._build_audio(left)
        h_row, res_lf = self._build_resolution(right)
        self._build_height_and_presets(h_row, res_lf)
        self._build_trim(right)
        ex3, tile_cb, tile_lbl = self._build_extra_options(right)
        self._build_threading_options(ex3, tile_cb, tile_lbl)

    def _build_advanced_columns(self, parent):
        # ── Two-column layout for the main controls ───────────────────────
        cols = tk.Frame(parent)
        cols.pack(fill="x", padx=px(8), pady=px(4))
        left = tk.Frame(cols)
        left.pack(side="left", fill="both", expand=True, padx=px(0, 6))
        right = tk.Frame(cols)
        right.pack(side="left", fill="both", expand=True, padx=px(6, 0))
        return left, right

    def _build_size_limit(self, left):
        """Size limit."""
        size_lf = tk.LabelFrame(
            left, text=f"  {t('webm_maker.target_size_section')}  ", padx=px(14), pady=px(10)
        )
        size_lf.pack(fill="x", pady=px(0, 6))

        # Toggle: enable size limit (defaults ON — preserves existing behavior)
        self.size_limit_enabled_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            size_lf,
            text=t("webm_maker.enable_size_limit_checkbox"),
            variable=self.size_limit_enabled_var,
            command=self._on_size_limit_toggle,
        ).pack(anchor="w", pady=px(0, 6))

        # Container holding the size controls (shown only when toggle is ON)
        self.size_controls = tk.Frame(size_lf)
        self.size_controls.pack(fill="x")

        size_row = tk.Frame(self.size_controls)
        size_row.pack(fill="x")
        self.size_limit_var = tk.StringVar(value="8")
        size_entry = tk.Entry(
            size_row,
            textvariable=self.size_limit_var,
            width=7,
            font=(UI_FONT, 14, "bold"),
            justify="center",
        )
        size_entry.pack(side="left")
        tk.Label(size_row, text="MB", font=(UI_FONT, 13)).pack(side="left", padx=px(4))

        # Quick-pick buttons
        quick = tk.Frame(self.size_controls)
        quick.pack(anchor="w", pady=px(6, 0))
        for mb in [4, 8, 10, 25, 50, 100, 200]:
            tk.Button(
                quick,
                text=f"{mb} MB",
                width=6,
                bg="#333",
                fg=CLR["fg"],
                font=(UI_FONT, 8),
                command=lambda v=mb: self.size_limit_var.set(str(v)),
            ).pack(side="left", padx=px(2))

        self.budget_lbl = tk.Label(self.size_controls, text="", fg=CLR["fgdim"], font=(UI_FONT, 9))
        self.budget_lbl.pack(anchor="w", pady=px(4, 0))
        # Update budget estimate whenever size or duration changes
        self.size_limit_var.trace_add("write", lambda *_: self._update_budget_label())

        # CRF override (visible only when size-limit toggle is OFF) ─────────
        # Seeded by the chosen quality preset's crf_hint, but user can override.
        self.crf_controls = tk.Frame(
            size_lf
        )  # NOT packed yet — _on_size_limit_toggle handles visibility
        tk.Label(
            self.crf_controls, text=t("webm_maker.crf_section"), font=(UI_FONT, 10, "bold")
        ).pack(anchor="w")
        crf_row = tk.Frame(self.crf_controls)
        crf_row.pack(fill="x", pady=px(4, 0))
        self.crf_var = tk.IntVar(value=31)  # preset will reseed in _on_quality_change
        return crf_row

    def _build_advanced_quality_slider(self, crf_row):
        tk.Scale(
            crf_row,
            variable=self.crf_var,
            from_=0,
            to=63,
            resolution=1,
            orient="horizontal",
            length=px(240),
            command=lambda *_: self._update_budget_label(),
        ).pack(side="left", padx=px(6))
        tk.Entry(crf_row, textvariable=self.crf_var, width=4, justify="center").pack(side="left")
        tk.Label(
            self.crf_controls, text=t("webm_maker.crf_hint"), fg=CLR["fgdim"], font=(UI_FONT, 8)
        ).pack(anchor="w", pady=px(2, 0))

    def _build_quality_preset(self, left):
        """Quality preset."""
        qual_lf = tk.LabelFrame(
            left, text=f"  {t('webm_maker.speed_section')}  ", padx=px(14), pady=px(10)
        )
        qual_lf.pack(fill="x", pady=px(0, 6))

        self.quality_var = tk.StringVar(value=QUALITY_LABELS[3])
        qual_cb = ttk.Combobox(
            qual_lf,
            textvariable=self.quality_var,
            values=QUALITY_LABELS,
            state="readonly",
            width=44,
        )
        qual_cb.pack(anchor="w")
        qual_cb.bind("<<ComboboxSelected>>", lambda *_: self._on_quality_change())
        tk.Label(
            qual_lf,
            text=t("webm_maker.lower_quality_faster_encode_higher_quality_very"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w", pady=px(4, 0))

    def _build_codec(self, left):
        """Codec."""
        codec_lf = tk.LabelFrame(
            left, text=f"  {t('webm_maker.video_codec_section')}  ", padx=px(14), pady=px(10)
        )
        codec_lf.pack(fill="x", pady=px(0, 6))

        self.codec_var = tk.StringVar(value="VP9")
        for label, val in [
            ("VP9  (better compression, recommended)", "VP9"),
            ("VP8  (wider legacy compatibility)", "VP8"),
        ]:
            tk.Radiobutton(
                codec_lf,
                text=label,
                variable=self.codec_var,
                value=val,
                command=self._on_codec_change,
            ).pack(anchor="w", pady=px(1))

        self.twopass_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            codec_lf, text=t("webm_maker.two_pass_checkbox"), variable=self.twopass_var
        ).pack(anchor="w", pady=px(6, 0))

    def _build_audio(self, left):
        """Audio."""
        audio_lf = tk.LabelFrame(
            left, text=f"  {t('webm_maker.audio_section')}  ", padx=px(14), pady=px(10)
        )
        audio_lf.pack(fill="x", pady=px(0, 6))

        self.audio_codec_var = tk.StringVar(value="Opus  (recommended)")
        audio_cb = ttk.Combobox(
            audio_lf,
            textvariable=self.audio_codec_var,
            values=list(AUDIO_OPTIONS.keys()),
            state="readonly",
            width=28,
        )
        audio_cb.pack(side="left")
        audio_cb.bind("<<ComboboxSelected>>", self._on_audio_change)

        tk.Label(audio_lf, text=t("webm_maker.bitrate")).pack(side="left", padx=px(12, 0))
        self.audio_br_var = tk.StringVar(value="128k")
        self.audio_br_cb = ttk.Combobox(
            audio_lf,
            textvariable=self.audio_br_var,
            values=AUDIO_BITRATES,
            state="readonly",
            width=7,
        )
        self.audio_br_cb.pack(side="left", padx=px(4))

    def _build_resolution(self, right):
        """Resolution."""
        res_lf = tk.LabelFrame(
            right, text=f"  {t('webm_maker.output_resolution_section')}  ", padx=px(14), pady=px(10)
        )
        res_lf.pack(fill="x", pady=px(0, 6))

        # "Use source resolution" lock — when checked, all child controls
        # below are disabled and the encoder skips the scale= filter.
        self.use_source_res_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            res_lf,
            text=t("webm_maker.use_source_resolution_checkbox"),
            variable=self.use_source_res_var,
            command=self._on_use_source_res_toggle,
        ).pack(anchor="w", pady=px(0, 4))

        # Lock aspect ratio
        self.lock_ar_var = tk.BooleanVar(value=True)
        self.lock_ar_cb = tk.Checkbutton(
            res_lf, text=t("webm_maker.lock_aspect_ratio"), variable=self.lock_ar_var
        )
        self.lock_ar_cb.pack(anchor="w")

        # Width slider + entry
        # The slider's IntVar drives the encoder. The entry has its own
        # StringVar so typing doesn't ping-pong against the slider on every
        # keystroke. They sync only at commit time (Return / FocusOut).
        w_row = tk.Frame(res_lf)
        w_row.pack(fill="x", pady=px(8, 2))
        tk.Label(w_row, text=t("crop.width_label"), width=7, anchor="e").pack(side="left")
        self.res_w_var = tk.IntVar(value=1920)
        self.res_w_entry_var = tk.StringVar(value="1920")
        self.w_slider = tk.Scale(
            w_row,
            variable=self.res_w_var,
            from_=128,
            to=7680,
            resolution=2,
            orient="horizontal",
            length=px(200),
            command=self._on_w_slider,
        )
        self.w_slider.pack(side="left", padx=px(6))
        vcmd_w = (self.register(self._validate_int_or_empty), "%P")
        self.w_entry = tk.Entry(
            w_row,
            textvariable=self.res_w_entry_var,
            width=6,
            justify="center",
            validate="key",
            validatecommand=vcmd_w,
        )
        self.w_entry.pack(side="left")
        self.w_entry.bind("<Return>", lambda e: self._commit_w_from_entry())
        self.w_entry.bind("<FocusOut>", lambda e: self._commit_w_from_entry())
        # Mirror slider movements into the entry, but only when the entry
        # doesn't currently have focus (avoid stomping on in-progress typing).
        self.res_w_var.trace_add("write", self._mirror_w_to_entry)

        # Height slider + entry — symmetric to width
        h_row = tk.Frame(res_lf)
        h_row.pack(fill="x", pady=px(2, 4))
        return h_row, res_lf

    def _build_height_and_presets(self, h_row, res_lf):
        tk.Label(h_row, text=t("crop.height_label"), width=7, anchor="e").pack(side="left")
        self.res_h_var = tk.IntVar(value=1080)
        self.res_h_entry_var = tk.StringVar(value="1080")
        self.h_slider = tk.Scale(
            h_row,
            variable=self.res_h_var,
            from_=128,
            to=4320,
            resolution=2,
            orient="horizontal",
            length=px(200),
            command=self._on_h_slider,
        )
        self.h_slider.pack(side="left", padx=px(6))
        vcmd_h = (self.register(self._validate_int_or_empty), "%P")
        self.h_entry = tk.Entry(
            h_row,
            textvariable=self.res_h_entry_var,
            width=6,
            justify="center",
            validate="key",
            validatecommand=vcmd_h,
        )
        self.h_entry.pack(side="left")
        self.h_entry.bind("<Return>", lambda e: self._commit_h_from_entry())
        self.h_entry.bind("<FocusOut>", lambda e: self._commit_h_from_entry())
        self.res_h_var.trace_add("write", self._mirror_h_to_entry)

        # Resolution presets
        preset_row = tk.Frame(res_lf)
        preset_row.pack(anchor="w", pady=px(4, 0))
        self._res_preset_label = tk.Label(
            preset_row, text=t("scene_detect.presets_label"), fg=CLR["fgdim"], font=(UI_FONT, 8)
        )
        self._res_preset_label.pack(side="left", padx=px(0, 4))
        self._res_preset_btns = []
        for label, w, h in [
            ("8K", 7680, 4320),
            ("4K", 3840, 2160),
            ("1440p", 2560, 1440),
            ("1080p", 1920, 1080),
            ("720p", 1280, 720),
            ("480p", 854, 480),
            ("360p", 640, 360),
        ]:
            btn = tk.Button(
                preset_row,
                text=label,
                width=5,
                bg="#333",
                fg=CLR["fg"],
                font=(UI_FONT, 7),
                command=lambda ww=w, hh=h: self._apply_res_preset(ww, hh),
            )
            btn.pack(side="left", padx=px(1))
            self._res_preset_btns.append(btn)

    def _build_trim(self, right):
        """Trim."""
        trim_lf = tk.LabelFrame(
            right, text=f"  {t('webm_maker.trim_section')}  ", padx=px(14), pady=px(8)
        )
        trim_lf.pack(fill="x", pady=px(0, 6))

        trim_row = tk.Frame(trim_lf)
        trim_row.pack(fill="x")
        tk.Label(trim_row, text="From").pack(side="left")
        self.trim = RangePicker(
            trim_row, end_hint="(empty = to the end)", source=lambda: self.src_row.path
        )
        self.trim.pack(side="left", padx=px(4))

    def _build_extra_options(self, right):
        """Extra options."""
        extra_lf = tk.LabelFrame(
            right, text=f"  {t('webm_maker.extra_options_section')}  ", padx=px(14), pady=px(8)
        )
        extra_lf.pack(fill="x", pady=px(0, 6))

        # Row 1
        ex1 = tk.Frame(extra_lf)
        ex1.pack(fill="x", pady=px(2))
        tk.Label(ex1, text=t("webm_maker.fps_label")).pack(side="left")
        self.fps_var = tk.StringVar(value="0")
        ttk.Combobox(
            ex1,
            textvariable=self.fps_var,
            values=["0", "24", "25", "30", "50", "60"],
            width=6,
            state="normal",
        ).pack(side="left", padx=px(4))

        tk.Label(ex1, text=t("webm_maker.pixel_format")).pack(side="left", padx=px(12, 0))
        # "Source" is relabelled with the detected format once a file loads.
        self.pixfmt = ChoiceBox(ex1, PIX_FORMATS, PixFmt.SOURCE, width=22)
        self.pixfmt.pack(side="left", padx=px(4))

        # Row 2
        ex2 = tk.Frame(extra_lf)
        ex2.pack(fill="x", pady=px(2))
        self.strip_meta_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            ex2, text=t("webm_maker.strip_metadata_checkbox"), variable=self.strip_meta_var
        ).pack(side="left")
        self.loop_var = tk.BooleanVar(value=False)
        tk.Checkbutton(ex2, text=t("webm_maker.loop_flag_checkbox"), variable=self.loop_var).pack(
            side="left", padx=px(20)
        )

        # Row 3 - VP9-specific
        ex3 = tk.Frame(extra_lf)
        ex3.pack(fill="x", pady=px(2))

        tile_lbl = tk.Label(ex3, text=t("webm_maker.tile_columns_vp9"))
        tile_lbl.pack(side="left")
        self.tile_col_var = tk.StringVar(value="2")
        tile_cb = ttk.Combobox(
            ex3,
            textvariable=self.tile_col_var,
            values=["0", "1", "2", "3", "4", "6"],
            state="readonly",
            width=4,
        )
        return ex3, tile_cb, tile_lbl

    def _build_threading_options(self, ex3, tile_cb, tile_lbl):
        tile_cb.pack(side="left", padx=px(4))
        self.add_tooltip(tile_lbl, t("webm_maker.tile_columns_tooltip"))
        self.add_tooltip(tile_cb, t("webm_maker.tile_columns_tooltip"))

        rowmt_lbl = tk.Label(ex3, text="  Row-MT", fg=CLR["fgdim"])
        rowmt_lbl.pack(side="left", padx=px(10, 0))
        self.rowmt_var = tk.BooleanVar(value=True)
        rowmt_cb = tk.Checkbutton(ex3, variable=self.rowmt_var)
        rowmt_cb.pack(side="left")
        self.add_tooltip(rowmt_lbl, t("webm_maker.row_mt_tooltip"))
        self.add_tooltip(rowmt_cb, t("webm_maker.row_mt_tooltip"))

        self.vp9_extra_widgets = [ex3]  # hide for VP8

    # ═════════════════════════════════════════════════════════════════════════
    #  Source
    # ═════════════════════════════════════════════════════════════════════════
    def _suggest_out(self):
        src = self.src_row.path
        return self.suggest_output(src, "web", ".webm") if src else ""

    def load_file(self, path):
        self.src_var.set(path)
        return True

    def _source_changed(self, path):
        """Re-probe on every change (browse, typing, paste, drag-and-drop, recent files)."""
        tasks.submit(
            partial(ff.try_probe, path),
            on_done=lambda info: self._apply_probe(path, info),
            name="WebMProbe",
        )

    def _apply_probe(self, path, info):
        if path != self.src_row.path:
            return
        if info is None or not info.has_video:
            self._src_path, self._src_dur = "", 0.0
            self.src_info_lbl.config(text="Not a readable video.", fg=CLR["red"])
            self._update_simple_estimate()
            return
        v = info.require_video()
        w, h = v.display_size
        self._src_path = path
        self._src_w, self._src_h = w, h
        self._src_dur = info.duration
        self._src_fps = round(v.fps or 0.0, 3)
        self._src_pixfmt = v.pix_fmt or ""
        self._src_has_audio = info.has_audio
        self._aspect = w / h if h else 1.0
        mins, secs = divmod(int(info.duration), 60)
        size_mb = os.path.getsize(path) / 1024 / 1024 if os.path.isfile(path) else 0
        self.src_info_lbl.config(
            text=f"{w}×{h}  |  {self._src_fps:g} fps  |  {mins}m {secs}s  |  {size_mb:.1f} MB",
            fg=CLR["accent"],
        )
        self._seed_resolution(w, h)
        self._refresh_pixfmt_dropdown()
        self._update_budget_label()
        self._update_simple_estimate()
        if not self.out_row.path:
            self.out_row.set(self._suggest_out())

    def _tab_changed(self):
        self.btn_render.config(text=f"▶ ENCODE ({self._mode().title()})")

    def _mode(self) -> str:
        try:
            return "simple" if self.notebook.index(self.notebook.select()) == 0 else "advanced"
        except tk.TclError:
            return "simple"

    # ═════════════════════════════════════════════════════════════════════════
    #  Simple estimate
    # ═════════════════════════════════════════════════════════════════════════
    def _simple_trim(self):
        start, until = self.simple_trim.span()
        end = until or 0.0  # 0: to the end
        eff = (end if end > start else self._src_dur) - start
        return start, end, eff

    def _update_simple_estimate(self):
        if not hasattr(self, "simple_est_lbl"):
            return
        if not self._src_dur:
            self.simple_est_lbl.config(text="Estimated:  load a source file")
            return
        try:
            _s, _e, eff = self._simple_trim()
        except ValueError:
            self.simple_est_lbl.config(text="Estimated:  (check the trim times)")
            return
        st = webm.simple_settings(
            self.simple_profile_var.get(),
            max(eff, 0.1),
            self._src_w,
            self._src_h,
            self._src_fps,
            self.simple_music_var.get(),
        )
        portrait = self._src_h > self._src_w
        if portrait:
            short = int(st.target_max_dim * self._src_w / self._src_h) // 2 * 2
            res = f"~{short}×{st.target_max_dim}"
        else:
            res = f"~{int(st.target_max_dim * self._src_h / self._src_w) // 2 * 2}p"
        audio = f" · {st.audio_bitrate_kbps} kbps audio" if st.audio_bitrate_kbps else ""
        self.simple_est_lbl.config(
            text=f"Estimated:  ~{st.video_bitrate_kbps} kbps video{audio} · "
            f"{res} · {st.fps_cap:g} fps"
        )

    def _seed_resolution(self, w, h):
        self._lock_updating = True
        try:
            self.res_w_var.set(w)
            self.res_h_var.set(h)
            self.res_w_entry_var.set(str(w))
            self.res_h_entry_var.set(str(h))
            self.w_slider.config(to=max(7680, w))
            self.h_slider.config(to=max(4320, h))
        finally:
            self._lock_updating = False

    def _apply_res_preset(self, w, h):
        self._lock_updating = True
        try:
            self.res_w_var.set(w)
            self.res_h_var.set(h)
            self.res_w_entry_var.set(str(w))
            self.res_h_entry_var.set(str(h))
        finally:
            self._lock_updating = False

    def _refresh_pixfmt_dropdown(self):
        """Update the dropdown's first entry to show the detected source pix_fmt."""
        detected = (
            f"Source ({self._src_pixfmt})" if self._src_pixfmt else PIX_FORMATS[PixFmt.SOURCE]
        )
        self.pixfmt.relabel(PixFmt.SOURCE, detected)

    @staticmethod
    def _validate_int_or_empty(proposed: str) -> bool:
        """Tk validatecommand: accept empty or all-digit input only."""
        return proposed == "" or proposed.isdigit()

    def _on_w_slider(self, val):
        """Slider drag → commit width via the same path as entry-commit."""
        if self._lock_updating:
            return
        with contextlib.suppress(ValueError, TypeError):
            self._apply_w(int(float(val)))

    def _on_h_slider(self, val):
        if self._lock_updating:
            return
        with contextlib.suppress(ValueError, TypeError):
            self._apply_h(int(float(val)))

    def _commit_w_from_entry(self):
        """User pressed Enter or tabbed away — parse and apply, or revert."""
        raw = self.res_w_entry_var.get().strip()
        if not raw:
            # Empty: revert visible text to the slider's current value
            self.res_w_entry_var.set(str(self.res_w_var.get()))
            return
        try:
            w = int(raw)
        except ValueError:
            self.res_w_entry_var.set(str(self.res_w_var.get()))
            return
        if w < 2:
            # Below the minimum — revert so the entry doesn't show a stale value
            self.res_w_entry_var.set(str(self.res_w_var.get()))
            return
        self._apply_w(w)

    def _commit_h_from_entry(self):
        raw = self.res_h_entry_var.get().strip()
        if not raw:
            self.res_h_entry_var.set(str(self.res_h_var.get()))
            return
        try:
            h = int(raw)
        except ValueError:
            self.res_h_entry_var.set(str(self.res_h_var.get()))
            return
        if h < 2:
            self.res_h_entry_var.set(str(self.res_h_var.get()))
            return
        self._apply_h(h)

    def _apply_w(self, w: int):
        """Snap, set IntVar, and (if lock-aspect on) update height. Single source of truth."""
        if w < 2:
            return
        w = _snap_even(w)
        self._lock_updating = True
        try:
            self.res_w_var.set(w)
            self.res_w_entry_var.set(str(w))
            if self.lock_ar_var.get() and self._aspect:
                h = _snap_even(round(w / self._aspect))
                self.res_h_var.set(h)
                self.res_h_entry_var.set(str(h))
        finally:
            self._lock_updating = False

    def _apply_h(self, h: int):
        if h < 2:
            return
        h = _snap_even(h)
        self._lock_updating = True
        try:
            self.res_h_var.set(h)
            self.res_h_entry_var.set(str(h))
            if self.lock_ar_var.get() and self._aspect:
                w = _snap_even(round(h * self._aspect))
                self.res_w_var.set(w)
                self.res_w_entry_var.set(str(w))
        finally:
            self._lock_updating = False

    def _mirror_w_to_entry(self, *_):
        """IntVar changed (from slider or programmatic seed) → mirror to entry,
        unless _apply_* already did it or the entry currently has focus."""
        if self._lock_updating:
            return
        try:
            if self.focus_get() is self.w_entry:
                return
        except (KeyError, tk.TclError):
            # focus_get() can raise during widget teardown
            return
        self.res_w_entry_var.set(str(self.res_w_var.get()))

    def _mirror_h_to_entry(self, *_):
        if self._lock_updating:
            return
        try:
            if self.focus_get() is self.h_entry:
                return
        except (KeyError, tk.TclError):
            return
        self.res_h_entry_var.set(str(self.res_h_var.get()))

    # ═════════════════════════════════════════════════════════════════════════
    #  Budget label
    # ═════════════════════════════════════════════════════════════════════════
    def _update_budget_label(self):
        try:
            size_mb = float(self.size_limit_var.get())
            dur = self._src_dur

            # Subtract audio from budget
            audio_key = (
                self.audio_codec_var.get() if hasattr(self, "audio_codec_var") else "No audio track"
            )
            audio_kbps = 0
            if AUDIO_OPTIONS.get(audio_key):
                try:
                    audio_kbps = int(self.audio_br_var.get().replace("k", ""))
                except Exception:
                    logger.debug("_update_budget_label: best-effort step failed", exc_info=True)
                    audio_kbps = 128

            if dur > 0:
                total_bits = size_mb * 1024 * 1024 * 8
                audio_bits = audio_kbps * 1000 * dur
                video_bits = max(0, total_bits - audio_bits)
                video_kbps = video_bits / dur / 1000
                self.budget_lbl.config(
                    text=(
                        f"≈ {video_kbps:.0f} kbps video bitrate  ({size_mb:.0f} MB ÷ {dur:.1f}s)"
                    ),
                    fg=CLR["accent"] if video_kbps > 100 else CLR["red"],
                )
            else:
                self.budget_lbl.config(
                    text=t("webm_maker.load_a_source_file_to_see_bitrate_estimate"), fg=CLR["fgdim"]
                )
        except (ValueError, AttributeError):
            pass

    # ═════════════════════════════════════════════════════════════════════════
    #  Codec / audio change handlers
    # ═════════════════════════════════════════════════════════════════════════
    def _on_codec_change(self):
        is_vp9 = self.codec_var.get() == "VP9"
        for w in self.vp9_extra_widgets:
            if is_vp9:
                w.pack(fill="x", pady=px(2))
            else:
                w.pack_forget()

    def _on_use_source_res_toggle(self):
        """Enable/disable all child controls in the Resolution section."""
        locked = self.use_source_res_var.get()
        state = "disabled" if locked else "normal"
        # Standard widgets accept "normal" / "disabled"
        for w in (
            self.lock_ar_cb,
            self.w_slider,
            self.w_entry,
            self.h_slider,
            self.h_entry,
            self._res_preset_label,
            *self._res_preset_btns,
        ):
            w.config(state=state)
        if not locked:
            # Returning to manual mode — make sure aspect lock is on by default
            self.lock_ar_var.set(True)

    def _on_audio_change(self, *_):
        has_audio = AUDIO_OPTIONS.get(self.audio_codec_var.get()) is not None
        self.audio_br_cb.config(state="readonly" if has_audio else "disabled")
        self._update_budget_label()

    def _on_size_limit_toggle(self):
        """Show size controls OR CRF controls — never both."""
        if self.size_limit_enabled_var.get():
            self.crf_controls.pack_forget()
            self.size_controls.pack(fill="x")
        else:
            self.size_controls.pack_forget()
            # Seed CRF from current preset before revealing
            self._seed_crf_from_preset()
            self.crf_controls.pack(fill="x")
        self._update_budget_label()

    def _on_quality_change(self):
        """When user changes quality preset, reseed CRF if it's user-visible."""
        if not self.size_limit_enabled_var.get():
            self._seed_crf_from_preset()

    def _seed_crf_from_preset(self):
        preset = next(p for p in QUALITY_PRESETS if p[0] == self.quality_var.get())
        self.crf_var.set(preset[4])  # crf_hint

    # ═════════════════════════════════════════════════════════════════════════
    #  Render
    # ═════════════════════════════════════════════════════════════════════════
    def _cancel(self):
        if self._task_id:
            self.cancel_render(self._task_id)
            self.status_var.set("Cancelling…")

    def _ensure_probe(self, src) -> bool:
        """Make sure metadata belongs to *src* (typed paths can be newer than the probe)."""
        if self._src_path != src or not self._src_dur:
            self._apply_probe(src, ff.try_probe(src))
        return bool(self._src_dur)

    def _start_render(self):
        src = self.src_row.path
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), "Load a source file first.", parent=self)
            return
        if not self._ensure_probe(src):
            messagebox.showwarning(t("common.warning"), "Load a source file first.", parent=self)
            return
        try:
            job = self._simple_job(src) if self._mode() == "simple" else self._advanced_job(src)
        except ValueError as exc:
            messagebox.showerror(t("common.error"), str(exc), parent=self)
            return
        if job is None:
            return
        out = self.confirm_output(
            self.out_row.path or self._suggest_out(), [src], default_ext=".webm"
        )
        if not out:
            return
        self.out_row.set(out)
        self._run_job(src, out, job)

    # ── settings snapshots (Tk thread) ───────────────────────────────────────

    def _simple_job(self, src):
        start, _end, eff = self._simple_trim()
        profile = self.simple_profile_var.get()
        label = webm.PROFILE_LABELS[profile]
        if start >= self._src_dur:
            raise ValueError(
                f"Trim start ({start:g}s) is past the end of the clip ({self._src_dur:.1f}s)."
            )
        if eff < 2.0:
            raise ValueError("Clip is too short to encode (< 2s).")
        cap = webm.PROFILE_DURATION_CAP_S[profile]
        if eff > cap:
            raise ValueError(
                f"Clip is {ff.format_time(eff, ms=False)} after trim, but the {label} profile "
                f"caps at {ff.format_time(cap, ms=False)}. Trim further or pick a smaller "
                f"profile."
            )
        job, st, warn = webm.simple_job(
            profile, start, eff, self._source(), self.simple_music_var.get()
        )
        if st.video_bitrate_kbps == webm.VIDEO_BITRATE_FLOOR:
            self._log("⚠ Video bitrate floored at 50 kbps; quality will be very poor.")
        if warn:
            self._log(warn)
        self._log(
            f"Simple · {label} · ~{st.video_bitrate_kbps} kbps video · "
            f"{st.audio_bitrate_kbps} kbps audio · max {st.target_max_dim}px · {st.fps_cap:g} fps"
        )
        return job

    def _source(self) -> webm.Source:
        return webm.Source(
            width=self._src_w,
            height=self._src_h,
            fps=self._src_fps,
            pixfmt=self._src_pixfmt,
            has_audio=self._src_has_audio,
        )

    def _trim_length(self) -> tuple[float, float]:
        """(start, seconds) of the trimmed part."""
        t_start, t_until = self.trim.span()
        t_end = t_until or 0.0  # 0: to the end
        if t_start >= self._src_dur:
            raise ValueError(f"Trim start ({t_start:g}s) is past the end of the clip.")
        eff = (t_end if t_end > t_start else self._src_dur) - t_start
        if eff <= 0.05:
            raise ValueError("The trimmed clip is empty.")
        return t_start, eff

    def _size_target(self, eff: float, audio_kbps: int) -> tuple[int, int, float]:
        """(byte cap, video kbps, MB) that fit the clip in the size limit."""
        try:
            size_mb = float(self.size_limit_var.get())
            assert size_mb > 0
        except (ValueError, AssertionError):
            raise ValueError("Enter a valid positive file-size target.") from None
        cap_bytes = int(size_mb * 1024 * 1024)
        budget = cap_bytes * 8 * 0.985 - audio_kbps * 1000 * eff  # ~1.5% container overhead
        return cap_bytes, max(50, min(int(budget / eff / 1000), 100_000)), size_mb

    def _crf(self) -> int:
        try:
            return int(self.crf_var.get())
        except (ValueError, tk.TclError):
            raise ValueError("CRF must be a whole number between 0 and 63.") from None

    def _advanced(self) -> webm.Advanced:
        """The advanced choices as set."""
        codec = self.codec_var.get()
        preset = next(p for p in QUALITY_PRESETS if p[0] == self.quality_var.get())
        _, vp9_cpu, vp9_deadline, vp8_cpu, _crf_hint = preset
        size = None
        if not self.use_source_res_var.get():
            ow, oh = self.res_w_var.get(), self.res_h_var.get()
            if (ow, oh) != (self._src_w, self._src_h):
                size = (ow, oh)
        audio_codec = AUDIO_OPTIONS.get(self.audio_codec_var.get())
        return webm.Advanced(
            codec=codec,
            cpu_used=vp9_cpu if codec == "VP9" else vp8_cpu,
            deadline=vp9_deadline,
            two_pass=self.twopass_var.get(),
            tile_columns=self.tile_col_var.get(),
            row_mt=self.rowmt_var.get(),
            pixfmt=self.pixfmt.value.value,
            size=size,
            fps=self.fps_var.get(),
            audio_codec=audio_codec,
            audio_bitrate=self.audio_br_var.get(),
            strip_metadata=self.strip_meta_var.get(),
            loop=self.loop_var.get(),
        )

    def _advanced_job(self, src):
        size_limited = self.size_limit_enabled_var.get()
        t_start, eff = self._trim_length()
        choices = self._advanced()
        audio_kbps = (
            int(choices.audio_bitrate.replace("k", "") or 128) if choices.audio_codec else 0
        )
        cap_bytes, bitrate, crf, size_mb = None, 0, 0, 0.0
        if size_limited:
            cap_bytes, bitrate, size_mb = self._size_target(eff, audio_kbps)
        else:
            crf = self._crf()
        job, warn = webm.advanced_job(
            choices, t_start, eff, self._source(), crf=crf, bitrate=bitrate, cap_bytes=cap_bytes
        )
        if warn:
            self._log(warn)
        self._log(
            f"Advanced · {choices.codec} · "
            + (f"{bitrate} kbps for {size_mb:g} MB" if size_limited else f"CRF {crf}")
            + f" · {eff:.1f}s"
        )
        return job

    # ── the queued job ───────────────────────────────────────────────────────

    def _run_job(self, src, out, job):
        self.btn_cancel.config(state="normal")
        self.btn_render.config(state="disabled")
        self.progress.config(value=0)
        self.status_var.set("Queued…")
        self._task_id = self.enqueue_render(
            "WebM Encode",
            out,
            work=webm.WebmEncode(src, job).run,
            expected_duration=job.dur,
            inputs=[src],
            hooks=Hooks(on_progress=self._job_progress, on_complete=partial(self._job_done, out)),
        )

    def _job_progress(self, tid, line):
        self._log(line)
        task = self.task(tid)
        if task is not None and task.fraction is not None:
            self.progress.config(value=task.fraction * 100)
            self.status_var.set(f"Encoding… {task.fraction * 100:.0f}%")

    def _job_done(self, out, tid, rc):
        self._task_id = None
        self.btn_render.config(state="normal")
        self.btn_cancel.config(state="disabled")
        self.progress.config(value=100 if rc == 0 else 0)
        self.last_task = self.task(tid)
        if rc == 0 and os.path.exists(out):
            mb = os.path.getsize(out) / 1024 / 1024
            self.status_var.set(f"✅  Done  →  {mb:.2f} MB  ({out})")
        elif rc == -1:
            self.status_var.set("Cancelled.")
        else:
            self.status_var.set("❌  Encode failed.")
        self.show_result(rc, out)

    def _log(self, msg: str):
        self.log(self.console, msg)

    def _status(self, msg: str):
        self.call_ui(self.status_var.set, msg)
