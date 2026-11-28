"""
tabs/visuals/media_gen.py  -  Media Generator

Synthetic media for testing, calibration and B-roll: SMPTE/EBU colour bars,
moving test patterns, a classic countdown leader with "2-pop", solid
colours, TV static, gradients and generative patterns - with line-up tones,
noise or silence, at any common resolution and frame rate.
"""

from __future__ import annotations

import contextlib
import os
import tkinter as tk
from tkinter import messagebox, ttk

from core.i18n import Choices, Labels, t
from core.ops.media_gen import AUDIO_EXTS, Generate, generate_cmd
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.kit.widgets import ColourButton
from tabs.widgets import save_section, scroll_body

PATTERNS = Labels(
    "media_gen.patterns",
    {
        "smpte_hd": "SMPTE HD colour bars",
        "smpte": "SMPTE colour bars",
        "ebu": "EBU colour bars (75%)",
        "testsrc": "Moving test pattern",
        "countdown": "Countdown leader (with 2-pop)",
        "color": "Solid colour",
        "black": "Black",
        "static": "TV static",
        "gradient": "Animated gradient",
        "mandelbrot": "Mandelbrot zoom",
        "life": "Game of Life",
        "none": "No video (audio file only)",
    },
)
AUDIO = Labels(
    "media_gen.audio",
    {
        "tone18": "1 kHz tone at −18 dBFS (EBU line-up)",
        "tone20": "1 kHz tone at −20 dBFS (SMPTE line-up)",
        "tone": "Sine tone at a chosen frequency",
        "pop": "2-pop beep (for countdown leaders)",
        "pink": "Pink noise",
        "white": "White noise",
        "silence": "Silence",
        "none": "No audio",
    },
)
SIZES = Choices(
    "media_gen.sizes",
    {
        "3840×2160 (4K UHD)": (3840, 2160),
        "2560×1440 (QHD)": (2560, 1440),
        "1920×1080 (Full HD)": (1920, 1080),
        "1280×720 (HD)": (1280, 720),
        "1080×1920 (vertical 9:16)": (1080, 1920),
        "1080×1080 (square)": (1080, 1080),
        "720×576 (PAL SD)": (720, 576),
        "720×480 (NTSC SD)": (720, 480),
    },
)
RATES = {
    "23.976": "24000/1001",
    "24": "24",
    "25": "25",
    "29.97": "30000/1001",
    "30": "30",
    "50": "50",
    "59.94": "60000/1001",
    "60": "60",
}


class MediaGeneratorTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        self.make_header(
            self,
            t("mgen.title"),
            t("mgen.colour_bars_test_patterns_countdown_leaders_line"),
            icon="🎨",
        )
        body = scroll_body(self)
        self._build_generate_options(body)
        self._build_output(body)
        self._pattern_changed()
        self._audio_changed()

    def _build_generate_options(self, body: tk.Frame) -> None:
        """Pattern, size, rate, length and sound."""
        f = tk.LabelFrame(body, text=t("mgen.what_to_generate"), padx=px(12), pady=px(8))
        f.pack(fill="x", padx=px(20), pady=px(10, 4))

        self.pattern_var = tk.StringVar(value=PATTERNS["smpte_hd"])
        pb = ttk.Combobox(
            f,
            textvariable=self.pattern_var,
            values=list(PATTERNS.values()),
            state="readonly",
            width=34,
        )
        pb.bind("<<ComboboxSelected>>", lambda _e: self._pattern_changed())
        self.form_row(f, 0, t("mgen.visual_pattern"), pb, label_width=16)
        self.colour_btn = ColourButton(f, "#202020", text=t("green_key.colour"), width=10)
        self.colour_btn.grid(row=0, column=2, sticky="w", padx=px(8))
        self.size_var = tk.StringVar(value=SIZES.label("1920×1080 (Full HD)"))
        self.form_row(
            f,
            1,
            t("common.resolution"),
            ttk.Combobox(
                f, textvariable=self.size_var, values=list(SIZES), state="readonly", width=34
            ),
            label_width=16,
        )
        self.rate_var = tk.StringVar(value="25")
        self.form_row(
            f,
            2,
            t("gif_maker.frame_rate"),
            ttk.Combobox(
                f, textvariable=self.rate_var, values=list(RATES), state="readonly", width=10
            ),
            label_width=16,
        )
        self.dur_var = tk.StringVar(value="10")
        self.form_row(
            f,
            3,
            t("mgen.duration_seconds"),
            tk.Entry(f, textvariable=self.dur_var, width=10),
            label_width=16,
        )
        self.audio_var = tk.StringVar(value=AUDIO["tone18"])
        ab = ttk.Combobox(
            f, textvariable=self.audio_var, values=list(AUDIO.values()), state="readonly", width=34
        )
        ab.bind("<<ComboboxSelected>>", lambda _e: self._audio_changed())
        self.form_row(f, 4, t("crossfader.audio"), ab, label_width=16)
        self.freq_var = tk.StringVar(value="440")
        self.freq_row = tk.Frame(f)
        tk.Label(self.freq_row, text="Hz").pack(side="right")
        tk.Entry(self.freq_row, textvariable=self.freq_var, width=8).pack(side="right")
        self.freq_row.grid(row=4, column=2, sticky="w", padx=px(8))
        self.hint = tk.Label(
            f, text="", fg=CLR["fgdim"], font=(UI_FONT, 8), justify="left", wraplength=px(640)
        )
        self.hint.grid(row=5, column=0, columnspan=3, sticky="w")

    def _build_output(self, body: tk.Frame) -> None:
        """Where it goes (the extension picks the format), the button and the log."""
        self.out_row = save_section(body, t("common.output_file"), suggest=self._suggest_name)
        out_lf = self.out_row.master
        tk.Label(
            out_lf,
            text=t("mgen.mp4_mkv_h_264_mov_prores_webm"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w")

        self.btn_render = self.make_render_btn(body, t("mgen.btn_generate"), self._render, width=24)
        self.btn_render.pack(pady=px(10))
        self.console = self.make_console_block(body, height=6)

    # ── helpers ──────────────────────────────────────────────────────────────

    def pattern(self) -> str:
        return next((k for k, v in PATTERNS.items() if v == self.pattern_var.get()), "smpte_hd")

    def audio(self) -> str:
        return next((k for k, v in AUDIO.items() if v == self.audio_var.get()), "none")

    def _suggest_name(self) -> str:
        from core.paths import user_videos_dir

        ext = ".wav" if self.pattern() == "none" else ".mp4"
        return os.path.join(user_videos_dir(), f"{self.pattern()}{ext}")

    def _pattern_changed(self) -> None:
        p = self.pattern()
        if p == "color":
            self.colour_btn.grid()
        else:
            self.colour_btn.grid_remove()
        if p == "countdown":
            self.audio_var.set(AUDIO["pop"])
            self.hint.config(text=t("mgen.counts_down_from_the_duration_at_least"))
        elif p == "none":
            self.hint.config(text=t("mgen.only_the_audio_is_written_choose_a"))
        else:
            self.hint.config(text="")
        self._audio_changed()

    def _audio_changed(self) -> None:
        if self.audio() == "tone":
            self.freq_row.grid()
        else:
            self.freq_row.grid_remove()

    # ── render ───────────────────────────────────────────────────────────────

    def _render(self) -> None:
        pattern, audio = self.pattern(), self.audio()
        try:
            dur = common.number(self.dur_var.get(), t("splicer.column_duration"), 0.1, 3600)
            freq = (
                common.number(self.freq_var.get(), t("mgen.frequency"), 10, 22000)
                if audio == "tone"
                else 1000
            )
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        if pattern == "none" and audio == "none":
            messagebox.showwarning(
                t("common.warning"), t("mgen.choose_a_pattern_or_an_audio_signal"), parent=self
            )
            return
        out = self.out_row.path or self._suggest_name()
        if pattern == "none" and os.path.splitext(out)[1].lower() not in AUDIO_EXTS:
            out = os.path.splitext(out)[0] + ".wav"
        out = self.confirm_output(out, [], default_ext=".wav" if pattern == "none" else ".mp4")
        if not out:
            return
        self.out_row.set(out)
        w, h = SIZES.get(self.size_var.get(), (1920, 1080))
        spec = Generate(
            pattern=pattern,
            audio=audio,
            seconds=dur,
            width=w,
            height=h,
            rate=RATES.get(self.rate_var.get(), "25"),
            colour=self.colour_btn.rgb,
            freq=freq,
        )
        cmd, dur, textfile = generate_cmd(spec, out)
        self.log(
            self.console,
            t(
                "mgen.generating_pattern_var_w_h_rate_var",
                pattern_var=self.pattern_var.get(),
                w=w,
                h=h,
                rate_var=self.rate_var.get(),
                dur=dur,
            ),
        )

        def done(rc: int) -> None:
            if textfile:
                with contextlib.suppress(OSError):
                    os.remove(textfile)
            self.show_result(rc, out)

        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=done,
            btn=self.btn_render,
            duration=dur,
            output=out,
            name="Media Generator",
        )
