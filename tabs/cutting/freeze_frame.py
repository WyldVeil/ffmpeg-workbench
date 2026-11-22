"""
tabs/cutting/freeze_frame.py  -  Freeze Frame

Hold a frame: insert a freeze in the middle of a clip (picture pauses,
sound pauses with it or keeps playing), extend the end with the last frame,
or export just the frozen moment as a clip.  Optional flash, black-and-
white and caption while frozen - the classic "record scratch" moment.
"""

from __future__ import annotations

import os
import shutil
import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops.freeze_frame import Freeze, caption_script, freeze_graph
from core.ops.shared import RenderJob, encoded_audio
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.kit.time_fields import MomentPicker
from tabs.widgets import EncoderPanel, FileRow, FramePreview, SaveRow, scroll_body

MODES = [
    ("insert", "Insert a freeze", "Pause the picture at the chosen moment, then carry on."),
    ("end", "Freeze the ending", "Hold the last frame at the end of the clip."),
    ("only", "Freeze only", "Export just the frozen moment as a still clip."),
]
AUDIO = {
    "pause": "Sound pauses during the freeze",
    "continue": "Sound keeps playing",
    "mute": "No sound",
}


class FreezeFrameTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._build_ui()

    def _build_ui(self):
        body, opt = self._build_header()
        self._build_output(body, opt)
        self._build_render(body)

    def _build_header(self):
        self.make_header(
            self,
            t("tab.freeze_frame"),
            "Pause the picture on a moment - with an optional flash and caption.",
            icon="⏸",
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        pv = tk.Frame(body)
        pv.pack(pady=px(4))
        self.preview = FramePreview(pv, 480, 270)
        self.preview.pack()
        self.pos_var = tk.DoubleVar(value=0)
        self.slider = ttk.Scale(
            pv,
            from_=0,
            to=1,
            variable=self.pos_var,
            length=px(480),
            command=lambda _v: self._moved(),
        )
        self.slider.pack(fill="x")
        framestep.slider(self.slider, self.preview)  # , and . step a frame

        opt = tk.LabelFrame(body, text="  Freeze  ", padx=px(12), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.mode_var = tk.StringVar(value="insert")
        for key, label, desc in MODES:
            row = tk.Frame(opt)
            row.pack(fill="x")
            tk.Radiobutton(
                row, text=label, value=key, variable=self.mode_var, width=18, anchor="w"
            ).pack(side="left")
            tk.Label(row, text=desc, fg=CLR["fgdim"], font=(UI_FONT, 9)).pack(side="left")
        return body, opt

    def _build_output(self, body, opt):
        r = tk.Frame(opt)
        r.pack(fill="x", pady=px(6, 2))
        tk.Label(r, text="At:").pack(side="left")
        self.at = MomentPicker(r, value="0", width=12)
        self.at.pack(side="left", padx=px(4))
        tk.Label(r, text="(follows the slider)   Hold for (s):").pack(side="left")
        self.hold_var = tk.StringVar(value="2")
        tk.Entry(r, textvariable=self.hold_var, width=6).pack(side="left", padx=px(4))
        r2 = tk.Frame(opt)
        r2.pack(fill="x", pady=px(2))
        self.audio_var = tk.StringVar(value=AUDIO["pause"])
        ttk.Combobox(
            r2, textvariable=self.audio_var, values=list(AUDIO.values()), state="readonly", width=32
        ).pack(side="left")
        self.flash_var = tk.BooleanVar(value=False)
        self.mono_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r2, text="White flash", variable=self.flash_var).pack(
            side="left", padx=px(8)
        )
        tk.Checkbutton(r2, text="Black & white while frozen", variable=self.mono_var).pack(
            side="left"
        )
        r3 = tk.Frame(opt)
        r3.pack(fill="x", pady=px(2))
        tk.Label(r3, text="Caption while frozen:").pack(side="left")
        self.caption_var = tk.StringVar()
        tk.Entry(r3, textvariable=self.caption_var, width=40).pack(side="left", padx=px(4))

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9"))
        self.encoder.pack(anchor="w")
        self.out_row = SaveRow(
            out_lf,
            t("common.output_file"),
            suggest=lambda: self.suggest_output(self.src_row.path, "freeze"),
        )

    def _build_render(self, body):
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = self.make_render_btn(body, "⏸  CREATE", self._render, width=22)
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        dur = self._info.duration if self._info else 0.0
        self.slider.configure(to=max(dur, 0.01))
        self.pos_var.set(dur / 2)
        self.preview.load(path, dur, dur / 2)
        self._moved()
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "freeze"))

    def _moved(self):
        pos = float(self.pos_var.get())
        self.at.set(pos)
        self.preview.show(pos)

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        dur = info.duration
        try:
            at = self.at.seconds(0.0, "At") or 0.0
            hold = common.number(self.hold_var.get(), "Hold for", 0.04, 600)
            if not 0 <= at < dur:
                raise ValueError(
                    f"The freeze time must be inside the clip (0 - {ff.format_time(dur)})."
                )
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "freeze")
        if not out:
            return
        mode = self.mode_var.get()
        work_dir = None
        caption_ass = None
        if self.caption_var.get().strip():
            from core.paths import temp_dir

            work_dir = temp_dir("freeze_")
            start = 0.0 if mode == "only" else (dur if mode == "end" else at)
            sc = caption_script(self.caption_var.get(), common.even_size(info), start, hold)
            caption_ass = sc.write(os.path.join(work_dir, "caption.ass"))
        audio_mode = next((k for k, v in AUDIO.items() if v == self.audio_var.get()), "pause")
        freeze = Freeze(
            mode=mode,
            at=at,
            hold=hold,
            audio_mode=audio_mode,
            flash=self.flash_var.get(),
            mono=self.mono_var.get(),
            caption_ass=caption_ass,
        )
        graph, total, audio_out = freeze_graph(freeze, dur, has_audio=info.has_audio)
        container = common.container_of(out)
        audio = encoded_audio(container, audio_out)

        def done(rc):
            if work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)
            self.show_result(rc, out)

        common.render(
            self,
            RenderJob(
                src=src,
                info=info,
                out=out,
                graph=graph,
                name="Freeze Frame",
                audio=audio,
                duration=total,
            ),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
            on_done=done,
        )
