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
from core import ui
from core.hardware import launch_preview
from core.i18n import t
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.cutting import crossfader as xf
from tabs.widgets import EncoderPanel, FileRow, scroll_body

FAMILIES = {
    "Dissolves": [("fade", "Fade"), ("dissolve", "Dissolve"), ("fadeblack", "Through black"),
                  ("fadewhite", "Through white"), ("fadegrays", "Through grey"), ("fadefast", "Fast fade"),
                  ("fadeslow", "Slow fade")],
    "Wipes": [("wipeleft", "Wipe left"), ("wiperight", "Wipe right"), ("wipeup", "Wipe up"),
              ("wipedown", "Wipe down"), ("wipetl", "Wipe to top-left"), ("wipetr", "Wipe to top-right"),
              ("wipebl", "Wipe to bottom-left"), ("wipebr", "Wipe to bottom-right"), ("radial", "Clock wipe")],
    "Slides & pushes": [("slideleft", "Slide left"), ("slideright", "Slide right"), ("slideup", "Slide up"),
                        ("slidedown", "Slide down"), ("coverleft", "Cover left"), ("coverright", "Cover right"),
                        ("revealleft", "Reveal left"), ("revealright", "Reveal right")],
    "Shapes": [("circleopen", "Circle open"), ("circleclose", "Circle close"), ("circlecrop", "Circle crop"),
               ("rectcrop", "Rectangle crop"), ("vertopen", "Doors open (vertical)"),
               ("horzopen", "Doors open (horizontal)"), ("diagtl", "Diagonal")],
    "Stylised": [("smoothleft", "Smooth left"), ("smoothright", "Smooth right"), ("pixelize", "Pixelate"),
                 ("hblur", "Blur"), ("zoomin", "Zoom in"), ("distance", "Distance"), ("squeezeh", "Squeeze"),
                 ("hlslice", "Slices"), ("hlwind", "Wind")],
}
CURVES = {"tri": "Linear", "qsin": "Smooth (quarter sine)", "hsin": "Smooth (half sine)", "exp": "Exponential",
          "log": "Logarithmic"}


class TransitionStudioTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._preview_dir = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.transition_studio"), "One polished transition between two clips.", icon="🎬")
        body = scroll_body(self)
        self.a_row = FileRow(body, "First clip (A):", self.src_var, label_width=16,
                             on_change=lambda p: self.out_row.path or self.out_row.set(
                                 self.suggest_output(p, "transition")))
        self.a_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.src_row = self.a_row
        self.b_row = FileRow(body, "Second clip (B):", label_width=16)
        self.b_row.pack(fill="x", padx=px(20), pady=px(2))

        opt = tk.LabelFrame(body, text="  Transition  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(6))
        top = tk.Frame(opt)
        top.pack(fill="x")
        self.family_var = tk.StringVar(value="Dissolves")
        fb = ttk.Combobox(top, textvariable=self.family_var, values=list(FAMILIES), state="readonly", width=16)
        fb.pack(side="left")
        fb.bind("<<ComboboxSelected>>", lambda _e: self._family_changed())
        self.trans_list = tk.Listbox(opt, height=6, exportselection=False)
        self.trans_list.pack(fill="x", pady=px(4))
        r = tk.Frame(opt)
        r.pack(fill="x", pady=px(2))
        tk.Label(r, text="Length (s):").pack(side="left")
        self.dur_var = tk.StringVar(value="1.0")
        ttk.Combobox(r, textvariable=self.dur_var, values=["0.3", "0.5", "0.75", "1.0", "1.5", "2.0", "3.0"],
                     width=6).pack(side="left", padx=px(4))
        self.freeze_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r, text="Hold the end of A / start of B (don't overlap the footage)",
                       variable=self.freeze_var).pack(side="left", padx=px(10))
        r2 = tk.Frame(opt)
        r2.pack(fill="x", pady=px(2))
        tk.Label(r2, text="Sound:").pack(side="left")
        self.audio_var = tk.StringVar(value="normal")
        for key, label in (("normal", "crossfade"), ("none", "no sound")):
            tk.Radiobutton(r2, text=label, variable=self.audio_var, value=key).pack(side="left")
        tk.Label(r2, text="  curve:").pack(side="left")
        self.curve_var = tk.StringVar(value=CURVES["tri"])
        ttk.Combobox(r2, textvariable=self.curve_var, values=list(CURVES.values()), state="readonly",
                     width=22).pack(side="left", padx=px(4))
        self.btn_preview = tk.Button(opt, text="▶  Preview the cut", relief="flat", command=self._preview)
        self.btn_preview.pack(anchor="w", pady=px(4))

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.a_row.path, "transition"))
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(body, text="🎬  RENDER TRANSITION", font=(UI_FONT, 12, "bold"),
                                    bg=CLR["green"], fg="white", width=24, cursor="hand2", command=self._render)
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self._family_changed()

    def _family_changed(self):
        self.trans_list.delete(0, "end")
        for _key, label in FAMILIES[self.family_var.get()]:
            self.trans_list.insert("end", label)
        self.trans_list.selection_set(0)

    def _transition(self) -> str:
        items = FAMILIES[self.family_var.get()]
        sel = self.trans_list.curselection()
        return items[sel[0] if sel else 0][0]

    def _settings(self):
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
    def _preview(self):
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

        def make():
            a, b = clips
            ctx_len = T + 1.5
            parts = []
            for i, (c, start) in enumerate(((a, max(0.0, a.duration - ctx_len)), (b, 0.0))):
                piece = os.path.join(tmp, f"{i}.mp4")
                res = ff.run([ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-ss", f"{start:.3f}",
                              "-t", f"{ctx_len:.3f}", "-i", c.path, "-vf", "scale=-2:360", "-c:v", "libx264",
                              "-preset", "ultrafast", "-crf", "26", "-c:a", "aac", piece])
                if res.returncode != 0:
                    raise RuntimeError("\n".join(res.log_tail[-4:]))
                parts.append(xf.Clip.from_info(ff.probe(piece)))
            out = os.path.join(tmp, "preview.mp4")
            from core import encoders as enc
            venc = enc.video("h264", "low", speed="fastest", hw="off", container="mp4")
            argv, _secs = xf.build_command(parts, out, T=T, transitions=[trans], freeze=freeze, audio_mode=audio,
                                           audio_curve=curve, venc=venc)
            res = ff.run(argv)
            if res.returncode != 0:
                raise RuntimeError("\n".join(res.log_tail[-4:]))
            return out

        def done(path):
            self.btn_preview.config(state="normal", text="▶  Preview the cut")
            launch_preview(path)

        def failed(exc):
            self.btn_preview.config(state="normal", text="▶  Preview the cut")
            messagebox.showerror(t("common.error"), f"The preview could not be made:\n{exc}", parent=self)

        ui.run_in_thread(make, on_done=done, on_error=failed, name="TransitionPreview")

    def destroy(self):
        if self._preview_dir:
            shutil.rmtree(self._preview_dir, ignore_errors=True)
        super().destroy()

    # ── Render ────────────────────────────────────────────────────────
    def _render(self):
        try:
            clips, T, curve = self._settings()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, clips[0].path, "transition", inputs=[clips[1].path])
        if not out:
            return
        venc = self.encoder.video_encode(common.container_of(out))
        argv, secs = xf.build_command(clips, out, T=T, transitions=[self._transition()],
                                      freeze=self.freeze_var.get(), audio_mode=self.audio_var.get(),
                                      audio_curve=curve, venc=venc)
        self.log(self.console, f"{self._transition()} · {T:g} s · {ff.format_time(secs)} total")
        self.run_ffmpeg(argv, self.console, on_done=lambda rc: self.show_result(rc, out), btn=self.btn_render,
                        btn_label="🎬  RENDER TRANSITION", duration=secs, output=out,
                        inputs=[c.path for c in clips], name="Transition Studio")
