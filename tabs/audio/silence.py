"""
tabs/audio/silence.py  -  Silence Remover

Jump-cut away the pauses in talks, lectures and podcasts.  Silences are
found with silencedetect (threshold, minimum length, padding kept around
speech); the cuts are snapped to video frame boundaries and done with
trim/atrim + concat in one exact pass (the graph is written to a file, so
any number of cuts works), keeping picture and sound in sync.
"""
from __future__ import annotations

import os
import re
import tkinter as tk
from tkinter import messagebox

from core import encoders as enc
from core import ffmpeg as ff
from core.i18n import t
from tabs import common
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, scroll_body


def detect_cmd(src: str, threshold_db: float, min_silence: float, stream: int = 0) -> list:
    return [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-i", src, "-map", f"0:a:{stream}", "-vn", "-sn",
            "-af", f"silencedetect=noise={threshold_db:g}dB:d={min_silence:g}", "-f", "null", "-"]


def parse_silences(text: str, duration: float) -> list:
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", text)]
    if len(ends) < len(starts):          # silent until the very end
        ends.append(duration)
    return [(max(0.0, a), b) for a, b in zip(starts, ends) if b > a]


def keep_zones(silences, duration: float, padding: float, fps: float = 0.0, min_keep: float = 0.05) -> list:
    """Parts to keep: everything except silences, with *padding* left around speech."""
    zones, pos = [], 0.0
    for a, b in silences:
        end = min(duration, a + padding)
        if end - pos > min_keep:
            zones.append((pos, end))
        pos = max(pos, b - padding)
    if duration - pos > min_keep:
        zones.append((pos, duration))
    if fps:
        snap = lambda x: round(x * fps) / fps  # noqa: E731
        zones = [(snap(a), snap(b)) for a, b in zones if snap(b) > snap(a)]
    merged = []
    for a, b in zones:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def cut_graph(zones, has_video: bool, has_audio: bool) -> str:
    n = len(zones)
    parts = []
    if has_video:
        parts.append(f"[0:v]split={n}" + "".join(f"[vs{i}]" for i in range(n)))
    if has_audio:
        parts.append(f"[0:a:0]asplit={n}" + "".join(f"[as{i}]" for i in range(n)))
    pads = ""
    for i, (a, b) in enumerate(zones):
        if has_video:
            parts.append(f"[vs{i}]trim=start={a:.4f}:end={b:.4f},setpts=PTS-STARTPTS[v{i}]")
            pads += f"[v{i}]"
        if has_audio:
            parts.append(f"[as{i}]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS[a{i}]")
            pads += f"[a{i}]"
    outs = ("[v]" if has_video else "") + ("[a]" if has_audio else "")
    parts.append(f"{pads}concat=n={n}:v={1 if has_video else 0}:a={1 if has_audio else 0}{outs}")
    return ";\n".join(parts)


class SilenceTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._zones = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.silence_remover"), "Cut out the pauses - jump cuts for talks and "
                         "podcasts.", icon="✂")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), self.src_var, kind="media",
                               on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=20, pady=(10, 4))
        opt = tk.LabelFrame(body, text="  Detection  ", padx=12, pady=8)
        opt.pack(fill="x", padx=20, pady=4)
        self.thr_var, self.min_var, self.pad_var = (tk.StringVar(value="-35"), tk.StringVar(value="0.6"),
                                                    tk.StringVar(value="0.15"))
        for col, (label, var, hint) in enumerate((("Silence below (dB)", self.thr_var, "-30 noisy room … -45 studio"),
                                                  ("Shortest pause (s)", self.min_var, "pauses shorter than this stay"),
                                                  ("Keep around speech (s)", self.pad_var, "breathing room"))):
            tk.Label(opt, text=label).grid(row=0, column=col, sticky="w", padx=6)
            tk.Entry(opt, textvariable=var, width=8).grid(row=1, column=col, sticky="w", padx=6)
            tk.Label(opt, text=hint, fg=CLR["fgdim"], font=(UI_FONT, 8)).grid(row=2, column=col, sticky="w", padx=6)
        self.report = tk.Label(body, text="", fg=CLR["accent"], font=(MONO_FONT, 10))
        self.report.pack(anchor="w", padx=24, pady=4)
        out_lf = tk.LabelFrame(body, text="  Output  ", padx=12, pady=8)
        out_lf.pack(fill="x", padx=20, pady=6)
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True, kind="media",
                               suggest=lambda: self.suggest_output(self.src_row.path, "tight"))
        self.out_row.pack(fill="x", pady=(8, 0))
        row = tk.Frame(body)
        row.pack(pady=8)
        self.btn_analyse = tk.Button(row, text="🔍  ANALYSE", font=(UI_FONT, 11, "bold"), width=14,
                                     command=self._analyse)
        self.btn_analyse.pack(side="left", padx=6)
        self.btn_render = tk.Button(row, text="✂  REMOVE SILENCES", font=(UI_FONT, 11, "bold"), bg=CLR["green"],
                                    fg="white", width=20, command=self._render)
        self.btn_render.pack(side="left", padx=6)
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self.console, csb = self.make_console(cf, height=6)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        self._zones = None
        self.report.config(text="")
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "tight"))

    def _params(self):
        return (common.number(self.thr_var.get(), "Silence threshold", -90, -5),
                common.number(self.min_var.get(), "Shortest pause", 0.05, 60),
                common.number(self.pad_var.get(), "Keep around speech", 0, 5))

    def _detect(self, src, info, thr, min_s, pad, runner):
        res = runner(detect_cmd(src, thr, min_s))
        text = "\n".join(res.log_tail if hasattr(res, "log_tail") else [])
        fps = info.video.fps if info.has_video and info.video else 0.0
        zones = keep_zones(parse_silences(text, info.duration), info.duration, pad, fps)
        return zones

    def _analyse(self):
        src = self.src_row.path
        info = common.check_source(self, src, need_video=False)
        if info is None:
            return
        try:
            thr, min_s, pad = self._params()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        self.btn_analyse.config(state="disabled", text="Listening…")

        def work():
            return self._detect(src, info, thr, min_s, pad, lambda c: ff.run(c, tail=100000))

        def done(zones):
            self.btn_analyse.config(state="normal", text="🔍  ANALYSE")
            self._zones = (src, (thr, min_s, pad), zones)
            kept = sum(b - a for a, b in zones)
            self.report.config(text=f"{len(zones)} parts kept · {ff.format_time(kept)} of "
                                    f"{ff.format_time(info.duration)} · removes {ff.format_time(info.duration - kept)}")

        from core import ui
        ui.run_in_thread(work, on_done=done, on_error=lambda e: (self.btn_analyse.config(
            state="normal", text="🔍  ANALYSE"), messagebox.showerror(t("common.error"), str(e), parent=self)),
            name="SilenceDetect")

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src, need_video=False)
        if info is None:
            return
        if not info.has_audio:
            messagebox.showwarning(t("common.warning"), "This file has no audio to analyse.", parent=self)
            return
        try:
            thr, min_s, pad = self._params()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "tight")
        if not out:
            return
        container = common.container_of(out)
        audio_only = not info.has_video or out.lower().endswith(common.AUDIO_ONLY_EXTS)
        venc = None if audio_only else self.encoder.video_encode(container)

        def work(_p, _c, ctx):
            tmp = ctx.tempdir()
            zones = self._detect(src, info, thr, min_s, pad,
                                 lambda c: ctx.span(0.0, 0.25).run(c, duration=info.duration, tail=200000))
            if not zones:
                raise RuntimeError("Everything is below the silence threshold - try a lower dB value.")
            kept = sum(b - a for a, b in zones)
            ctx.log(f"Keeping {len(zones)} parts ({ff.format_time(kept)} of {ff.format_time(info.duration)})")
            graph = cut_graph(zones, not audio_only, True)
            with open(os.path.join(tmp, "cuts.txt"), "w", encoding="utf-8") as fh:
                fh.write(graph)
            cmd = [ff.ffmpeg_path(), "-y", *(venc.pre_input if venc else []), "-i", src, "-/filter_complex", "cuts.txt"]
            if audio_only:
                cmd += ["-map", "[a]", *common.audio_encoder_args(out)]
            else:
                if venc.filter_suffix:
                    raise RuntimeError("Choose a software or NVENC/QSV encoder for this tool.")
                cmd += ["-map", "[v]", "-map", "[a]", *venc.args,
                        *enc.audio(enc.default_audio_for(container), container=container)]
            ctx.span(0.25, 1.0).run(cmd + [ctx.output()], duration=kept, cwd=tmp)
            return 0

        common.queue_job(self, name="Silence Remover", out=out, work=work, btn=self.btn_render,
                         btn_label="✂  REMOVE SILENCES", console=self.console, duration=info.duration, inputs=[src])
