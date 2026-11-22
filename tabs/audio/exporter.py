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

import logging
import os
import tkinter as tk
from enum import StrEnum
from functools import partial
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import tasks
from core.i18n import t
from core.ops import audio_export as ae
from core.queue_manager import Hooks
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit import dialogs
from tabs.kit.widgets import ChoiceBox

logger = logging.getLogger("crossfader.audio.exporter")


# ── Format definitions ───────────────────────────────────────────────────────
#  key: (extension, codec_args, supports_bitrate, supports_vbr, supports_bit_depth)
CHANNEL_LAYOUTS = [
    ("Auto (keep original)", ""),
    ("Mono", "1"),
    ("Stereo", "2"),
    ("2.1 (3ch)", "3"),
    ("4.0 Quad", "4"),
    ("5.0 Surround", "5"),
    ("5.1 Surround", "6"),
    ("7.1 Surround", "8"),
]


class SizeUnit(StrEnum):
    KB = "KB"
    MB = "MB"


#: the sample-rate drop-down: what it shows -> the rate ("" = the source's)
SAMPLE_RATES = {"Auto": ""} | {
    f"{r} Hz": str(r)
    for r in (8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000)
}


class AudioExporterTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_path = ""
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # -- header --
        self.make_header(
            self, t("tab.audio_converter"), subtitle=t("exporter.subtitle"), icon="\U0001f4be"
        )  # floppy disk

        # Scrollable body
        outer = tk.Frame(self, bg=CLR["bg"])
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=CLR["bg"], highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self._inner = tk.Frame(canvas, bg=CLR["bg"])
        self._inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

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
        row = self.make_file_row(sec, t("exporter.input_file"), self.src_var, self._browse_src)
        row.pack(fill="x", pady=px(4))

    def _build_source_info_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.source_info"))
        self._info_lbl = tk.Label(
            sec,
            text="  No file loaded - browse to open a file",
            font=(MONO_FONT, 9),
            anchor="w",
            justify="left",
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        )
        self._info_lbl.pack(fill="x", padx=px(4), pady=px(2))

    # ── Format ───────────────────────────────────────────────────────────────

    def _build_format_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.format"))
        row = tk.Frame(sec, bg=CLR["bg"])
        row.pack(fill="x", pady=px(4))

        tk.Label(
            row,
            text=t("exporter.format_label"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left")
        self.fmt_box = ChoiceBox(
            row, ae.FORMATS, ae.AudioFormat.MP3, width=16, on_change=self._on_format_change
        )
        self.fmt_box.pack(side="left", padx=px(8))

        self._fmt_note = tk.Label(
            row,
            text=t("exporter.lossy_note"),
            font=(UI_FONT, 8, "italic"),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        )
        self._fmt_note.pack(side="left", padx=px(8))

    # ── Quality / Bitrate ────────────────────────────────────────────────────

    def _build_quality_section(self):
        self._build_quality_section_qual_sec()
        self._build_quality_section_vbr()
        self._build_quality_compression()

    def _build_quality_section_qual_sec(self):
        self._qual_sec = self.make_section(self._inner, title=t("exporter.section.quality"))

        # Bitrate mode row
        r0 = tk.Frame(self._qual_sec, bg=CLR["bg"])
        r0.pack(fill="x", pady=px(4))
        tk.Label(
            r0, text=t("exporter.mode_label"), font=(UI_FONT, 9, "bold"), bg=CLR["bg"], fg=CLR["fg"]
        ).pack(side="left")
        self.br_mode_var = tk.StringVar(value="CBR")
        self._rb_cbr = tk.Radiobutton(
            r0,
            text=t("exporter.cbr"),
            variable=self.br_mode_var,
            value="CBR",
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["input_bg"],
            activebackground=CLR["bg"],
            command=self._toggle_br_mode,
        )
        self._rb_cbr.pack(side="left", padx=px(12, 4))
        self._rb_vbr = tk.Radiobutton(
            r0,
            text=t("exporter.vbr"),
            variable=self.br_mode_var,
            value="VBR",
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["input_bg"],
            activebackground=CLR["bg"],
            command=self._toggle_br_mode,
        )
        self._rb_vbr.pack(side="left", padx=px(4))

        # CBR bitrate row
        self._cbr_row = tk.Frame(self._qual_sec, bg=CLR["bg"])
        self._cbr_row.pack(fill="x", pady=px(4))
        tk.Label(
            self._cbr_row,
            text=t("exporter.bitrate_label"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left")
        self.bitrate_var = tk.StringVar(value="320k")
        self._br_cb = ttk.Combobox(
            self._cbr_row,
            textvariable=self.bitrate_var,
            values=ae.FORMAT_DEFS[ae.AudioFormat.MP3]["bitrates"],
            state="readonly",
            width=8,
        )
        self._br_cb.pack(side="left", padx=px(8))

        # VBR quality row
        self._vbr_row = tk.Frame(self._qual_sec, bg=CLR["bg"])
        tk.Label(
            self._vbr_row,
            text=t("exporter.vbr_quality"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left")

    def _build_quality_section_vbr(self):
        self.vbr_var = tk.IntVar(value=2)
        self._vbr_scale = tk.Scale(
            self._vbr_row,
            from_=0,
            to=9,
            orient="horizontal",
            variable=self.vbr_var,
            length=px(200),
            bg=CLR["bg"],
            fg=CLR["fg"],
            highlightthickness=0,
        )
        self._vbr_scale.pack(side="left", padx=px(8))
        self._vbr_hint = tk.Label(
            self._vbr_row,
            text="0 = best, 9 = smallest",
            font=(UI_FONT, 8),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        )
        self._vbr_hint.pack(side="left", padx=px(4))

        # Lossless bit depth row
        self._depth_row = tk.Frame(self._qual_sec, bg=CLR["bg"])
        tk.Label(
            self._depth_row,
            text=t("exporter.bit_depth"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left")
        self.depth_var = tk.StringVar(value="16-bit")
        self._depth_cb = ttk.Combobox(
            self._depth_row,
            textvariable=self.depth_var,
            values=["16-bit", "24-bit", "32-bit"],
            state="readonly",
            width=12,
        )
        self._depth_cb.pack(side="left", padx=px(8))

        # FLAC compression row
        self._comp_row = tk.Frame(self._qual_sec, bg=CLR["bg"])
        tk.Label(
            self._comp_row,
            text=t("exporter.compression"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left")
        self.comp_var = tk.IntVar(value=5)
        self._comp_scale = tk.Scale(
            self._comp_row,
            from_=0,
            to=12,
            orient="horizontal",
            variable=self.comp_var,
            length=px(200),
            bg=CLR["bg"],
            fg=CLR["fg"],
            highlightthickness=0,
        )

    def _build_quality_compression(self):
        self._comp_scale.pack(side="left", padx=px(8))
        tk.Label(
            self._comp_row,
            text=t("exporter.compression_hint"),
            font=(UI_FONT, 8),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(side="left", padx=px(4))

        # Initial state
        self._toggle_br_mode()

    # ── Sample rate & channels ───────────────────────────────────────────────

    def _build_sample_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.sample"))

        r0 = tk.Frame(sec, bg=CLR["bg"])
        r0.pack(fill="x", pady=px(4))
        tk.Label(
            r0,
            text=t("exporter.sample_rate"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left")
        self.sr_var = tk.StringVar(value="Auto")
        self._sr_cb = ttk.Combobox(
            r0, textvariable=self.sr_var, values=list(SAMPLE_RATES), state="readonly", width=14
        )
        self._sr_cb.pack(side="left", padx=px(8))

        tk.Label(
            r0, text=t("exporter.channels"), font=(UI_FONT, 9, "bold"), bg=CLR["bg"], fg=CLR["fg"]
        ).pack(side="left", padx=px(20, 0))
        self.ch_var = tk.StringVar(value="Auto (keep original)")
        self._ch_cb = ttk.Combobox(
            r0,
            textvariable=self.ch_var,
            values=[c[0] for c in CHANNEL_LAYOUTS],
            state="readonly",
            width=20,
        )
        self._ch_cb.pack(side="left", padx=px(8))

    # ── Trim ─────────────────────────────────────────────────────────────────

    def _build_trim_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.trim"))
        row = tk.Frame(sec, bg=CLR["bg"])
        row.pack(fill="x", pady=px(4))

        tk.Label(
            row,
            text=t("exporter.start_label"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left")
        self.trim_start = tk.StringVar(value="")
        tk.Entry(
            row,
            textvariable=self.trim_start,
            width=12,
            relief="flat",
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
            font=(UI_FONT, 9),
        ).pack(side="left", padx=px(4))
        tk.Label(
            row, text=t("exporter.time_hint"), font=(UI_FONT, 8), bg=CLR["bg"], fg=CLR["fgdim"]
        ).pack(side="left", padx=px(0, 16))

        tk.Label(
            row, text=t("exporter.end_label"), font=(UI_FONT, 9, "bold"), bg=CLR["bg"], fg=CLR["fg"]
        ).pack(side="left")
        self.trim_end = tk.StringVar(value="")
        tk.Entry(
            row,
            textvariable=self.trim_end,
            width=12,
            relief="flat",
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
            font=(UI_FONT, 9),
        ).pack(side="left", padx=px(4))
        tk.Label(
            row, text=t("exporter.blank_hint"), font=(UI_FONT, 8), bg=CLR["bg"], fg=CLR["fgdim"]
        ).pack(side="left")

    # ── Effects (fade, normalize) ────────────────────────────────────────────

    def _build_effects_section(self):
        r1, sec = self._build_effects_section_fade_in()
        r3 = self._build_effects_section_norm(r1, sec)
        self._build_effects_section_replay_gain(r3)

    def _build_effects_section_fade_in(self):
        sec = self.make_section(self._inner, title=t("exporter.section.effects"))

        # Fades
        r0 = tk.Frame(sec, bg=CLR["bg"])
        r0.pack(fill="x", pady=px(4))
        tk.Label(
            r0, text=t("exporter.fade_in"), font=(UI_FONT, 9, "bold"), bg=CLR["bg"], fg=CLR["fg"]
        ).pack(side="left")
        self.fade_in = tk.StringVar(value="0")
        tk.Entry(
            r0,
            textvariable=self.fade_in,
            width=6,
            relief="flat",
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
            font=(UI_FONT, 9),
        ).pack(side="left", padx=px(4))
        tk.Label(r0, text=t("exporter.sec"), font=(UI_FONT, 8), bg=CLR["bg"], fg=CLR["fgdim"]).pack(
            side="left", padx=px(0, 16)
        )

        tk.Label(
            r0, text=t("exporter.fade_out"), font=(UI_FONT, 9, "bold"), bg=CLR["bg"], fg=CLR["fg"]
        ).pack(side="left")
        self.fade_out = tk.StringVar(value="0")
        tk.Entry(
            r0,
            textvariable=self.fade_out,
            width=6,
            relief="flat",
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
            font=(UI_FONT, 9),
        ).pack(side="left", padx=px(4))
        tk.Label(r0, text=t("exporter.sec"), font=(UI_FONT, 8), bg=CLR["bg"], fg=CLR["fgdim"]).pack(
            side="left"
        )

        # Volume
        r1 = tk.Frame(sec, bg=CLR["bg"])
        r1.pack(fill="x", pady=px(4))
        tk.Label(
            r1,
            text=t("exporter.volume_adjust"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left")
        self.volume_var = tk.StringVar(value="0")
        tk.Entry(
            r1,
            textvariable=self.volume_var,
            width=6,
            relief="flat",
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
            font=(UI_FONT, 9),
        ).pack(side="left", padx=px(4))
        return r1, sec

    def _build_effects_section_norm(self, r1, sec):
        tk.Label(
            r1, text=t("exporter.volume_hint"), font=(UI_FONT, 8), bg=CLR["bg"], fg=CLR["fgdim"]
        ).pack(side="left")

        # Normalization
        r2 = tk.Frame(sec, bg=CLR["bg"])
        r2.pack(fill="x", pady=px(4))
        tk.Label(
            r2, text=t("exporter.normalize"), font=(UI_FONT, 9, "bold"), bg=CLR["bg"], fg=CLR["fg"]
        ).pack(side="left")
        self.norm_box = ChoiceBox(r2, ae.NORMALISE, ae.Normalise.NONE, width=22)
        self.norm_box.pack(side="left", padx=px(8))

        self._norm_opts = tk.Frame(sec, bg=CLR["bg"])
        self._norm_opts.pack(fill="x", pady=px(2))
        tk.Label(
            self._norm_opts,
            text="  " + t("exporter.target_lufs"),
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left")
        self.lufs_var = tk.StringVar(value="-16.0")
        tk.Entry(
            self._norm_opts,
            textvariable=self.lufs_var,
            width=7,
            relief="flat",
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
            font=(UI_FONT, 9),
        ).pack(side="left", padx=px(4))
        tk.Label(
            self._norm_opts,
            text=t("exporter.true_peak"),
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left", padx=px(12, 0))
        self.tp_var = tk.StringVar(value="-1.5")
        tk.Entry(
            self._norm_opts,
            textvariable=self.tp_var,
            width=7,
            relief="flat",
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
            font=(UI_FONT, 9),
        ).pack(side="left", padx=px(4))
        tk.Label(
            self._norm_opts, text="dBTP", font=(UI_FONT, 8), bg=CLR["bg"], fg=CLR["fgdim"]
        ).pack(side="left")

        # Mono mix-down
        r3 = tk.Frame(sec, bg=CLR["bg"])
        r3.pack(fill="x", pady=px(4))
        self.strip_silence_var = tk.BooleanVar(value=False)
        return r3

    def _build_effects_section_replay_gain(self, r3):
        tk.Checkbutton(
            r3,
            text=t("exporter.strip_silence"),
            variable=self.strip_silence_var,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["input_bg"],
            activebackground=CLR["bg"],
        ).pack(side="left")

        self.replay_gain_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r3,
            text=t("exporter.replay_gain"),
            variable=self.replay_gain_var,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["input_bg"],
            activebackground=CLR["bg"],
        ).pack(side="left", padx=px(20, 0))

    # ── Max file size ────────────────────────────────────────────────────────

    def _build_size_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.size"))
        row = tk.Frame(sec, bg=CLR["bg"])
        row.pack(fill="x", pady=px(4))

        self.limit_enabled = tk.BooleanVar(value=False)
        tk.Checkbutton(
            row,
            text=t("exporter.target_max_size"),
            variable=self.limit_enabled,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["input_bg"],
            activebackground=CLR["bg"],
        ).pack(side="left")
        self.max_size_var = tk.StringVar(value="10")
        tk.Entry(
            row,
            textvariable=self.max_size_var,
            width=8,
            relief="flat",
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
            font=(UI_FONT, 9),
        ).pack(side="left", padx=px(4))

        self.size_unit = ChoiceBox(row, {u: u.value for u in SizeUnit}, SizeUnit.MB, width=4)
        self.size_unit.pack(side="left", padx=px(4))
        tk.Label(
            row, text=t("exporter.auto_calc_hint"), font=(UI_FONT, 8), bg=CLR["bg"], fg=CLR["fgdim"]
        ).pack(side="left", padx=px(8))

    # ── Metadata ─────────────────────────────────────────────────────────────

    def _build_metadata_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.metadata"))

        self.meta_vars = {}
        fields = [
            ("Title", "title", 30),
            ("Artist", "artist", 30),
            ("Album", "album", 30),
            ("Genre", "genre", 16),
            ("Year", "date", 8),
            ("Track #", "track", 6),
            ("Comment", "comment", 40),
        ]

        row = None
        for i, (label, key, w) in enumerate(fields):
            if i % 3 == 0:
                row = tk.Frame(sec, bg=CLR["bg"])
                row.pack(fill="x", pady=px(2))
            var = tk.StringVar(value="")
            self.meta_vars[key] = var
            tk.Label(row, text=label, font=(UI_FONT, 9), bg=CLR["bg"], fg=CLR["fg"]).pack(
                side="left", padx=px(8, 0)
            )
            tk.Entry(
                row,
                textvariable=var,
                width=w,
                relief="flat",
                bg=CLR["input_bg"],
                fg=CLR["input_fg"],
                insertbackground=CLR["accent"],
                font=(UI_FONT, 9),
            ).pack(side="left", padx=px(4))

        opts_row = tk.Frame(sec, bg=CLR["bg"])
        opts_row.pack(fill="x", pady=px(4))
        self.strip_meta_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            opts_row,
            text=t("exporter.strip_all_meta"),
            variable=self.strip_meta_var,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["input_bg"],
            activebackground=CLR["bg"],
        ).pack(side="left", padx=px(8))
        self.strip_art_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            opts_row,
            text=t("exporter.strip_art"),
            variable=self.strip_art_var,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["input_bg"],
            activebackground=CLR["bg"],
        ).pack(side="left", padx=px(8))
        self.copy_meta_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            opts_row,
            text=t("exporter.copy_meta"),
            variable=self.copy_meta_var,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["input_bg"],
            activebackground=CLR["bg"],
        ).pack(side="left", padx=px(8))

    # ── Output ───────────────────────────────────────────────────────────────

    def _build_output_section(self):
        sec = self.make_section(self._inner, title=t("exporter.section.output"))
        self.out_var = tk.StringVar()
        row = self.make_file_row(sec, t("exporter.save_as"), self.out_var, self._browse_out)
        row.pack(fill="x", pady=px(4))

        # Overwrite warning
        opts = tk.Frame(sec, bg=CLR["bg"])
        opts.pack(fill="x", pady=px(2))
        self.overwrite_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            opts,
            text=t("exporter.overwrite"),
            variable=self.overwrite_var,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["input_bg"],
            activebackground=CLR["bg"],
        ).pack(side="left", padx=px(8))
        self.open_folder_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            opts,
            text=t("exporter.open_folder"),
            variable=self.open_folder_var,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["input_bg"],
            activebackground=CLR["bg"],
        ).pack(side="left", padx=px(8))

        # Export button
        btn_row = tk.Frame(self._inner, bg=CLR["bg"])
        btn_row.pack(fill="x", pady=px(12, 6))
        self.btn_render = self.make_render_btn(
            btn_row, t("exporter.export_btn"), self._export, width=30
        )
        self.btn_render.pack(pady=px(4))

    # ── Console ──────────────────────────────────────────────────────────────

    def _build_console_section(self):
        self.console = self.make_console_block(
            self._inner, height=8, bg=CLR["bg"], padx=px(20), pady=px(4, 14)
        )

    # ── Callbacks ────────────────────────────────────────────────────────────

    def _browse_src(self):
        p = dialogs.askopenfilename(
            title="Open audio / video file",
            filetypes=[
                (
                    "Audio files",
                    "*.mp3 *.wav *.flac *.aac *.m4a *.ogg *.opus "
                    "*.wma *.aiff *.ac3 *.alac *.ape *.wv",
                ),
                ("Video files", "*.mp4 *.mkv *.mov *.avi *.webm *.wmv *.flv *.ts"),
                ("All files", "*.*"),
            ],
        )
        if p:
            self.file_path = p
            self.src_var.set(p)
            self._probe_source(p)

    def _browse_out(self):
        fmt_def = ae.FORMAT_DEFS[self.fmt_box.value]
        ext = fmt_def["ext"]
        p = dialogs.asksaveasfilename(
            title="Save audio as",
            defaultextension=ext,
            filetypes=[(ae.FORMATS[self.fmt_box.value], f"*{ext}"), ("All files", "*.*")],
        )
        if p:
            self.out_var.set(p)

    def _probe_source(self, path):
        """Read the source in the background and show what it is."""

        def _do():
            info = ff.try_probe(path)
            if info is None:
                self.after(
                    0,
                    lambda: self._info_lbl.config(text="  Could not read file info", fg=CLR["red"]),
                )
                return
            if not info.audio_streams:
                self.after(
                    0,
                    lambda: self._info_lbl.config(
                        text="  No audio stream found in file", fg=CLR["red"]
                    ),
                )
                return
            a = info.audio_streams[0]
            bits = a.bits_per_raw_sample or a.raw.get("bits_per_sample", 0)
            ch = f"{a.channels}ch" + (f" ({a.channel_layout})" if a.channel_layout else "")
            text = (
                f"  Codec: {a.codec_long or a.codec or '?'}  |  {a.sample_rate or '?'} Hz  |  {ch}"
                + (f"  |  Depth: {bits}-bit" if bits else "")
                + f"\n  Duration: {ff.format_time(info.duration)}  |  "
                f"Bitrate: {info.bit_rate // 1000}k  |  Size: {info.size / (1024 * 1024):.2f} MB"
            )
            # fill empty metadata fields from the source's tags
            tags = {**info.tags, **a.tags}
            for key, var in self.meta_vars.items():
                val = tags.get(key, tags.get(key.upper(), ""))
                if val and not var.get():
                    self.after(0, lambda v=var, x=val: v.set(str(x)))
            self.after(0, lambda: self._info_lbl.config(text=text, fg=CLR["accent"]))
            self.after(
                0,
                lambda: self.log_tagged(self.console, f"Loaded: {os.path.basename(path)}", "info"),
            )

        tasks.submit(_do)

    def _on_format_change(self, *_):
        """Reconfigure quality section when format changes."""
        fmt = self.fmt_box.value
        fd = ae.FORMAT_DEFS[fmt]

        # Update format note
        if fd["lossy"]:
            self._fmt_note.config(text=t("exporter.lossy_note"))
        else:
            self._fmt_note.config(
                text=t("exporter.lossless_uncompressed")
                if fmt in (ae.AudioFormat.WAV, ae.AudioFormat.AIFF)
                else t("exporter.lossless_compressed")
            )

        self._toggle_br_mode()

    def _toggle_br_mode(self):
        """Show/hide quality controls based on format + mode."""
        fmt = self.fmt_box.value
        fd = ae.FORMAT_DEFS[fmt]

        # Hide everything first
        self._cbr_row.pack_forget()
        self._vbr_row.pack_forget()
        self._depth_row.pack_forget()
        self._comp_row.pack_forget()

        if fd["lossy"]:
            # Lossy: show CBR/VBR controls
            self._rb_cbr.pack(side="left", padx=px(12, 4))
            self._rb_vbr.pack(side="left", padx=px(4))

            # Update bitrate list
            self._br_cb.config(values=fd["bitrates"])
            self.bitrate_var.set(fd["default_br"])

            has_vbr = fd.get("vbr_flag") is not None

            if not has_vbr:
                self.br_mode_var.set("CBR")
            self._rb_vbr.config(state="normal" if has_vbr else "disabled")

            if self.br_mode_var.get() == "CBR" or not has_vbr:
                self._cbr_row.pack(fill="x", pady=px(4))
            else:
                self._vbr_row.pack(fill="x", pady=px(4))
                vr = fd.get("vbr_range", (0, 9))
                self._vbr_scale.config(from_=vr[0], to=vr[1])
                self.vbr_var.set(fd.get("vbr_default", vr[0]))
                self._vbr_hint.config(
                    text=f"{vr[0]} = {'best' if fd.get('ext') != '.ogg' else 'worst'}, "
                    f"{vr[1]} = {'smallest' if fd.get('ext') != '.ogg' else 'best'}"
                )
        else:
            # Lossless: show bit depth
            self._rb_cbr.pack_forget()
            self._rb_vbr.pack_forget()

            depths = fd.get("bit_depths", {})
            if depths:
                self._depth_row.pack(fill="x", pady=px(4))
                self._depth_cb.config(values=list(depths.keys()))
                self.depth_var.set(fd.get("default_depth", "16-bit"))

            # FLAC compression
            if fmt is ae.AudioFormat.FLAC:
                self._comp_row.pack(fill="x", pady=px(4))
                cr = fd.get("compression_range", (0, 12))
                self._comp_scale.config(from_=cr[0], to=cr[1])
                self.comp_var.set(fd.get("compression_default", 5))

    # ── Build FFmpeg command ─────────────────────────────────────────────────

    def _settings(self) -> ae.Export:
        """Validate the form (UI thread).  Raises ValueError with a readable message."""
        fmt = self.fmt_box.value
        fd = ae.FORMAT_DEFS[fmt]
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

        size_bytes: float | None = None
        vbr: str | None = None
        bitrate: str | None = None
        if fd["lossy"]:
            if self.limit_enabled.get():
                unit = 1024 * 1024 if self.size_unit.value is SizeUnit.MB else 1024
                size_bytes = num(self.max_size_var, "Maximum size", 0.001, 1e6) * unit
            elif self.br_mode_var.get() == "VBR" and fd.get("vbr_flag"):
                vbr = str(self.vbr_var.get())
            else:
                bitrate = self.bitrate_var.get()
        norm = self.norm_box.value
        return ae.Export(
            fmt=fmt,
            start=start,
            end=end,
            volume=num(self.volume_var, "Volume", -60, 40),
            fade_in=num(self.fade_in, "Fade in", 0, 600),
            fade_out=num(self.fade_out, "Fade out", 0, 600),
            norm=norm,
            lufs=num(self.lufs_var, "Target loudness", -70, -5)
            if norm is ae.Normalise.LOUDNESS
            else -16.0,
            tp=(
                num(self.tp_var, "True peak", -9, 0)
                if norm is ae.Normalise.LOUDNESS
                else num(self.tp_var, "Peak level", -20, 0)
                if norm is ae.Normalise.PEAK
                else -1.0
            ),
            strip_silence=self.strip_silence_var.get(),
            replay_gain=self.replay_gain_var.get(),
            strip_meta=self.strip_meta_var.get() or not self.copy_meta_var.get(),
            strip_art=self.strip_art_var.get(),
            tags={k: v.get().strip() for k, v in self.meta_vars.items() if v.get().strip()},
            sample_rate=SAMPLE_RATES.get(self.sr_var.get(), ""),
            channels=next(
                (val for label, val in CHANNEL_LAYOUTS if label == self.ch_var.get()), ""
            ),
            size_bytes=size_bytes,
            vbr=vbr,
            bitrate=bitrate,
            depth="" if fd["lossy"] else fd.get("bit_depths", {}).get(self.depth_var.get(), ""),
            compression=str(self.comp_var.get()) if fmt is ae.AudioFormat.FLAC else "5",
        )

    def _export(self):
        typed = self.src_var.get().strip()
        if typed and os.path.isfile(typed):
            self.file_path = typed
        src = self.file_path
        info = ff.try_probe(src) if src else None
        if info is None or not info.has_audio:
            messagebox.showwarning(
                t("exporter.no_input"),
                t("exporter.no_input") if not src else f"No audio found in:\n{src}",
                parent=self,
            )
            return
        try:
            cfg = self._settings()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        ext = ae.FORMAT_DEFS[cfg.fmt]["ext"]
        out = self.out_var.get().strip() or self.suggest_output(src, "export", ext)
        if os.path.splitext(out)[1].lower() != ext:
            out = os.path.splitext(out)[0] + ext
        out = self.confirm_output(out, [src], default_ext=ext)
        if not out:
            return
        self.out_var.set(out)
        end = min(cfg.end, info.duration) if cfg.end is not None and info.duration else cfg.end
        length = max(0.01, (end if end is not None else info.duration) - cfg.start)
        cover = None
        if not cfg.strip_art and ext in ae.COVER_ART_EXTS:
            cover = next(
                (s.index for s in info.streams if s.type == "video" and s.attached_pic), None
            )
        try:
            ae.codec_args(cfg, length)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        rate = int(cfg.sample_rate or info.require_audio().sample_rate or 48000)
        job = ae.ExportJob(src=src, export=cfg, end=end, length=length, cover=cover, rate=rate)

        def opened(_tid: str, rc: int) -> None:
            if rc == 0 and self.open_folder_var.get():
                self.open_path(os.path.dirname(out))

        self.log_tagged(self.console, f"Exporting: {os.path.basename(src)} -> {cfg.fmt}", "info")
        common.queue_job(
            self,
            common.Job(
                name="Audio Converter",
                out=out,
                work=partial(job.run, out=out),
                duration=length,
                inputs=[src],
            ),
            btn=self.btn_render,
            console=self.console,
            hooks=Hooks(on_complete=opened),
        )
