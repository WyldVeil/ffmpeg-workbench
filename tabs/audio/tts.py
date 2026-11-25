"""
tab_ttsvoiceover.py  ─  TTS Voice-Over
Generate natural-sounding voice-over from text using Microsoft Edge TTS
(free, no API key, 10 high-quality neural voices) and burn it into a video
or export as standalone audio.

Dependencies are auto-installed into  <project_root>/libs/  on first run.
No manual pip install required.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import time
import tkinter as tk
from collections.abc import Callable
from functools import partial
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import proc as _proc
from core import tasks
from core import tts as tts_core
from core.i18n import t
from core.ops import voiceover as vo
from core.scaling import px
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab, make_header
from tabs.kit import dialogs
from tabs.kit.time_fields import MomentPicker
from tabs.kit.tktyped import linked_scrollbar, on_write
from tabs.kit.widgets import ChoiceBox
from tabs.widgets import SaveRow, scroll_body

logger = logging.getLogger("crossfader.audio.tts")

EDGE_TTS_OK = tts_core.edge_available()
SYSTEM_TTS = tts_core.system_engine_label()  # "" when the OS has no voice engine
ENGINE_EDGE = "Online neural voices (best quality)"
ENGINE_SYSTEM = f"Offline - {SYSTEM_TTS}" if SYSTEM_TTS else ""

# ── Voice catalogue ───────────────────────────────────────────────────────
VOICES = [
    # (Display name,              edge-tts voice ID,                  description)
    # Brian = en-GB-RyanNeural: the closest Edge TTS match to the famous
    # Amazon Polly "Brian" - warm, natural British male, the voice everyone
    # knows from the internet. (Polly Brian is proprietary; this is the
    # best free equivalent.)
    (
        t("tts.brian_british_male"),
        "en-GB-RyanNeural",
        t("tts.warm_british_male_closest_free_match_to_the_famo"),
    ),
    (
        t("tts.aria_us_female"),
        "en-US-AriaNeural",
        t("tts.warm_expressive_american_female_great_for_ads"),
    ),
    (
        t("tts.jenny_us_female"),
        "en-US-JennyNeural",
        t("tts.friendly_conversational_american_female"),
    ),
    (
        t("tts.guy_us_male"),
        "en-US-GuyNeural",
        t("tts.clear_neutral_american_male_good_for_narration"),
    ),
    (
        t("tts.emma_us_female"),
        "en-US-EmmaMultilingualNeural",
        t("tts.natural_engaging_american_female"),
    ),
    (t("tts.davis_us_male"), "en-US-DavisNeural", t("tts.casual_approachable_american_male")),
    (
        t("tts.sonia_british_female"),
        "en-GB-SoniaNeural",
        t("tts.polished_british_female_professional_tone"),
    ),
    (
        t("tts.thomas_british_male"),
        "en-GB-ThomasNeural",
        t("tts.clear_confident_british_male_great_for_explainer"),
    ),
    (
        t("tts.natasha_au_female"),
        "en-AU-NatashaNeural",
        t("tts.clear_australian_female_friendly_and_bright"),
    ),
    (
        t("tts.clara_ca_female"),
        "en-CA-ClaraNeural",
        t("tts.smooth_canadian_female_neutral_and_trustworthy"),
    ),
]

VOICE_NAMES = [v[0] for v in VOICES]
VOICE_IDS = {v[0]: v[1] for v in VOICES}
VOICE_DESCS = {v[0]: v[2] for v in VOICES}

# ── Style presets ─────────────────────────────────────────────────────────
STYLE_PRESETS = {
    "Normal": {"rate": "+0%", "pitch": "+0Hz", "volume": "+0%"},
    "Podcast  (warm)": {"rate": "-5%", "pitch": "-2Hz", "volume": "+5%"},
    "Narrator (slow)": {"rate": "-15%", "pitch": "-4Hz", "volume": "+0%"},
    "Fast-read": {"rate": "+25%", "pitch": "+0Hz", "volume": "+0%"},
    "Dramatic": {"rate": "-10%", "pitch": "-8Hz", "volume": "+10%"},
    "Energetic  (ads)": {"rate": "+15%", "pitch": "+4Hz", "volume": "+10%"},
    "Whisper": {"rate": "-20%", "pitch": "-10Hz", "volume": "-20%"},
    "Audiobook": {"rate": "-8%", "pitch": "-2Hz", "volume": "+0%"},
}

# Average reading speed for duration estimate
WORDS_PER_MINUTE_BASE = 150


SPEECH_FORMATS = {
    vo.SpeechFormat.MP3_320: "MP3  320k",
    vo.SpeechFormat.MP3_192: "MP3  192k",
    vo.SpeechFormat.WAV: "WAV  (lossless)",
    vo.SpeechFormat.AAC: "AAC  256k",
    vo.SpeechFormat.FLAC: "FLAC  (lossless)",
}


class TTSVoiceOverTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self._tts_audio_path = ""  # last generated TTS audio file
        self._build_ui()

    def _out_ext(self) -> str:
        """A video, or the speech alone in the chosen format."""
        return ".mp4" if self.mode_var.get() == "video" else vo.SPEECH_EXT[self.audio_fmt.value]

    # ─────────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        # ── Header ───────────────────────────────────────────────────────
        make_header(self, t("tab.tts_voice_over"), t("tts.subtitle"), icon="🗣")

        # ── Engine status banner ──────────────────────────────────────────
        self._build_engine_banner()

        # ── Scrollable body ───────────────────────────────────────────────
        inner = scroll_body(self)

        self._build_text_section(inner)
        self._build_voice_section(inner)
        self._build_settings_section(inner)
        self._build_output_section(inner)
        self._build_video_section(inner)
        self._build_extras_section(inner)
        self._build_action_buttons(inner)
        self._build_console_section(inner)

        self._update_stats()

    # ── Engine banner ─────────────────────────────────────────────────────
    def _build_engine_banner(self) -> None:
        engines = [e for e in (ENGINE_EDGE if EDGE_TTS_OK else "", ENGINE_SYSTEM) if e]
        ok = bool(engines)
        banner = tk.Frame(self, bg="#1A2A1A" if ok else "#2A1A1A", pady=px(4))
        banner.pack(fill="x")
        self._engine_var = tk.StringVar(value=engines[0] if engines else "")
        if not ok:
            tk.Label(
                banner,
                text=t("tts.no_voice_engine_available_the_online_voices"),
                bg=banner["bg"],
                fg="#EF9A9A",
                font=(UI_FONT, 9),
            ).pack(side="left", padx=px(16))
            return
        tk.Label(
            banner, text=t("tts.voice_engine"), bg=banner["bg"], fg=CLR["fgdim"], font=(UI_FONT, 9)
        ).pack(side="left", padx=px(16, 4))
        cb = ttk.Combobox(
            banner, textvariable=self._engine_var, values=engines, state="readonly", width=40
        )
        cb.pack(side="left")
        self._engine_note = tk.Label(
            banner, text="", bg=banner["bg"], fg=CLR["fgdim"], font=(UI_FONT, 9)
        )
        self._engine_note.pack(side="left", padx=px(8))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._engine_changed())
        self.after(50, self._engine_changed)

    def _engine(self) -> str:
        return "edge" if self._engine_var.get() == ENGINE_EDGE else "system"

    def _engine_changed(self) -> None:
        if not hasattr(self, "_engine_note"):
            return
        if self._engine() == "edge":
            self._engine_note.config(text=t("tts.needs_internet_uses_the_proxy_from_settings"))
        else:
            self._engine_note.config(text=t("tts.works_offline_the_voice_list_and_pitch"))

    # ── Text section ──────────────────────────────────────────────────────
    def _build_text_section(self, parent: tk.Misc) -> None:
        lf = tk.LabelFrame(
            parent, text=f"  📝  {t('tts.script_section')}  ", padx=px(14), pady=px(8)
        )
        lf.pack(fill="x", padx=px(16), pady=px(6))

        # Toolbar
        tb = tk.Frame(lf)
        tb.pack(fill="x", pady=px(0, 4))
        tk.Button(tb, text=t("tts.load_file_button"), command=self._load_text_file).pack(
            side="left", padx=px(2)
        )
        tk.Button(tb, text=t("tts.clear_button"), command=self._clear_text).pack(
            side="left", padx=px(2)
        )
        tk.Button(tb, text=t("tts.paste_button"), command=self._paste_text).pack(
            side="left", padx=px(2)
        )

        self._stats_lbl = tk.Label(tb, text="", fg=CLR["fgdim"], font=(UI_FONT, 9))
        self._stats_lbl.pack(side="right", padx=px(8))

        # Text area
        txt_frame = tk.Frame(lf)
        txt_frame.pack(fill="both", expand=True)
        self.text_box = tk.Text(
            txt_frame,
            height=8,
            wrap="word",
            font=(MONO_FONT, 11),
            relief="sunken",
            bd=px(1),
            undo=True,
        )
        txt_sb = linked_scrollbar(txt_frame, self.text_box)
        self.text_box.pack(side="left", fill="both", expand=True)
        txt_sb.pack(side="right", fill="y")
        # Every change counts (typing, any paste, drag and drop, a loaded file),
        # not just key presses
        self.text_box.bind("<<Modified>>", self._text_modified)

        # Placeholder hint
        self.text_box.insert(
            "1.0",
            "Type or paste your script here…\n\n"
            "Tip: Use punctuation for natural pauses. "
            "Commas = short pause, full stops = longer pause.",
        )
        self.text_box.bind("<FocusIn>", self._clear_placeholder)

    # ── Voice section ─────────────────────────────────────────────────────
    def _build_voice_section(self, parent: tk.Misc) -> None:
        lf = tk.LabelFrame(parent, text=f"  {t('tts.voice_section')}  ", padx=px(14), pady=px(8))
        lf.pack(fill="x", padx=px(16), pady=px(4))

        top = tk.Frame(lf)
        top.pack(fill="x")

        # Voice picker
        tk.Label(
            top, text=t("tts.voice_label"), font=(UI_FONT, 10, "bold"), width=8, anchor="e"
        ).pack(side="left")
        self.voice_var = tk.StringVar(value=VOICE_NAMES[0])
        voice_cb = ttk.Combobox(
            top, textvariable=self.voice_var, values=VOICE_NAMES, state="readonly", width=28
        )
        voice_cb.pack(side="left", padx=px(8))
        voice_cb.bind("<<ComboboxSelected>>", self._on_voice_change)

        # Description label
        self._voice_desc_lbl = tk.Label(
            top, text=VOICE_DESCS[VOICE_NAMES[0]], fg=CLR["fgdim"], font=(UI_FONT, 9), anchor="w"
        )
        self._voice_desc_lbl.pack(side="left", padx=px(8))

        # (Preview voice button is in the player bar below)

        # Style preset
        bottom = tk.Frame(lf)
        bottom.pack(fill="x", pady=px(6, 0))
        tk.Label(
            bottom, text=t("common.preset"), font=(UI_FONT, 10, "bold"), width=8, anchor="e"
        ).pack(side="left")
        self.preset_var = tk.StringVar(value="Normal")
        preset_cb = ttk.Combobox(
            bottom,
            textvariable=self.preset_var,
            values=list(STYLE_PRESETS.keys()),
            state="readonly",
            width=22,
        )
        preset_cb.pack(side="left", padx=px(8))
        preset_cb.bind("<<ComboboxSelected>>", self._apply_preset)
        tk.Label(
            bottom,
            text=t("tts.quick_style_presets_apply_rate_pitch_volume_toge"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 9),
        ).pack(side="left", padx=px(4))

    # ── Settings section ──────────────────────────────────────────────────
    def _build_settings_section(self, parent: tk.Misc) -> None:
        lf = tk.LabelFrame(
            parent, text=f"  ⚙  {t('tts.voice_settings_section')}  ", padx=px(14), pady=px(8)
        )
        lf.pack(fill="x", padx=px(16), pady=px(4))

        def _slider_row(
            frame: tk.LabelFrame,
            label: str,
            var: tk.IntVar,
            lo: int,
            hi: int,
            default: int,
            fmt_fn: Callable[[int], str],
            unit: str = "",
        ) -> tk.Scale:
            row = tk.Frame(frame)
            row.pack(fill="x", pady=px(3))
            tk.Label(row, text=label, width=16, anchor="e", font=(UI_FONT, 9)).pack(side="left")
            sl = tk.Scale(
                row,
                variable=var,
                from_=lo,
                to=hi,
                resolution=1,
                orient="horizontal",
                length=px(260),
            )
            sl.pack(side="left", padx=px(6))
            lbl = tk.Label(
                row, text=fmt_fn(default) + unit, width=9, fg=CLR["accent"], font=(MONO_FONT, 9)
            )
            lbl.pack(side="left")
            on_write(var, lambda: lbl.config(text=fmt_fn(var.get()) + unit))
            return sl

        # Rate:   -50 … +100  (percent)
        self.rate_var = tk.IntVar(value=0)
        # Pitch:  -20 … +20   (Hz)
        self.pitch_var = tk.IntVar(value=0)
        # Volume: -50 … +50   (percent)
        self.vol_var = tk.IntVar(value=0)

        def rate_fmt(v: int) -> str:
            return f"+{v}%" if v >= 0 else f"{v}%"

        def pitch_fmt(v: int) -> str:
            return f"+{v}Hz" if v >= 0 else f"{v}Hz"

        def vol_fmt(v: int) -> str:
            return f"+{v}%" if v >= 0 else f"{v}%"

        _slider_row(lf, t("tts.speed_label"), self.rate_var, -50, 100, 0, rate_fmt)
        _slider_row(lf, t("tts.pitch_label"), self.pitch_var, -20, 20, 0, pitch_fmt)
        _slider_row(lf, t("tts.volume_offset_label"), self.vol_var, -50, 50, 0, vol_fmt)

        note = tk.Frame(lf)
        note.pack(fill="x", pady=px(4, 0))
        tk.Label(
            note,
            text=t("tts.ℹ_rate_and_pitch_only_apply_with_edge_tts"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w")

    # ── Output section ────────────────────────────────────────────────────
    def _build_output_section(self, parent: tk.Misc) -> None:
        lf = tk.LabelFrame(
            parent, text=f"  🎧  {t('tts.output_mode_section')}  ", padx=px(14), pady=px(8)
        )
        lf.pack(fill="x", padx=px(16), pady=px(4))

        self.mode_var = tk.StringVar(value="standalone")
        mode_row = tk.Frame(lf)
        mode_row.pack(fill="x")
        tk.Radiobutton(
            mode_row,
            text=t("tts.standalone_option"),
            variable=self.mode_var,
            value="standalone",
            command=self._on_mode_change,
            font=(UI_FONT, 10),
        ).pack(side="left", padx=px(12))
        tk.Radiobutton(
            mode_row,
            text=t("tts.burn_into_video_option"),
            variable=self.mode_var,
            value="video",
            command=self._on_mode_change,
            font=(UI_FONT, 10),
        ).pack(side="left", padx=px(12))

        # Format picker
        fmt_row = tk.Frame(lf)
        fmt_row.pack(fill="x", pady=px(4))
        tk.Label(fmt_row, text=t("tts.audio_format_label"), width=16, anchor="e").pack(side="left")
        self.audio_fmt = ChoiceBox(fmt_row, SPEECH_FORMATS, vo.SpeechFormat.MP3_320, width=18)
        self.audio_fmt.pack(side="left", padx=px(8))

        # Output path
        self.out_var = tk.StringVar()
        SaveRow(lf, t("common.output_file"), self.out_var, kind="media", ext=self._out_ext).pack(
            fill="x", pady=px(4)
        )

    # ── Video section ─────────────────────────────────────────────────────
    def _build_video_section(self, parent: tk.Misc) -> None:
        self._video_lf = tk.LabelFrame(
            parent, text=f"  🎬  {t('tts.video_section')}  ", padx=px(14), pady=px(8)
        )
        # Packed/hidden by _on_mode_change

        vid_row = tk.Frame(self._video_lf)
        vid_row.pack(fill="x", pady=px(2))
        tk.Label(vid_row, text=t("tts.source_video_label"), width=16, anchor="e").pack(side="left")
        self.vid_var = tk.StringVar()
        self._vid_path = ""
        tk.Entry(vid_row, textvariable=self.vid_var, width=52, relief="flat").pack(
            side="left", padx=px(8)
        )
        tk.Button(vid_row, text=t("btn.browse"), command=self._browse_video).pack(side="left")

        # Timestamp
        ts_row = tk.Frame(self._video_lf)
        ts_row.pack(fill="x", pady=px(2))
        tk.Label(ts_row, text=t("tts.insert_at_label"), width=16, anchor="e").pack(side="left")
        self.timestamp = MomentPicker(ts_row, value="0", width=10)
        self.timestamp.pack(side="left", padx=px(8))
        tk.Label(ts_row, text=t("tts.insert_hint"), fg=CLR["fgdim"], font=(UI_FONT, 9)).pack(
            side="left"
        )

        # Mix mode
        mix_row = tk.Frame(self._video_lf)
        mix_row.pack(fill="x", pady=px(2))
        tk.Label(mix_row, text=t("tts.audio_mode_label"), width=16, anchor="e").pack(side="left")
        self.mix_mode_var = tk.StringVar(value="mix")
        tk.Radiobutton(
            mix_row, text=t("tts.mix_option"), variable=self.mix_mode_var, value="mix"
        ).pack(side="left", padx=px(8))
        tk.Radiobutton(
            mix_row, text=t("tts.replace_option"), variable=self.mix_mode_var, value="replace"
        ).pack(side="left", padx=px(8))

        # Original audio volume (only relevant for mix mode)
        orig_row = tk.Frame(self._video_lf)
        orig_row.pack(fill="x", pady=px(2))
        tk.Label(orig_row, text=t("tts.orig_vol_label"), width=16, anchor="e").pack(side="left")
        self.orig_vol_var = tk.DoubleVar(value=0.5)
        tk.Scale(
            orig_row,
            variable=self.orig_vol_var,
            from_=0,
            to=1.0,
            resolution=0.05,
            orient="horizontal",
            length=px(180),
        ).pack(side="left", padx=px(8))
        orig_lbl = tk.Label(orig_row, text="50%", fg=CLR["accent"], font=(MONO_FONT, 9), width=6)
        orig_lbl.pack(side="left")
        on_write(
            self.orig_vol_var,
            lambda: orig_lbl.config(text=f"{int(self.orig_vol_var.get() * 100)}%"),
        )
        tk.Label(
            orig_row,
            text=t("tts.original_video_audio_when_mixing"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(side="left", padx=px(6))

        # Video output format
        vfmt_row = tk.Frame(self._video_lf)
        vfmt_row.pack(fill="x", pady=px(2))
        tk.Label(vfmt_row, text=t("tts.video_output"), width=16, anchor="e").pack(side="left")
        video_outs = {
            vo.VideoOut.COPY: "Copy video stream  (fast, no re-encode)",
            vo.VideoOut.CRF18: t("tts.re_encode_h_264_crf_18_high_quality"),
            vo.VideoOut.CRF23: t("tts.re_encode_h_264_crf_23_balanced"),
        }
        self.vid_fmt = ChoiceBox(vfmt_row, video_outs, vo.VideoOut.COPY, width=38)
        self.vid_fmt.pack(side="left", padx=px(8))

    # ── Extras section ────────────────────────────────────────────────────
    def _build_extras_section(self, parent: tk.Misc) -> None:
        lf = tk.LabelFrame(
            parent, text=f"  {t('tts.extra_options_section')}  ", padx=px(14), pady=px(8)
        )
        lf.pack(fill="x", padx=px(16), pady=px(4))

        row1 = tk.Frame(lf)
        row1.pack(fill="x", pady=px(2))
        self.srt_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            row1,
            text=t("tts.export_srt_subtitle_file_alongside_output"),
            variable=self.srt_var,
            font=(UI_FONT, 10),
        ).pack(side="left")

        row2 = tk.Frame(lf)
        row2.pack(fill="x", pady=px(2))
        self.normalise_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            row2,
            text=t("tts.auto_normalise_tts_audio_to_16_lufs_broadcast_st"),
            variable=self.normalise_var,
            font=(UI_FONT, 10),
        ).pack(side="left")

        row3 = tk.Frame(lf)
        row3.pack(fill="x", pady=px(2))
        tk.Label(row3, text=t("tts.silence_padding")).pack(side="left")
        tk.Label(row3, text=t("tts.before"), fg=CLR["fgdim"]).pack(side="left", padx=px(8, 2))
        self.pad_before_var = tk.StringVar(value="0.0")
        tk.Entry(row3, textvariable=self.pad_before_var, width=5, relief="flat").pack(side="left")
        tk.Label(row3, text=t("tts.s_after"), fg=CLR["fgdim"]).pack(side="left", padx=px(4, 2))
        self.pad_after_var = tk.StringVar(value="0.3")
        tk.Entry(row3, textvariable=self.pad_after_var, width=5, relief="flat").pack(side="left")
        tk.Label(row3, text="s", fg=CLR["fgdim"]).pack(side="left", padx=px(2))
        tk.Label(
            row3,
            text=t("tts.adds_silence_before_after_speech_in_the_output_f"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(side="left", padx=px(8))

    # ── Action buttons ────────────────────────────────────────────────────
    def _build_action_buttons(self, parent: tk.Misc) -> None:
        btn_row = tk.Frame(parent)
        btn_row.pack(pady=px(10, 4), padx=px(16), fill="x")

        tk.Button(
            btn_row,
            text=t("tts.generate_preview_button"),
            bg="#1565C0",
            fg="white",
            font=(UI_FONT, 11),
            width=20,
            command=self._generate_preview,
        ).pack(side="left", padx=px(6))

        tk.Button(
            btn_row,
            text=t("tts.voice_sample"),
            bg="#37474F",
            fg="white",
            font=(UI_FONT, 11),
            width=16,
            command=self._preview_voice,
        ).pack(side="left", padx=px(6))

        self.btn_render = self.make_render_btn(
            btn_row, t("tts.generate_export_button"), self._render, width=26
        )
        self.btn_render.pack(side="left", padx=px(6))

        # Status label for async feedback
        self._gen_status = tk.Label(btn_row, text="", fg=CLR["accent"], font=(UI_FONT, 9))
        self._gen_status.pack(side="left", padx=px(12))

        # ── In-tab player bar ─────────────────────────────────────────────
        player_lf = tk.LabelFrame(
            parent, text=f"  {t('tts.preview_player_section')}  ", padx=px(12), pady=px(6)
        )
        player_lf.pack(fill="x", padx=px(16), pady=px(0, 6))

        ctrl = tk.Frame(player_lf)
        ctrl.pack(fill="x")

        self._btn_play = tk.Button(
            ctrl,
            text=t("tts.play_button"),
            bg="#1B5E20",
            fg="white",
            width=10,
            font=(UI_FONT, 10),
            state="disabled",
            command=self._player_play,
        )
        self._btn_play.pack(side="left", padx=px(4))

        self._btn_stop = tk.Button(
            ctrl,
            text=t("tts.stop_button"),
            bg="#B71C1C",
            fg="white",
            width=10,
            font=(UI_FONT, 10),
            state="disabled",
            command=self._player_stop,
        )
        self._btn_stop.pack(side="left", padx=px(4))

        self._player_time_lbl = tk.Label(
            ctrl, text="--:--", fg=CLR["accent"], font=(MONO_FONT, 10), width=6
        )
        self._player_time_lbl.pack(side="left", padx=px(8))

        self._player_file_lbl = tk.Label(
            ctrl, text=t("tts.no_audio_yet"), fg=CLR["fgdim"], font=(UI_FONT, 9), anchor="w"
        )
        self._player_file_lbl.pack(side="left", padx=px(4), fill="x", expand=True)

        self._player_proc: _proc.Popen[bytes] | None = None  # ffplay subprocess
        self._player_path = ""  # path to currently loaded audio
        self._player_timer: str | None = None  # after() id for clock update

    # ── Console ───────────────────────────────────────────────────────────
    def _build_console_section(self, parent: tk.Misc) -> None:
        self.console = self.make_console_block(parent, height=5, padx=px(16), pady=px(6))

    # ─────────────────────────────────────────────────────────────────────
    #  Helpers
    # ─────────────────────────────────────────────────────────────────────
    def _clear_placeholder(self, event: object) -> None:
        content = self.text_box.get("1.0", "end-1c")
        if content.startswith("Type or paste your script here"):
            self.text_box.delete("1.0", "end")

    def _clear_text(self) -> None:
        self.text_box.delete("1.0", "end")
        self._update_stats()

    def _paste_text(self) -> None:
        try:
            text = self.clipboard_get()
            self.text_box.insert(tk.INSERT, text)
            self._update_stats()
        except Exception:
            logger.debug("_paste_text: best-effort step failed", exc_info=True)

    def _load_text_file(self) -> None:
        p = dialogs.askopenfilename(
            filetypes=[(t("tts.text_files"), "*.txt *.srt *.md"), ("All", t("ducker.item_2"))]
        )
        if p:
            with open(p, encoding="utf-8", errors="replace") as f:
                self.text_box.delete("1.0", "end")
                self.text_box.insert("1.0", f.read())
            self._update_stats()

    def _text_modified(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if self.text_box.edit_modified():
            self.text_box.edit_modified(False)
            self._update_stats()

    def _update_stats(self) -> None:
        content = self._get_text()
        chars = len(content)
        words = len(content.split()) if content.strip() else 0
        # Estimate duration based on rate
        wpm = (
            WORDS_PER_MINUTE_BASE * (1 + self.rate_var.get() / 100)
            if hasattr(self, "rate_var")
            else WORDS_PER_MINUTE_BASE
        )
        wpm = max(60, wpm)
        secs = (words / wpm) * 60 if words else 0
        m, s = divmod(int(secs), 60)
        dur_str = f"{m}m {s:02d}s" if m else f"{s}s"
        self._stats_lbl.config(
            text=t("tts.chars_chars_words_words_dur_str", chars=chars, words=words, dur_str=dur_str)
        )

    def _get_text(self) -> str:
        t = self.text_box.get("1.0", "end-1c").strip()
        if t.startswith("Type or paste your script here"):
            return ""
        return t

    def _on_voice_change(self, *_: object) -> None:
        name = self.voice_var.get()
        self._voice_desc_lbl.config(text=VOICE_DESCS.get(name, ""))

    def _apply_preset(self, *_: object) -> None:
        p = STYLE_PRESETS.get(self.preset_var.get(), {})
        if p:

            def parse_int(s: str) -> int:
                return int(s.replace("%", "").replace("Hz", "").replace("+", ""))

            if hasattr(self, "rate_var"):
                self.rate_var.set(parse_int(p["rate"]))
                self.pitch_var.set(parse_int(p["pitch"]))
                self.vol_var.set(parse_int(p["volume"]))

    def _on_mode_change(self) -> None:
        if self.mode_var.get() == "video":
            self._video_lf.pack(
                fill="x",
                padx=px(16),
                pady=px(4),
                after=self._video_lf.master.children.get(
                    list(self._video_lf.master.children)[-2], self._video_lf
                ),
            )
        else:
            self._video_lf.pack_forget()

    def _browse_video(self) -> None:
        p = dialogs.askopenfilename(
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All", t("ducker.item_2"))]
        )
        if p:
            self._vid_path = p
            self.vid_var.set(p)
            base = os.path.splitext(p)[0]
            if not self.out_var.get():
                self.out_var.set(base + "_voiceover.mp4")

    # ─────────────────────────────────────────────────────────────────────
    #  TTS generation
    # ─────────────────────────────────────────────────────────────────────
    def _edge_voice_str(self) -> tuple[str, str, str, str]:
        name = self.voice_var.get()
        vid = VOICE_IDS.get(name, "en-US-BrianMultilingualNeural")
        rate = self.rate_var.get()
        pitch = self.pitch_var.get()
        vol = self.vol_var.get()
        rate_s = f"+{rate}%" if rate >= 0 else f"{rate}%"
        pitch_s = f"+{pitch}Hz" if pitch >= 0 else f"{pitch}Hz"
        vol_s = f"+{vol}%" if vol >= 0 else f"{vol}%"
        return vid, rate_s, pitch_s, vol_s

    def _synth_settings(self) -> tts_core.Voice:
        voice_id, rate, pitch, volume = self._edge_voice_str()
        return tts_core.Voice(self._engine(), voice_id, rate, pitch, volume)

    def _synth_async(
        self,
        text: str,
        out_path: str,
        with_subs: bool,
        on_ready: Callable[[str, str | None], object],
    ) -> None:
        """Speak *text* in a worker thread, then call on_ready(path, srt) on the UI thread."""
        if not self._engine_var.get():
            self._on_tts_error("No voice engine is available.")
            return
        cfg = self._synth_settings()

        def ready(srt: str | None) -> None:
            self._on_tts_done(out_path, srt)
            on_ready(out_path, srt)

        tasks.submit(
            partial(tts_core.synthesize, text, out_path, cfg, subtitles=with_subs),
            on_done=ready,
            on_error=lambda e: self._on_tts_error(str(e)),
            name="TTS",
        )

    def _scratch(self, name: str) -> str:
        if not getattr(self, "_scratch_dir", None):
            self._scratch_dir = tempfile.mkdtemp(prefix="wme_tts_preview_")
        ext = ".mp3" if self._engine() == "edge" else ".wav"
        return os.path.join(self._scratch_dir, name + ext)

    def _on_tts_done(self, audio_path: str, srt_text: str | None) -> None:
        self._tts_audio_path = audio_path
        self._pending_srt = srt_text
        self._gen_status.config(text=t("tts.tts_generated"), fg="#66BB6A")
        self.log(
            self.console,
            t("tts.tts_audio_ready_audio_path", audio_path=os.path.basename(audio_path)),
        )
        if srt_text:
            self.log(self.console, t("log.tts.srt_subtitle_data_captured"))

    def _on_tts_error(self, msg: str) -> None:
        self._gen_status.config(text=f"❌  {msg}", fg="#EF9A9A")
        self.log(self.console, t("tts.tts_error_msg", msg=msg))
        self.btn_render.config(state="normal", text=t("tts.generate_export"))

    # ─────────────────────────────────────────────────────────────────────
    #  In-tab player (ffplay -nodisp)
    # ─────────────────────────────────────────────────────────────────────
    def _player_load(self, path: str) -> None:
        """Load a generated audio file into the in-tab player."""
        self._player_stop()
        self._player_path = path
        name = os.path.basename(path)
        self._player_file_lbl.config(text=name)
        self._btn_play.config(state="normal")
        self._btn_stop.config(state="disabled")
        self._player_time_lbl.config(text="00:00")

    def _player_play(self) -> None:
        """Play _player_path via ffplay -nodisp (no window, audio only)."""
        if not self._player_path or not os.path.exists(self._player_path):
            return
        self._player_stop()
        cmd = ff.listen_cmd(self._player_path)
        try:
            self._player_proc = _proc.spawn(cmd)
        except _proc.ProcessError as e:
            self.log(self.console, t("tts.warn_ffplay_failed_e", e=e))
            return
        self._player_start_time = time.monotonic()
        self._btn_play.config(state="disabled")
        self._btn_stop.config(state="normal")
        self._player_tick()

    def _player_stop(self) -> None:
        """Stop playback and reset the timer."""
        if self._player_proc:
            _proc.kill_tree(self._player_proc)
            self._player_proc = None
        if self._player_timer:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._player_timer)
            self._player_timer = None
        self._player_time_lbl.config(text="00:00")
        if self._player_path:
            self._btn_play.config(state="normal")
        self._btn_stop.config(state="disabled")

    def _player_tick(self) -> None:
        """Update the elapsed-time counter every second."""
        if self._player_proc and self._player_proc.poll() is None:
            elapsed = int(time.monotonic() - self._player_start_time)
            m, s = divmod(elapsed, 60)
            self._player_time_lbl.config(text=f"{m:02d}:{s:02d}")
            self._player_timer = self.after(1000, self._player_tick)
        else:
            # Playback finished naturally
            self._player_stop()

    # ─────────────────────────────────────────────────────────────────────
    #  Preview (generate + load into player, no export)
    # ─────────────────────────────────────────────────────────────────────
    def _play_sample(self, path: str, _srt: str | None) -> None:
        self._player_load(path)
        self._player_play()

    def _preview_voice(self) -> None:
        """Speak a short sample sentence in the chosen voice."""
        name = self.voice_var.get().split("(")[0].strip()
        sample = (
            f"Hello, this is {name}. I can narrate your videos, create podcasts, "
            "or add voice-over to any project."
        )
        self._gen_status.config(text=t("tts.generating_voice_sample"), fg=CLR["accent"])
        self._synth_async(
            sample,
            self._scratch("voice_sample"),
            False,
            self._play_sample,
        )

    def _generate_preview(self) -> None:
        """Speak the whole script into the in-tab player (nothing is exported)."""
        text = self._get_text()
        if not text:
            messagebox.showwarning(t("common.warning"), t("tts.enter_some_text_first"), parent=self)
            return
        self._gen_status.config(text=t("tts.generating"), fg=CLR["accent"])
        self.log(self.console, t("log.tts.generating_tts_preview"))

        def ready(path: str, _srt: str | None) -> None:
            self._player_load(path)
            self._player_play()
            self.log(self.console, t("log.tts.loaded_into_in_tab_player_press_play_to_replay"))

        self._synth_async(text, self._scratch("script_preview"), False, ready)

    # ─────────────────────────────────────────────────────────────────────
    #  Main render
    # ─────────────────────────────────────────────────────────────────────
    def _voiceover_source(self) -> tuple[ff.MediaInfo | None, list[str], str] | None:
        """(video info or None, inputs, default output) - None after saying why it can't go on."""
        from tabs import common

        if self.mode_var.get() != "video":
            ext = vo.SPEECH_EXT[self.audio_fmt.value]
            return None, [], os.path.join(os.path.expanduser("~"), "voiceover" + ext)
        vid = (self.vid_var.get() or getattr(self, "_vid_path", "")).strip()
        info = common.check_source(self, vid)
        if info is None:
            return None
        self._vid_path = vid
        return info, [vid], self.suggest_output(vid, "voiceover")

    def _placement(self) -> vo.Placement | None:
        """Where the speech goes (None after saying the numbers are wrong)."""
        video = self.mode_var.get() == "video"
        try:
            pads = (
                max(0.0, float(self.pad_before_var.get() or 0)),
                max(0.0, float(self.pad_after_var.get() or 0)),
            )
            offset = (self.timestamp.seconds(0.0, t("tts.insert_at")) or 0.0) if video else 0.0
        except ValueError:
            messagebox.showwarning(
                t("common.warning"),
                t("tts.padding_and_start_time_must_be_numbers"),
                parent=self,
            )
            return None
        return vo.Placement(
            video=video,
            pads=pads,
            offset=offset,
            volume=1.0 + self.vol_var.get() / 100.0,
            normalise=self.normalise_var.get(),
            fmt=self.audio_fmt.value,
            replace=self.mix_mode_var.get() == "replace",
            original_volume=float(self.orig_vol_var.get()),
            video_out=self.vid_fmt.value,
        )

    def _render(self) -> None:
        from tabs import common

        text = self._get_text()
        if not text:
            messagebox.showwarning(
                t("common.warning"), t("tts.enter_some_text_in_the_script_box"), parent=self
            )
            return
        if not self._engine_var.get():
            messagebox.showwarning(
                t("common.warning"), t("tts.no_voice_engine_is_available"), parent=self
            )
            return
        source = self._voiceover_source()
        if source is None:
            return
        info, inputs, default = source
        placement = self._placement()
        if placement is None:
            return
        out = self.confirm_output(self.out_var.get().strip() or default, inputs)
        if not out:
            return
        self.out_var.set(out)
        job = vo.VoiceoverJob(
            text=text,
            voice=self._synth_settings(),
            placement=placement,
            video=self._vid_path,
            info=info,
            subtitles=self.srt_var.get(),
        )

        self._gen_status.config(text=t("tts.running_tts_engine"), fg=CLR["accent"])
        common.queue_job(
            self,
            common.Job(
                name="TTS Voice-Over",
                out=out,
                work=partial(job.run, out=out),
                duration=info.duration if info else None,
                inputs=inputs,
            ),
            btn=self.btn_render,
            console=self.console,
        )
