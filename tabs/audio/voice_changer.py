"""
tabs/audio/voice_changer.py  -  Voice Changer

Chipmunk, deep/anonymous, robot, radio, stadium echo and more - or any
pitch in semitones.  Pitch changes keep the speed (and optionally the
voice's formants) using Rubber Band when available, with the source's real
sample rate otherwise.  Works on audio files and on videos (picture kept).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops import voice as vc
from core.ops.shared import audio_filter_cmd
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import FileRow, save_section, scroll_body


class VoiceChangerTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info: ff.MediaInfo | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.make_header(self, t("tab.voice_changer"), t("voice_changer.subtitle"), icon="🎙️")
        body = scroll_body(self)
        self.src_row = FileRow(
            body,
            "Input:",
            self.src_var,
            kind="media",
            on_change=lambda p: setattr(self, "_info", ff.try_probe(p)),
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        opt = tk.LabelFrame(body, text="  Effect  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.preset_var = tk.StringVar(value=vc.PRESETS["chipmunk"][0])
        ttk.Combobox(
            opt,
            textvariable=self.preset_var,
            values=[v[0] for v in vc.PRESETS.values()],
            state="readonly",
            width=32,
        ).grid(row=0, column=0, sticky="w")
        self.semi_var = tk.DoubleVar(value=4)
        tk.Scale(
            opt,
            variable=self.semi_var,
            from_=-12,
            to=12,
            resolution=0.5,
            orient="horizontal",
            length=px(220),
            label="Custom pitch (semitones)",
        ).grid(row=1, column=0, sticky="w", pady=px(4))
        self.formant_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            opt,
            text="Keep the voice's character (formants) - sounds more natural",
            variable=self.formant_var,
        ).grid(row=2, column=0, sticky="w")
        tk.Button(
            opt, text="▶ Preview 12 s", relief="flat", cursor="hand2", command=self._preview
        ).grid(row=0, column=1, padx=px(12))
        self.out_row = save_section(
            body,
            t("common.output_file"),
            kind="media",
            suggest=lambda: self.suggest_output(self.src_row.path, "voice"),
        )
        out_lf = self.out_row.master
        tk.Label(
            out_lf,
            text="  ℹ  Save as .mp3/.wav/.flac/.m4a for audio only, or a video type to keep "
            "the picture.",
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w")
        self.btn_render = self.make_render_btn(
            body, t("voice_changer.apply_button"), self._render, width=22
        )
        self.btn_render.pack(pady=px(10))
        self.console = self.make_console_block(body, height=5)

    def chain(self, info: ff.MediaInfo | None) -> str:
        key = next((k for k, v in vc.PRESETS.items() if v[0] == self.preset_var.get()), "chipmunk")
        sr = info.require_audio().sample_rate if info is not None and info.audio else 48000
        return vc.voice_filter(
            key, float(self.semi_var.get()), sr, self.formant_var.get(), vc.rubberband_available()
        )

    def _preview(self) -> None:
        src = self.src_row.path
        info = ff.try_probe(src) if src else None
        common.preview_audio(
            self, src, self.chain(info), start=min(5.0, (info.duration / 3) if info else 0)
        )

    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src, need_video=False)
        if info is None:
            return
        if not info.has_audio:
            from tkinter import messagebox

            messagebox.showwarning(t("common.warning"), "This file has no audio.", parent=self)
            return
        out = common.output_for(self, self.out_row, src, "voice")
        if not out:
            return
        af = self.chain(info)
        cmd = audio_filter_cmd(src, af, out, info)
        self.log(self.console, f"Effect: {self.preset_var.get()}")
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=info.duration or None,
            output=out,
            inputs=[src],
            name="Voice Changer",
        )
