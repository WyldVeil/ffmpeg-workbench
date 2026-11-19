"""
tab_audioexporter.py  ─  Audio Exporter

Comprehensive audio file converter / exporter.  Open any audio (or
video-with-audio) file and re-save it with full control over:

  • Output format (MP3, AAC/M4A, WAV, FLAC, OGG Vorbis, Opus, WMA, AIFF, ALAC, AC3)
  • Codec & encoder selection
  • Bitrate / quality (CBR, VBR, lossless)
  • Sample rate, bit depth, channel layout
  • Loudness normalization (EBU R128 / peak)
  • Trim (start / end time)
  • Fade in / fade out
  • Metadata (title, artist, album, genre, year, track, comment)
  • Max file-size targeting (auto-calculates bitrate)
  • ReplayGain tagging
"""
import tkinter as tk
from tkinter import messagebox, ttk
from core import dialogs
import json
import math
import os
import subprocess

from tabs.base_tab import BaseTab, CLR, UI_FONT, MONO_FONT
from core.hardware import get_binary_path, CREATE_NO_WINDOW
from core import ffmpeg as ff
from core.i18n import t
from tabs import common


# ── Format definitions ───────────────────────────────────────────────────────
#  key: (extension, codec_args, supports_bitrate, supports_vbr, supports_bit_depth)
FORMAT_DEFS = {
    "MP3": {
        "ext": ".mp3",
        "codec": ["-c:a", "libmp3lame"],
        "lossy": True,
        "bitrates": ["64k", "96k", "128k", "160k", "192k", "224k", "256k", "320k"],
        "default_br": "320k",
        "vbr_flag": "-q:a",
        "vbr_range": (0, 9),          # 0 = best
        "vbr_default": 2,
        "sample_rates": [8000, 11025, 16000, 22050, 32000, 44100, 48000],
    },
    "AAC (M4A)": {
        "ext": ".m4a",
        "codec": ["-c:a", "aac"],
        "lossy": True,
        "bitrates": ["64k", "96k", "128k", "160k", "192k", "256k", "320k"],
        "default_br": "256k",
        "vbr_flag": None,
        "sample_rates": [22050, 32000, 44100, 48000, 96000],
    },
    "OGG Vorbis": {
        "ext": ".ogg",
        "codec": ["-c:a", "libvorbis"],
        "lossy": True,
        "bitrates": ["64k", "96k", "128k", "160k", "192k", "256k", "320k"],
        "default_br": "192k",
        "vbr_flag": "-q:a",
        "vbr_range": (0, 10),         # 10 = best
        "vbr_default": 6,
        "sample_rates": [8000, 11025, 16000, 22050, 32000, 44100, 48000],
    },
    "Opus": {
        "ext": ".opus",
        "codec": ["-c:a", "libopus"],
        "lossy": True,
        "bitrates": ["32k", "48k", "64k", "96k", "128k", "160k", "192k", "256k", "320k", "450k", "510k"],
        "default_br": "128k",
        "vbr_flag": None,
        "sample_rates": [8000, 12000, 16000, 24000, 48000],
    },
    "WAV": {
        "ext": ".wav",
        "codec": [],  # set dynamically from bit depth
        "lossy": False,
        "bit_depths": {"16-bit": "pcm_s16le", "24-bit": "pcm_s24le", "32-bit": "pcm_s32le", "32-bit float": "pcm_f32le"},
        "default_depth": "16-bit",
        "sample_rates": [8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000],
    },
    "FLAC": {
        "ext": ".flac",
        "codec": ["-c:a", "flac"],
        "lossy": False,
        # the FLAC encoder takes s16/s32; 24-bit = s32 marked as 24 significant bits
        "bit_depths": {"16-bit": "s16", "24-bit": "s32:24", "32-bit": "s32"},
        "default_depth": "16-bit",
        "compression_range": (0, 12),   # 0 = fastest, 12 = smallest
        "compression_default": 5,
        "sample_rates": [8000, 16000, 22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000],
    },
    "ALAC (M4A)": {
        "ext": ".m4a",
        "codec": ["-c:a", "alac"],
        "lossy": False,
        "bit_depths": {"16-bit": "s16p", "24-bit": "s32p:24"},     # ALAC stores at most 24 bits here
        "default_depth": "16-bit",
        "sample_rates": [44100, 48000, 88200, 96000, 176400, 192000],
    },
    "AIFF": {
        "ext": ".aiff",
        "codec": [],  # set from bit depth
        "lossy": False,
        "bit_depths": {"16-bit": "pcm_s16be", "24-bit": "pcm_s24be", "32-bit": "pcm_s32be"},
        "default_depth": "16-bit",
        "sample_rates": [22050, 32000, 44100, 48000, 88200, 96000],
    },
    "WMA": {
        "ext": ".wma",
        "codec": ["-c:a", "wmav2"],
        "lossy": True,
        "bitrates": ["64k", "96k", "128k", "160k", "192k", "256k", "320k"],
        "default_br": "192k",
        "vbr_flag": None,
        "sample_rates": [22050, 32000, 44100, 48000],
    },
    "AC3": {
        "ext": ".ac3",
        "codec": ["-c:a", "ac3"],
        "lossy": True,
        "bitrates": ["128k", "192k", "224k", "256k", "320k", "384k", "448k", "640k"],
        "default_br": "384k",
        "vbr_flag": None,
        "sample_rates": [32000, 44100, 48000],
    },
}

