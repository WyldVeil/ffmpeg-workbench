"""
tabs/audio/voice_isolation.py  -  Voice Isolation

Remove background noise from speech.

* Neural (RNNoise / arnndn): five bundled models for different kinds of
  signal and noise (assets/models/rnnoise - not subject to copyright), or
  your own .rnnn file.  Blend control keeps some of the original.
* Spectral (afftdn): classic FFT noise reduction with a noise floor.
* Non-local means (anlmdn): only offered when it passes a self-test - the
  filter crashes in some FFmpeg builds (9.0.2 included).

Optional rumble filter, preview, audio-only or video output.
"""
from __future__ import annotations

import os
import subprocess
import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.paths import resource_path
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import FileRow, scroll_body

MODELS = {   # file: label (signal / noise the model was trained for)
    "sh.rnnn": "Speech, recorded in a room - best for talking",
    "bd.rnnn": "Voice incl. laughter, recording noise",
    "lq.rnnn": "Voice, general background noise",
    "cb.rnnn": "Any sound, recording noise",
    "mp.rnnn": "Any sound, general background noise",
}
_CUSTOM = "Custom model (.rnnn file)…"
_ANLMDN_OK = None


def models_dir() -> str:
    return resource_path("assets", "models", "rnnoise")


def anlmdn_works() -> bool:
    """anlmdn crashes (heap corruption) in some FFmpeg builds: test it once."""
    global _ANLMDN_OK
    if _ANLMDN_OK is None:
        try:
            r = ff.capture([ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "lavfi",
                            "-i", "anoisesrc=d=1:c=pink", "-af", "anlmdn", "-f", "null", "-"], timeout=30)
            _ANLMDN_OK = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            _ANLMDN_OK = False
    return _ANLMDN_OK


def denoise_filter(method: str, *, model: str = "", blend: float = 0.0, floor_db: float = -30,
                   reduction_db: float = 12, strength: float = 0.00015, rumble: bool = True) -> str:
    parts = ["highpass=f=80"] if rumble else []
    if method == "neural":
        parts.append(f"arnndn=model={fl.filter_path(model)}:mix={1.0 - blend:.2f}")
    elif method == "nlm":
        parts.append(f"anlmdn=s={strength:g}")
    else:
        parts.append(f"afftdn=nf={floor_db:g}:nr={reduction_db:g}:tn=1")
    return ",".join(parts)


class VoiceIsolationTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.voice_isolation"), "Remove background noise from speech.", icon="🎤")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), self.src_var, kind="media",
                               on_change=lambda p: self.out_row.path or self.out_row.set(
                                   self.suggest_output(p, "clean")))
        self.src_row.pack(fill="x", padx=20, pady=(10, 4))
        opt = tk.LabelFrame(body, text="  Method  ", padx=12, pady=8)
        opt.pack(fill="x", padx=20, pady=4)
        self.method_var = tk.StringVar(value="neural")
        methods = [("neural", "Neural network (RNNoise) - recommended"), ("fft", "Spectral noise reduction (FFT)")]
        if anlmdn_works():
            methods.append(("nlm", "Non-local means"))
        for key, label in methods:
            tk.Radiobutton(opt, text=label, value=key, variable=self.method_var).pack(anchor="w")
        mr = tk.Frame(opt)
        mr.pack(fill="x", pady=4)
        tk.Label(mr, text="Model:").pack(side="left")
        self.model_var = tk.StringVar(value=MODELS["sh.rnnn"])
        mb = ttk.Combobox(mr, textvariable=self.model_var, values=[*MODELS.values(), _CUSTOM], state="readonly",
                          width=46)
        mb.pack(side="left", padx=6)
        mb.bind("<<ComboboxSelected>>", lambda _e: self._model_changed())
        self.custom_row = FileRow(opt, "Custom model:", kind="any", label_width=14)
        self.blend_var = tk.DoubleVar(value=0.0)
        tk.Scale(opt, variable=self.blend_var, from_=0.0, to=0.8, resolution=0.05, orient="horizontal",
                 length=220, label="Keep some original sound (more natural)").pack(anchor="w")
        fr = tk.Frame(opt)
        fr.pack(anchor="w", pady=2)
        self.floor_var, self.red_var = tk.StringVar(value="-30"), tk.StringVar(value="12")
        tk.Label(fr, text="FFT: noise floor (dB)").pack(side="left")
        tk.Entry(fr, textvariable=self.floor_var, width=5).pack(side="left", padx=4)
        tk.Label(fr, text="reduction (dB)").pack(side="left")
        tk.Entry(fr, textvariable=self.red_var, width=5).pack(side="left", padx=4)
        self.rumble_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opt, text="Remove low rumble (80 Hz high-pass)", variable=self.rumble_var).pack(anchor="w")
        tk.Button(opt, text="▶ Preview 12 s", relief="flat", command=self._preview).pack(anchor="w", pady=4)
        out_lf = tk.LabelFrame(body, text="  Output  ", padx=12, pady=8)
        out_lf.pack(fill="x", padx=20, pady=6)
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True, kind="media",
                               suggest=lambda: self.suggest_output(self.src_row.path, "clean"))
        self.out_row.pack(fill="x")
        self.btn_render = tk.Button(body, text="🎤  CLEAN UP", font=(UI_FONT, 12, "bold"), bg=CLR["green"],
                                    fg="white", width=20, command=self._render)
        self.btn_render.pack(pady=8)
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def _model_changed(self):
        if self.model_var.get() == _CUSTOM:
            self.custom_row.pack(fill="x", pady=2)
        else:
            self.custom_row.pack_forget()

    def _filter(self) -> str:
        method = self.method_var.get()
        model = ""
        if method == "neural":
            if self.model_var.get() == _CUSTOM:
                model = self.custom_row.path
                if not model or not os.path.isfile(model):
                    raise ValueError("Choose a custom .rnnn model file.")
            else:
                name = next((k for k, v in MODELS.items() if v == self.model_var.get()), "sh.rnnn")
                model = os.path.join(models_dir(), name)
                if not os.path.isfile(model):
                    raise ValueError(f"The bundled model is missing: {model}")
        return denoise_filter(method, model=model, blend=float(self.blend_var.get()),
                              floor_db=common.number(self.floor_var.get(), "Noise floor", -80, -20),
                              reduction_db=common.number(self.red_var.get(), "Reduction", 0.01, 97),
                              rumble=self.rumble_var.get())

    def _preview(self):
        try:
            af = self._filter()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        common.preview_audio(self, self.src_row.path, af)

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src, need_video=False)
        if info is None:
            return
        if not info.has_audio:
            messagebox.showwarning(t("common.warning"), "This file has no audio.", parent=self)
            return
        try:
            af = self._filter()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "clean")
        if not out:
            return
        cmd = [ff.ffmpeg_path(), "-y", "-i", src, "-af", af, *common.audio_job_args(out, info), out]
        self.run_ffmpeg(cmd, self.console, on_done=lambda rc: self.show_result(rc, out), btn=self.btn_render,
                        btn_label="🎤  CLEAN UP", duration=info.duration or None, output=out, inputs=[src],
                        name="Voice Isolation")
