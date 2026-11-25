"""
tabs/visuals/region_blur.py  -  Privacy Blur

Hide faces, number plates, names or screens: drag rectangles on the frame,
choose blur, pixelate or a solid box for each, and when it applies (from /
to, mm:ss or seconds).  Regions are stored relative to the frame, so they fit
any resolution; any number of regions of any kind can be combined.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops.region_blur import KINDS, Region, blur_graph
from core.ops.shared import RenderJob
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit.time_fields import RangePicker
from tabs.kit.tktyped import selected_rows
from tabs.widgets import FileRow, FramePreview, output_section, scroll_body

PRESETS = {  # name: (x, y, w, h) as fractions of the frame
    "Face (centre)": (0.38, 0.18, 0.24, 0.40),
    "Number plate (lower centre)": (0.40, 0.70, 0.20, 0.08),
    "Bottom strip (names, captions)": (0.0, 0.82, 1.0, 0.18),
    "Top-left corner": (0.0, 0.0, 0.25, 0.18),
    "Top-right corner": (0.75, 0.0, 0.25, 0.18),
    "Whole frame": (0.0, 0.0, 1.0, 1.0),
}
PW, PH = 480, 270


@dataclass
class _Drag:
    """A region being drawn on the preview: where the press was, the box so far."""

    start: tuple[int, int]
    rect: tuple[int, int, int, int] | None = None


class RegionBlurTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info: ff.MediaInfo | None = None
        self.regions: list[Region] = []
        self._drag: _Drag | None = None
        self._job: str | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        body, left, top = self._build_header()
        ed = self._build_selected_region(left, top)
        self._build_output(body, ed)
        self._build_console(body)

    def _build_header(self) -> tuple[tk.Frame, tk.Frame, tk.Frame]:
        self.make_header(
            self,
            t("tab.privacy_blur"),
            t("region_blur.hide_faces_plates_names_or_screens"),
            icon="🕶",
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        top = tk.Frame(body)
        top.pack(fill="x", padx=px(20), pady=px(4))
        left = tk.Frame(top)
        left.pack(side="left")
        self.preview = FramePreview(left, PW, PH)
        self.preview.pack()
        self.preview.after_draw = self._draw_regions
        c = self.preview.canvas
        c.config(cursor="crosshair")
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._move)
        c.bind("<ButtonRelease-1>", self._release)
        self.pos_var = tk.DoubleVar(value=0)
        self.slider = ttk.Scale(
            left,
            from_=0,
            to=1,
            variable=self.pos_var,
            length=px(PW),
            command=lambda _v: self.preview.show(float(self.pos_var.get())),
        )
        self.slider.pack(fill="x")
        framestep.slider(self.slider, self.preview)  # , and . step a frame
        self.result_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            left,
            text=t("region_blur.show_the_result_in_the_preview"),
            variable=self.result_var,
            command=self._refresh_preview,
        ).pack(anchor="w")
        return body, left, top

    def _build_selected_region(self, left: tk.Frame, top: tk.Frame) -> tk.LabelFrame:
        tk.Label(
            left,
            text=t("region_blur.drag_on_the_picture_to_add_a"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w")

        right = tk.Frame(top)
        right.pack(side="left", fill="both", expand=True, padx=px(12, 0))
        tk.Label(right, text=t("region_blur.regions"), font=(UI_FONT, 10, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(right, height=7, font=(MONO_FONT, 9), exportselection=False)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self._select())
        pr = tk.Frame(right)
        pr.pack(fill="x", pady=px(2))
        self.preset_var = tk.StringVar(value="Add a preset region…")
        pb = ttk.Combobox(
            pr, textvariable=self.preset_var, values=list(PRESETS), state="readonly", width=28
        )
        pb.pack(side="left")
        pb.bind("<<ComboboxSelected>>", lambda _e: self._add_preset())
        tk.Button(pr, text=t("btn.remove"), relief="flat", command=self._remove).pack(
            side="left", padx=px(4)
        )
        ed = tk.LabelFrame(right, text=t("region_blur.selected_region"), padx=px(8), pady=px(4))
        ed.pack(fill="x", pady=px(4))
        self.kind_var = tk.StringVar(value=KINDS["blur"])
        ttk.Combobox(
            ed, textvariable=self.kind_var, values=list(KINDS.values()), state="readonly", width=16
        ).grid(row=0, column=0, sticky="w")
        self.strength_var = tk.DoubleVar(value=0.6)
        tk.Scale(
            ed,
            variable=self.strength_var,
            from_=0.1,
            to=1.0,
            resolution=0.05,
            orient="horizontal",
            length=px(140),
            label=t("region_blur.strength_col"),
        ).grid(row=0, column=1, sticky="w", padx=px(6))
        return ed

    def _build_output(self, body: tk.Frame, ed: tk.LabelFrame) -> None:
        tf = tk.Frame(ed)
        tf.grid(row=1, column=0, columnspan=2, sticky="w")
        tk.Label(tf, text=t("muter.from")).pack(side="left")
        self.window = RangePicker(
            tf, end_hint="(empty = to the end)", here=lambda: float(self.pos_var.get())
        )
        self.window.pack(side="left")
        tk.Button(
            ed, text=t("region_blur.apply_to_region"), relief="flat", command=self._apply_edit
        ).grid(row=2, column=0, sticky="w", pady=px(2))
        here = tk.Frame(ed)
        here.grid(row=2, column=1, sticky="w")
        tk.Button(here, text=t("region_blur.from_here"), relief="flat", command=self.mark_in).pack(
            side="left"
        )
        tk.Button(here, text=t("region_blur.to_here"), relief="flat", command=self.mark_out).pack(
            side="left", padx=px(4, 0)
        )

        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9"),
            suggest=lambda: self.suggest_output(self.src_row.path, "blurred"),
        )
        self.btn_render = self.make_render_btn(
            body, t("region_blur.apply_blur"), self._render, width=22
        )

    def _build_console(self, body: tk.Frame) -> None:
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

    # ── Source / preview ──────────────────────────────────────────────
    def _source_changed(self, path: str) -> None:
        self._info = info = ff.try_probe(path)
        dur = info.duration if info else 0.0
        self.slider.configure(to=max(dur, 0.01))
        self.preview.load(path, dur, 0.0)
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "blurred"))
        self._refresh_preview()

    def _size(self) -> tuple[int, int]:
        return common.even_size(self._info) if self._info and self._info.has_video else (1920, 1080)

    def _refresh_preview(self) -> None:
        if self.result_var.get() and self.regions:
            w, h = self._size()
            self.preview.set_filter(blur_graph(self.regions, w, h, "[in]", ""), keep_time=True)
        else:
            self.preview.set_filter("", keep_time=False)
        self._draw_regions()

    def _draw_regions(self) -> None:
        c = self.preview.canvas
        c.delete("region")
        box = self.preview.image_box()
        if box is None:
            return
        x0, y0, bw, bh = box
        sel = self._selected()
        for i, r in enumerate(self.regions):
            colour = CLR["accent"] if i == sel else CLR["orange"]
            c.create_rectangle(
                x0 + r.x * bw,
                y0 + r.y * bh,
                x0 + (r.x + r.w) * bw,
                y0 + (r.y + r.h) * bh,
                outline=colour,
                width=px(2),
                dash=() if i == sel else px(4, 2),
                tags="region",
            )
            c.create_text(
                x0 + r.x * bw + px(4),
                y0 + r.y * bh + px(2),
                text=str(i + 1),
                anchor="nw",
                fill=colour,
                font=(UI_FONT, 9, "bold"),
                tags="region",
            )
        if self._drag and self._drag.rect:
            a, b, cc, d = self._drag.rect
            c.create_rectangle(
                a, b, cc, d, outline="white", width=px(1), dash=px(3, 3), tags="region"
            )

    def _frac(self, ex: int, ey: int) -> tuple[float, float] | None:
        box = self.preview.image_box()
        if box is None:
            return None
        x0, y0, bw, bh = box
        return min(1.0, max(0.0, (ex - x0) / bw)), min(1.0, max(0.0, (ey - y0) / bh))

    def _press(self, ev: tk.Event[tk.Misc]) -> None:
        if self.preview.image_box() is not None:
            self._drag = _Drag((ev.x, ev.y))

    def _move(self, ev: tk.Event[tk.Misc]) -> None:
        if self._drag:
            sx, sy = self._drag.start
            self._drag.rect = (min(sx, ev.x), min(sy, ev.y), max(sx, ev.x), max(sy, ev.y))
            self._draw_regions()

    def _release(self, ev: tk.Event[tk.Misc]) -> None:
        drag, self._drag = self._drag, None
        if not drag or not drag.rect:
            return
        a = self._frac(drag.rect[0], drag.rect[1])
        b = self._frac(drag.rect[2], drag.rect[3])
        if a and b and (b[0] - a[0]) > 0.01 and (b[1] - a[1]) > 0.01:
            self._add(Region(a[0], a[1], b[0] - a[0], b[1] - a[1]))
        self._draw_regions()

    # ── Regions ───────────────────────────────────────────────────────
    def _selected(self) -> int | None:
        sel = selected_rows(self.listbox)
        return sel[0] if sel else None

    def _add(self, region: Region) -> None:
        region.kind = next((k for k, v in KINDS.items() if v == self.kind_var.get()), "blur")
        region.strength = float(self.strength_var.get())
        self.regions.append(region)
        self._relist(len(self.regions) - 1)

    def _add_preset(self) -> None:
        frac = PRESETS.get(self.preset_var.get())
        if frac:
            self._add(Region(*frac))
        self.preset_var.set("Add a preset region…")

    def _remove(self) -> None:
        i = self._selected()
        if i is not None:
            self.regions.pop(i)
            self._relist(min(i, len(self.regions) - 1))

    def _relist(self, select: int | None = None) -> None:
        self.listbox.delete(0, "end")
        for i, r in enumerate(self.regions):
            self.listbox.insert("end", f"{i + 1}. {r.label()}")
        if select is not None and select >= 0 and self.regions:
            self.listbox.selection_set(select)
            self._select()
        self._refresh_preview()

    def _select(self) -> None:
        i = self._selected()
        if i is None:
            return
        r = self.regions[i]
        self.kind_var.set(KINDS[r.kind])
        self.strength_var.set(r.strength)
        self.window.set(r.start, r.end)
        self._draw_regions()

    # i / o (and the buttons): the selected region starts / stops at the frame shown
    def mark_in(self) -> None:
        self.window.start.use_here()
        if self._selected() is not None:
            self._apply_edit()

    def mark_out(self) -> None:
        self.window.end.use_here()
        if self._selected() is not None:
            self._apply_edit()

    def _apply_edit(self) -> None:
        i = self._selected()
        if i is None:
            return
        try:
            start, end = self.window.span()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        r = self.regions[i]
        r.kind = next((k for k, v in KINDS.items() if v == self.kind_var.get()), "blur")
        r.strength, r.start, r.end = float(self.strength_var.get()), start, end
        self._relist(i)

    # ── Render ────────────────────────────────────────────────────────
    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        if not self.regions:
            messagebox.showwarning(
                t("common.warning"), t("region_blur.add_at_least_one_region_drag_on"), parent=self
            )
            return
        out = common.output_for(self, self.out_row, src, "blurred")
        if not out:
            return
        w, h = common.even_size(info)
        graph = blur_graph(self.regions, w, h)
        common.render(
            self,
            RenderJob(src=src, info=info, out=out, graph=graph, name="Privacy Blur"),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
        )
