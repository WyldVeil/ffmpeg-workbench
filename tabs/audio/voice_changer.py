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
from core.ops.shared import audio_job_args
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import FileRow, scroll_body

PRESETS = {      # id: (label, semitones, extra filters)
    "chipmunk": ("Chipmunk (high pitch)", 6, ""),
    "deep": ("Deep / anonymous (low pitch)", -5, ""),
    "demon": ("Demon", -8, "aecho=0.8:0.85:60:0.35"),
    "robot": ("Robot", 0, "afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':"
                          "win_size=512:overlap=0.75,flanger=delay=3:depth=2:regen=40"),
    "radio": ("Old radio", 0, "highpass=f=300,lowpass=f=3400,acompressor=threshold=0.1:ratio=6,"
                              "volume=1.5"),
    "phone": ("Telephone", 0, "highpass=f=400,lowpass=f=3000,aformat=sample_rates=8000,aresample=48000"),
    "stadium": ("Stadium echo", 0, "aecho=0.8:0.9:1000|1800:0.3|0.2"),
    "cave": ("Cave reverb", 0, "aecho=0.8:0.88:60|120|200:0.4|0.3|0.2"),
    "whisper": ("Whisper", 0, "afftfilt=real='hypot(re,im)*cos((random(0)*2-1)*2*3.14)':"
                              "imag='hypot(re,im)*sin((random(1)*2-1)*2*3.14)':win_size=128:overlap=0.8,"
                              "highpass=f=500"),
    "custom": ("Custom pitch only", None, ""),
}


def voice_filter(preset: str, semitones: float, sample_rate: int, keep_formants: bool,
                 rubberband: bool) -> str:
    """The -af chain for a preset (semitones used for 'custom' and pitched presets)."""
    _label, default_st, extra = PRESETS[preset]
    st = semitones if default_st is None else default_st
    parts = []
    if abs(st) > 0.01:
        k = 2 ** (st / 12)
        if rubberband:
            parts.append(f"rubberband=pitch={k:.5f}" + (":formant=preserved" if keep_formants else ""))
        else:
            sr = sample_rate or 48000
            parts += [f"asetrate={round(sr * k)}", f"aresample={sr}", f"atempo={1 / k:.5f}"]
    if extra:
        parts.append(extra)
    return ",".join(parts) or "anull"


class VoiceChangerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.voice_changer"), t("voice_changer.subtitle"), icon="🎙️")
        body = scroll_body(self)
        self.src_row = FileRow(body, "Input:", self.src_var, kind="media",
                               on_change=lambda p: setattr(self, "_info", ff.try_probe(p)))
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        opt = tk.LabelFrame(body, text="  Effect  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.preset_var = tk.StringVar(value=PRESETS["chipmunk"][0])
        ttk.Combobox(opt, textvariable=self.preset_var, values=[v[0] for v in PRESETS.values()],
                     state="readonly", width=32).grid(row=0, column=0, sticky="w")
        self.semi_var = tk.DoubleVar(value=4)
        tk.Scale(opt, variable=self.semi_var, from_=-12, to=12, resolution=0.5, orient="horizontal",
                 length=px(220), label="Custom pitch (semitones)").grid(row=1, column=0, sticky="w", pady=px(4))
        self.formant_var = tk.BooleanVar(value=False)
        tk.Checkbutton(opt, text="Keep the voice's character (formants) - sounds more natural",
                       variable=self.formant_var).grid(row=2, column=0, sticky="w")
        tk.Button(opt, text="▶ Preview 12 s", relief="flat", cursor="hand2",
                  command=self._preview).grid(row=0, column=1, padx=px(12))
        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True, kind="media",
                               suggest=lambda: self.suggest_output(self.src_row.path, "voice"))
        self.out_row.pack(fill="x")
        tk.Label(out_lf, text="  ℹ  Save as .mp3/.wav/.flac/.m4a for audio only, or a video type to keep "
                              "the picture.", fg=CLR["fgdim"], font=(UI_FONT, 8)).pack(anchor="w")
        self.btn_render = tk.Button(body, text=t("voice_changer.apply_button"), font=(UI_FONT, 11, "bold"),
                                    bg=CLR["green"], fg="white", width=22, command=self._render)
        self.btn_render.pack(pady=px(10))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def chain(self, info) -> str:
        key = next((k for k, v in PRESETS.items() if v[0] == self.preset_var.get()), "chipmunk")
        sr = info.require_audio().sample_rate if info is not None and info.audio else 48000
        return voice_filter(key, float(self.semi_var.get()), sr, self.formant_var.get(),
                            ff.has_filter("rubberband"))

    def _preview(self):
        src = self.src_row.path
        info = ff.try_probe(src) if src else None
        common.preview_audio(self, src, self.chain(info), start=min(5.0, (info.duration / 3) if info else 0))

    def _render(self):
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
        cmd = [ff.ffmpeg_path(), "-y", "-i", src, "-af", af, *audio_job_args(out, info), out]
        self.log(self.console, f"Effect: {self.preset_var.get()}")
        self.run_ffmpeg(cmd, self.console, on_done=lambda rc: self.show_result(rc, out), btn=self.btn_render,
                        btn_label=t("voice_changer.apply_button"), duration=info.duration or None,
                        output=out, inputs=[src], name="Voice Changer")
