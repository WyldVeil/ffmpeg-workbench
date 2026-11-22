"""
tabs/visuals/denoise.py  -  Video Denoiser

Remove sensor noise, grain and compression mush with a before/after view:

* Fast (hqdn3d) - quick spatial + temporal smoothing, fine for most clips.
* Best quality (non-local means) - keeps detail; on the GPU (Vulkan) when
  available, which is many times faster than the CPU version.
* Temporal (atadenoise) - averages over time; ideal for locked-off shots.
* Grain (fftdnoiz) - frequency-domain cleaning for film grain.
"""

from __future__ import annotations

import tkinter as tk
from functools import partial
from tkinter import ttk

from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.ops.denoise import denoise_chain
from core.ops.shared import RenderJob
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, BaseTab
from tabs.widgets import CompareView, FileRow, output_section, scroll_body

METHODS = {
    "fast": "Fast - spatial + temporal (hqdn3d)",
    "nlm": "Best quality - non-local means",
    "temporal": "Temporal - static / tripod shots (atadenoise)",
    "grain": "Film grain - frequency domain",
}
PRESETS = {"Light": 2.5, "Medium": 5.0, "Strong": 7.5, "Very strong": 10.0}


class DenoiseTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._job = None
        self._build_ui()

    def _preset(self, value):
        self.strength_var.set(value)
        self._changed()

    def _build_ui(self):
        self.make_header(
            self, t("tab.denoise"), "Clean up noisy, grainy or blocky video.", icon="✨"
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        self.compare = CompareView(body, 400, 225, after_label="Denoised")
        self.compare.pack(pady=px(4))
        opt = tk.LabelFrame(body, text="  Denoise  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.method_var = tk.StringVar(value=METHODS["fast"])
        mb = ttk.Combobox(
            opt,
            textvariable=self.method_var,
            values=list(METHODS.values()),
            state="readonly",
            width=44,
        )
        mb.grid(row=0, column=0, columnspan=3, sticky="w")
        mb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self.gpu_lbl = tk.Label(opt, text="", fg=CLR["fgdim"])
        self.gpu_lbl.grid(row=0, column=3, sticky="w", padx=px(8))
        self.strength_var = tk.DoubleVar(value=PRESETS["Medium"])
        tk.Scale(
            opt,
            variable=self.strength_var,
            from_=1,
            to=10,
            resolution=0.5,
            orient="horizontal",
            length=px(260),
            label="Strength",
            command=lambda _v: self._changed(),
        ).grid(row=1, column=0, sticky="w", pady=px(4))
        pf = tk.Frame(opt)
        pf.grid(row=1, column=1, columnspan=3, sticky="w")
        for name, val in PRESETS.items():
            tk.Button(
                pf,
                text=name,
                relief="flat",
                command=partial(self._preset, val),
            ).pack(side="left", padx=px(2))
        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9", "prores"),
            suggest=lambda: self.suggest_output(self.src_row.path, "denoised"),
        )
        self.btn_render = self.make_render_btn(body, "✨  DENOISE", self._render, width=22)
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)
        self._changed()

    def _method(self):
        return next((k for k, v in METHODS.items() if v == self.method_var.get()), "fast")

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        self.compare.load(path)
        self._changed()
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "denoised"))

    def _changed(self):
        nlm = self._method() == "nlm"
        self.gpu_lbl.config(
            text=("GPU (Vulkan) will be used" if fl.vulkan_ok() else "CPU - slow on long clips")
            if nlm
            else ""
        )
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(300, self._refresh)

    def _refresh(self):
        self._job = None
        self.compare.set_filter(denoise_chain(self._method(), float(self.strength_var.get())))

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        out = common.output_for(self, self.out_row, src, "denoised")
        if not out:
            return
        gpu = self._method() == "nlm" and fl.vulkan_ok()
        chain = denoise_chain(
            self._method(), float(self.strength_var.get()), gpu=gpu, ten_bit=self.encoder.ten_bit
        )
        common.render(
            self,
            RenderJob(
                src=src,
                info=info,
                out=out,
                chain=chain,
                name="Denoise",
                pre_input=ff.VULKAN_DEVICE if gpu else (),
            ),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
        )
