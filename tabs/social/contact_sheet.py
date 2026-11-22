"""
tabs/social/contact_sheet.py  -  Contact Sheet: a grid of frames from a video,
with its details on top.

Open a video and a classic contact sheet appears straight away (4 x 5 frames,
the file's details, each frame's time).  From there:

* the grid: preset sizes or any columns x rows, tile width, gaps, margin,
  borders, background;
* the frames: "Previous set" / "Next set" show other moments (each set lands
  between the ones before, never repeating one), evenly spread or at shot
  changes, leaving out the start and end (titles, credits) if wanted;
* the text: which details the header shows (or a title of your own), where
  the times go, tile numbers, font, size and colour;
* the picture: PNG, JPEG or WebP.

The preview is the real sheet, made small; saving makes the same sheet at full
size with the same frames.  The work is core/ops/contact_sheet.py.
"""

from __future__ import annotations

import os
import shutil
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import partial
from tkinter import messagebox

from core import ass, proc, tasks
from core import ffmpeg as ff
from core.errors import Cancelled, CommandError
from core.i18n import t
from core.ops import contact_sheet as cs
from core.paths import temp_dir
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab, add_tooltip
from tabs.kit.widgets import ChoiceBox, ColourButton
from tabs.widgets import FileRow, FramePreview, SaveRow, scroll_body

GRIDS = ((3, 3), (4, 4), (4, 5), (5, 6), (6, 8), (8, 10))
SAMPLING = {cs.Sampling.EVEN: "Evenly through the video", cs.Sampling.SCENES: "At shot changes"}
CORNERS = {
    cs.Corner.BOTTOM_RIGHT: "Bottom right",
    cs.Corner.BOTTOM_LEFT: "Bottom left",
    cs.Corner.TOP_RIGHT: "Top right",
    cs.Corner.TOP_LEFT: "Top left",
    cs.Corner.OFF: "Off",
}
DETAILS = {
    cs.Detail.NAME: "File name",
    cs.Detail.SIZE: "File size",
    cs.Detail.DURATION: "Duration",
    cs.Detail.RESOLUTION: "Resolution",
    cs.Detail.FPS: "Frame rate",
    cs.Detail.BITRATE: "Bit rate",
    cs.Detail.VIDEO: "Video codec",
    cs.Detail.AUDIO: "Audio",
    cs.Detail.CONTAINER: "Container",
}


class Picture(StrEnum):
    PNG = "png"
    JPG = "jpg"
    WEBP = "webp"


PICTURES = {
    Picture.PNG: "PNG (sharpest)",
    Picture.JPG: "JPEG (smaller)",
    Picture.WEBP: "WebP (smallest)",
}


class Sensitivity(StrEnum):
    FEWER = "fewer"
    NORMAL = "normal"
    MORE = "more"


SENSITIVITY = {
    Sensitivity.FEWER: "Clear cuts only",
    Sensitivity.NORMAL: "Normal",
    Sensitivity.MORE: "Subtle cuts too",
}
THRESHOLDS = {Sensitivity.FEWER: 0.45, Sensitivity.NORMAL: 0.3, Sensitivity.MORE: 0.18}
PREVIEW_W, PREVIEW_H = 880, 520


class ContactSheetTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self._info: ff.MediaInfo | None = None
        self._variant = 0
        self._cuts: dict[tuple[str, float], list[float]] = {}  # (file, threshold) -> shot changes
        self._gen = 0  # bumps on every preview request: older results are dropped
        self._after: str | None = None
        self._folder: str | None = None  # the previews (made on first use)
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.make_header(
            self,
            t("tab.contact_sheet"),
            "A grid of frames from a video, with its details - choose the grid, step through other frames.",
            icon="🎞",
        )
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), kind="video", on_change=self._load)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self._build_preview(body)
        cols = tk.Frame(body)
        cols.pack(fill="x", padx=px(20), pady=px(6, 0))
        self._build_grid(cols)
        self._build_frames(cols)
        self._build_text(cols)
        self._build_output(body)

    def _build_preview(self, body):
        self.preview = FramePreview(body, PREVIEW_W, PREVIEW_H, show_time=False)
        self.preview.pack(pady=px(6, 0))
        nav = tk.Frame(body)
        nav.pack(pady=px(4, 0))
        self.btn_prev = tk.Button(
            nav, text="◀  Previous set", relief="flat", command=partial(self._step, -1)
        )
        self.btn_prev.pack(side="left")
        add_tooltip(self.btn_prev, "Back to the set before")
        self.set_lbl = tk.Label(
            nav, text="Set 1", width=8, font=(UI_FONT, 10, "bold"), fg=CLR["accent"]
        )
        self.set_lbl.pack(side="left", padx=px(8))
        self.btn_next = tk.Button(
            nav, text="Next set  ▶", relief="flat", command=partial(self._step, 1)
        )
        self.btn_next.pack(side="left")
        add_tooltip(self.btn_next, "Other moments from the video (the sets never repeat)")
        tk.Button(nav, text="⟲ First set", relief="flat", command=self._first_set).pack(
            side="left", padx=px(12, 0)
        )
        self.status_lbl = tk.Label(
            body, text="Open a video to see its contact sheet.", fg=CLR["fgdim"]
        )
        self.status_lbl.pack(pady=px(2, 0))

    def _spin(self, parent, row, label, value, low, high, *, step=1, unit=""):
        """A labelled number box on *row* of *parent*'s grid; its StringVar."""
        tk.Label(parent, text=label, anchor="w").grid(row=row, column=0, sticky="w", pady=px(2))
        var = tk.StringVar(value=str(value))
        box = tk.Spinbox(parent, from_=low, to=high, increment=step, textvariable=var, width=6)
        box.grid(row=row, column=1, sticky="w", pady=px(2))
        if unit:
            tk.Label(parent, text=unit, fg=CLR["fgdim"]).grid(row=row, column=2, sticky="w")
        var.trace_add("write", self._changed)
        return var

    def _build_grid(self, cols):
        f = tk.LabelFrame(cols, text="  Grid  ", padx=px(10), pady=px(6))
        f.pack(side="left", fill="both", expand=True, padx=px(0, 6))
        presets = tk.Frame(f)
        presets.grid(row=0, column=0, columnspan=3, sticky="w", pady=px(0, 4))
        for c, r in GRIDS:
            b = tk.Button(
                presets, text=f"{c}×{r}", relief="flat", width=4, command=partial(self._grid, c, r)
            )
            b.pack(side="left", padx=px(0, 2))
        self.cols_var = self._spin(f, 1, "Columns:", 4, 1, 12)
        self.rows_var = self._spin(f, 2, "Rows:", 5, 1, 20)
        self.tile_var = self._spin(f, 3, "Tile width:", 384, 120, 1280, step=16, unit="px")
        self.gap_var = self._spin(f, 4, "Gap:", 8, 0, 60, unit="px")
        self.margin_var = self._spin(f, 5, "Margin:", 20, 0, 160, unit="px")
        self.border_var = self._spin(f, 6, "Border:", 0, 0, 16, unit="px")
        swatches = tk.Frame(f)
        swatches.grid(row=7, column=0, columnspan=3, sticky="w", pady=px(4, 0))
        self.bg_btn = ColourButton(
            swatches, "#141414", text="Background", width=12, on_change=self._recolour
        )
        self.bg_btn.pack(side="left", padx=px(0, 4))
        self.border_btn = ColourButton(
            swatches, "#FFFFFF", text="Border", width=12, on_change=self._recolour
        )
        self.border_btn.pack(side="left")

    def _build_frames(self, cols):
        f = tk.LabelFrame(cols, text="  Frames  ", padx=px(10), pady=px(6))
        f.pack(side="left", fill="both", expand=True, padx=px(0, 6))
        tk.Label(f, text="Take frames:", anchor="w").grid(row=0, column=0, sticky="w")
        self.sampling = ChoiceBox(
            f, SAMPLING, cs.Sampling.EVEN, width=22, on_change=self._sampling_changed
        )
        self.sampling.grid(row=1, column=0, columnspan=3, sticky="w", pady=px(0, 4))
        tk.Label(f, text="Shot changes:", anchor="w").grid(row=2, column=0, sticky="w")
        self.sensitivity = ChoiceBox(
            f, SENSITIVITY, Sensitivity.NORMAL, width=16, on_change=self._changed
        )
        self.sensitivity.grid(row=3, column=0, columnspan=3, sticky="w", pady=px(0, 4))
        self.skip_start_var = self._spin(f, 4, "Skip start:", 0, 0, 45, unit="%")
        self.skip_end_var = self._spin(f, 5, "Skip end:", 0, 0, 45, unit="%")
        tk.Label(
            f,
            text="Skipping leaves out opening titles and end credits.",
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
            wraplength=px(200),
            justify="left",
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=px(4, 0))
        self._sampling_changed()

    def _build_text(self, cols):
        f = tk.LabelFrame(cols, text="  Text  ", padx=px(10), pady=px(6))
        f.pack(side="left", fill="both", expand=True)
        ticks = tk.Frame(f)
        ticks.grid(row=0, column=0, columnspan=4, sticky="w")
        self.detail_vars: dict[cs.Detail, tk.BooleanVar] = {}
        for i, (detail, label) in enumerate(DETAILS.items()):
            var = tk.BooleanVar(value=detail in cs.DEFAULT_DETAILS)
            var.trace_add("write", self._changed)
            tk.Checkbutton(ticks, text=label, variable=var).grid(
                row=i // 3, column=i % 3, sticky="w"
            )
            self.detail_vars[detail] = var
        tk.Label(f, text="Title:", anchor="w").grid(row=1, column=0, sticky="w", pady=px(4, 0))
        self.title_var = tk.StringVar()
        self.title_var.trace_add("write", self._changed)
        title = tk.Entry(f, textvariable=self.title_var, width=24)
        title.grid(row=1, column=1, columnspan=3, sticky="w", pady=px(4, 0))
        add_tooltip(title, "Shown in place of the file name (leave empty for the name)")
        tk.Label(f, text="Times:", anchor="w").grid(row=2, column=0, sticky="w", pady=px(2))
        self.corner = ChoiceBox(
            f, CORNERS, cs.Corner.BOTTOM_RIGHT, width=12, on_change=self._changed
        )
        self.corner.grid(row=2, column=1, sticky="w")
        self.numbers_var = tk.BooleanVar(value=False)
        self.numbers_var.trace_add("write", self._changed)
        tk.Checkbutton(f, text="Tile numbers", variable=self.numbers_var).grid(
            row=2, column=2, columnspan=2, sticky="w"
        )
        self._build_font(f)

    def _build_font(self, f):
        tk.Label(f, text="Font:", anchor="w").grid(row=3, column=0, sticky="w", pady=px(2))
        self.font_var = tk.StringVar(value="Montserrat")
        fonts = tk.OptionMenu(f, self.font_var, *ass.families())
        fonts.configure(relief="flat", width=14)
        fonts.grid(row=3, column=1, sticky="w")
        self.font_var.trace_add("write", self._changed)
        size = tk.Frame(f)
        size.grid(row=4, column=0, columnspan=4, sticky="w")
        self.text_size_var = self._spin(size, 0, "Text size:", 0, 0, 60, unit="(0 = automatic)")
        self.text_btn = ColourButton(
            f, "#FFFFFF", text="Text colour", width=12, on_change=self._recolour
        )
        self.text_btn.grid(row=5, column=0, columnspan=2, sticky="w", pady=px(4, 0))

    def _build_output(self, body):
        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(8))
        self.out_row = SaveRow(
            out_lf, t("common.output_file"), kind="image", ext=".png", suggest=self._suggest
        )
        self.out_row.pack(fill="x")
        row = tk.Frame(out_lf)
        row.pack(fill="x", pady=px(4, 0))
        tk.Label(row, text="Picture:").pack(side="left")
        self.picture = ChoiceBox(
            row, PICTURES, Picture.PNG, width=18, on_change=self._picture_changed
        )
        self.picture.pack(side="left", padx=px(6))
        self.quality_var = tk.StringVar(value="92")
        tk.Label(row, text="Quality:").pack(side="left", padx=px(12, 4))
        tk.Spinbox(row, from_=40, to=100, textvariable=self.quality_var, width=5).pack(side="left")
        self.btn_render = self.make_render_btn(
            body, "💾  Save contact sheet", self._render, width=24
        )
        self.btn_render.pack(pady=px(6))
        self.console = self.make_console_block(body, height=4)

    # ── Settings ──────────────────────────────────────────────────────────

    def _grid(self, cols, rows):
        self.cols_var.set(str(cols))
        self.rows_var.set(str(rows))

    def _recolour(self, _colour: str) -> None:
        self._changed()

    def _sampling_changed(self, *_):
        scenes = self.sampling.value is cs.Sampling.SCENES
        self.sensitivity.configure(state="readonly" if scenes else "disabled")
        self._changed()

    def _picture_changed(self, picture):
        path = self.out_row.path
        if path:
            self.out_row.set(os.path.splitext(path)[0] + "." + picture.value)

    def _settings(self) -> tuple[cs.Layout, cs.Labels, cs.Picking]:
        """The sheet as set on the page.  ValueError (a sentence) when a box is wrong."""
        layout = cs.Layout(
            cols=int(common.number(self.cols_var.get(), "Columns", 1, 12)),
            rows=int(common.number(self.rows_var.get(), "Rows", 1, 20)),
            tile_width=int(common.number(self.tile_var.get(), "Tile width", 64, 1920)),
            gap=int(common.number(self.gap_var.get(), "Gap", 0, 200)),
            margin=int(common.number(self.margin_var.get(), "Margin", 0, 400)),
            border=int(common.number(self.border_var.get(), "Border", 0, 40)),
            background=self.bg_btn.colour,
            border_colour=self.border_btn.colour,
        )
        labels = cs.Labels(
            details=frozenset(d for d, var in self.detail_vars.items() if var.get()),
            title=self.title_var.get(),
            timestamps=self.corner.value,
            numbers=self.numbers_var.get(),
            font=self.font_var.get(),
            text_size=int(common.number(self.text_size_var.get() or "0", "Text size", 0, 120)),
            text_colour=self.text_btn.colour,
        )
        picking = cs.Picking(
            sampling=self.sampling.value,
            skip_start=common.number(self.skip_start_var.get() or "0", "Skip start", 0, 45),
            skip_end=common.number(self.skip_end_var.get() or "0", "Skip end", 0, 45),
            variant=self._variant,
        )
        return layout, labels, picking

    # ── The preview ───────────────────────────────────────────────────────

    def _load(self, path):
        self._info = ff.try_probe(path) if path else None
        self._variant = 0
        if self._info is not None and not self._info.has_video:
            self._info = None
            self.status_lbl.config(text="That file has no picture - choose a video.", fg=CLR["red"])
        self._changed()

    def _step(self, by):
        self._variant = max(0, self._variant + by)
        self._changed()

    def _first_set(self):
        self._variant = 0
        self._changed()

    def _changed(self, *_):
        """Any setting changed: make a new preview once the typing stops."""
        self.set_lbl.config(text=f"Set {self._variant + 1}")
        self.btn_prev.config(state="normal" if self._variant > 0 else "disabled")
        if self._after is not None:
            self.after_cancel(self._after)
        self._after = self.after(350, self._preview)

    def _preview(self):
        self._after = None
        info = self._info
        if info is None:
            return
        try:
            layout, labels, picking = self._settings()
        except ValueError as exc:
            self.status_lbl.config(text=str(exc), fg=CLR["red"])
            return
        self._gen += 1
        self._folder = self._folder or temp_dir("sheet_preview_")
        small, k = _smaller(info, layout)
        if labels.text_size:
            labels = replace(labels, text_size=max(6, round(labels.text_size * k)))
        job = _Preview(
            sheet=cs.Plan(info.path, (), cs.geometry(info, small, 0), small, labels, ()),
            info=info,
            picking=picking,
            threshold=self._threshold(),
            cuts=self._cuts,
            folder=self._folder,
            gen=self._gen,
            stale=self._stale,
        )
        full = cs.plan(info, layout, self._settings()[1], picking).geo
        self.status_lbl.config(text="Making the preview…", fg=CLR["fgdim"])
        tasks.submit(
            job.make,
            on_done=partial(self._preview_ready, job.gen, layout.count, full),
            on_error=partial(self._preview_failed, job.gen),
            name="ContactSheetPreview",
            pool="preview",
        )

    def _stale(self, gen: int) -> bool:
        return gen != self._gen

    def _threshold(self) -> float | None:
        if self.sampling.value is not cs.Sampling.SCENES:
            return None
        return THRESHOLDS[self.sensitivity.value]

    def _preview_ready(self, gen, frames, full, path):
        if gen != self._gen or not self.winfo_exists():
            return
        self.preview.load(path, duration=0.0)
        self.status_lbl.config(
            text=f"{frames} frames  ·  saved at {full.width} × {full.height} px", fg=CLR["fgdim"]
        )

    def _preview_failed(self, gen, exc):
        if gen != self._gen or isinstance(exc, Cancelled):
            return
        self.status_lbl.config(text=f"No preview: {exc}", fg=CLR["red"])

    # ── Saving ────────────────────────────────────────────────────────────

    def _suggest(self):
        src = self.src_row.path
        return (
            self.suggest_output(src, "contact_sheet", "." + self.picture.value.value) if src else ""
        )

    def _output(self, src: str) -> str | None:
        ext = "." + self.picture.value.value
        out = self.out_row.path or self.suggest_output(src, "contact_sheet", ext)
        if os.path.splitext(out)[1].lower().replace("jpeg", "jpg") != ext:
            out = os.path.splitext(out)[0] + ext
        out = self.confirm_output(out, [src], default_ext=ext)
        if out:
            self.out_row.set(out)
        return out

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        try:
            layout, labels, picking = self._settings()
            quality = int(common.number(self.quality_var.get() or "92", "Quality", 1, 100))
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = self._output(src)
        if not out:
            return
        threshold = self._threshold()
        job = cs.SheetJob(
            info=info,
            layout=layout,
            labels=labels,
            picking=picking,
            threshold=threshold,
            cuts=self._cuts.get((src, threshold)) if threshold is not None else None,
            quality=quality,
        )
        self.log(self.console, f"Contact sheet {layout.cols}×{layout.rows} → {out}")
        common.queue_job(
            self,
            common.Job(name="Contact Sheet", out=out, work=job.run, duration=None, inputs=[src]),
            btn=self.btn_render,
            console=self.console,
        )

    def on_app_closing(self):
        if self._folder:
            shutil.rmtree(self._folder, ignore_errors=True)
            self._folder = None  # a new one is made if the page is used again