CHANNEL_LAYOUTS = [
    ("Auto (keep original)", ""),
    ("Mono",                 "1"),
    ("Stereo",               "2"),
    ("2.1 (3ch)",            "3"),
    ("4.0 Quad",             "4"),
    ("5.0 Surround",        "5"),
    ("5.1 Surround",        "6"),
    ("7.1 Surround",        "8"),
]

NORM_MODES = [
    "None",
    "EBU R128 (loudnorm)",
    "Peak normalize",
]


class AudioExporterTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_path = ""
        self._src_info = {}   # ffprobe metadata cache
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # -- header --
        self.make_header(self, t("tab.audio_converter"),
                         subtitle=t("exporter.subtitle"),
                         icon="\U0001F4BE")  # floppy disk

        # Scrollable body
        outer = tk.Frame(self, bg=CLR["bg"])
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=CLR["bg"], highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self._inner = tk.Frame(canvas, bg=CLR["bg"])
        self._inner.bind("<Configure>",
                         lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # Mousewheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel, add=True)

        self._build_source_section()
        self._build_source_info_section()
        self._build_format_section()
        self._build_quality_section()
        self._build_sample_section()
        self._build_trim_section()
        self._build_effects_section()
        self._build_size_section()
        self._build_metadata_section()
        self._build_output_section()
        self._build_console_section()

    # ── Source ────────────────────────────────────────────────────────────────

    def _build_source_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.source"))
        self.src_var = tk.StringVar()
        row = self.make_file_row(sec, t("exporter.input_file"), self.src_var,
                                 self._browse_src)
        row.pack(fill="x", pady=4)

    def _build_source_info_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.source_info"))
        self._info_lbl = tk.Label(sec,
                                  text="  No file loaded - browse to open a file",
                                  font=(MONO_FONT, 9), anchor="w", justify="left",
                                  bg=CLR["bg"], fg=CLR["fgdim"])
        self._info_lbl.pack(fill="x", padx=4, pady=2)

    # ── Format ───────────────────────────────────────────────────────────────

    def _build_format_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.format"))
        row = tk.Frame(sec, bg=CLR["bg"])
        row.pack(fill="x", pady=4)

        tk.Label(row, text=t("exporter.format_label"), font=(UI_FONT, 9, "bold"),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        self.fmt_var = tk.StringVar(value="MP3")
        cb = ttk.Combobox(row, textvariable=self.fmt_var,
                          values=list(FORMAT_DEFS.keys()),
                          state="readonly", width=16)
        cb.pack(side="left", padx=8)
        cb.bind("<<ComboboxSelected>>", self._on_format_change)

        self._fmt_note = tk.Label(row, text=t("exporter.lossy_note"),
                                  font=(UI_FONT, 8, "italic"),
                                  bg=CLR["bg"], fg=CLR["fgdim"])
        self._fmt_note.pack(side="left", padx=8)

    # ── Quality / Bitrate ────────────────────────────────────────────────────

    def _build_quality_section(self):
        self._qual_sec = self.make_section(self._inner, title=t("exporter.section.quality"))

        # Bitrate mode row
        r0 = tk.Frame(self._qual_sec, bg=CLR["bg"])
        r0.pack(fill="x", pady=4)
        tk.Label(r0, text=t("exporter.mode_label"), font=(UI_FONT, 9, "bold"),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        self.br_mode_var = tk.StringVar(value="CBR")
        self._rb_cbr = tk.Radiobutton(r0, text=t("exporter.cbr"),
                                       variable=self.br_mode_var, value="CBR",
                                       bg=CLR["bg"], fg=CLR["fg"],
                                       selectcolor=CLR["input_bg"],
                                       activebackground=CLR["bg"],
                                       command=self._toggle_br_mode)
        self._rb_cbr.pack(side="left", padx=(12, 4))
        self._rb_vbr = tk.Radiobutton(r0, text=t("exporter.vbr"),
                                       variable=self.br_mode_var, value="VBR",
                                       bg=CLR["bg"], fg=CLR["fg"],
                                       selectcolor=CLR["input_bg"],
                                       activebackground=CLR["bg"],
                                       command=self._toggle_br_mode)
        self._rb_vbr.pack(side="left", padx=4)

        # CBR bitrate row
        self._cbr_row = tk.Frame(self._qual_sec, bg=CLR["bg"])
        self._cbr_row.pack(fill="x", pady=4)
        tk.Label(self._cbr_row, text=t("exporter.bitrate_label"), font=(UI_FONT, 9, "bold"),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        self.bitrate_var = tk.StringVar(value="320k")
        self._br_cb = ttk.Combobox(self._cbr_row, textvariable=self.bitrate_var,
                                    values=FORMAT_DEFS["MP3"]["bitrates"],
                                    state="readonly", width=8)
        self._br_cb.pack(side="left", padx=8)

        # VBR quality row
        self._vbr_row = tk.Frame(self._qual_sec, bg=CLR["bg"])
        tk.Label(self._vbr_row, text=t("exporter.vbr_quality"), font=(UI_FONT, 9, "bold"),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        self.vbr_var = tk.IntVar(value=2)
        self._vbr_scale = tk.Scale(self._vbr_row, from_=0, to=9,
                                    orient="horizontal", variable=self.vbr_var,
                                    length=200,
                                    bg=CLR["bg"], fg=CLR["fg"],
                                    highlightthickness=0)
        self._vbr_scale.pack(side="left", padx=8)
        self._vbr_hint = tk.Label(self._vbr_row, text="0 = best, 9 = smallest",
                                   font=(UI_FONT, 8), bg=CLR["bg"], fg=CLR["fgdim"])
        self._vbr_hint.pack(side="left", padx=4)

        # Lossless bit depth row
        self._depth_row = tk.Frame(self._qual_sec, bg=CLR["bg"])
        tk.Label(self._depth_row, text=t("exporter.bit_depth"), font=(UI_FONT, 9, "bold"),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        self.depth_var = tk.StringVar(value="16-bit")
        self._depth_cb = ttk.Combobox(self._depth_row, textvariable=self.depth_var,
                                       values=["16-bit", "24-bit", "32-bit"],
                                       state="readonly", width=12)
        self._depth_cb.pack(side="left", padx=8)

        # FLAC compression row
        self._comp_row = tk.Frame(self._qual_sec, bg=CLR["bg"])
        tk.Label(self._comp_row, text=t("exporter.compression"), font=(UI_FONT, 9, "bold"),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        self.comp_var = tk.IntVar(value=5)
        self._comp_scale = tk.Scale(self._comp_row, from_=0, to=12,
                                     orient="horizontal", variable=self.comp_var,
                                     length=200,
                                     bg=CLR["bg"], fg=CLR["fg"],
                                     highlightthickness=0)
        self._comp_scale.pack(side="left", padx=8)
        tk.Label(self._comp_row, text=t("exporter.compression_hint"),
                 font=(UI_FONT, 8), bg=CLR["bg"], fg=CLR["fgdim"]).pack(side="left", padx=4)

        # Initial state
        self._toggle_br_mode()

    # ── Sample rate & channels ───────────────────────────────────────────────

    def _build_sample_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.sample"))

        r0 = tk.Frame(sec, bg=CLR["bg"])
        r0.pack(fill="x", pady=4)
        tk.Label(r0, text=t("exporter.sample_rate"), font=(UI_FONT, 9, "bold"),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        self.sr_var = tk.StringVar(value="Auto")
        sr_vals = ["Auto"] + [f"{r} Hz" for r in [8000, 11025, 16000, 22050,
                   32000, 44100, 48000, 88200, 96000, 176400, 192000]]
        self._sr_cb = ttk.Combobox(r0, textvariable=self.sr_var,
                                    values=sr_vals, state="readonly", width=14)
        self._sr_cb.pack(side="left", padx=8)

        tk.Label(r0, text=t("exporter.channels"), font=(UI_FONT, 9, "bold"),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left", padx=(20, 0))
        self.ch_var = tk.StringVar(value="Auto (keep original)")
        self._ch_cb = ttk.Combobox(r0, textvariable=self.ch_var,
                                    values=[c[0] for c in CHANNEL_LAYOUTS],
                                    state="readonly", width=20)
        self._ch_cb.pack(side="left", padx=8)

    # ── Trim ─────────────────────────────────────────────────────────────────

    def _build_trim_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.trim"))
        row = tk.Frame(sec, bg=CLR["bg"])
        row.pack(fill="x", pady=4)

        tk.Label(row, text=t("exporter.start_label"), font=(UI_FONT, 9, "bold"),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        self.trim_start = tk.StringVar(value="")
        tk.Entry(row, textvariable=self.trim_start, width=12,
                 relief="flat", bg=CLR["input_bg"], fg=CLR["input_fg"],
                 insertbackground=CLR["accent"],
                 font=(UI_FONT, 9)).pack(side="left", padx=4)
        tk.Label(row, text=t("exporter.time_hint"), font=(UI_FONT, 8),
                 bg=CLR["bg"], fg=CLR["fgdim"]).pack(side="left", padx=(0, 16))

        tk.Label(row, text=t("exporter.end_label"), font=(UI_FONT, 9, "bold"),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        self.trim_end = tk.StringVar(value="")
        tk.Entry(row, textvariable=self.trim_end, width=12,
                 relief="flat", bg=CLR["input_bg"], fg=CLR["input_fg"],
                 insertbackground=CLR["accent"],
                 font=(UI_FONT, 9)).pack(side="left", padx=4)
        tk.Label(row, text=t("exporter.blank_hint"), font=(UI_FONT, 8),
                 bg=CLR["bg"], fg=CLR["fgdim"]).pack(side="left")

    # ── Effects (fade, normalize) ────────────────────────────────────────────

    def _build_effects_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.effects"))

        # Fades
        r0 = tk.Frame(sec, bg=CLR["bg"])
        r0.pack(fill="x", pady=4)
        tk.Label(r0, text=t("exporter.fade_in"), font=(UI_FONT, 9, "bold"),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        self.fade_in = tk.StringVar(value="0")
        tk.Entry(r0, textvariable=self.fade_in, width=6,
                 relief="flat", bg=CLR["input_bg"], fg=CLR["input_fg"],
                 insertbackground=CLR["accent"],
                 font=(UI_FONT, 9)).pack(side="left", padx=4)
        tk.Label(r0, text=t("exporter.sec"), font=(UI_FONT, 8),
                 bg=CLR["bg"], fg=CLR["fgdim"]).pack(side="left", padx=(0, 16))

        tk.Label(r0, text=t("exporter.fade_out"), font=(UI_FONT, 9, "bold"),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        self.fade_out = tk.StringVar(value="0")
        tk.Entry(r0, textvariable=self.fade_out, width=6,
                 relief="flat", bg=CLR["input_bg"], fg=CLR["input_fg"],
                 insertbackground=CLR["accent"],
                 font=(UI_FONT, 9)).pack(side="left", padx=4)
        tk.Label(r0, text=t("exporter.sec"), font=(UI_FONT, 8),
                 bg=CLR["bg"], fg=CLR["fgdim"]).pack(side="left")

        # Volume
        r1 = tk.Frame(sec, bg=CLR["bg"])
        r1.pack(fill="x", pady=4)
        tk.Label(r1, text=t("exporter.volume_adjust"), font=(UI_FONT, 9, "bold"),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        self.volume_var = tk.StringVar(value="0")
        tk.Entry(r1, textvariable=self.volume_var, width=6,
                 relief="flat", bg=CLR["input_bg"], fg=CLR["input_fg"],
                 insertbackground=CLR["accent"],
                 font=(UI_FONT, 9)).pack(side="left", padx=4)
        tk.Label(r1, text=t("exporter.volume_hint"),
                 font=(UI_FONT, 8), bg=CLR["bg"], fg=CLR["fgdim"]).pack(side="left")

        # Normalization
        r2 = tk.Frame(sec, bg=CLR["bg"])
        r2.pack(fill="x", pady=4)
        tk.Label(r2, text=t("exporter.normalize"), font=(UI_FONT, 9, "bold"),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        self.norm_var = tk.StringVar(value="None")
        ttk.Combobox(r2, textvariable=self.norm_var, values=NORM_MODES,
                     state="readonly", width=22).pack(side="left", padx=8)

        self._norm_opts = tk.Frame(sec, bg=CLR["bg"])
        self._norm_opts.pack(fill="x", pady=2)
        tk.Label(self._norm_opts, text="  " + t("exporter.target_lufs"), font=(UI_FONT, 9),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        self.lufs_var = tk.StringVar(value="-16.0")
        tk.Entry(self._norm_opts, textvariable=self.lufs_var, width=7,
                 relief="flat", bg=CLR["input_bg"], fg=CLR["input_fg"],
                 insertbackground=CLR["accent"],
                 font=(UI_FONT, 9)).pack(side="left", padx=4)
        tk.Label(self._norm_opts, text=t("exporter.true_peak"), font=(UI_FONT, 9),
                 bg=CLR["bg"], fg=CLR["fg"]).pack(side="left", padx=(12, 0))
        self.tp_var = tk.StringVar(value="-1.5")
        tk.Entry(self._norm_opts, textvariable=self.tp_var, width=7,
                 relief="flat", bg=CLR["input_bg"], fg=CLR["input_fg"],
                 insertbackground=CLR["accent"],
                 font=(UI_FONT, 9)).pack(side="left", padx=4)
        tk.Label(self._norm_opts, text="dBTP", font=(UI_FONT, 8),
                 bg=CLR["bg"], fg=CLR["fgdim"]).pack(side="left")

        # Mono mix-down
        r3 = tk.Frame(sec, bg=CLR["bg"])
        r3.pack(fill="x", pady=4)
        self.strip_silence_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r3, text=t("exporter.strip_silence"),
                       variable=self.strip_silence_var,
                       bg=CLR["bg"], fg=CLR["fg"],
                       selectcolor=CLR["input_bg"],
                       activebackground=CLR["bg"]).pack(side="left")

        self.replay_gain_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r3, text=t("exporter.replay_gain"),
                       variable=self.replay_gain_var,
                       bg=CLR["bg"], fg=CLR["fg"],
                       selectcolor=CLR["input_bg"],
                       activebackground=CLR["bg"]).pack(side="left", padx=(20, 0))

    # ── Max file size ────────────────────────────────────────────────────────

    def _build_size_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.size"))
        row = tk.Frame(sec, bg=CLR["bg"])
        row.pack(fill="x", pady=4)

        self.limit_enabled = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text=t("exporter.target_max_size"),
                       variable=self.limit_enabled,
                       bg=CLR["bg"], fg=CLR["fg"],
                       selectcolor=CLR["input_bg"],
                       activebackground=CLR["bg"]).pack(side="left")
        self.max_size_var = tk.StringVar(value="10")
        tk.Entry(row, textvariable=self.max_size_var, width=8,
                 relief="flat", bg=CLR["input_bg"], fg=CLR["input_fg"],
                 insertbackground=CLR["accent"],
                 font=(UI_FONT, 9)).pack(side="left", padx=4)

        self.size_unit_var = tk.StringVar(value="MB")
        ttk.Combobox(row, textvariable=self.size_unit_var,
                     values=["KB", "MB"], state="readonly",
                     width=4).pack(side="left", padx=4)
        tk.Label(row, text=t("exporter.auto_calc_hint"),
                 font=(UI_FONT, 8), bg=CLR["bg"], fg=CLR["fgdim"]).pack(side="left", padx=8)

    # ── Metadata ─────────────────────────────────────────────────────────────

    def _build_metadata_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.metadata"))

        self.meta_vars = {}
        fields = [
            ("Title",   "title",   30),
            ("Artist",  "artist",  30),
            ("Album",   "album",   30),
            ("Genre",   "genre",   16),
            ("Year",    "date",     8),
            ("Track #", "track",    6),
            ("Comment", "comment", 40),
        ]

        row = None
        for i, (label, key, w) in enumerate(fields):
            if i % 3 == 0:
                row = tk.Frame(sec, bg=CLR["bg"])
                row.pack(fill="x", pady=2)
            var = tk.StringVar(value="")
            self.meta_vars[key] = var
            tk.Label(row, text=label, font=(UI_FONT, 9),
                     bg=CLR["bg"], fg=CLR["fg"]).pack(side="left", padx=(8, 0))
            tk.Entry(row, textvariable=var, width=w,
                     relief="flat", bg=CLR["input_bg"], fg=CLR["input_fg"],
                     insertbackground=CLR["accent"],
                     font=(UI_FONT, 9)).pack(side="left", padx=4)

        opts_row = tk.Frame(sec, bg=CLR["bg"])
        opts_row.pack(fill="x", pady=4)
        self.strip_meta_var = tk.BooleanVar(value=False)
        tk.Checkbutton(opts_row, text=t("exporter.strip_all_meta"),
                       variable=self.strip_meta_var,
                       bg=CLR["bg"], fg=CLR["fg"],
                       selectcolor=CLR["input_bg"],
                       activebackground=CLR["bg"]).pack(side="left", padx=8)
        self.strip_art_var = tk.BooleanVar(value=False)
        tk.Checkbutton(opts_row, text=t("exporter.strip_art"),
                       variable=self.strip_art_var,
                       bg=CLR["bg"], fg=CLR["fg"],
                       selectcolor=CLR["input_bg"],
                       activebackground=CLR["bg"]).pack(side="left", padx=8)
        self.copy_meta_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opts_row, text=t("exporter.copy_meta"),
                       variable=self.copy_meta_var,
                       bg=CLR["bg"], fg=CLR["fg"],
                       selectcolor=CLR["input_bg"],
                       activebackground=CLR["bg"]).pack(side="left", padx=8)

    # ── Output ───────────────────────────────────────────────────────────────

    def _build_output_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.output"))
        self.out_var = tk.StringVar()
        row = self.make_file_row(sec, t("exporter.save_as"), self.out_var,
                                 self._browse_out)
        row.pack(fill="x", pady=4)

        # Overwrite warning
        opts = tk.Frame(sec, bg=CLR["bg"])
        opts.pack(fill="x", pady=2)
        self.overwrite_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opts, text=t("exporter.overwrite"),
                       variable=self.overwrite_var,
                       bg=CLR["bg"], fg=CLR["fg"],
                       selectcolor=CLR["input_bg"],
                       activebackground=CLR["bg"]).pack(side="left", padx=8)
        self.open_folder_var = tk.BooleanVar(value=False)
        tk.Checkbutton(opts, text=t("exporter.open_folder"),
                       variable=self.open_folder_var,
                       bg=CLR["bg"], fg=CLR["fg"],
                       selectcolor=CLR["input_bg"],
                       activebackground=CLR["bg"]).pack(side="left", padx=8)

        # Export button
        btn_row = tk.Frame(self._inner, bg=CLR["bg"])
        btn_row.pack(fill="x", pady=(12, 6))
        self.btn_export = self.make_render_btn(btn_row, t("exporter.export_btn"),
                                                self._export, color=CLR["green"],
                                                width=30)
        self.btn_export.pack(pady=4)

    # ── Console ──────────────────────────────────────────────────────────────

    def _build_console_section(self):
        cf = tk.Frame(self._inner, bg=CLR["bg"])
        cf.pack(fill="both", expand=True, padx=20, pady=(4, 14))
        self.console, csb = self.make_console(cf, height=8)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    # ── Callbacks ────────────────────────────────────────────────────────────

    def _browse_src(self):
        p = dialogs.askopenfilename(
            title="Open audio / video file",
            filetypes=[
                ("Audio files", "*.mp3 *.wav *.flac *.aac *.m4a *.ogg *.opus "
                                "*.wma *.aiff *.ac3 *.alac *.ape *.wv"),
                ("Video files", "*.mp4 *.mkv *.mov *.avi *.webm *.wmv *.flv *.ts"),
                ("All files", "*.*"),
            ])
        if p:
            self.file_path = p
            self.src_var.set(p)
            self._probe_source(p)

    def _browse_out(self):
        fmt_def = FORMAT_DEFS[self.fmt_var.get()]
        ext = fmt_def["ext"]
        p = dialogs.asksaveasfilename(
            title="Save audio as",
            defaultextension=ext,
            filetypes=[(self.fmt_var.get(), f"*{ext}"), ("All files", "*.*")])
        if p:
            self.out_var.set(p)

    def _probe_source(self, path):
        """Run ffprobe in background to populate source info."""
        def _do():
            ffprobe = get_binary_path("ffprobe.exe")
            cmd = [ffprobe, "-v", "quiet", "-print_format", "json",
                   "-show_format", "-show_streams", path]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   creationflags=CREATE_NO_WINDOW)
                data = json.loads(r.stdout)
            except Exception:
                self.after(0, lambda: self._info_lbl.config(
                    text="  Could not read file info", fg=CLR["red"]))
                return

            self._src_info = data
            fmt_info = data.get("format", {})
            # Find first audio stream
            audio_stream = None
            for s in data.get("streams", []):
                if s.get("codec_type") == "audio":
                    audio_stream = s
                    break

            if not audio_stream:
                self.after(0, lambda: self._info_lbl.config(
                    text="  No audio stream found in file", fg=CLR["red"]))
                return

            codec = audio_stream.get("codec_long_name",
                    audio_stream.get("codec_name", "?"))
            sr = audio_stream.get("sample_rate", "?")
            ch = audio_stream.get("channels", "?")
            ch_layout = audio_stream.get("channel_layout", "")
            bits = audio_stream.get("bits_per_raw_sample",
                   audio_stream.get("bits_per_sample", ""))
            duration = fmt_info.get("duration", "?")
            bitrate = fmt_info.get("bit_rate", "?")
            size = fmt_info.get("size", "?")

            try:
                dur_f = float(duration)
                dur_str = f"{int(dur_f//3600):02d}:{int((dur_f%3600)//60):02d}:{dur_f%60:05.2f}"
            except (ValueError, TypeError):
                dur_str = str(duration)

            try:
                br_str = f"{int(bitrate)//1000}k"
            except (ValueError, TypeError):
                br_str = str(bitrate)

            try:
                sz_mb = int(size) / (1024 * 1024)
                sz_str = f"{sz_mb:.2f} MB"
            except (ValueError, TypeError):
                sz_str = str(size)

            bits_str = f"  |  Depth: {bits}-bit" if bits else ""
            ch_str = f"{ch}ch" + (f" ({ch_layout})" if ch_layout else "")

            info = (f"  Codec: {codec}  |  {sr} Hz  |  {ch_str}{bits_str}\n"
                    f"  Duration: {dur_str}  |  Bitrate: {br_str}  |  "
                    f"Size: {sz_str}")

            # Auto-populate metadata fields from source tags
            tags = {**fmt_info.get("tags", {}),
                    **audio_stream.get("tags", {})}
            for key, var in self.meta_vars.items():
                val = tags.get(key, tags.get(key.upper(), ""))
                if val and not var.get():
                    self.after(0, lambda v=var, x=val: v.set(str(x)))

            self.after(0, lambda: self._info_lbl.config(
                text=info, fg=CLR["accent"]))
            self.after(0, lambda: self.log_tagged(
                self.console, f"Loaded: {os.path.basename(path)}", "info"))

        self.run_in_thread(_do)

    def _on_format_change(self, *_):
        """Reconfigure quality section when format changes."""
        fmt = self.fmt_var.get()
        fd = FORMAT_DEFS[fmt]

        # Update format note
        if fd["lossy"]:
            self._fmt_note.config(text=t("exporter.lossy_note"))
        else:
            self._fmt_note.config(text=t("exporter.lossless_uncompressed")
                                  if fmt in ("WAV", "AIFF")
                                  else t("exporter.lossless_compressed"))

        self._toggle_br_mode()

    def _toggle_br_mode(self):
        """Show/hide quality controls based on format + mode."""
        fmt = self.fmt_var.get()
        fd = FORMAT_DEFS[fmt]

        # Hide everything first
        self._cbr_row.pack_forget()
        self._vbr_row.pack_forget()
        self._depth_row.pack_forget()
        self._comp_row.pack_forget()

        if fd["lossy"]:
            # Lossy: show CBR/VBR controls
            self._rb_cbr.pack(side="left", padx=(12, 4))
            self._rb_vbr.pack(side="left", padx=4)

            # Update bitrate list
            self._br_cb.config(values=fd["bitrates"])
            self.bitrate_var.set(fd["default_br"])

            has_vbr = fd.get("vbr_flag") is not None

            if not has_vbr:
                self.br_mode_var.set("CBR")
            self._rb_vbr.config(state="normal" if has_vbr else "disabled")

            if self.br_mode_var.get() == "CBR" or not has_vbr:
                self._cbr_row.pack(fill="x", pady=4)
            else:
                self._vbr_row.pack(fill="x", pady=4)
                vr = fd.get("vbr_range", (0, 9))
                self._vbr_scale.config(from_=vr[0], to=vr[1])
                self.vbr_var.set(fd.get("vbr_default", vr[0]))
                self._vbr_hint.config(
                    text=f"{vr[0]} = {'best' if fd.get('ext') != '.ogg' else 'worst'}, "
                         f"{vr[1]} = {'smallest' if fd.get('ext') != '.ogg' else 'best'}")
        else:
            # Lossless: show bit depth
            self._rb_cbr.pack_forget()
            self._rb_vbr.pack_forget()

            depths = fd.get("bit_depths", {})
            if depths:
                self._depth_row.pack(fill="x", pady=4)
                self._depth_cb.config(values=list(depths.keys()))
                self.depth_var.set(fd.get("default_depth", "16-bit"))

            # FLAC compression
            if fmt == "FLAC":
                self._comp_row.pack(fill="x", pady=4)
                cr = fd.get("compression_range", (0, 12))
                self._comp_scale.config(from_=cr[0], to=cr[1])
                self.comp_var.set(fd.get("compression_default", 5))

    # ── Build FFmpeg command ─────────────────────────────────────────────────

    def _settings(self) -> dict:
        """Validate the form (UI thread).  Raises ValueError with a readable message."""
        fmt = self.fmt_var.get()
        fd = FORMAT_DEFS[fmt]
        ss, to = self.trim_start.get().strip(), self.trim_end.get().strip()
        start = ff.parse_time(ss) if ss else 0.0
        end = ff.parse_time(to) if to else None
        if end is not None and end <= start:
            raise ValueError("The end time must be after the start time.")

        def num(var, label, lo, hi):
            v = var.get().strip() or "0"
            try:
                x = float(v.replace("dB", "").replace("db", ""))
            except ValueError:
                raise ValueError(f"{label} must be a number.") from None
            if not lo <= x <= hi:
                raise ValueError(f"{label} must be between {lo:g} and {hi:g}.")
            return x

        cfg = {
            "fmt": fmt, "start": start, "end": end,
            "volume": num(self.volume_var, "Volume", -60, 40),
            "fade_in": num(self.fade_in, "Fade in", 0, 600), "fade_out": num(self.fade_out, "Fade out", 0, 600),
            "norm": self.norm_var.get(), "strip_silence": self.strip_silence_var.get(),
            "replay_gain": self.replay_gain_var.get(),
            "strip_meta": self.strip_meta_var.get() or not self.copy_meta_var.get(),
            "strip_art": self.strip_art_var.get(),
            "tags": {k: v.get().strip() for k, v in self.meta_vars.items() if v.get().strip()},
            "sr": "" if self.sr_var.get() == "Auto" else self.sr_var.get().replace(" Hz", ""),
            "ch": next((val for label, val in CHANNEL_LAYOUTS if label == self.ch_var.get()), ""),
        }
        if cfg["norm"] == "EBU R128 (loudnorm)":
            cfg["lufs"] = num(self.lufs_var, "Target loudness", -70, -5)
            cfg["tp"] = num(self.tp_var, "True peak", -9, 0)
        elif cfg["norm"] == "Peak normalize":
            cfg["tp"] = num(self.tp_var, "Peak level", -20, 0)
        if fd["lossy"]:
            if self.limit_enabled.get():
                cfg["size_bytes"] = num(self.max_size_var, "Maximum size", 0.001, 1e6) * (
                    1024 * 1024 if self.size_unit_var.get() == "MB" else 1024)
            elif self.br_mode_var.get() == "VBR" and fd.get("vbr_flag"):
                cfg["vbr"] = str(self.vbr_var.get())
            else:
                cfg["bitrate"] = self.bitrate_var.get()
        else:
            cfg["depth"] = fd.get("bit_depths", {}).get(self.depth_var.get(), "")
            if fmt == "FLAC":
                cfg["compression"] = str(self.comp_var.get())
        return cfg

    @staticmethod
    def codec_args(cfg: dict, seconds: float) -> list:
        fmt = cfg["fmt"]
        fd = FORMAT_DEFS[fmt]
        if fd["lossy"]:
            args = list(fd["codec"])
            if "size_bytes" in cfg:
                lo, hi = BITRATE_LIMITS_K.get(fmt, (8, 640))
                kbps = int(cfg["size_bytes"] * 8 * 0.97 / max(seconds, 0.1) / 1000)
                if kbps < lo:
                    raise ValueError(f"{fmt} can't get this short enough for that size "
                                     f"(it would need {kbps} kbps; the minimum is {lo}).")
                args += ["-b:a", f"{min(kbps, hi)}k"]
            elif "vbr" in cfg:
                args += [fd["vbr_flag"], cfg["vbr"]]
            else:
                args += ["-b:a", cfg["bitrate"]]
            return args
        depth = cfg.get("depth", "")
        if fmt in ("WAV", "AIFF"):
            return ["-c:a", depth or ("pcm_s16le" if fmt == "WAV" else "pcm_s16be")]
        args = list(fd["codec"])
        if depth:
            sample_fmt, _, bits = depth.partition(":")
            args += ["-sample_fmt", sample_fmt] + (["-bits_per_raw_sample", bits] if bits else [])
        if fmt == "FLAC":
            args += ["-compression_level", cfg.get("compression", "5")]
        return args

    @staticmethod
    def base_chain(cfg: dict, length: float, keep=None) -> list:
        """Filters before normalisation: silence trim, volume, fades (no full-file buffering)."""
        chain = []
        a, b = keep if keep else (0.0, length)
        if keep:
            chain += [f"atrim=start={a:.3f}:end={b:.3f}", "asetpts=PTS-STARTPTS"]
        final = max(0.01, b - a)
        if cfg["volume"]:
            chain.append(f"volume={cfg['volume']:g}dB")
        if cfg["fade_in"] > 0:
            chain.append(f"afade=t=in:st=0:d={min(cfg['fade_in'], final):.3f}")
        if cfg["fade_out"] > 0:
            d = min(cfg["fade_out"], final)
            chain.append(f"afade=t=out:st={final - d:.3f}:d={d:.3f}")
        return chain

    def _export(self):
        typed = self.src_var.get().strip()
        if typed and os.path.isfile(typed):
            self.file_path = typed
        src = self.file_path
        info = ff.try_probe(src) if src else None
        if info is None or not info.has_audio:
            messagebox.showwarning(t("exporter.no_input"),
                                   t("exporter.no_input") if not src else f"No audio found in:\n{src}", parent=self)
            return
        try:
            cfg = self._settings()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        ext = FORMAT_DEFS[cfg["fmt"]]["ext"]
        out = self.out_var.get().strip() or self.suggest_output(src, "export", ext)
        if os.path.splitext(out)[1].lower() != ext:
            out = os.path.splitext(out)[0] + ext
        out = self.confirm_output(out, [src], default_ext=ext)
        if not out:
            return
        self.out_var.set(out)
        end = min(cfg["end"], info.duration) if cfg["end"] is not None and info.duration else cfg["end"]
        length = max(0.01, (end if end is not None else info.duration) - cfg["start"])
        cover = None
        if not cfg["strip_art"] and ext in COVER_ART_EXTS:
            cover = next((s.index for s in info.streams if s.type == "video" and s.attached_pic), None)
        try:
            self.codec_args(cfg, length)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return

        def work(_p, _c, ctx):
            from core import loudness
            inp = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y"]
            if cfg["start"] > 0:
                inp += ["-ss", f"{cfg['start']:.3f}"]
            if end is not None:
                inp += ["-to", f"{end:.3f}"]
            inp += ["-i", src]
            keep = None
            seg_len = length
            if cfg["strip_silence"]:
                ctx.log("Finding leading and trailing silence…")
                res = ctx.span(0, 0.2).run(inp + ["-map", "0:a:0", "-af", "silencedetect=n=-50dB:d=0.1",
                                                  "-f", "null", "-"], duration=length, tail=200000)
                keep = silence_bounds(res.log_tail, length)
                seg_len = keep[1] - keep[0]
                ctx.log(f"Keeping {keep[0]:.2f}s → {keep[1]:.2f}s")
            chain = self.base_chain(cfg, length, keep)
            gain_db = 0.0
            if cfg["replay_gain"]:
                ctx.log("Measuring ReplayGain…")
                res = ctx.span(0.2, 0.4).run(inp + ["-map", "0:a:0", "-af", ",".join(chain + ["replaygain"]),
                                                    "-f", "null", "-"], duration=length, tail=200000)
                gain_db, peak = parse_replaygain(res.log_tail)
                if peak > 0:   # never push the peak above -1 dBFS
                    gain_db = min(gain_db, -20 * math.log10(peak) - 1.0)
                ctx.log(f"ReplayGain: {gain_db:+.2f} dB")
                chain.append(f"volume={gain_db:.2f}dB")
            rate = cfg["sr"] or str(info.audio.sample_rate or 48000)
            if cfg["norm"] == "EBU R128 (loudnorm)":
                ctx.log("Measuring loudness (pass 1 of 2)…")
                cmd = inp + ["-map", "0:a:0", "-af", ",".join(chain + [
                    f"loudnorm=I={cfg['lufs']}:TP={cfg['tp']}:LRA=11:print_format=json"]), "-f", "null", "-"]
                res = ctx.span(0.4, 0.6).run(cmd, duration=length, tail=200000)
                meas = loudness.parse_measurement(res.log_tail)
                chain.append(loudness.normalise_filter(cfg["lufs"], cfg["tp"], 11, meas, int(rate)))
            elif cfg["norm"] == "Peak normalize":
                ctx.log("Measuring the peak level…")
                res = ctx.span(0.4, 0.6).run(inp + ["-map", "0:a:0", "-af", ",".join(chain + ["volumedetect"]),
                                                    "-f", "null", "-"], duration=length, tail=200000)
                peak = parse_max_volume(res.log_tail)
                chain.append(f"volume={cfg['tp'] - peak:.2f}dB")
                ctx.log(f"Peak {peak:.1f} dBFS → {cfg['tp']:.1f} dBFS")
            cmd = list(inp) + ["-map", "0:a:0"]
            if cover is not None:
                cmd += ["-map", f"0:{cover}", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
            cmd += self.codec_args(cfg, seg_len)
            if cfg["sr"]:
                cmd += ["-ar", cfg["sr"]]
            if cfg["ch"]:
                cmd += ["-ac", cfg["ch"]]
            if chain:
                cmd += ["-af", ",".join(chain)]
            if cfg["strip_meta"]:
                cmd += ["-map_metadata", "-1"]
            for key, val in cfg["tags"].items():
                cmd += ["-metadata", f"{key}={val}"]
            if out.lower().endswith(".m4a"):
                cmd += ["-movflags", "+faststart"]
            ctx.log("Encoding…")
            analysed = cfg["norm"] != "None" or cfg["replay_gain"] or cfg["strip_silence"]
            ctx.span(0.6 if analysed else 0.0, 1.0).run(cmd + [ctx.output(out)], duration=seg_len)
            return 0

        def opened(rc):
            if rc == 0 and self.open_folder_var.get():
                self.open_path(os.path.dirname(out))

        self.log_tagged(self.console, f"Exporting: {os.path.basename(src)} -> {cfg['fmt']}", "info")
        common.queue_job(self, name="Audio Converter", out=out, work=work, btn=self.btn_export,
                         btn_label=t("exporter.export_btn"), console=self.console, duration=length,
                         inputs=[src], on_done=opened)


BITRATE_LIMITS_K = {"MP3": (8, 320), "AAC (M4A)": (16, 512), "OGG Vorbis": (45, 500), "Opus": (6, 510),
                    "WMA": (24, 320), "AC3": (32, 640)}
COVER_ART_EXTS = (".mp3", ".flac", ".m4a")


def _lines(res):
    return res if isinstance(res, list) else str(res).splitlines()


def silence_bounds(log, length: float, edge: float = 0.05) -> tuple:
    """(start, end) to keep after dropping leading/trailing silence found by silencedetect."""
    starts, ends = [], []
    for line in _lines(log):
        if "silence_start:" in line:
            starts.append(float(line.split("silence_start:")[1].split()[0]))
        elif "silence_end:" in line:
            ends.append(float(line.split("silence_end:")[1].split()[0].rstrip("|")))
    a, b = 0.0, length
    if starts and starts[0] <= edge and ends:
        a = ends[0]
    if starts and (len(ends) < len(starts) or ends[-1] >= length - edge):
        b = starts[-1]
    if b - a < 0.05:          # all silent: keep everything rather than nothing
        return 0.0, length
    return a, b


def parse_replaygain(log) -> tuple:
    gain, peak = 0.0, 0.0
    for line in _lines(log):
        if "track_gain =" in line:
            gain = float(line.split("track_gain =")[1].split("dB")[0])
        elif "track_peak =" in line:
            peak = float(line.split("track_peak =")[1].split()[0])
    return gain, peak


def parse_max_volume(log) -> float:
    for line in reversed(_lines(log)):
        if "max_volume:" in line:
            return float(line.split("max_volume:")[1].split("dB")[0])
    raise RuntimeError("Could not measure the peak level.")
