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

from core import encoders as enc
from core import ffmpeg as ff
from core.i18n import t
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import FileRow, SaveRow, scroll_body

MODES = {
    "replace": "Replace the soundtrack",
    "mix": "Mix with the original sound",
    "strip": "Remove all sound",
}


def replace_graph(
    mode: str,
    video_dur: float,
    *,
    offset: float = 0.0,
    new_vol: float = 1.0,
    orig_vol: float = 0.5,
    loop: bool = False,
    fade: float = 0.0,
    has_orig: bool = True,
) -> str:
    """filter_complex with input 0 = video, 1 = new audio, output [a]."""
    st = "aformat=sample_rates=48000:channel_layouts=stereo"
    new = f"[1:a:0]{st},asetpts=PTS-STARTPTS"
    if offset < 0:
        new += f",atrim=start={-offset:.3f},asetpts=PTS-STARTPTS"
    elif offset > 0:
        new += f",adelay=delays={round(offset * 1000)}:all=1"
    new += f",volume={new_vol:.3f},apad,atrim=duration={video_dur:.3f}"
    if fade > 0:
        new += f",afade=t=in:st={max(offset, 0):.3f}:d={fade:.2f},afade=t=out:st={max(video_dur - fade, 0):.3f}:d={fade:.2f}"
    if mode == "mix" and has_orig:
        return (
            f"{new}[n];[0:a:0]{st},volume={orig_vol:.3f},apad,atrim=duration={video_dur:.3f}[o];"
            f"[o][n]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]"
        )
    return f"{new}[a]"


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
        container = common.container_of(out)
        if mode == "strip":
            cmd = [
                ff.ffmpeg_path(),
                "-y",
                "-i",
                src,
                "-map",
                "0",
                "-map",
                "-0:a",
                "-c",
                "copy",
                out,
            ]
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
            graph = replace_graph(
                mode,
                info.duration,
                offset=offset,
                new_vol=float(self.new_vol.get()),
                orig_vol=float(self.orig_vol.get()),
                loop=self.loop_var.get(),
                fade=1.0 if self.fade_var.get() else 0.0,
                has_orig=info.has_audio,
            )
            cmd = [
                ff.ffmpeg_path(),
                "-y",
                "-i",
                src,
                *(["-stream_loop", "-1"] if self.loop_var.get() else []),
                "-i",
                self.audio_row.path,
                "-filter_complex",
                graph,
                "-map",
                "0:v",
                "-c:v",
                "copy",
                "-map",
                "[a]",
                *enc.audio(enc.default_audio_for(container), container=container),
                "-t",
                f"{info.duration:.3f}",
            ]
            if container in ("mp4", "mov", "m4v"):
                cmd += ["-movflags", "+faststart"]
            cmd.append(out)
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
