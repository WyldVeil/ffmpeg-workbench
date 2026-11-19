"""
tabs/transcoder/codec.py  -  Codec Cruncher

Re-encode a video with any codec - H.264, HEVC, AV1, VP9, ProRes, DNxHR,
FFV1 - on the GPU when available, at a chosen quality and speed.  The
container follows the codec (or your choice when it can hold it), every
audio track and subtitle can be kept, HDR is kept for 10-bit HEVC/AV1 and
tone-mapped for 8-bit outputs.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, scroll_body

CONTAINERS = ("auto", "mp4", "mkv", "mov", "webm")
DEFAULT_CONTAINER = {"h264": "mp4", "hevc": "mp4", "av1": "mp4", "vp9": "webm", "prores": "mov", "dnxhr": "mov",
                     "ffv1": "mkv"}
AUDIO = {
    "aac256": ("AAC 256 kb/s", "aac", "256k"), "aac192": ("AAC 192 kb/s", "aac", "192k"),
    "opus": ("Opus 160 kb/s", "opus", "160k"), "mp3": ("MP3 320 kb/s", "mp3", "320k"),
    "flac": ("FLAC (lossless)", "flac", None), "pcm24": ("PCM 24-bit (editing)", "pcm24", None),
    "copy": ("Copy (no change)", "copy", None), "none": (t("codec.remove_audio"), "", None),
}


def container_for(codec: str, choice: str) -> str:
    if choice != "auto" and enc.video_ok_for(choice, codec):
        return choice
    return DEFAULT_CONTAINER.get(codec, "mkv")


def audio_args(info: ff.MediaInfo, key: str, container: str, keep_all: bool) -> tuple[list, list]:
    """(args, notes) for the audio tracks."""
    label, codec, rate = AUDIO[key]
    audios = [s for s in info.streams if s.type == "audio"]
    if not audios or not codec:
        return ["-an"], []
    notes = []
    tracks = audios if keep_all else audios[:1]
    args = []
    for n, a in enumerate(tracks):
        args += ["-map", f"0:{a.index}"]
        want = codec
        if want == "copy":
            probe_key = enc.audio_key_from_probe(a.codec)
            if probe_key and enc.audio_ok_for(container, probe_key):
                args += [f"-c:a:{n}", "copy"]
                continue
            want = enc.default_audio_for(container)
            notes.append(f"Audio track {n + 1} ({a.codec}) can't be copied into {container.upper()} - "
                         f"converted to {want}.")
        elif not enc.audio_ok_for(container, want):
            notes.append(f"{label} doesn't fit {container.upper()} - using {enc.default_audio_for(container)}.")
            want = enc.default_audio_for(container)
        spec = enc.audio(want, rate, container=container)
        args += [f"{x}:{n}" if x in ("-c:a", "-b:a", "-q:a") else x for x in spec]
    return args, notes


class CodecCruncherTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self._info = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.codec_cruncher"), t("codec.desc_subtitle"), icon="🔩")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_file"), on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=20, pady=(10, 2))
        self.src_var = self.src_row.var
        self.info_lbl = tk.Label(body, text="", fg=CLR["fgdim"], anchor="w")
        self.info_lbl.pack(fill="x", padx=20)

        vc = tk.LabelFrame(body, text=t("section.video_codec"), padx=12, pady=8)
        vc.pack(fill="x", padx=20, pady=5)
        self.copy_video = tk.BooleanVar(value=False)
        tk.Checkbutton(vc, text="Copy the video as it is (only change audio / container)", variable=self.copy_video,
                       command=self._update_ext).pack(anchor="w")
        self.encoder = EncoderPanel(vc, codecs=("h264", "hevc", "av1", "vp9", "prores", "dnxhr", "ffv1"),
                                    on_change=self._update_ext)
        self.encoder.pack(anchor="w", pady=(4, 0))
        r = tk.Frame(vc)
        r.pack(anchor="w", pady=(6, 0))
        tk.Label(r, text="Container:").pack(side="left")
        self.container_var = tk.StringVar(value="auto")
        cb = ttk.Combobox(r, textvariable=self.container_var, values=list(CONTAINERS), state="readonly", width=8)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._update_ext())
        tk.Label(r, text="(auto = the usual one for the codec)", fg=CLR["fgdim"]).pack(side="left")

        ac = tk.LabelFrame(body, text=t("section.audio_codec"), padx=12, pady=8)
        ac.pack(fill="x", padx=20, pady=5)
        self.audio_var = tk.StringVar(value=AUDIO["aac256"][0])
        ttk.Combobox(ac, textvariable=self.audio_var, values=[v[0] for v in AUDIO.values()], state="readonly",
                     width=24).pack(side="left")
        self.keep_all = tk.BooleanVar(value=True)
        tk.Checkbutton(ac, text="Keep every audio track and the subtitles", variable=self.keep_all).pack(
            side="left", padx=12)

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=12, pady=8)
        out_lf.pack(fill="x", padx=20, pady=6)
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.src_row.path, "encoded", self._ext()))
        self.out_row.pack(fill="x")
        self.out_var = self.out_row.var
        self.btn_render = tk.Button(body, text=t("codec.btn_encode"), font=(UI_FONT, 12, "bold"), bg=CLR["accent"],
                                    fg="white", width=28, command=self._render)
        self.btn_render.pack(pady=8)
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self.console, csb = self.make_console(cf, height=7)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def _codec(self) -> str:
        if self.copy_video.get() and self._info is not None and self._info.has_video:
            return {"dnxhd": "dnxhr"}.get(self._info.video.codec, self._info.video.codec)
        return self.encoder.codec

    def _container(self) -> str:
        return container_for(self._codec(), self.container_var.get())

    def _ext(self) -> str:
        return "." + self._container()

    def _update_ext(self, *_a):
        if not hasattr(self, "out_row"):           # called while the page is still being built
            return
        out = self.out_row.path
        if out and self.src_row.path:
            want = self._ext()
            if not out.lower().endswith(want):
                self.out_row.set(os.path.splitext(out)[0] + want)

    def _source_changed(self, path):
        self._info = info = ff.try_probe(path)
        if info is not None and info.has_video:
            v = info.video
            self.info_lbl.config(text=f"{ff.format_time(info.duration)}  ·  {v.codec} {v.width}×{v.height}  ·  "
                                      f"{v.fps:.3g} fps  ·  {v.pix_fmt}{'  ·  HDR' if v.is_hdr else ''}  ·  "
                                      f"{len([s for s in info.streams if s.type == 'audio'])} audio track(s)")
        else:
            self.info_lbl.config(text="")
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "encoded", self._ext()))
        else:
            self._update_ext()

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        self._info = info
        container = self._container()
        out = self.out_row.path or self.suggest_output(src, "encoded", "." + container)
        if os.path.splitext(out)[1].lower() != "." + container:
            out = os.path.splitext(out)[0] + "." + container
        out = self.confirm_output(out, [src], default_ext="." + container)
        if not out:
            return
        self.out_row.set(out)
        key = next((k for k, v in AUDIO.items() if v[0] == self.audio_var.get()), "aac256")
        a_args, notes = audio_args(info, key, container, self.keep_all.get())
        v = info.video
        cmd = [ff.ffmpeg_path(), "-y"]
        if self.copy_video.get():
            v_args = ["-map", "0:v:0", "-c:v", "copy"]
            if v.codec == "hevc" and container in ("mp4", "mov"):
                v_args += ["-tag:v", "hvc1"]
            label = "copy"
        else:
            venc = self.encoder.video_encode(container)
            chain = []
            if v.is_hdr and not venc.pix_fmt.endswith(("10le", "p010le", "p010")) and venc.encoder not in (
                    "prores_ks", "dnxhd", "ffv1"):
                chain.append(fl.hdr_to_sdr_chain(out_fmt=venc.pix_fmt or "yuv420p"))
                notes.append("HDR source tone-mapped to SDR for this 8-bit output (tick 10-bit to keep HDR).")
            cmd += venc.pre_input
            v_args = ["-map", "0:v:0", *venc.vf_args(",".join(chain) or None), *venc.args]
            label = venc.label
        cmd += ["-i", src, *v_args, *a_args]
        if self.keep_all.get():
            subs = [s for s in info.streams if s.type == "subtitle"]
            sub_codec = {"mkv": "copy", "mp4": "mov_text", "mov": "mov_text", "webm": "webvtt"}.get(container)
            n = 0
            for s in subs:
                if sub_codec == "copy" or s.codec in ("subrip", "ass", "ssa", "webvtt", "mov_text"):
                    cmd += ["-map", f"0:{s.index}", f"-c:s:{n}", sub_codec or "copy"]
                    n += 1
        cmd += ["-map_metadata", "0", "-map_chapters", "0", out]
        for note in notes:
            self.log(self.console, "  " + note)
        self.log(self.console, f"Encoding with {label} → {container.upper()}…")
        self.run_ffmpeg(cmd, self.console, on_done=lambda rc: self.show_result(rc, out), btn=self.btn_render,
                        btn_label=t("codec.btn_encode"), duration=info.duration or None, output=out, inputs=[src],
                        name="Codec Cruncher")
