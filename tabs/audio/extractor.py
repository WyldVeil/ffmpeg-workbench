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
from core.i18n import Labels, t
from core.ops import extractor as xo
from core.scaling import px
from tabs import common
from tabs.base_tab import BaseTab
from tabs.kit.time_fields import RangePicker
from tabs.widgets import FileRow, save_section, scroll_body

#: how xo.FORMATS is shown (in the current language)
FORMAT_LABELS = Labels("extractor.formats", {k: v[0] for k, v in xo.FORMATS.items()})


class AudioExtractorTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info: ff.MediaInfo | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.make_header(
            self,
            t("tab.audio_extractor"),
            t("extractor.save_a_video_s_soundtrack_as_its"),
            icon="🎵",
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
        opt = tk.LabelFrame(body, text=t("extractor.extract"), padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.fmt_var = tk.StringVar(value=FORMAT_LABELS["copy"])
        fb = ttk.Combobox(
            opt,
            textvariable=self.fmt_var,
            values=list(FORMAT_LABELS.values()),
            state="readonly",
            width=34,
        )
        fb.bind("<<ComboboxSelected>>", lambda _e: self._suggest_name(force=True))
        self.form_row(opt, 0, t("common.format"), fb)
        self.track_var = tk.StringVar()
        self.track_box = ttk.Combobox(opt, textvariable=self.track_var, state="readonly", width=40)
        self.form_row(opt, 1, t("extractor.track"), self.track_box)
        self.part = RangePicker(opt, start="", source=lambda: self.src_row.path)
        self.form_row(opt, 2, t("extractor.from_to_optional"), self.part)
        self.norm_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            opt,
            text=t("extractor.normalise_loudness_to_16_lufs_two_pass"),
            variable=self.norm_var,
        ).grid(row=3, column=1, sticky="w")
        self.out_row = save_section(body, t("common.output_file"), kind="audio", ext=".m4a")
        self.btn_render = self.make_render_btn(
            body, t("extractor.extract_2"), self._render, width=20
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

    def _key(self) -> str:
        return FORMAT_LABELS.code_of(self.fmt_var.get(), "copy")

    def _stream(self) -> int:
        values = list(self.track_box.cget("values"))
        return values.index(self.track_var.get()) if self.track_var.get() in values else 0

    def _ext(self) -> str:
        key = self._key()
        if key == "copy" and not self.norm_var.get():
            streams = self._info.audio_streams if self._info else ()
            return xo.copy_extension(streams[self._stream()].codec) if streams else ".mka"
        return xo.FORMATS[key][1] or ".m4a"

    def _suggest_name(self, force: bool = False) -> None:
        src = self.src_row.path
        if src and (force or not self.out_row.path):
            self.out_row.set(self.suggest_output(src, "audio", self._ext()))

    def _source_changed(self, path: str) -> None:
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

    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src, need_video=False)
        if info is None:
            return
        if not info.has_audio:
            messagebox.showwarning(
                t("common.warning"), t("extractor.this_file_has_no_audio_track"), parent=self
            )
            return
        self._info = info
        try:
            start, end = self.part.span()
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
        dur = (end if end is not None else info.duration) - start
        job = xo.ExtractJob(
            src=src,
            stream=stream,
            start=start,
            end=end,
            encode=xo.FORMATS[key][2] if key != "copy" or not self.norm_var.get() else None,
            normalise=self.norm_var.get(),
            sample_rate=info.audio_streams[stream].sample_rate or 48000,
            seconds=dur,
        )
        if not self.norm_var.get():
            self.run_ffmpeg(
                job.command(out),
                self.console,
                on_done=lambda rc: self.show_result(rc, out),
                btn=self.btn_render,
                duration=dur,
                output=out,
                inputs=[src],
                name="Audio Extractor",
            )
            return
        common.queue_job(
            self,
            common.Job(name="Audio Extractor", out=out, work=job.run, duration=dur, inputs=[src]),
            btn=self.btn_render,
            console=self.console,
        )
