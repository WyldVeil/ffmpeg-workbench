"""
tabs/audio/replacer.py  -  Audio Replacer

Swap or mix a video's soundtrack with another audio file (music, a
voice-over, a cleaned recording).  The video always keeps its full length:
the new audio is offset (positive or negative), optionally looped and faded,
and padded or trimmed to the picture.  Mixing keeps both at the levels you
choose (no automatic halving) with a limiter against clipping.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops import audio_replace as ar
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import FileRow, SaveRow, scroll_body

MODES = {
    "replace": "Replace the soundtrack",
    "mix": "Mix with the original sound",
    "strip": "Remove all sound",
}


class AudioReplacerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self, t("tab.audio_replacer"), "Replace or mix a video's soundtrack.", icon="🎼"
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body,
            "Video:",
            self.src_var,
            on_change=lambda p: (
                self.out_row.path or self.out_row.set(self.suggest_output(p, "new_audio"))
            ),
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.audio_row = FileRow(body, "New audio:", kind="media")
        self.audio_row.pack(fill="x", padx=px(20), pady=px(2))
        opt = tk.LabelFrame(body, text="  Options  ", padx=px(12), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.mode_var = tk.StringVar(value=MODES["replace"])
        ttk.Combobox(
            opt, textvariable=self.mode_var, values=list(MODES.values()), state="readonly", width=30
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        self.new_vol, self.orig_vol = tk.DoubleVar(value=1.0), tk.DoubleVar(value=0.35)
        tk.Scale(
            opt,
            variable=self.new_vol,
            from_=0,
            to=2,
            resolution=0.05,
            orient="horizontal",
            length=px(180),
            label="New audio volume",
        ).grid(row=1, column=0, sticky="w")
        tk.Scale(
            opt,
            variable=self.orig_vol,
            from_=0,
            to=2,
            resolution=0.05,
            orient="horizontal",
            length=px(180),
            label="Original volume (mix)",
        ).grid(row=1, column=1, sticky="w")
        r = tk.Frame(opt)
        r.grid(row=2, column=0, columnspan=2, sticky="w", pady=px(4))
        tk.Label(r, text="Start the new audio at (s, negative skips its start):").pack(side="left")
        self.offset_var = tk.StringVar(value="0")
        tk.Entry(r, textvariable=self.offset_var, width=8).pack(side="left", padx=px(4))
        self.loop_var, self.fade_var = tk.BooleanVar(value=False), tk.BooleanVar(value=True)
        tk.Checkbutton(
            opt, text="Loop the new audio if it's shorter than the video", variable=self.loop_var
        ).grid(row=3, column=0, columnspan=2, sticky="w")
        tk.Checkbutton(opt, text="Fade in and out (1 s)", variable=self.fade_var).grid(
            row=4, column=0, sticky="w"
        )
        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.out_row = SaveRow(
            out_lf,
            t("common.output_file"),
            suggest=lambda: self.suggest_output(self.src_row.path, "new_audio"),
        )
        self.out_row.pack(fill="x")
        self.btn_render = tk.Button(
            body,
            text="🎼  APPLY",
            font=(UI_FONT, 12, "bold"),
            bg=CLR["green"],
            fg="white",
            width=20,
            command=self._render,
        )
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        mode = next((k for k, v in MODES.items() if v == self.mode_var.get()), "replace")
        out = common.output_for(self, self.out_row, src, "new_audio", inputs=[self.audio_row.path])
        if not out:
            return
        if mode == "strip":
            cmd = ar.strip_cmd(src, out)
        else:
            ainfo = common.check_source(
                self, self.audio_row.path, need_video=False, what="New audio"
            )
            if ainfo is None:
                return
            try:
                offset = ff.parse_time(self.offset_var.get().lstrip("-") or "0") * (
                    -1 if self.offset_var.get().strip().startswith("-") else 1
                )
            except ValueError as exc:
                messagebox.showwarning(t("common.warning"), str(exc), parent=self)
                return
            r = ar.Replacement(
                mix=mode == "mix",
                offset=offset,
                new_volume=float(self.new_vol.get()),
                original_volume=float(self.orig_vol.get()),
                loop=self.loop_var.get(),
                fade=1.0 if self.fade_var.get() else 0.0,
            )
            cmd = ar.replace_cmd(src, self.audio_row.path, out, info, r)
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=info.duration or None,
            output=out,
            inputs=[src, self.audio_row.path],
            name="Audio Replacer",
        )