def _smaller(info: ff.MediaInfo, layout: cs.Layout) -> tuple[cs.Layout, float]:
    """*layout* shrunk so the preview sheet is about the preview's width, and by how much."""
    full = cs.geometry(info, layout, 0)
    k = min(1.0, PREVIEW_W * 1.25 / max(full.width, 1))
    if k >= 1.0:
        return layout, 1.0
    small = replace(
        layout,
        tile_width=max(48, int(layout.tile_width * k)),
        gap=round(layout.gap * k),
        margin=round(layout.margin * k),
        border=max(1, round(layout.border * k)) if layout.border else 0,
    )
    return small, small.tile_width / layout.tile_width


def _shot_changes(src: str, threshold: float, cancel: Callable[[], bool]) -> list[float]:
    res = proc.run(cs.scene_cmd(src, threshold), timeout=None, cancel=cancel, merge_stderr=True)
    if not res.ok:
        raise CommandError("couldn't look for shot changes in this file")
    return cs.parse_cuts(res.stdout.splitlines())


@dataclass(frozen=True, kw_only=True)
class _Preview:
    """Makes a small sheet in the background (finding the shot changes first
    when needed, remembered in *cuts*); stops when a newer one is asked for."""

    sheet: cs.Plan  # the small layout and labels (times worked out here)
    info: ff.MediaInfo
    picking: cs.Picking
    threshold: float | None
    cuts: dict[tuple[str, float], list[float]]
    folder: str
    gen: int
    stale: Callable[[int], bool]

    def _stale(self) -> bool:
        return self.stale(self.gen)

    def make(self) -> str:
        cuts: list[float] = []
        if self.threshold is not None:
            key = (self.info.path, self.threshold)
            if key not in self.cuts:
                self.cuts[key] = _shot_changes(self.info.path, self.threshold, self._stale)
            cuts = self.cuts[key]
        p = cs.plan(self.info, self.sheet.layout, self.sheet.labels, self.picking, cuts)
        tiles = os.path.join(self.folder, f"p{self.gen}")
        os.makedirs(tiles, exist_ok=True)
        out = os.path.join(self.folder, f"sheet{self.gen}.png")
        try:
            cs.make(p, tiles, out, _quick_run, cancelled=self._stale)
        finally:
            shutil.rmtree(tiles, ignore_errors=True)
        return out


def _quick_run(cmd: list[str], _done: float) -> bool:
    return proc.run(cmd, timeout=60).ok
