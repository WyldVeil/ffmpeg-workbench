"""
tab_karaoke.py  ─  Karaoke Video Generator

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
from core.scaling import px
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit import dialogs

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
ASS_COLOURS = {
    "Yellow": "&H0000FFFF",  # #FFFF00
    "White": "&H00FFFFFF",  # #FFFFFF
    "Cyan": "&H00FFFF00",  # #00FFFF
    "Green": "&H0000FF00",  # #00FF00
    "Pink": "&H008080FF",  # #FF8080 - soft pink, readable
    "Orange": "&H000080FF",  # #FF8000
}


# ── ASS builder ───────────────────────────────────────────────────────────────


def _ass_time(seconds: float) -> str:
    """Format seconds → ASS timestamp  H:MM:SS.cs"""
    total_cs = round(seconds * 100)
    cs = total_cs % 100
    s = (total_cs // 100) % 60
    m = (total_cs // 6000) % 60
    h = total_cs // 360000
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _build_ass(segments, path, font_size, primary, secondary, res_x=1920, res_y=1080):
    """
    Write an ASS karaoke file.

    primary   = colour that fills in as the word is sung  (e.g. green)
    secondary = colour of words waiting to be sung         (e.g. yellow)

    Each Dialogue line uses \\kf<cs> tags (fill karaoke, centiseconds).
    The fill animation sweeps left-to-right within each word as it is sung,
    which is exactly what you see on a real karaoke machine.
    """
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "Collisions: Normal\n"
        f"PlayResX: {res_x}\n"
        f"PlayResY: {res_y}\n"
        "Timer: 100.0\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # Bold=-1 (yes), Alignment=2 (bottom-centre), Outline=3, Shadow=1
        f"Style: Karaoke,Montserrat,{font_size},{primary},{secondary},&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,3,1,2,10,10,40,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, "
        "MarginL, MarginR, MarginV, Effect, Text\n"
    )

    dialogue_lines = []

    for seg in segments:
        words = seg.get("words") or []

        if not words:
            # No word-level data - treat whole segment as one karaoke block
            from core.ass import escape as _esc

            text = _esc(seg.get("text", "").strip())
            if not text:
                continue
            t0, t1 = seg["start"], seg["end"]
            dur_cs = max(1, round((t1 - t0) * 100))
            dialogue_lines.append(
                f"Dialogue: 0,{_ass_time(t0)},{_ass_time(t1)},Karaoke,,0,0,0,,{{\\kf{dur_cs}}}{text}\n"
            )
            continue

        # Build the \\kf chain, accounting for silence gaps between words
        seg_start = words[0]["start"]
        seg_end = words[-1]["end"]
        prev_end = seg_start
        parts = []

        from core.ass import escape as _esc

        for n, w in enumerate(words):
            w_start = w.get("start", prev_end)
            w_end = w.get("end", w_start + 0.1)
            word_text = _esc(w.get("word", "").lstrip() if n == 0 else w.get("word", ""))

            # Silence/gap before this word: an empty timed token keeps the sweep in
            # sync (no extra space - the word already carries its own).
            gap_cs = max(0, round((w_start - prev_end) * 100))
            if gap_cs > 0:
                parts.append(f"{{\\kf{gap_cs}}}")

            dur_cs = max(1, round((w_end - w_start) * 100))
            parts.append(f"{{\\kf{dur_cs}}}{word_text}")
            prev_end = w_end

        text_body = "".join(parts)
        dialogue_lines.append(
            f"Dialogue: 0,{_ass_time(seg_start)},{_ass_time(seg_end)},Karaoke,,0,0,0,,{text_body}\n"
        )

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header)
        fh.writelines(dialogue_lines)

    return len(dialogue_lines)


# ── Tab ───────────────────────────────────────────────────────────────────────


def _whisper_ok():
    """The bundled whisper.cpp engine is present (models download on demand)."""
    from core import whisper

    return bool(whisper.cli_path())


def segments_from_whispercpp(path: str) -> list:
    """whisper.cpp full JSON (-ojf) -> [{'start','end','text','words':[{'word','start','end'}]}]."""
    import json

    with open(path, encoding="utf-8", errors="replace") as fh:
        data = json.load(fh)
    segments = []
    for seg in data.get("transcription", []):
        words = []
        for tok in seg.get("tokens", []):
            text = tok.get("text", "")
            if not text or text.startswith("[_"):
                continue
            a = tok.get("offsets", {}).get("from", 0) / 1000.0
            b = tok.get("offsets", {}).get("to", 0) / 1000.0
            if words and not text.startswith(" ") and text.strip():
                words[-1]["word"] += text
                words[-1]["end"] = max(words[-1]["end"], b)
            elif text.strip():
                words.append({"word": text, "start": a, "end": max(b, a + 0.05)})
        off = seg.get("offsets", {})
        if words:
            segments.append(
                {
                    "start": off.get("from", 0) / 1000.0,
                    "end": off.get("to", 0) / 1000.0,
                    "text": seg.get("text", "").strip(),
                    "words": words,
                }
            )
    return segments


def vocal_remove_filter() -> str:
    """Centre-panned vocals out, bass (usually centred too) kept, same polarity on both sides."""
    return (
        "asplit[ka][kb];[ka]pan=stereo|c0=c0-c1|c1=c0-c1[side];"
        "[kb]lowpass=f=180,pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1[bass];"
        "[side][bass]amix=inputs=2:normalize=0,alimiter=limit=0.95"
    )


class KaraokeTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self._cancel = threading.Event()
        self._build_ui()

    # ─── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=CLR["panel"])
        hdr.pack(fill="x")
        hdr_inner = tk.Frame(hdr, bg=CLR["panel"])
        hdr_inner.pack(fill="x", padx=px(20), pady=px(14, 13))
        tk.Label(
            hdr_inner,
            text="🎤  " + t("tab.karaoke_generator"),
            font=(UI_FONT, 15, "bold"),
            bg=CLR["panel"],
            fg=CLR["accent"],
        ).pack(side="left")
        tk.Label(
            hdr_inner,
            text=t("karaoke.subtitle"),
            font=(UI_FONT, 10),
            bg=CLR["panel"],
            fg=CLR["fgdim"],
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=px(16, 0))
        tk.Frame(self, bg=CLR["border"], height=px(1)).pack(fill="x")

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
            values=list(ASS_COLOURS.keys()),
            state="readonly",
            width=8,
        ).pack(side="left", padx=px(6))
        tk.Label(rc, text=f"  {t('karaoke.fill_colour_label')}").pack(side="left")
        self._col_sung = tk.StringVar(value="Green")
        ttk.Combobox(
            rc,
            textvariable=self._col_sung,
            values=list(ASS_COLOURS.keys()),
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

        # ── Output ────────────────────────────────────────────────────────
        ro = tk.Frame(self)
        ro.pack(fill="x", padx=px(16), pady=px(6))
        tk.Label(
            ro, text=t("common.output_file"), font=(UI_FONT, 10, "bold"), width=16, anchor="e"
        ).pack(side="left")
        self._out_var = tk.StringVar()
        tk.Entry(ro, textvariable=self._out_var, width=60, relief="flat").pack(
            side="left", padx=px(8)
        )
        tk.Button(
            ro, text=t("common.save_as"), command=self._browse_out, cursor="hand2", relief="flat"
        ).pack(side="left")

        # ── Action buttons ─────────────────────────────────────────────────
        btn_row = tk.Frame(self)
        btn_row.pack(pady=px(8))
        self._btn_go = tk.Button(
            btn_row,
            text=t("karaoke.generate_button"),
            font=(UI_FONT, 12, "bold"),
            bg="#7B1FA2",
            fg="white",
            height=2,
            width=26,
            command=self._generate,
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
        cf = tk.Frame(self)
        cf.pack(fill="both", expand=True, padx=px(16), pady=px(4))
        self.console, csb = self.make_console(cf, height=8)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

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

    def _install_whisper(self):
        from core import whisper

        key = MODELS.get(self._model_var.get(), "base")
        self._log(f"Downloading {whisper.MODELS[key].label}…")
        tasks.submit(
            partial(whisper.download_model, key),
            on_done=lambda _p: (self._log("✅  Model ready."), self._check_whisper()),
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

    def _browse_out(self):
        p = dialogs.asksaveasfilename(
            defaultextension=".mp4", filetypes=[("MP4", "*.mp4"), ("MKV", "*.mkv")]
        )
        if p:
            self._out_var.set(p)

    def _log(self, msg):
        self.after(
            0,
            lambda m=msg: [
                self.console.insert(tk.END, m + "\n"),
                self.console.see(tk.END),
            ],
        )

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
            settings = {
                "model": MODELS.get(self._model_var.get(), "base"),
                "lang": LANG_CODES.get(self._lang_var.get()) or "auto",
                "darkness": float(self._darkness.get()),
                "font_size": max(12, int(self._font_size.get())),
                "primary": ASS_COLOURS.get(self._col_sung.get(), ASS_COLOURS["Green"]),
                "secondary": ASS_COLOURS.get(self._col_unsung.get(), ASS_COLOURS["Yellow"]),
                "rm_vocals": self._rm_vocals.get(),
            }
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
            worker_fn=lambda p, c, ctx: self._worker(src, out, settings, ctx),
            on_progress=lambda _t, line: self._log(line),
            on_complete=done,
            inputs=[src],
        )

    # ─── Background worker (queue job) ───────────────────────────────────────
    def _worker(self, src, out, cfg, ctx):
        import os

        from core import ass as ass_mod
        from core import ffmpeg as ff
        from core import whisper

        info = ff.probe(src)
        tmp = ctx.tempdir()
        if not whisper.is_installed(cfg["model"]):
            ctx.log(f"Downloading the {whisper.MODELS[cfg['model']].label} model (one time)…")
            whisper.download_model(cfg["model"], lambda f: ctx.progress(0.2 * f), ctx.cancelled)
        wav = os.path.join(tmp, "audio.wav")
        ctx.log("Step 1/3: transcribing the lyrics…")
        ctx.span(0.2, 0.25).run(whisper.audio_extract_cmd(src, wav), duration=info.duration or None)
        base = os.path.join(tmp, "words")
        cmd = whisper.transcribe_cmd(
            wav,
            whisper.model_path(cfg["model"]),
            base,
            language=cfg["lang"],
            max_len=40,
            formats=("json",),
        )
        res = ff.run(
            cmd,
            on_line=lambda line: (
                ctx.progress(0.25 + 0.45 * f)
                if (f := whisper.parse_progress(line)) is not None
                else None
            ),
            cancel=ctx.cancelled,
        )
        if res.cancelled:
            raise ff.CancelledError("cancelled")
        if res.returncode != 0 or not os.path.isfile(base + ".json"):
            raise RuntimeError("Speech recognition failed:\n" + "\n".join(res.log_tail[-6:]))
        segments = segments_from_whispercpp(base + ".json")
        if not segments:
            raise RuntimeError("No sung or spoken words were recognised.")
        ctx.log(f"Step 2/3: {len(segments)} lyric lines.")
        w, h = info.require_video().display_size if info.has_video else (1920, 1080)
        ass_path = os.path.join(tmp, "karaoke.ass")
        _build_ass(
            segments,
            ass_path,
            font_size=round(cfg["font_size"] * min(w, h) / 1080),
            primary=cfg["primary"],
            secondary=cfg["secondary"],
            res_x=w,
            res_y=h,
        )
        ctx.log("Step 3/3: rendering the karaoke video…")
        if info.has_video:
            cmd = [ff.ffmpeg_path(), "-y", "-i", src]
            vf = f"curves=all='0/0 1/{cfg['darkness']:.2f}',{ass_mod.subtitles_filter(ass_path)},format=yuv420p"
        else:  # audio only: lyrics on a dark background
            cmd = [
                ff.ffmpeg_path(),
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x101018:s={w}x{h}:r=30",
                "-i",
                src,
                "-shortest",
            ]
            vf = f"{ass_mod.subtitles_filter(ass_path)},format=yuv420p"
        a_in = "0:a:0" if info.has_video else "1:a:0"
        cmd += ["-map", "0:v:0", "-map", a_in, "-vf", vf]
        if cfg["rm_vocals"]:
            cmd += ["-af", vocal_remove_filter()]
        cmd += [
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "medium",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "256k",
            "-movflags",
            "+faststart",
            ctx.output(out),
        ]
        ctx.span(0.7, 1.0).run(cmd, duration=info.duration or None)
        return 0

    def _finish(self):
        self._btn_go.config(state="normal", text=t("karaoke.generate_button"))
        self._btn_stop.config(state="disabled")
        self._progress.stop()
        self._status("")
