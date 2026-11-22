"""
tabs/social/auto_subs.py  -  Auto-Subtitles

Speech to subtitles with whisper.cpp (bundled, runs fully offline once a
model has been downloaded).  Choose a model size, language (or automatic
detection) and line length; optionally translate to English.  The result is
an .srt / .vtt file next to the video, which can then be burned in with the
Hard-Subber or added as a soft subtitle track.
"""

from __future__ import annotations

import os
import re
import shutil
import tkinter as tk
from functools import partial
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import tasks, whisper
from core.i18n import t
from core.queue_manager import Hooks
from core.scaling import px
from core.tools import ToolId
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import FileRow, SaveRow, scroll_body

FORMATS = {
    "srt": "SubRip (.srt) - works everywhere",
    "vtt": "WebVTT (.vtt) - web players",
    "both": "Both .srt and .vtt",
    "txt": "Plain transcript (.txt)",
}
SOFT_SUB_CODEC = {
    "mp4": "mov_text",
    "m4v": "mov_text",
    "mov": "mov_text",
    "mkv": "srt",
    "webm": "webvtt",
}


class AutoSubsTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._last_subs = None
        self._last_lang = ""
        self._downloading = False
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self,
            t("tab.auto_subtitles"),
            "Turn speech into subtitles with Whisper - runs entirely on this computer.",
            icon="🗣",
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body,
            t("common.source_video"),
            self.src_var,
            kind="media",
            on_change=self._source_changed,
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))

        eng = tk.LabelFrame(body, text="  Speech recognition  ", padx=px(12), pady=px(8))
        eng.pack(fill="x", padx=px(20), pady=px(6))

        def row(r, label, *widgets):
            tk.Label(eng, text=label, anchor="w", width=18).grid(
                row=r, column=0, sticky="w", pady=px(3)
            )
            for i, w in enumerate(widgets):
                w.grid(row=r, column=1 + i, sticky="w", padx=px(0, 8), pady=px(3))

        self.model_var = tk.StringVar()
        self.model_box = ttk.Combobox(eng, textvariable=self.model_var, state="readonly", width=40)
        self.model_box.bind("<<ComboboxSelected>>", lambda _e: self._model_status())
        self.dl_btn = tk.Button(
            eng, text="Download", relief="flat", cursor="hand2", command=self._download
        )
        row(0, "Model:", self.model_box, self.dl_btn)
        self.model_lbl = tk.Label(eng, text="", fg=CLR["fgdim"], font=(UI_FONT, 8))
        self.model_lbl.grid(row=1, column=1, columnspan=3, sticky="w")
        self.lang_var = tk.StringVar(value=whisper.LANGUAGES[0][1])
        row(
            2,
            "Spoken language:",
            ttk.Combobox(
                eng,
                textvariable=self.lang_var,
                state="readonly",
                values=[lbl for _k, lbl in whisper.LANGUAGES],
                width=24,
            ),
        )
        self.translate_var = tk.BooleanVar(value=False)
        row(
            3,
            "",
            tk.Checkbutton(
                eng, text="Translate the subtitles into English", variable=self.translate_var
            ),
        )
        self.maxlen_var = tk.IntVar(value=42)
        row(
            4,
            "Max. characters/line:",
            tk.Scale(
                eng, variable=self.maxlen_var, from_=0, to=80, orient="horizontal", length=px(200)
            ),
            tk.Label(
                eng,
                text="(0 = whole sentences; 32-42 suits most screens)",
                fg=CLR["fgdim"],
                font=(UI_FONT, 8),
            ),
        )
        self.prompt_var = tk.StringVar()
        row(
            5,
            "Names / terms:",
            tk.Entry(eng, textvariable=self.prompt_var, width=44),
            tk.Label(eng, text="(optional - helps spelling)", fg=CLR["fgdim"], font=(UI_FONT, 8)),
        )
        self.track_var = tk.StringVar()
        self.track_box = ttk.Combobox(eng, textvariable=self.track_var, state="readonly", width=40)
        row(6, "Audio track:", self.track_box)

        out = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out.pack(fill="x", padx=px(20), pady=px(6))
        self.fmt_var = tk.StringVar(value=FORMATS["srt"])
        fr = tk.Frame(out)
        fr.pack(fill="x")
        tk.Label(fr, text="Format:", width=10, anchor="w").pack(side="left")
        ttk.Combobox(
            fr, textvariable=self.fmt_var, values=list(FORMATS.values()), state="readonly", width=34
        ).pack(side="left")
        self.out_row = SaveRow(out, "Save as:", kind="subtitle", ext=".srt", suggest=self._suggest)
        self.out_row.pack(fill="x", pady=px(6, 0))

        self.btn_render = tk.Button(
            body,
            text="🗣  GENERATE SUBTITLES",
            font=(UI_FONT, 12, "bold"),
            bg="#00695C",
            fg="white",
            width=26,
            cursor="hand2",
            command=self._run,
        )
        self.btn_render.pack(pady=px(8))
        after = tk.Frame(body)
        after.pack(pady=px(0, 6))
        self.burn_btn = tk.Button(
            after,
            text="🔥 Burn into the video…",
            relief="flat",
            state="disabled",
            command=self._burn,
        )
        self.burn_btn.pack(side="left", padx=px(6))
        self.mux_btn = tk.Button(
            after,
            text="📎 Add as a subtitle track…",
            relief="flat",
            state="disabled",
            command=self._mux,
        )
        self.mux_btn.pack(side="left", padx=px(6))

        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=8)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self._fill_models()
        if not whisper.cli_path():
            self.log_tagged(
                self.console,
                "The whisper-cli engine is missing from this build - run "
                "scripts/fetch_binaries.py.",
                "error",
            )

    # ── models ───────────────────────────────────────────────────────────────

    def _fill_models(self, keep=None):
        labels = []
        for m in whisper.MODELS.values():
            labels.append(m.label + ("   ✓" if whisper.is_installed(m.key) else ""))
        self.model_box.config(values=labels)
        key = keep or self.model_key() or whisper.DEFAULT_MODEL
        self.model_var.set(labels[list(whisper.MODELS).index(key)])
        self._model_status()

    def model_key(self):
        label = self.model_var.get().replace("   ✓", "")
        return next((k for k, m in whisper.MODELS.items() if m.label == label), None)

    def _model_status(self):
        key = self.model_key() or whisper.DEFAULT_MODEL
        if whisper.is_installed(key):
            self.model_lbl.config(text="Downloaded - ready to use offline.")
            self.dl_btn.config(state="disabled")
        else:
            m = whisper.MODELS[key]
            self.model_lbl.config(
                text=f"Not downloaded yet ({m.size_mb} MB, once). It will be "
                f"fetched automatically when you generate."
            )
            self.dl_btn.config(state="normal" if not self._downloading else "disabled")

    def _download(self, then=None):
        key = self.model_key() or whisper.DEFAULT_MODEL
        if self._downloading:
            return
        self._downloading = True
        self.dl_btn.config(state="disabled", text="Downloading… 0%")

        def progress(f):
            self.call_ui(lambda: self.dl_btn.config(text=f"Downloading… {f * 100:.0f}%"))

        def done(_path):
            self._downloading = False
            self.dl_btn.config(text="Download")
            self._fill_models(key)
            self.log_tagged(self.console, f"Model ready: {whisper.MODELS[key].label}", "success")
            if then is not None:
                then()

        def failed(exc):
            self._downloading = False
            self.dl_btn.config(text="Download", state="normal")
            messagebox.showerror(
                t("common.error"), f"Could not download the model:\n{exc}", parent=self
            )

        tasks.submit(
            partial(whisper.download_model, key, progress),
            on_done=done,
            on_error=failed,
            name="WhisperModelDownload",
        )

    # ── source ───────────────────────────────────────────────────────────────

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        tracks = []
        if self._info is not None:
            for i, a in enumerate(self._info.audio_streams):
                desc = f"Track {i + 1}: {a.codec}, {a.channels} ch"
                if a.language:
                    desc += f" [{a.language}]"
                if a.title:
                    desc += f" {a.title}"
                tracks.append(desc)
        self.track_box.config(values=tracks or ["(no audio)"])
        self.track_var.set(tracks[0] if tracks else "(no audio)")
        self.out_row.set(self._suggest())
        self._last_subs = None
        self.burn_btn.config(state="disabled")
        self.mux_btn.config(state="disabled")

    def _fmt(self):
        return next((k for k, v in FORMATS.items() if v == self.fmt_var.get()), "srt")

    def _suggest(self):
        src = self.src_row.path
        if not src:
            return ""
        ext = {"vtt": ".vtt", "txt": ".txt"}.get(self._fmt(), ".srt")
        return os.path.splitext(src)[0] + ext

    # ── generate ─────────────────────────────────────────────────────────────

    def _run(self):
        src = self.src_row.path
        info = common.check_source(self, src, need_video=False)
        if info is None:
            return
        if not info.has_audio:
            messagebox.showwarning(
                t("common.warning"), "This file has no audio track to transcribe.", parent=self
            )
            return
        if not whisper.cli_path():
            messagebox.showerror(
                t("common.error"),
                "The speech-to-text engine (whisper-cli) is missing from this build.",
                parent=self,
            )
            return
        fmt = self._fmt()
        exts = {"srt": [".srt"], "vtt": [".vtt"], "both": [".srt", ".vtt"], "txt": [".txt"]}[fmt]
        target = self.out_row.path or self._suggest()
        base = os.path.splitext(target)[0]
        first = self.confirm_output(base + exts[0], [src], default_ext=exts[0])
        if not first:
            return
        base = os.path.splitext(first)[0]
        outputs = [base + e for e in exts]
        self.out_row.set(outputs[0])
        key = self.model_key() or whisper.DEFAULT_MODEL
        lang = next((k for k, lbl in whisper.LANGUAGES if lbl == self.lang_var.get()), "auto")
        tracks = list(self.track_box.cget("values"))
        stream = tracks.index(self.track_var.get()) if self.track_var.get() in tracks else 0
        translate, max_len = self.translate_var.get(), int(self.maxlen_var.get())
        prompt = self.prompt_var.get()
        formats = {"both": ("srt", "vtt")}.get(fmt, (fmt,))
        duration = info.duration or None
        detected = {"lang": lang if lang != "auto" else ""}

        def work(_p, cancelled, ctx):
            if not whisper.is_installed(key):
                ctx.log(f"Downloading the {whisper.MODELS[key].label} model (one time)…")
                whisper.download_model(key, lambda f: ctx.progress(0.25 * f), cancelled)
            tmp = ctx.tempdir()
            wav = os.path.join(tmp, "audio.wav")
            ctx.span(0.25, 0.3).run(
                whisper.audio_extract_cmd(src, wav, stream=stream), duration=duration
            )
            out_base = os.path.join(tmp, "subs")
            cmd = whisper.transcribe_cmd(
                wav,
                whisper.model_path(key),
                out_base,
                language=lang,
                translate=translate,
                max_len=max_len,
                formats=formats,
                prompt=prompt,
            )
            ctx.log("$ " + ff.format_cmd(cmd))

            def on_line(line):
                m = re.search(r"auto-detected language:\s*([a-z]{2,3})", line)
                if m:
                    detected["lang"] = m.group(1)
                frac = whisper.parse_progress(line)
                if frac is not None:
                    ctx.progress(0.3 + 0.7 * frac)
                elif line.startswith("[") or "auto-detected language" in line:
                    ctx.log(line)

            res = ff.run(cmd, on_line=on_line, cancel=cancelled)
            if res.cancelled:
                raise ff.CancelledError("cancelled")
            if res.returncode != 0:
                ctx.task.log_tail = res.log_tail[-40:]
                raise RuntimeError("Speech recognition failed:\n" + "\n".join(res.log_tail[-6:]))
            for fmt_, final in zip(formats, outputs, strict=False):
                produced = f"{out_base}.{fmt_}"
                if not os.path.isfile(produced) or os.path.getsize(produced) == 0:
                    raise RuntimeError("No speech was recognised in this file.")
                shutil.copyfile(produced, ctx.output(final))
            return 0

        def on_complete(tid, rc):
            self.btn_render.config(text="🗣  GENERATE SUBTITLES", command=self._run, state="normal")
            self._fill_models(key)
            task = self.task(tid)
            self.last_task = task
            if rc == 0:
                self._last_subs = outputs[0]
                self._last_lang = "en" if translate else detected["lang"]
                if outputs[0].endswith((".srt", ".vtt")):
                    self.burn_btn.config(state="normal")
                    self.mux_btn.config(state="normal")
                self.log_tagged(self.console, "Saved: " + ", ".join(outputs), "success")
            self.show_result(rc, outputs[0])

        tid = self.enqueue_render(
            "Auto-Subtitles",
            outputs[0],
            work=work,
            expected_duration=duration,
            inputs=[src],
            hooks=Hooks(
                on_progress=lambda _t, line: self.log(self.console, line), on_complete=on_complete
            ),
        )
        self.btn_render.config(text=f"✖ {t('btn.cancel')}", command=lambda: self.cancel_render(tid))
        self.log(
            self.console,
            f"Transcribing with {whisper.MODELS[key].label} "
            f"({'auto language' if lang == 'auto' else lang})…",
        )

    # ── follow-ups ───────────────────────────────────────────────────────────

    def _burn(self):
        subs, src = self._last_subs, self.src_row.path
        if not subs or not os.path.isfile(subs):
            return
        from tabs.social.hard_subber import HardSubberTab

        page = self.goto_tool(ToolId.HARD_SUBBER)
        if isinstance(page, HardSubberTab):
            page.src_var.set(src)
            page.sub_row.set(subs)

    def _mux(self):
        subs, src = self._last_subs, self.src_row.path
        info = common.check_source(self, src)
        if info is None or not subs:
            return
        container = common.container_of(src)
        codec = SOFT_SUB_CODEC.get(container)
        ext = os.path.splitext(src)[1] if codec else ".mkv"
        codec = codec or "srt"
        out = self.confirm_output(
            self.suggest_output(src, "subtitled", ext), [src, subs], default_ext=ext
        )
        if not out:
            return
        lang = _iso639_2(getattr(self, "_last_lang", "") or "")
        n = len([st for st in info.streams if st.type == "subtitle"])  # new track goes after these
        cmd = [
            ff.ffmpeg_path(),
            "-y",
            "-i",
            src,
            "-i",
            subs,
            "-map",
            "0",
            "-map",
            "1:0",
            "-c",
            "copy",
            f"-c:s:{n}",
            codec,
            f"-metadata:s:s:{n}",
            f"language={lang}",
            f"-disposition:s:{n}",
            "default",
            out,
        ]
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.mux_btn,
            duration=info.duration or None,
            output=out,
            inputs=[src, subs],
            name="Add subtitle track",
        )


_ISO2_TO_3 = {
    "en": "eng",
    "es": "spa",
    "fr": "fra",
    "de": "deu",
    "it": "ita",
    "pt": "por",
    "nl": "nld",
    "pl": "pol",
    "ru": "rus",
    "uk": "ukr",
    "tr": "tur",
    "ar": "ara",
    "fa": "fas",
    "hi": "hin",
    "bn": "ben",
    "ur": "urd",
    "ja": "jpn",
    "ko": "kor",
    "zh": "zho",
    "vi": "vie",
    "th": "tha",
    "id": "ind",
    "ms": "msa",
    "tl": "tgl",
    "sw": "swa",
    "ro": "ron",
    "el": "ell",
    "cs": "ces",
    "sv": "swe",
    "da": "dan",
    "no": "nor",
    "fi": "fin",
    "hu": "hun",
    "he": "heb",
}


def _iso639_2(code: str) -> str:
    return _ISO2_TO_3.get(code, "und")
