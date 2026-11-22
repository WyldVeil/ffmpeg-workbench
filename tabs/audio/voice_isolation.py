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
import tkinter as tk
from tkinter import messagebox, ttk

from core import capabilities
from core.i18n import t
from core.ops.shared import audio_filter_cmd
from core.ops.voice_isolation import denoise_filter, models_dir
from core.scaling import px
from tabs import common
from tabs.base_tab import BaseTab
from tabs.widgets import FileRow, save_section, scroll_body

MODELS = {  # file: label (signal / noise the model was trained for)
    "sh.rnnn": "Speech, recorded in a room - best for talking",
    "bd.rnnn": "Voice incl. laughter, recording noise",
    "lq.rnnn": "Voice, general background noise",
    "cb.rnnn": "Any sound, recording noise",
    "mp.rnnn": "Any sound, general background noise",
}
_CUSTOM = "Custom model (.rnnn file)…"


def anlmdn_works() -> bool:
    """anlmdn crashes (heap corruption) in some FFmpeg builds: it is tested once (core.capabilities)."""
    return bool(capabilities.get("anlmdn"))


class VoiceIsolationTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self, t("tab.voice_isolation"), "Remove background noise from speech.", icon="🎤"
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body,
            t("common.source_video"),
            self.src_var,
            kind="media",
            on_change=lambda p: (
                self.out_row.path or self.out_row.set(self.suggest_output(p, "clean"))
            ),
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        opt = self._build_methods(body)
        self._build_method_options(opt)
        self._build_output(body)

    def _build_methods(self, body):
        """Which method cleans the voice."""
        opt = tk.LabelFrame(body, text="  Method  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.method_var = tk.StringVar(value="neural")
        methods = [
            ("neural", "Neural network (RNNoise) - recommended"),
            ("fft", "Spectral noise reduction (FFT)"),
        ]
        methods.append(("nlm", "Non-local means"))
        radios = {
            key: tk.Radiobutton(opt, text=label, value=key, variable=self.method_var)
            for key, label in methods
        }
        for key, radio in radios.items():
            if key != "nlm" or capabilities.peek("anlmdn", False):
                radio.pack(anchor="w")
        if capabilities.peek("anlmdn") is None:  # not known yet: offer it once the self-test passes

            def _offer(ok: bool) -> None:
                if ok:
                    radios["nlm"].pack(anchor="w", after=radios["fft"])

            capabilities.on_ready("anlmdn", _offer)
        return opt

    def _build_method_options(self, opt):
        """The chosen method's settings and a preview."""
        mr = tk.Frame(opt)
        mr.pack(fill="x", pady=px(4))
        tk.Label(mr, text="Model:").pack(side="left")
        self.model_var = tk.StringVar(value=MODELS["sh.rnnn"])
        mb = ttk.Combobox(
            mr,
            textvariable=self.model_var,
            values=[*MODELS.values(), _CUSTOM],
            state="readonly",
            width=46,
        )
        mb.pack(side="left", padx=px(6))
        mb.bind("<<ComboboxSelected>>", lambda _e: self._model_changed())
        self.custom_row = FileRow(opt, "Custom model:", kind="any", label_width=14)
        self.blend_var = tk.DoubleVar(value=0.0)
        tk.Scale(
            opt,
            variable=self.blend_var,
            from_=0.0,
            to=0.8,
            resolution=0.05,
            orient="horizontal",
            length=px(220),
            label="Keep some original sound (more natural)",
        ).pack(anchor="w")
        fr = tk.Frame(opt)
        fr.pack(anchor="w", pady=px(2))
        self.floor_var, self.red_var = tk.StringVar(value="-30"), tk.StringVar(value="12")
        tk.Label(fr, text="FFT: noise floor (dB)").pack(side="left")
        tk.Entry(fr, textvariable=self.floor_var, width=5).pack(side="left", padx=px(4))
        tk.Label(fr, text="reduction (dB)").pack(side="left")
        tk.Entry(fr, textvariable=self.red_var, width=5).pack(side="left", padx=px(4))
        self.rumble_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            opt, text="Remove low rumble (80 Hz high-pass)", variable=self.rumble_var
        ).pack(anchor="w")
        tk.Button(opt, text="▶ Preview 12 s", relief="flat", command=self._preview).pack(
            anchor="w", pady=px(4)
        )

    def _build_output(self, body):
        """Where the result goes, the button and the log."""
        self.out_row = save_section(
            body,
            t("common.output_file"),
            kind="media",
            suggest=lambda: self.suggest_output(self.src_row.path, "clean"),
        )
        self.btn_render = self.make_render_btn(body, "🎤  CLEAN UP", self._render, width=20)
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

    def _model_changed(self):
        if self.model_var.get() == _CUSTOM:
            self.custom_row.pack(fill="x", pady=px(2))
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
        return denoise_filter(
            method,
            model=model,
            blend=float(self.blend_var.get()),
            floor_db=common.number(self.floor_var.get(), "Noise floor", -80, -20),
            reduction_db=common.number(self.red_var.get(), "Reduction", 0.01, 97),
            rumble=self.rumble_var.get(),
        )

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
        cmd = audio_filter_cmd(src, af, out, info)
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=info.duration or None,
            output=out,
            inputs=[src],
            name="Voice Isolation",
        )
