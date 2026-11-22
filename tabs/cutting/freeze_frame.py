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

from core import ass
from core import ffmpeg as ff
from core.i18n import t
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, FramePreview, scroll_body

MODES = [("insert", "Insert a freeze", "Pause the picture at the chosen moment, then carry on."),
         ("end", "Freeze the ending", "Hold the last frame at the end of the clip."),
         ("only", "Freeze only", "Export just the frozen moment as a still clip.")]
AUDIO = {"pause": "Sound pauses during the freeze", "continue": "Sound keeps playing",
         "mute": "No sound"}


def freeze_graph(mode: str, at: float, hold: float, dur: float, *, has_audio: bool,
                 audio_mode: str = "pause", flash: bool = False, mono: bool = False,
                 caption_ass: str | None = None) -> tuple:
    """(filter_complex, output seconds, audio_out)."""
    look = []
    if mono:
        look.append("hue=s=0")
    if mode == "only":
        v = (f"[0:v]trim=start={at:.4f},setpts=PTS-STARTPTS,trim=end_frame=1,"
             f"tpad=stop_mode=clone:stop_duration={hold:.4f}")
        if look:
            v += "," + ",".join(look)
        start, total = 0.0, hold
        parts = [v + "[fz]"]
        vlast = "[fz]"
    elif mode == "end":
        at = dur
        parts = [f"[0:v]setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={hold:.4f}[fz]"]
        vlast, start, total = "[fz]", dur, dur + hold
        if look:
            when = ":enable=gte(t\\," + f"{dur:.4f})"
            parts.append(vlast + ",".join(f + when for f in look) + "[fl]")
            vlast = "[fl]"
    else:
        parts = ["[0:v]setpts=PTS-STARTPTS,split[va][vb]",
                 f"[va]trim=end={at:.4f},setpts=PTS-STARTPTS[v0]",
                 f"[vb]trim=start={at:.4f},setpts=PTS-STARTPTS,"
                 f"tpad=start_mode=clone:start_duration={hold:.4f}[v1]",
                 "[v0][v1]concat=n=2:v=1:a=0[fz]"]
        vlast, start, total = "[fz]", at, dur + hold
        if look:
            when = ":enable=between(t\\," + f"{at:.4f}" + "\\," + f"{at + hold:.4f})"
            parts.append(vlast + ",".join(f + when for f in look) + "[fl]")
            vlast = "[fl]"
    if flash:
        parts.append(f"{vlast}fade=t=in:st={start:.4f}:d=0.35:color=white[fx]")
        vlast = "[fx]"
    if caption_ass:
        parts.append(f"{vlast}{ass.subtitles_filter(caption_ass)}[cap]")
        vlast = "[cap]"
    parts.append(f"{vlast}null[v]")

    audio_out = has_audio and audio_mode != "mute"
    if audio_out:
        if mode == "only":
            audio_out = False
        elif mode == "end":
            parts.append(f"[0:a:0]asetpts=PTS-STARTPTS,apad=pad_dur={hold:.4f}[a]")
        elif audio_mode == "continue":
            parts.append(f"[0:a:0]asetpts=PTS-STARTPTS,apad=pad_dur={hold:.4f}[a]")
        else:
            ms = int(round(hold * 1000))
            parts += ["[0:a:0]asetpts=PTS-STARTPTS,asplit[xa][xb]",
                      f"[xa]atrim=end={at:.4f},asetpts=PTS-STARTPTS[a0]",
                      f"[xb]atrim=start={at:.4f},asetpts=PTS-STARTPTS,adelay=delays={ms}:all=1[a1]",
                      "[a0][a1]concat=n=2:v=0:a=1[a]"]
    return ";".join(parts), total, audio_out


class FreezeFrameTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.freeze_frame"), "Pause the picture on a moment - with "
                         "an optional flash and caption.", icon="⏸")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), self.src_var,
                               on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        pv = tk.Frame(body)
        pv.pack(pady=px(4))
        self.preview = FramePreview(pv, 480, 270)
        self.preview.pack()
        self.pos_var = tk.DoubleVar(value=0)
        self.slider = ttk.Scale(pv, from_=0, to=1, variable=self.pos_var, length=px(480),
                                command=lambda _v: self._moved())
        self.slider.pack(fill="x")

        opt = tk.LabelFrame(body, text="  Freeze  ", padx=px(12), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.mode_var = tk.StringVar(value="insert")
        for key, label, desc in MODES:
            row = tk.Frame(opt)
            row.pack(fill="x")
            tk.Radiobutton(row, text=label, value=key, variable=self.mode_var, width=18,
                           anchor="w").pack(side="left")
            tk.Label(row, text=desc, fg=CLR["fgdim"], font=(UI_FONT, 9)).pack(side="left")
        r = tk.Frame(opt)
        r.pack(fill="x", pady=px(6, 2))
        tk.Label(r, text="At:").pack(side="left")
        self.at_var = tk.StringVar(value="0")
        tk.Entry(r, textvariable=self.at_var, width=12).pack(side="left", padx=px(4))
        tk.Label(r, text="(follows the slider)   Hold for (s):").pack(side="left")
        self.hold_var = tk.StringVar(value="2")
        tk.Entry(r, textvariable=self.hold_var, width=6).pack(side="left", padx=px(4))
        r2 = tk.Frame(opt)
        r2.pack(fill="x", pady=px(2))
        self.audio_var = tk.StringVar(value=AUDIO["pause"])
        ttk.Combobox(r2, textvariable=self.audio_var, values=list(AUDIO.values()), state="readonly",
                     width=32).pack(side="left")
        self.flash_var = tk.BooleanVar(value=False)
        self.mono_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r2, text="White flash", variable=self.flash_var).pack(side="left", padx=px(8))
        tk.Checkbutton(r2, text="Black & white while frozen", variable=self.mono_var).pack(side="left")
        r3 = tk.Frame(opt)
        r3.pack(fill="x", pady=px(2))
        tk.Label(r3, text="Caption while frozen:").pack(side="left")
        self.caption_var = tk.StringVar()
        tk.Entry(r3, textvariable=self.caption_var, width=40).pack(side="left", padx=px(4))

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.src_row.path, "freeze"))
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(body, text="⏸  CREATE", font=(UI_FONT, 12, "bold"), bg=CLR["green"],
                                    fg="white", width=22, cursor="hand2", command=self._render)
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

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
        self.at_var.set(ff.format_time(pos))
        self.preview.show(pos)

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        dur = info.duration
        try:
            at = ff.parse_time(self.at_var.get() or "0")
            hold = common.number(self.hold_var.get(), "Hold for", 0.04, 600)
            if not 0 <= at < dur:
                raise ValueError(f"The freeze time must be inside the clip (0 - {ff.format_time(dur)}).")
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
            w, h = common.even_size(info)
            sc = ass.Script(w, h)
            u = min(w, h) / 1080
            sc.style(ass.Style("Cap", font="Montserrat", size=round(64 * u), bold=True,
                               outline=4 * u, shadow=2 * u, alignment=2, margin_v=round(80 * u)))
            start = 0.0 if mode == "only" else (dur if mode == "end" else at)
            sc.add(start, start + hold, f"{{\\fad(150,150)}}{ass.escape(self.caption_var.get())}", "Cap")
            caption_ass = sc.write(os.path.join(work_dir, "caption.ass"))
        audio_mode = next((k for k, v in AUDIO.items() if v == self.audio_var.get()), "pause")
        graph, total, audio_out = freeze_graph(mode, at, hold, dur, has_audio=info.has_audio,
                                               audio_mode=audio_mode, flash=self.flash_var.get(),
                                               mono=self.mono_var.get(), caption_ass=caption_ass)
        container = common.container_of(out)
        from core import encoders as enc
        audio = (["-map", "[a]", *enc.audio(enc.default_audio_for(container), container=container)]
                 if audio_out else ["-an"])

        def done(rc):
            if work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)
            self.show_result(rc, out)

        common.render(self, src=src, info=info, out=out, graph=graph, encoder=self.encoder,
                      console=self.console, btn=self.btn_render, btn_label="⏸  CREATE",
                      name="Freeze Frame", audio=audio, duration=total, on_done=done)
