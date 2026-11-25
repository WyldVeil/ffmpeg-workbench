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

from core import ffmpeg as ff
from core.i18n import t
from core.ops import codec as co
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, BaseTab
from tabs.widgets import EncoderPanel, FileRow, save_section, scroll_body

CONTAINERS = ("auto", "mp4", "mkv", "mov", "webm")


class CodecCruncherTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self._info: ff.MediaInfo | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.make_header(self, t("tab.codec_cruncher"), t("codec.desc_subtitle"), icon="🔩")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_file"), on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.src_var = self.src_row.var
        self.info_lbl = tk.Label(body, text="", fg=CLR["fgdim"], anchor="w")
        self.info_lbl.pack(fill="x", padx=px(20))

        vc = tk.LabelFrame(body, text=t("section.video_codec"), padx=px(12), pady=px(8))
        vc.pack(fill="x", padx=px(20), pady=px(5))
        self.copy_video = tk.BooleanVar(value=False)
        tk.Checkbutton(
            vc,
            text=t("codec.copy_the_video_as_it_is_only"),
            variable=self.copy_video,
            command=self._update_ext,
        ).pack(anchor="w")
        self.encoder = EncoderPanel(
            vc,
            codecs=("h264", "hevc", "av1", "vp9", "prores", "dnxhr", "ffv1"),
            on_change=self._update_ext,
        )
        self.encoder.pack(anchor="w", pady=px(4, 0))
        r = tk.Frame(vc)
        r.pack(anchor="w", pady=px(6, 0))
        tk.Label(r, text=t("screen_recorder.container")).pack(side="left")
        self.container_var = tk.StringVar(value="auto")
        cb = ttk.Combobox(
            r, textvariable=self.container_var, values=list(CONTAINERS), state="readonly", width=8
        )
        cb.pack(side="left", padx=px(6))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._update_ext())
        tk.Label(r, text=t("codec.auto_the_usual_one_for_the_codec"), fg=CLR["fgdim"]).pack(
            side="left"
        )

        ac = tk.LabelFrame(body, text=t("section.audio_codec"), padx=px(12), pady=px(8))
        ac.pack(fill="x", padx=px(20), pady=px(5))
        self.audio_var = tk.StringVar(value=co.AUDIO["aac256"][0])
        ttk.Combobox(
            ac,
            textvariable=self.audio_var,
            values=[v[0] for v in co.AUDIO.values()],
            state="readonly",
            width=24,
        ).pack(side="left")
        self.keep_all = tk.BooleanVar(value=True)
        tk.Checkbutton(
            ac, text=t("codec.keep_every_audio_track_and_the_subtitles"), variable=self.keep_all
        ).pack(side="left", padx=px(12))

        self.out_row = save_section(
            body,
            t("common.output_file"),
            suggest=lambda: self.suggest_output(self.src_row.path, "encoded", self._ext()),
        )
        self.out_var = self.out_row.var
        self.btn_render = self.make_render_btn(body, t("codec.btn_encode"), self._render, width=28)
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=7)

    def _codec(self) -> str:
        if self.copy_video.get() and self._info is not None and self._info.has_video:
            return {"dnxhd": "dnxhr"}.get(
                self._info.require_video().codec, self._info.require_video().codec
            )
        return self.encoder.codec

    def _container(self) -> str:
        return co.container_for(self._codec(), self.container_var.get())

    def _ext(self) -> str:
        return "." + self._container()

    def _update_ext(self, *_a: object) -> None:
        if not hasattr(self, "out_row"):  # called while the page is still being built
            return
        out = self.out_row.path
        if out and self.src_row.path:
            want = self._ext()
            if not out.lower().endswith(want):
                self.out_row.set(os.path.splitext(out)[0] + want)

    def _source_changed(self, path: str) -> None:
        self._info = info = ff.try_probe(path)
        if info is not None and info.has_video:
            v = info.require_video()
            self.info_lbl.config(
                text=t(
                    "codec.duration_codec_width_height_fps_3g_fps",
                    duration=ff.format_time(info.duration),
                    codec=v.codec,
                    width=v.width,
                    height=v.height,
                    fps=v.fps,
                    pix_fmt=v.pix_fmt,
                    value="  ·  HDR" if v.is_hdr else "",
                    value2=len([s for s in info.streams if s.type == "audio"]),
                )
            )
        else:
            self.info_lbl.config(text="")
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "encoded", self._ext()))
        else:
            self._update_ext()

    def _render(self) -> None:
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
        key = next((k for k, v in co.AUDIO.items() if v[0] == self.audio_var.get()), "aac256")
        venc = None if self.copy_video.get() else self.encoder.video_encode(container)
        cmd, notes = co.crunch_cmd(
            src, out, info, venc, key, container, keep_all=self.keep_all.get()
        )
        label = "copy" if venc is None else venc.label
        for note in notes:
            self.log(self.console, "  " + note)
        self.log(
            self.console,
            t("codec.encoding_with_label_container", label=label, container=container.upper()),
        )
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=info.duration or None,
            output=out,
            inputs=[src],
            name="Codec Cruncher",
        )
