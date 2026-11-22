"""
tabs/audio/extractor.py  -  Audio Extractor

Pull the soundtrack out of a video: as-is (no re-encoding - the original
codec in a matching file type), or converted to MP3, AAC, FLAC, WAV, Opus
or Ogg Vorbis.  Choose the track, optionally trim and normalise loudness
(two-pass EBU R128).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import loudness as ln
from core.i18n import t
from core.ops.extractor import copy_extension
from core.ops.shared import audio_encoder_args
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import FileRow, scroll_body

FORMATS = {  # id: (label, extension or None = from codec, encoder args)
    "copy": ("Original (no re-encoding)", None, ["-c:a", "copy"]),
    "mp3": ("MP3 (VBR, very high quality)", ".mp3", ["-c:a", "libmp3lame", "-q:a", "0"]),
    "aac": ("AAC in .m4a (256 kbps)", ".m4a", ["-c:a", "aac", "-b:a", "256k"]),
    "flac": ("FLAC (lossless)", ".flac", ["-c:a", "flac", "-compression_level", "8"]),
    "wav": ("WAV (uncompressed)", ".wav", ["-c:a", "pcm_s24le"]),
    "opus": ("Opus (160 kbps)", ".opus", ["-c:a", "libopus", "-b:a", "160k"]),
    "ogg": ("Ogg Vorbis (quality 6)", ".ogg", ["-c:a", "libvorbis", "-q:a", "6"]),
}


class AudioExtractorTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self, t("tab.audio_extractor"), "Save a video's soundtrack as its own file.", icon="🎵"
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body,
            t("common.source_video"),
            self.src_var,
            kind="media",
            on_change=self._source_changed,
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        opt = tk.LabelFrame(body, text="  Extract  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.fmt_var = tk.StringVar(value=FORMATS["copy"][0])
        fb = ttk.Combobox(
            opt,
            textvariable=self.fmt_var,
            values=[v[0] for v in FORMATS.values()],
            state="readonly",
            width=34,
        )
        fb.grid(row=0, column=1, sticky="w")
        fb.bind("<<ComboboxSelected>>", lambda _e: self._suggest_name(force=True))
        tk.Label(opt, text="Format:").grid(row=0, column=0, sticky="w")
        tk.Label(opt, text="Track:").grid(row=1, column=0, sticky="w")
        self.track_var = tk.StringVar()
        self.track_box = ttk.Combobox(opt, textvariable=self.track_var, state="readonly", width=40)
        self.track_box.grid(row=1, column=1, sticky="w", pady=px(4))
        tk.Label(opt, text="From / to (optional):").grid(row=2, column=0, sticky="w")
        tr = tk.Frame(opt)
        tr.grid(row=2, column=1, sticky="w")
        self.from_var, self.to_var = tk.StringVar(), tk.StringVar()
        tk.Entry(tr, textvariable=self.from_var, width=10).pack(side="left")
        tk.Entry(tr, textvariable=self.to_var, width=10).pack(side="left", padx=px(6))
        self.norm_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            opt,
            text="Normalise loudness to -16 LUFS (two-pass, re-encodes)",
            variable=self.norm_var,
        ).grid(row=3, column=1, sticky="w")
        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True, kind="audio", ext=".m4a")
        self.out_row.pack(fill="x")
        self.btn_render = tk.Button(
            body,
            text="🎵  EXTRACT",
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

    def _key(self):
        return next((k for k, v in FORMATS.items() if v[0] == self.fmt_var.get()), "copy")

    def _stream(self):
        values = list(self.track_box.cget("values"))
        return values.index(self.track_var.get()) if self.track_var.get() in values else 0

    def _ext(self):
        key = self._key()
        if key == "copy" and not self.norm_var.get():
            streams = self._info.audio_streams if self._info else []
            return copy_extension(streams[self._stream()].codec) if streams else ".mka"
        return FORMATS[key][1] or ".m4a"

    def _suggest_name(self, force=False):
        src = self.src_row.path
        if src and (force or not self.out_row.path):
            self.out_row.set(self.suggest_output(src, "audio", self._ext()))

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        tracks = (
            [
                f"Track {i + 1}: {a.codec}, {a.channels} ch"
                + (f" [{a.language}]" if a.language else "")
                + (f" {a.title}" if a.title else "")
                for i, a in enumerate(self._info.audio_streams)
            ]
            if self._info
            else []
        )
        self.track_box.config(values=tracks or ["(no audio)"])
        self.track_var.set(tracks[0] if tracks else "(no audio)")
        self._suggest_name(force=True)

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src, need_video=False)
        if info is None:
            return
        if not info.has_audio:
            messagebox.showwarning(
                t("common.warning"), "This file has no audio track.", parent=self
            )
            return
        self._info = info
        try:
            start = ff.parse_time(self.from_var.get()) if self.from_var.get().strip() else 0.0
            end = ff.parse_time(self.to_var.get()) if self.to_var.get().strip() else None
            if end is not None and end <= start:
                raise ValueError("'to' must be after 'from'.")
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = self.out_row.path or self.suggest_output(src, "audio", self._ext())
        import os

        if os.path.splitext(out)[1].lower() != self._ext():
            out = os.path.splitext(out)[0] + self._ext()
        out = self.confirm_output(out, [src], default_ext=self._ext())
        if not out:
            return
        self.out_row.set(out)
        stream = self._stream()
        key = self._key()
        enc = FORMATS[key][2]
        if key == "copy" and self.norm_var.get():
            enc = audio_encoder_args(out)
        inp = [ff.ffmpeg_path(), "-y"]
        if start:
            inp += ["-ss", ff.ts(start)]
        if end is not None:
            inp += ["-to", ff.ts(end)]
        inp += ["-i", src, "-map", f"0:a:{stream}", "-vn", "-sn", "-dn"]
        dur = (end if end is not None else info.duration) - start
        sr = info.audio_streams[stream].sample_rate or 48000

        if not self.norm_var.get():
            self.run_ffmpeg(
                inp + enc + [out],
                self.console,
                on_done=lambda rc: self.show_result(rc, out),
                btn=self.btn_render,
                btn_label="🎵  EXTRACT",
                duration=dur,
                output=out,
                inputs=[src],
                name="Audio Extractor",
            )
            return

        def work(_p, _c, ctx):
            m = ln.measure(src, -16, -1.5, 11, stream=stream, ctx=ctx.span(0, 0.4))
            ctx.span(0.4, 1.0).run(
                [*inp, "-af", ln.normalise_filter(-16, -1.5, 11, m, sr), *enc, ctx.output()],
                duration=dur,
            )
            return 0

        common.queue_job(
            self,
            name="Audio Extractor",
            out=out,
            work=work,
            btn=self.btn_render,
            btn_label="🎵  EXTRACT",
            console=self.console,
            duration=dur,
            inputs=[src],
        )
