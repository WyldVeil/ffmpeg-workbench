"""
tabs/audio/midifier.py  -  MIDIfier

Three-stage audio pipeline (no Python packages needed - see core/midi.py):
  1. Audio -> MIDI   FFmpeg semitone filter bank + piano-roll note picking
  2. MIDI  -> WAV    FluidSynth + a General MIDI SoundFont (GeneralUser GS bundled)
  3. WAV   -> MP3    FFmpeg (or WAV / FLAC / M4A by the output name)
A .mid/.midi source skips stage 1 (plain MIDI rendering).

Produces a high-quality MIDI re-render of any song.
"""

import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import midi, tasks
from core.i18n import t
from core.ops import midify as md
from core.ops.shared import AUDIO_ONLY_EXTS
from core.scaling import px
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab, add_tooltip
from tabs.kit import dialogs
from tabs.widgets import scroll_body


class MIDIfierTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_path = ""
        self._deps_ok = False
        self._fs_path = None  # resolved fluidsynth path
        self._sf2_path = None  # resolved soundfont path
        self._build_ui()
        tasks.submit(self._check_deps)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        self._build_dependencies_status()
        self._build_source()
        self._build_pipeline_overview()
        opt_sec, r2 = self._build_options()
        self._build_save_midi(opt_sec, r2)
        self._build_output()
        self._build_run_button()
        self._build_console()

    def _build_header(self):
        self.make_header(
            self, t("tab.midifier"), subtitle=t("midifier.subtitle"), icon="\U0001f3b9"
        )

        self._inner = scroll_body(self)

    def _build_dependencies_status(self):
        """Dependencies status."""
        dep_sec = self.make_section(self._inner, title=t("midifier.section.deps"))
        self._dep_lbl = tk.Label(
            dep_sec,
            text=t("midifier.checking_deps"),
            font=(MONO_FONT, 9),
            anchor="w",
            justify="left",
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        )
        self._dep_lbl.pack(fill="x", padx=px(4), pady=px(2))
        self._dep_btn = tk.Button(
            dep_sec,
            text="Choose SoundFont…",
            font=(UI_FONT, 9, "bold"),
            bg=CLR["accent"],
            fg="white",
            relief="flat",
            cursor="hand2",
            command=self._choose_soundfont,
        )
        add_tooltip(self._dep_btn, "Use any General MIDI SoundFont (.sf2 / .sf3) you have.")

    def _build_source(self):
        """Source."""
        src_sec = self.make_section(self._inner, title=t("midifier.section.source"))
        self.src_var = tk.StringVar()
        row = self.make_file_row(src_sec, t("midifier.input_file"), self.src_var, self._browse_src)
        row.pack(fill="x", pady=px(4))

    def _build_pipeline_overview(self):
        """Pipeline overview."""
        pipe_sec = self.make_section(self._inner, title=t("midifier.section.pipeline"))
        steps = [
            ("1", t("midifier.step1_title"), t("midifier.step1_desc")),
            ("2", t("midifier.step2_title"), t("midifier.step2_desc")),
            ("3", t("midifier.step3_title"), t("midifier.step3_desc")),
        ]
        for num, title, desc in steps:
            sf = tk.Frame(pipe_sec, bg=CLR["bg"])
            sf.pack(fill="x", pady=px(3), padx=px(4))
            tk.Label(
                sf,
                text=f"  {num} ",
                font=(UI_FONT, 11, "bold"),
                bg=CLR["accent"],
                fg="white",
                width=3,
            ).pack(side="left", padx=px(0, 8))
            tf = tk.Frame(sf, bg=CLR["bg"])
            tf.pack(side="left", fill="x", expand=True)
            tk.Label(
                tf, text=title, font=(UI_FONT, 10, "bold"), bg=CLR["bg"], fg=CLR["fg"], anchor="w"
            ).pack(fill="x")
            tk.Label(
                tf,
                text=desc,
                font=(UI_FONT, 8),
                bg=CLR["bg"],
                fg=CLR["fgdim"],
                anchor="w",
                wraplength=px(500),
            ).pack(fill="x")

    def _build_options(self):
        """Options."""
        opt_sec = self.make_section(self._inner, title=t("midifier.section.options"))

        r0 = tk.Frame(opt_sec, bg=CLR["bg"])
        r0.pack(fill="x", pady=px(4))
        tk.Label(
            r0,
            text=t("midifier.midi_program"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left")
        self.program_var = tk.StringVar(value="0 - Acoustic Grand Piano")
        programs = [
            "0 - Acoustic Grand Piano",
            "4 - Electric Piano",
            "24 - Nylon Guitar",
            "25 - Steel Guitar",
            "33 - Electric Bass",
            "40 - Violin",
            "48 - String Ensemble",
            "56 - Trumpet",
            "65 - Alto Sax",
            "73 - Flute",
            "80 - Square Lead (Synth)",
            "88 - Pad (New Age)",
        ]
        ttk.Combobox(
            r0, textvariable=self.program_var, values=programs, state="readonly", width=30
        ).pack(side="left", padx=px(8))

        r1 = tk.Frame(opt_sec, bg=CLR["bg"])
        r1.pack(fill="x", pady=px(4))
        tk.Label(
            r1, text=t("midifier.gain_label"), font=(UI_FONT, 9, "bold"), bg=CLR["bg"], fg=CLR["fg"]
        ).pack(side="left")
        self.gain_var = tk.StringVar(value="1.0")
        tk.Entry(
            r1,
            textvariable=self.gain_var,
            width=6,
            relief="flat",
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
            font=(UI_FONT, 9),
        ).pack(side="left", padx=px(4))
        tk.Label(
            r1, text=t("midifier.gain_hint"), font=(UI_FONT, 8), bg=CLR["bg"], fg=CLR["fgdim"]
        ).pack(side="left", padx=px(4))

        r2 = tk.Frame(opt_sec, bg=CLR["bg"])
        r2.pack(fill="x", pady=px(4))
        tk.Label(
            r2,
            text=t("midifier.sample_rate"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left")
        self.sr_var = tk.StringVar(value="44100")
        ttk.Combobox(
            r2,
            textvariable=self.sr_var,
            values=["22050", "44100", "48000"],
            state="readonly",
            width=8,
        ).pack(side="left", padx=px(8))
        return opt_sec, r2

    def _build_save_midi(self, opt_sec, r2):
        tk.Label(r2, text="Hz", font=(UI_FONT, 8), bg=CLR["bg"], fg=CLR["fgdim"]).pack(side="left")

        r3 = tk.Frame(opt_sec, bg=CLR["bg"])
        r3.pack(fill="x", pady=px(4))
        self.save_midi_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r3,
            text=t("midifier.save_midi"),
            variable=self.save_midi_var,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["input_bg"],
            activebackground=CLR["bg"],
        ).pack(side="left")

        r4 = tk.Frame(opt_sec, bg=CLR["bg"])
        r4.pack(fill="x", pady=px(4))
        tk.Label(
            r4,
            text=t("midifier.note_thresh"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left")
        self.note_thresh_var = tk.StringVar(value="0.5")
        tk.Entry(
            r4,
            textvariable=self.note_thresh_var,
            width=6,
            relief="flat",
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
            font=(UI_FONT, 9),
        ).pack(side="left", padx=px(4))
        tk.Label(
            r4,
            text=t("midifier.note_thresh_hint"),
            font=(UI_FONT, 8),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(side="left", padx=px(4))

        r5 = tk.Frame(opt_sec, bg=CLR["bg"])
        r5.pack(fill="x", pady=px(4))
        tk.Label(
            r5, text=t("midifier.min_note"), font=(UI_FONT, 9, "bold"), bg=CLR["bg"], fg=CLR["fg"]
        ).pack(side="left")
        self.min_note_var = tk.StringVar(value="0.05")
        tk.Entry(
            r5,
            textvariable=self.min_note_var,
            width=6,
            relief="flat",
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
            font=(UI_FONT, 9),
        ).pack(side="left", padx=px(4))
        tk.Label(
            r5, text=t("midifier.min_note_hint"), font=(UI_FONT, 8), bg=CLR["bg"], fg=CLR["fgdim"]
        ).pack(side="left", padx=px(4))

    def _build_output(self):
        """Output."""
        out_sec = self.make_section(self._inner, title=t("midifier.section.output"))
        self.out_var = tk.StringVar()
        out_row = self.make_file_row(out_sec, t("midifier.save_as"), self.out_var, self._browse_out)
        out_row.pack(fill="x", pady=px(4))

    def _build_run_button(self):
        """Run button."""
        btn_f = tk.Frame(self._inner, bg=CLR["bg"])
        btn_f.pack(fill="x", pady=px(12, 6))
        self.btn_render = self.make_render_btn(btn_f, t("midifier.run_btn"), self._run, width=30)
        self.btn_render.pack(pady=px(4))
        add_tooltip(self.btn_render, t("midifier.run_tooltip"))

    def _build_console(self):
        """Console."""
        self.console = self.make_console_block(
            self._inner, height=10, bg=CLR["bg"], padx=px(20), pady=px(4, 14)
        )

    # ── Browse ────────────────────────────────────────────────────────────

    def _browse_src(self):
        p = dialogs.askopenfilename(
            title="Open audio file",
            filetypes=[
                (
                    "Audio, video or MIDI",
                    "*.mp3 *.wav *.flac *.aac *.m4a *.ogg *.opus *.wma *.aiff "
                    "*.mp4 *.mkv *.mov *.mid *.midi",
                ),
                ("All files", "*.*"),
            ],
        )
        if p:
            self.file_path = p
            self.src_var.set(p)
            base = os.path.splitext(p)[0]
            if not self.out_var.get():
                self.out_var.set(base + "_midified.mp3")

    def _browse_out(self):
        p = dialogs.asksaveasfilename(
            title="Save MP3 as",
            defaultextension=".mp3",
            filetypes=[("MP3", "*.mp3"), ("All files", "*.*")],
        )
        if p:
            self.out_var.set(p)

    # ── Dependency check ─────────────────────────────────────────────────

    def _check_deps(self):
        """Find FluidSynth and a SoundFont (worker thread)."""
        status = ["  ✓  Audio → MIDI: built in (FFmpeg filter bank)"]
        self._fs_path = midi.find_fluidsynth()
        if self._fs_path:
            status.append(f"  ✓  FluidSynth: {self._fs_path}")
        else:
            status.append("  ✗  FluidSynth not found - on Linux install the 'fluidsynth' package")
        if not (self._sf2_path and os.path.isfile(self._sf2_path)):
            fonts = midi.find_soundfonts()
            self._sf2_path = fonts[0] if fonts else None
        if self._sf2_path:
            status.append(f"  ✓  SoundFont: {os.path.basename(self._sf2_path)}")
        else:
            status.append("  ✗  No SoundFont found - choose a .sf2 file")
        ok = bool(self._fs_path and self._sf2_path)
        self._deps_ok = ok
        info = "\n".join(status)

        def _update():
            self._dep_lbl.config(text=info, fg=CLR["green"] if ok else CLR["orange"])
            self._dep_btn.pack(fill="x", padx=px(4), pady=px(6, 4))

        self.after(0, _update)

    def _choose_soundfont(self):
        p = dialogs.askopenfilename(
            title="Choose a SoundFont",
            filetypes=[("SoundFont", "*.sf2 *.sf3"), ("All files", "*.*")],
        )
        if p:
            self._sf2_path = p
            tasks.submit(self._check_deps)

    # ── Main pipeline ────────────────────────────────────────────────────

    def _audio_source(self, src: str) -> ff.MediaInfo | None:
        """*src*'s details when it has sound (else says why)."""
        from tabs import common

        info = common.check_source(self, src, need_video=False)
        if info is not None and not info.has_audio:
            messagebox.showwarning(t("common.warning"), "This file has no audio.", parent=self)
            return None
        return info

    def _player(self) -> tuple[str, str] | None:
        """(FluidSynth, SoundFont) when both are here (else says what's missing)."""
        if self._fs_path and self._sf2_path:
            return self._fs_path, self._sf2_path
        missing = (
            "On Linux, install the 'fluidsynth' package."
            if not self._fs_path
            else "Choose a SoundFont (.sf2) file."
        )
        messagebox.showwarning(
            t("midifier.missing_deps"),
            "FluidSynth and a SoundFont are needed to play the notes back.\n\n" + missing,
            parent=self,
        )
        return None

    def _run(self):
        from tabs import common

        typed = self.src_var.get().strip()
        if typed and os.path.isfile(typed):
            self.file_path = typed
        src = self.file_path
        if not src or not os.path.isfile(src):
            messagebox.showwarning(t("midifier.no_input"), t("midifier.no_input"), parent=self)
            return
        is_midi = src.lower().endswith((".mid", ".midi"))
        info = None if is_midi else self._audio_source(src)
        player = self._player() if is_midi or info is not None else None
        if player is None:
            return
        try:
            threshold = common.number(
                self.note_thresh_var.get() or "0.5", "Note threshold", 0.05, 0.99
            )
            min_dur = common.number(
                self.min_note_var.get() or "0.05", "Minimum note length", 0.01, 5
            )
            gain = common.number(self.gain_var.get() or "0.6", "Gain", 0.05, 5)
            rate = int(common.number(self.sr_var.get() or "44100", "Sample rate", 8000, 192000))
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        program = int(self.program_var.get().split(" - ")[0] or 0)
        out = self.out_var.get().strip() or self.suggest_output(src, "midified", ".mp3")
        if not out.lower().endswith(AUDIO_ONLY_EXTS):
            out = os.path.splitext(out)[0] + ".mp3"
        out = self.confirm_output(out, [src], default_ext=".mp3")
        if not out:
            return
        self.out_var.set(out)
        save_midi = self.save_midi_var.get() and not is_midi
        fs_exe, sf2 = player
        length = info.duration if info else None

        job = md.MidifyJob(
            src,
            out,
            is_midi,
            save_midi,
            fs_exe,
            sf2,
            length,
            md.Settings(threshold, min_dur, gain, rate, program),
        )

        self.log_tagged(self.console, t("midifier.starting"), "info")
        common.queue_job(
            self,
            common.Job(name="MIDIfier", out=out, work=job.run, duration=length, inputs=[src]),
            btn=self.btn_render,
            console=self.console,
        )
