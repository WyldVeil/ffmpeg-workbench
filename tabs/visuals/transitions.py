"""
tabs/visuals/transitions.py  -  Transition Studio

One cut between two clips, done with care: pick any of FFmpeg's 50+
transitions by family, its length and the audio crossfade curve, preview
just the seconds around the cut, then render.  Clips of different sizes,
frame rates, formats or without sound are normalised automatically (the
same engine as the Crossfader, which joins many clips at once).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import tasks
from core.hardware import launch_preview
from core.i18n import t
from core.ops import crossfader as xf
from core.scaling import px
from tabs import common
from tabs.base_tab import BaseTab
from tabs.kit.tktyped import selected_rows
from tabs.widgets import FileRow, output_section, scroll_body

FAMILIES = {
    "Dissolves": [
        ("fade", "Fade"),
        ("dissolve", "Dissolve"),
        ("fadeblack", "Through black"),
        ("fadewhite", "Through white"),
        ("fadegrays", "Through grey"),
        ("fadefast", "Fast fade"),
        ("fadeslow", "Slow fade"),
    ],
    "Wipes": [
        ("wipeleft", "Wipe left"),
        ("wiperight", "Wipe right"),
        ("wipeup", "Wipe up"),
        ("wipedown", "Wipe down"),
        ("wipetl", "Wipe to top-left"),
        ("wipetr", "Wipe to top-right"),
        ("wipebl", "Wipe to bottom-left"),
        ("wipebr", "Wipe to bottom-right"),
        ("radial", "Clock wipe"),
    ],
    "Slides & pushes": [
        ("slideleft", "Slide left"),
        ("slideright", "Slide right"),
        ("slideup", "Slide up"),
        ("slidedown", "Slide down"),
        ("coverleft", "Cover left"),
        ("coverright", "Cover right"),
        ("revealleft", "Reveal left"),
        ("revealright", "Reveal right"),
    ],
    "Shapes": [
        ("circleopen", "Circle open"),
        ("circleclose", "Circle close"),
        ("circlecrop", "Circle crop"),
        ("rectcrop", "Rectangle crop"),
        ("vertopen", "Doors open (vertical)"),
        ("horzopen", "Doors open (horizontal)"),
        ("diagtl", "Diagonal"),
    ],
    "Stylised": [
        ("smoothleft", "Smooth left"),
        ("smoothright", "Smooth right"),
        ("pixelize", "Pixelate"),
        ("hblur", "Blur"),
        ("zoomin", "Zoom in"),
        ("distance", "Distance"),
        ("squeezeh", "Squeeze"),
        ("hlslice", "Slices"),
        ("hlwind", "Wind"),
    ],
}
CURVES = {
    "tri": "Linear",
    "qsin": "Smooth (quarter sine)",
    "hsin": "Smooth (half sine)",
    "exp": "Exponential",
    "log": "Logarithmic",
}


class TransitionStudioTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._preview_dir = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.make_header(
            self,
            t("tab.transition_studio"),
            "One polished transition between two clips.",
            icon="🎬",
        )
        body = scroll_body(self)
        self.a_row = FileRow(
            body,
            "First clip (A):",
            self.src_var,
            label_width=16,
            on_change=lambda p: (
                self.out_row.path or self.out_row.set(self.suggest_output(p, "transition"))
            ),
        )
        self.a_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.src_row = self.a_row
        self.b_row = FileRow(body, "Second clip (B):", label_width=16)
        self.b_row.pack(fill="x", padx=px(20), pady=px(2))
        self._build_transition_options(body)
        self._build_output(body)
        self._family_changed()

    def _build_transition_options(self, body: tk.Frame) -> None:
        """Which transition, its length, the sound under it, and a preview."""
        opt = tk.LabelFrame(body, text="  Transition  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(6))
        top = tk.Frame(opt)
        top.pack(fill="x")
        self.family_var = tk.StringVar(value="Dissolves")
        fb = ttk.Combobox(
            top, textvariable=self.family_var, values=list(FAMILIES), state="readonly", width=16
        )
        fb.pack(side="left")
        fb.bind("<<ComboboxSelected>>", lambda _e: self._family_changed())
        self.trans_list = tk.Listbox(opt, height=6, exportselection=False)
        self.trans_list.pack(fill="x", pady=px(4))
        r = tk.Frame(opt)
        r.pack(fill="x", pady=px(2))
        tk.Label(r, text="Length (s):").pack(side="left")
        self.dur_var = tk.StringVar(value="1.0")
        ttk.Combobox(
            r,
            textvariable=self.dur_var,
            values=["0.3", "0.5", "0.75", "1.0", "1.5", "2.0", "3.0"],
            width=6,
        ).pack(side="left", padx=px(4))
        self.freeze_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r,
            text="Hold the end of A / start of B (don't overlap the footage)",
            variable=self.freeze_var,
        ).pack(side="left", padx=px(10))
        r2 = tk.Frame(opt)
        r2.pack(fill="x", pady=px(2))
        tk.Label(r2, text="Sound:").pack(side="left")
        self.audio_var = tk.StringVar(value="normal")
        for key, label in (("normal", "crossfade"), ("none", "no sound")):
            tk.Radiobutton(r2, text=label, variable=self.audio_var, value=key).pack(side="left")
        tk.Label(r2, text="  curve:").pack(side="left")
        self.curve_var = tk.StringVar(value=CURVES["tri"])
        ttk.Combobox(
            r2,
            textvariable=self.curve_var,
            values=list(CURVES.values()),
            state="readonly",
            width=22,
        ).pack(side="left", padx=px(4))
        self.btn_preview = tk.Button(
            opt, text="▶  Preview the cut", relief="flat", command=self._preview
        )
        self.btn_preview.pack(anchor="w", pady=px(4))

    def _build_output(self, body: tk.Frame) -> None:
        """Encoder, where the result goes, the button and the log."""
        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9"),
            suggest=lambda: self.suggest_output(self.a_row.path, "transition"),
        )
        self.btn_render = self.make_render_btn(
            body, "🎬  RENDER TRANSITION", self._render, width=24
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

    def _family_changed(self) -> None:
        self.trans_list.delete(0, "end")
        for _key, label in FAMILIES[self.family_var.get()]:
            self.trans_list.insert("end", label)
        self.trans_list.selection_set(0)

    def _transition(self) -> str:
        items = FAMILIES[self.family_var.get()]
        sel = selected_rows(self.trans_list)
        return items[sel[0] if sel else 0][0]

    def _settings(self) -> tuple[list[xf.Clip], float, str]:
        a, b = self.a_row.path, self.b_row.path
        for path, what in ((a, "First clip"), (b, "Second clip")):
            if not path or not os.path.isfile(path):
                raise ValueError(f"{what}: choose a video file.")
        T = common.number(self.dur_var.get(), "Length", 0.04, 30)
        clips = [xf.Clip.from_info(ff.probe(a)), xf.Clip.from_info(ff.probe(b))]
        problem = xf.validate(clips, T, self.freeze_var.get())
        if problem:
            raise ValueError(problem)
        curve = next((k for k, v in CURVES.items() if v == self.curve_var.get()), "tri")
        return clips, T, curve

    # ── Preview: only the seconds around the cut, small and fast ───────
    def _preview(self) -> None:
        try:
            clips, T, curve = self._settings()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        if self._preview_dir:
            shutil.rmtree(self._preview_dir, ignore_errors=True)
        self._preview_dir = tmp = tempfile.mkdtemp(prefix="wme_transition_")
        trans, freeze, audio = self._transition(), self.freeze_var.get(), self.audio_var.get()
        self.btn_preview.config(state="disabled", text="Making the preview…")

        def make() -> str:
            fade = xf.Crossfade(T, [trans], freeze, audio, curve)
            return xf.preview_cut(clips, fade, tmp)

        def done(path: str) -> None:
            self.btn_preview.config(state="normal", text="▶  Preview the cut")
            launch_preview(path)

        def failed(exc: BaseException) -> None:
            self.btn_preview.config(state="normal", text="▶  Preview the cut")
            messagebox.showerror(
                t("common.error"), f"The preview could not be made:\n{exc}", parent=self
            )

        tasks.submit(make, on_done=done, on_error=failed, name="TransitionPreview")

    def destroy(self) -> None:
        if self._preview_dir:
            shutil.rmtree(self._preview_dir, ignore_errors=True)
        super().destroy()

    # ── Render ────────────────────────────────────────────────────────
    def _render(self) -> None:
        try:
            clips, T, curve = self._settings()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(
            self, self.out_row, clips[0].path, "transition", inputs=[clips[1].path]
        )
        if not out:
            return
        venc = self.encoder.video_encode(common.container_of(out))
        fade = xf.Crossfade(
            T, [self._transition()], self.freeze_var.get(), self.audio_var.get(), curve
        )
        argv, secs = xf.build_command(clips, out, fade, venc=venc)
        self.log(self.console, f"{self._transition()} · {T:g} s · {ff.format_time(secs)} total")
        self.run_ffmpeg(
            argv,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=secs,
            output=out,
            inputs=[c.path for c in clips],
            name="Transition Studio",
        )
