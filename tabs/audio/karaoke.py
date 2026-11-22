"""
tabs/audio/karaoke.py - Karaoke Video Generator (the engine is core/ops/karaoke.py)

Turns any music video into a singalong karaoke track in three steps:
  1. Whisper (word_timestamps=True) transcribes every word with precise timing.
  2. An ASS subtitle file is built using \\kf (fill-karaoke) tags so that each
     word's colour animates left-to-right as it is sung - yellow = upcoming,
     green = filling in as you sing it.  Standard karaoke machine behaviour.
  3. FFmpeg renders the final video:
       • Centre-channel vocal cancellation removes the lead vocals
       • curves filter darkens the video for legibility
       • ass= filter burns the karaoke subtitles at the bottom of the frame
"""

import os
import threading
import tkinter as tk
from functools import partial
from tkinter import messagebox, ttk

from core import tasks
from core.i18n import t
from core.ops import karaoke as ko
from core.queue_manager import Hooks
from core.scaling import px
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab, make_header
from tabs.kit import dialogs
from tabs.widgets import SaveRow

# ── Constants ─────────────────────────────────────────────────────────────────

MODELS = {  # label: core.whisper model key (whisper.cpp, downloaded once on first use)
    "Tiny - fastest, rough (32 MB)": "tiny",
    "Base - fast (60 MB)": "base",
    "Small - good balance (190 MB)": "small",
    "Large v3 Turbo - most accurate (574 MB)": "turbo",
}

LANGUAGES = [
    "auto-detect",
    "English",
    "Spanish",
    "French",
    "German",
    "Italian",
    "Portuguese",
    "Dutch",
    "Russian",
    "Chinese",
    "Japanese",
    "Korean",
    "Arabic",
    "Hindi",
    "Turkish",
    "Polish",
]

LANG_CODES = {
    "auto-detect": None,
    "English": "en",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Italian": "it",
    "Portuguese": "pt",
    "Dutch": "nl",
    "Russian": "ru",
    "Chinese": "zh",
    "Japanese": "ja",
    "Korean": "ko",
    "Arabic": "ar",
    "Hindi": "hi",
    "Turkish": "tr",
    "Polish": "pl",
}

# ASS colours are &HAABBGGRR  (AA=alpha 00=opaque, then BGR, then R last)


# ── Tab ───────────────────────────────────────────────────────────────────────


def _whisper_ok():
    """The bundled whisper.cpp engine is present (models download on demand)."""
    from core import whisper

    return bool(whisper.cli_path())


class KaraokeTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self._cancel = threading.Event()
        self._build_ui()

    # ─── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_header()
        self._build_whisper_status_banner()
        self._build_source()
        self._build_settings()
        self._build_output()
        self._build_action_buttons()

    def _build_header(self):
        # Header
        make_header(self, t("tab.karaoke_generator"), t("karaoke.subtitle"), icon="🎤")

    def _build_whisper_status_banner(self):
        """Whisper status banner."""
        # ── Whisper status banner ──────────────────────────────────────────
        self._banner = tk.Frame(self, bg="#1A2A1A", relief="groove", bd=px(1))
        self._banner.pack(fill="x", padx=px(16), pady=px(8, 4))
        self._whisper_lbl = tk.Label(
            self._banner,
            text=t("karaoke.checking_for_whisper"),
            bg="#1A2A1A",
            fg=CLR["fgdim"],
            font=(MONO_FONT, 9),
        )
        self._whisper_lbl.pack(side="left", padx=px(12), pady=px(6))
        tk.Button(
            self._banner,
            text=t("karaoke.install_whisper_button"),
            bg="#2E7D32",
            fg="white",
            command=self._install_whisper,
        ).pack(side="right", padx=px(8), pady=px(4))
        self.after(120, self._check_whisper)

    def _build_source(self):
        """Source."""
        # ── Source ────────────────────────────────────────────────────────
        r1 = tk.Frame(self)
        r1.pack(fill="x", padx=px(16), pady=px(8))
        tk.Label(
            r1,
            text=t("karaoke.music_video_label"),
            font=(UI_FONT, 10, "bold"),
            width=16,
            anchor="e",
        ).pack(side="left")
        self._src_var = tk.StringVar()
        tk.Entry(r1, textvariable=self._src_var, width=60, relief="flat").pack(
            side="left", padx=px(8)
        )
        tk.Button(
            r1, text=t("btn.browse"), command=self._browse_src, cursor="hand2", relief="flat"
        ).pack(side="left")

    def _build_settings(self):
        """Settings."""
        # ── Settings ──────────────────────────────────────────────────────
        sf = tk.LabelFrame(
            self, text=f"  {t('karaoke.karaoke_settings_section')}  ", padx=px(16), pady=px(10)
        )
        sf.pack(fill="x", padx=px(20), pady=px(6))

        # Whisper model + language
        ra = tk.Frame(sf)
        ra.pack(fill="x", pady=px(3))
        tk.Label(ra, text=t("karaoke.whisper_model_label"), width=16, anchor="e").pack(side="left")
        self._model_var = tk.StringVar(value=list(MODELS.keys())[2])
        ttk.Combobox(
            ra, textvariable=self._model_var, values=list(MODELS.keys()), state="readonly", width=34
        ).pack(side="left", padx=px(8))
        tk.Label(ra, text=t("karaoke.language")).pack(side="left")
        self._lang_var = tk.StringVar(value="auto-detect")
        ttk.Combobox(
            ra, textvariable=self._lang_var, values=LANGUAGES, state="readonly", width=14
        ).pack(side="left", padx=px(8))

        # Video darkness
        rb = tk.Frame(sf)
        rb.pack(fill="x", pady=px(3))
        tk.Label(rb, text=t("karaoke.video_darkness_label"), width=16, anchor="e").pack(side="left")
        self._darkness = tk.DoubleVar(value=0.28)
        tk.Scale(
            rb,
            variable=self._darkness,
            from_=0.05,
            to=0.65,
            resolution=0.01,
            orient="horizontal",
            length=px(200),
        ).pack(side="left", padx=px(8))
        self._dark_lbl = tk.Label(rb, text="", width=7, fg=CLR["accent"])
        self._dark_lbl.pack(side="left")
        self._darkness.trace_add("write", self._update_dark_lbl)
        self._update_dark_lbl()
        tk.Label(
            rb, text=t("karaoke.max_brightness_hint"), fg=CLR["fgdim"], font=(UI_FONT, 9)
        ).pack(side="left", padx=px(12))

        # Font size + colours
        rc = tk.Frame(sf)
        rc.pack(fill="x", pady=px(3))
        tk.Label(rc, text=t("karaoke.subtitle_font_label"), width=16, anchor="e").pack(side="left")
        self._font_size = tk.StringVar(value="72")
        tk.Entry(rc, textvariable=self._font_size, width=5, relief="flat").pack(
            side="left", padx=px(8, 16)
        )
        tk.Label(rc, text=f"{t('karaoke.px_label')}   {t('karaoke.unsung_colour_label')}").pack(
            side="left"
        )
        self._col_unsung = tk.StringVar(value="Yellow")
        ttk.Combobox(
            rc,
            textvariable=self._col_unsung,
            values=list(ko.ASS_COLOURS.keys()),
            state="readonly",
            width=8,
        ).pack(side="left", padx=px(6))
        tk.Label(rc, text=f"  {t('karaoke.fill_colour_label')}").pack(side="left")
        self._col_sung = tk.StringVar(value="Green")
        ttk.Combobox(
            rc,
            textvariable=self._col_sung,
            values=list(ko.ASS_COLOURS.keys()),
            state="readonly",
            width=8,
        ).pack(side="left", padx=px(6))
        tk.Label(
            rc, text=t("karaoke.colour_as_sung_hint"), fg=CLR["fgdim"], font=(UI_FONT, 9)
        ).pack(side="left", padx=px(8))

        # Vocal removal checkbox
        rd = tk.Frame(sf)
        rd.pack(fill="x", pady=px(3))
        self._rm_vocals = tk.BooleanVar(value=True)
        tk.Checkbutton(
            rd,
            text=t("karaoke.remove_vocals_checkbox"),
            variable=self._rm_vocals,
        ).pack(side="left")

    def _build_output(self):
        """Output."""
        # ── Output ────────────────────────────────────────────────────────
        self._out_var = tk.StringVar()
        SaveRow(self, t("common.output_file"), self._out_var).pack(
            fill="x", padx=px(16), pady=px(6)
        )

    def _build_action_buttons(self):
        """Action buttons."""
        # ── Action buttons ─────────────────────────────────────────────────
        btn_row = tk.Frame(self)
        btn_row.pack(pady=px(8))
        self._btn_go = self.make_render_btn(
            btn_row, t("karaoke.generate_button"), self._generate, width=26
        )
        self._btn_go.pack(side="left", padx=px(8))
        self._btn_stop = tk.Button(
            btn_row,
            text=t("btn.stop"),
            font=(UI_FONT, 10),
            bg=CLR["red"],
            fg="white",
            height=2,
            width=10,
            state="disabled",
            command=self._stop,
        )
        self._btn_stop.pack(side="left", padx=px(4))

        # Progress
        self._prog_lbl = tk.Label(self, text="", fg=CLR["accent"], font=(UI_FONT, 10, "bold"))
        self._prog_lbl.pack()
        self._progress = ttk.Progressbar(self, mode="indeterminate", length=px(600))
        self._progress.pack(pady=px(2))

        # Console
        self.console = self.make_console_block(self, height=8, padx=px(16), pady=px(4))

    # ─── Helpers ──────────────────────────────────────────────────────────────
    def _update_dark_lbl(self, *_):
        pct = int(self._darkness.get() * 100)
        self._dark_lbl.config(text=t("karaoke.bright").format(pct))

    def _check_whisper(self):
        from core import whisper

        key = MODELS.get(self._model_var.get(), "base")
        if not whisper.cli_path():
            self._whisper_lbl.config(
                text="⚠  The speech engine (whisper-cli) is missing from this build.", fg=CLR["red"]
            )
        elif whisper.is_installed(key):
            self._whisper_lbl.config(
                text="✅  Speech recognition ready (runs offline).", fg=CLR["green"]
            )
        else:
            self._whisper_lbl.config(
                text=f"ℹ  The {whisper.MODELS[key].label} model downloads once on first use.",
                fg=CLR["fgdim"],
            )

    def _model_ready(self, _path):
        self._log("✅  Model ready.")
        self._check_whisper()

    def _install_whisper(self):
        from core import whisper

        key = MODELS.get(self._model_var.get(), "base")
        self._log(f"Downloading {whisper.MODELS[key].label}…")
        tasks.submit(
            partial(whisper.download_model, key),
            on_done=self._model_ready,
            on_error=lambda e: self._log(f"❌  Download failed: {e}"),
            name="KaraokeModel",
        )

    def _browse_src(self):
        p = dialogs.askopenfilename(
            title="Select music video",
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All", t("ducker.item_2"))],
        )
        if p:
            self._src_var.set(p)
            if not self._out_var.get():
                self._out_var.set(os.path.splitext(p)[0] + "_karaoke.mp4")

    def _append_log(self, msg):
        self.console.insert(tk.END, msg + "\n")
        self.console.see(tk.END)

    def _log(self, msg):
        self.after(0, self._append_log, msg)

    def _status(self, msg):
        self.after(0, lambda m=msg: self._prog_lbl.config(text=m))

    def _stop(self):
        self._cancel.set()
        if getattr(self, "_task", None):
            self.cancel_render(self._task)
        self._log(t("log.karaoke.cancelling"))

    # ─── Generate ─────────────────────────────────────────────────────────────
    def _generate(self):
        src = self._src_var.get().strip()
        out = self._out_var.get().strip()

        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("karaoke.no_source_message"))
            return
        if not _whisper_ok():
            messagebox.showerror(
                t("karaoke.whisper_not_installed_title"), t("karaoke.install_first_message")
            )
            return
        out = self.confirm_output(out or self.suggest_output(src, "karaoke"), [src])
        if not out:
            return
        self._out_var.set(out)
        try:
            settings = ko.Karaoke(
                model=MODELS.get(self._model_var.get(), "base"),
                language=LANG_CODES.get(self._lang_var.get()) or "auto",
                darkness=float(self._darkness.get()),
                font_size=max(12, int(self._font_size.get())),
                sung=ko.ASS_COLOURS.get(self._col_sung.get(), ko.ASS_COLOURS["Green"]),
                waiting=ko.ASS_COLOURS.get(self._col_unsung.get(), ko.ASS_COLOURS["Yellow"]),
                remove_vocals=self._rm_vocals.get(),
            )
        except ValueError:
            messagebox.showerror(
                t("msg.bad_input_title"), t("msg.font_size_whole_number"), parent=self
            )
            return
        self._btn_go.config(state="disabled", text=t("app.status.queued_btn"))
        self._btn_stop.config(state="normal")
        self._progress.start(10)
        self.console.delete("1.0", tk.END)

        def done(tid, rc):
            self._finish()
            self.last_task = self.task(tid)
            self.show_result(rc, out)

        self._task = self.enqueue_render(
            "Karaoke",
            output_path=out,
            work=partial(ko.make_karaoke, src, out, settings),
            inputs=[src],
            hooks=Hooks(on_progress=lambda _t, line: self._log(line), on_complete=done),
        )

    def _finish(self):
        self._btn_go.config(state="normal", text=t("karaoke.generate_button"))
        self._btn_stop.config(state="disabled")
        self._progress.stop()
        self._status("")
