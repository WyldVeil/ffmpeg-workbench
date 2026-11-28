"""
tabs/social/hard_subber.py  -  Hard-Subber

Burn subtitles into the picture: an external .srt/.vtt/.ass file or a
subtitle track already inside the video.  SRT/VTT get the chosen style (any
bundled font, size, colours, outline or box, position); ASS files keep their
own styling unless overridden.  A live preview shows the result.

Subtitle files in legacy encodings (Windows-1252, Latin-1…) are converted
to UTF-8 automatically, and odd file names can't break the filter.
"""

from __future__ import annotations

import logging
import os
import shutil
import tkinter as tk
from tkinter import messagebox, ttk

from core import ass
from core import ffmpeg as ff
from core import filters as fl
from core.i18n import Labels, t, t_or
from core.ops.shared import RenderJob
from core.ops.subtitles import POSITIONS, force_style, to_utf8_copy
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import UI_FONT, BaseTab
from tabs.kit.tktyped import on_write
from tabs.kit.widgets import ColourButton
from tabs.widgets import FileRow, FramePreview, output_section, scroll_body

logger = logging.getLogger("crossfader.social.hard_subber")


def _external() -> str:
    """The choice that means "the subtitle file chosen below", not a track in the video."""
    return t_or("hard_subber.external_file", "External subtitle file")


#: how POSITIONS is shown (in the current language)
POSITION_LABELS = Labels("hard_subber.positions", {k: v[0] for k, v in POSITIONS.items()})


class HardSubberTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info: ff.MediaInfo | None = None
        self._tracks: list[tuple[str, int, str]] = []  # (label, stream index, codec)
        self._work: str | None = None
        self._job: str | None = None
        self._build_ui()

    def take_subtitles(self, video: str, subtitles: str) -> None:
        """Burn *subtitles* into *video* (handed over by Auto Subtitles)."""
        self.src_var.set(video)
        self.sub_row.set(subtitles)

    def _build_ui(self) -> None:
        body, r0, st = self._build_header()
        self._build_output(body, r0, st)
        self._build_out(body)

    def _build_header(self) -> tuple[tk.Frame, tk.Frame, tk.LabelFrame]:
        self.make_header(
            self,
            t("tab.hard_subber"),
            t("hard_subber.subtitle")
            if t("hard_subber.subtitle") != "hard_subber.subtitle"
            else "Burn subtitles permanently into the picture.",
            icon="💬",
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        srow = tk.Frame(body)
        srow.pack(fill="x", padx=px(20), pady=px(2))
        tk.Label(
            srow, text=t("hard_subber.subtitles"), width=16, anchor="w", font=(UI_FONT, 10, "bold")
        ).pack(side="left")
        self.track_var = tk.StringVar(value=_external())
        self.track_box = ttk.Combobox(
            srow, textvariable=self.track_var, values=[_external()], state="readonly", width=40
        )
        self.track_box.pack(side="left", padx=px(6))
        self.track_box.bind("<<ComboboxSelected>>", lambda _e: self._schedule())
        self.sub_row = FileRow(
            body,
            t("hard_subber.subtitle_file"),
            kind="subtitle",
            on_change=lambda _p: self._subs_changed(),
        )
        self.sub_row.pack(fill="x", padx=px(20), pady=px(2))

        pv = tk.Frame(body)
        pv.pack(pady=px(6))
        self.preview = FramePreview(pv, 480, 270)
        self.preview.pack()
        self.pos_var = tk.DoubleVar(value=0)
        self.slider = ttk.Scale(
            pv,
            from_=0,
            to=1,
            variable=self.pos_var,
            length=px(480),
            command=lambda _v: self.preview.show(float(self.pos_var.get())),
        )
        self.slider.pack(fill="x")
        framestep.slider(self.slider, self.preview)  # , and . step a frame

        st = tk.LabelFrame(
            body, text=t("hard_subber.style_srt_vtt_or_override_ass"), padx=px(10), pady=px(6)
        )
        st.pack(fill="x", padx=px(20), pady=px(4))
        r0 = tk.Frame(st)
        r0.pack(fill="x", pady=px(2))
        tk.Label(r0, text=t("hard_subber.font_label")).pack(side="left")
        self.font_var = tk.StringVar(value="Open Sans")
        ttk.Combobox(
            r0, textvariable=self.font_var, values=ass.families(["Arial", "DejaVu Sans"]), width=16
        ).pack(side="left", padx=px(6))
        tk.Label(r0, text=t("hard_subber.size_label")).pack(side="left", padx=px(8, 0))
        self.size_var = tk.IntVar(value=22)
        tk.Scale(
            r0,
            variable=self.size_var,
            from_=10,
            to=48,
            orient="horizontal",
            length=px(140),
            command=lambda _v: self._schedule(),
        ).pack(side="left", padx=px(4))
        return body, r0, st

    def _build_output(self, body: tk.Frame, r0: tk.Frame, st: tk.LabelFrame) -> None:
        self.col_btn = ColourButton(
            r0,
            "#FFFFFF",
            text=t("hard_subber.text"),
            width=8,
            on_change=lambda _c: self._schedule(),
        )
        self.col_btn.pack(side="left", padx=px(4))
        self.out_btn = ColourButton(
            r0,
            "#000000",
            text=t("hard_subber.outline_box"),
            width=12,
            on_change=lambda _c: self._schedule(),
        )
        self.out_btn.pack(side="left", padx=px(4))
        r1 = tk.Frame(st)
        r1.pack(fill="x", pady=px(2))
        tk.Label(r1, text=t("hard_subber.outline_label")).pack(side="left")
        self.outline_var = tk.DoubleVar(value=2.0)
        tk.Scale(
            r1,
            variable=self.outline_var,
            from_=0,
            to=6,
            resolution=0.5,
            orient="horizontal",
            length=px(110),
            command=lambda _v: self._schedule(),
        ).pack(side="left", padx=px(4))
        self.box_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r1, text=t("hard_subber.background_box"), variable=self.box_var, command=self._schedule
        ).pack(side="left", padx=px(8))
        tk.Label(r1, text=t("hard_subber.position_label")).pack(side="left")
        self.position_var = tk.StringVar(value=POSITION_LABELS["bottom"])
        pb = ttk.Combobox(
            r1,
            textvariable=self.position_var,
            values=list(POSITION_LABELS.values()),
            state="readonly",
            width=14,
        )
        pb.pack(side="left", padx=px(4))
        pb.bind("<<ComboboxSelected>>", lambda _e: self._schedule())
        tk.Label(r1, text=t("hard_subber.margin")).pack(side="left", padx=px(8, 0))
        self.margin_var = tk.IntVar(value=20)
        tk.Scale(
            r1,
            variable=self.margin_var,
            from_=0,
            to=120,
            orient="horizontal",
            length=px(110),
            command=lambda _v: self._schedule(),
        ).pack(side="left", padx=px(4))
        self.override_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            st,
            text=t("hard_subber.apply_this_style_to_ass_subtitles_too"),
            variable=self.override_var,
            command=self._schedule,
        ).pack(anchor="w")
        for var in (self.font_var,):
            on_write(var, lambda: self._schedule())

    def _build_out(self, body: tk.Frame) -> None:
        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9"),
            suggest=lambda: self.suggest_output(self.src_row.path, "subbed"),
        )
        self.btn_render = self.make_render_btn(
            body, t("hard_subber.burn_subtitles"), self._render, width=24
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

    # ── state ────────────────────────────────────────────────────────────────

    def _source_changed(self, path: str) -> None:
        self._info = ff.try_probe(path)
        info = self._info
        self._tracks = []
        values = [_external()]
        if info is not None:
            subs = [s for s in info.streams if s.type == "subtitle"]
            for i, s in enumerate(subs):
                lang, title = s.language, s.title
                label = (
                    f"Track {i + 1}: {s.codec}"
                    + (f" [{lang}]" if lang else "")
                    + (f" {title}" if title else "")
                )
                if s.codec in ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"):
                    label += " (picture-based)"
                self._tracks.append((label, i, s.codec))
                values.append(label)
        self.track_box.config(values=values)
        self.track_var.set(values[1] if len(values) > 1 and not self.sub_row.path else _external())
        dur = info.duration if info else 0.0
        self.slider.configure(to=max(dur, 0.01))
        self.pos_var.set(min(dur * 0.2, 30.0))
        self.preview.load(path, dur, float(self.pos_var.get()))
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "subbed"))
        self._schedule()

    def _subs_changed(self) -> None:
        self.track_var.set(_external())
        self._schedule()

    def _style(self) -> str:
        key = POSITION_LABELS.code_of(self.position_var.get(), "bottom")
        return force_style(
            font=self.font_var.get() or "Open Sans",
            size=int(self.size_var.get()),
            colour=self.col_btn.colour,
            outline_colour=self.out_btn.colour,
            outline=float(self.outline_var.get()),
            box=self.box_var.get(),
            position=key,
            margin=int(self.margin_var.get()),
        )

    def subtitle_filter(self, folder: str) -> str:
        """The burn filter; external files are first copied (UTF-8) into *folder*."""
        track = next((tr for tr in self._tracks if tr[0] == self.track_var.get()), None)
        if track is not None:
            _label, index, codec = track
            if codec in ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"):
                raise ValueError(
                    "Picture-based subtitle tracks are burned with the overlay "
                    "method - choose a text track or an external file."
                )
            return fl.subtitles_filter(
                self.src_row.path,
                stream_index=index,
                force_style=self._style(),
                fonts_dir=ass.fonts_dir(),
            )
        path = self.sub_row.path
        if not path or not os.path.isfile(path):
            raise ValueError("Choose a subtitle file (or a subtitle track inside the video).")
        copy = to_utf8_copy(path, folder)
        is_ass = copy.lower().endswith((".ass", ".ssa"))
        style = self._style() if (not is_ass or self.override_var.get()) else ""
        return fl.subtitles_filter(copy, force_style=style, fonts_dir=ass.fonts_dir())

    def _schedule(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(300, self._refresh)

    def _refresh(self) -> None:
        self._job = None
        if not self.src_row.path:
            return
        if self._work is None:
            from core.paths import temp_dir

            self._work = temp_dir("subber_preview_")
        try:
            vf = self.subtitle_filter(self._work)
        except (ValueError, OSError):
            self.preview.set_filter("")
            return
        self.preview.set_filter(vf, keep_time=True)

    # ── render ───────────────────────────────────────────────────────────────

    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        from core.paths import temp_dir

        work = temp_dir("subber_")
        try:
            chain = self.subtitle_filter(work)
        except (ValueError, OSError) as exc:
            shutil.rmtree(work, ignore_errors=True)
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(
            self,
            self.out_row,
            src,
            "subbed",
            inputs=[self.sub_row.path] if self.sub_row.path else [],
        )
        if not out:
            shutil.rmtree(work, ignore_errors=True)
            return

        def done(rc: int) -> None:
            shutil.rmtree(work, ignore_errors=True)
            self.show_result(rc, out)

        common.render(
            self,
            RenderJob(src=src, info=info, out=out, chain=chain, name="Hard-Subber"),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
            on_done=done,
        )

    def on_app_closing(self) -> None:
        if self._work:
            shutil.rmtree(self._work, ignore_errors=True)
            self._work = None  # a new one is made if the page is used again
