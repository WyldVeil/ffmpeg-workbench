"""
tabs/social/collage.py  -  Video Collage / Grid

Any number of clips on one canvas, playing together.

* Auto layout (default): the arrangement that shows every clip as large as
  possible while keeping them similar in size - uniform grids, gallery-style
  rows or columns for mixed shapes (see core/grid.py).  Fixed grids and a
  picture-in-picture layout are still there.
* Fit (whole clips, bars where shapes differ) or fill (cells filled, edges
  cropped), gap and background colour.
* Length: the longest clip by default (shorter ones loop), the shortest, or
  any length.  With looping off, a finished clip holds its last frame,
  leaves the background showing, or the others re-arrange to fill its
  space ("reflow").
* Sound from the first clip, all clips mixed, or none.
"""

from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import grid
from core.i18n import Choices, Labels, t
from core.ops.shared import frame_rate
from core.scaling import px
from core.timecode import Timecode
from tabs import common
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit import dialogs
from tabs.kit.tktyped import linked_scrollbar, on_write, selected_rows
from tabs.widgets import EncoderPanel, SaveRow


def _fmt(seconds: float) -> str:
    return f"{Timecode.of(seconds):.2}"


@dataclass(frozen=True, slots=True)
class _Layout:
    """A collage layout the user can pick."""

    label: str
    slots: int  # clips it takes (Auto: any number, at least 2)
    cols: int = 1
    rows: int = 1
    auto: bool = False  # Auto: the best grid for however many clips
    pip: bool = False  # main video with pictures-in-picture


@dataclass(frozen=True, slots=True)
class _Slot:
    """A clip added to the collage."""

    path: str
    name: str
    duration: float
    aspect: float
    has_audio: bool


LAYOUTS = {  # stable ids; labels are what the user sees
    "auto": _Layout("Auto - best fit for any number of clips", 1, auto=True),
    "2x1": _Layout("2×1 (Side by side)", 2, 2, 1),
    "1x2": _Layout("1×2 (Stacked)", 2, 1, 2),
    "2x2": _Layout("2×2 (Quad grid)", 4, 2, 2),
    "3x1": _Layout("3×1 (Triple wide)", 3, 3, 1),
    "1x3": _Layout("1×3 (Triple tall)", 3, 1, 3),
    "3x2": _Layout("3×2 (Six pack)", 6, 3, 2),
    "3x3": _Layout("3×3 (Nine grid)", 9, 3, 3),
    "pip": _Layout("Main video + 2 picture-in-picture", 3, 2, 2, pip=True),
}
BACKGROUNDS = Choices(
    "collage.backgrounds",
    {"Black": "black", "White": "white", "Grey": "gray", "Charcoal": "0x1E1E1E"},
)
AUDIO_MODES = Labels(
    "collage.audio_modes",
    {"first": "Audio from clip 1", "mix": "Mix all clips", "none": "No audio"},
)
LENGTHS = Labels(
    "collage.lengths",
    {
        "longest": "As long as the longest clip",
        "shortest": "As long as the shortest clip",
        "custom": "Custom length (seconds):",
    },
)
ENDS = Labels(
    "collage.ends",
    {
        "hold": "hold its last frame",
        "background": "show the background",
        "reflow": "re-arrange the others",
    },
)
MAX_CLIPS = 25


#: how the layouts are shown (in the current language)
LAYOUT_LABELS = Labels("collage.layouts", {k: v.label for k, v in LAYOUTS.items()})


class VideoCollageTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self._slots: list[_Slot] = []
        self._job: str | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        slots_lf, stb = self._build_header()
        or2, out_lf = self._build_output_settings(slots_lf, stb)
        self._build_audio_mode(or2, out_lf)
        self._changed()

    def _build_header(self) -> tuple[tk.LabelFrame, tk.Frame]:
        self.make_header(
            self,
            t("tab.video_collage"),
            t("collage.any_number_of_clips_on_one_screen"),
            icon="🎞",
        )
        layout_lf = tk.LabelFrame(
            self,
            text=f"  {t('collage.grid_layout_section')}  ",
            padx=px(15),
            pady=px(8),
            font=(UI_FONT, 9, "bold"),
        )
        layout_lf.pack(fill="x", padx=px(20), pady=px(10, 6))
        left = tk.Frame(layout_lf)
        left.pack(side="left", fill="y")
        self._layout_var = tk.StringVar(value=LAYOUT_LABELS["auto"])
        lb = ttk.Combobox(
            left,
            textvariable=self._layout_var,
            values=list(LAYOUT_LABELS.values()),
            state="readonly",
            width=40,
        )
        lb.pack(anchor="w")
        lb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self._mode_var = tk.StringVar(value="fit")
        tk.Radiobutton(
            left,
            text=t("collage.show_whole_clips_bars_where_shapes_differ"),
            variable=self._mode_var,
            value="fit",
            command=self._changed,
        ).pack(anchor="w", pady=px(6, 0))
        tk.Radiobutton(
            left,
            text=t("collage.fill_each_space_crop_the_edges"),
            variable=self._mode_var,
            value="fill",
            command=self._changed,
        ).pack(anchor="w")
        self._layout_lbl = tk.Label(
            left, text="", fg=CLR["accent"], justify="left", wraplength=px(330)
        )
        self._layout_lbl.pack(anchor="w", pady=px(6, 0))
        self._grid_canvas = tk.Canvas(
            layout_lf, bg=CLR["console_bg"], width=px(320), height=px(180), highlightthickness=0
        )
        self._grid_canvas.pack(side="right", padx=px(10), pady=px(4))

        slots_lf = tk.LabelFrame(
            self,
            text=f"  {t('collage.video_slots_section')}  ",
            padx=px(15),
            pady=px(6),
            font=(UI_FONT, 9, "bold"),
        )
        slots_lf.pack(fill="both", expand=True, padx=px(20), pady=px(4))
        stb = tk.Frame(slots_lf)
        stb.pack(fill="x", pady=px(0, 4))
        for text, cmd in (
            ("➕ " + t("collage.add_videos_button"), self._add_videos),
            ("🗑 " + t("collage.remove_button"), self._remove_selected),
            ("⬆", self._move_up),
            ("⬇", self._move_down),
            (t("collage.clear_button"), self._clear_all),
        ):
            tk.Button(stb, text=text, relief="flat", cursor="hand2", command=cmd).pack(
                side="left", padx=px(2)
            )
        return slots_lf, stb

    def _set_out_size(self, w: int, h: int) -> None:
        self._out_w.set(str(w))
        self._out_h.set(str(h))

    def _build_output_settings(
        self, slots_lf: tk.LabelFrame, stb: tk.Frame
    ) -> tuple[tk.Frame, tk.LabelFrame]:
        self._slot_count_lbl = tk.Label(stb, text=t("collage.0_videos_loaded"), fg=CLR["fgdim"])
        self._slot_count_lbl.pack(side="right", padx=px(8))
        list_f = tk.Frame(slots_lf)
        list_f.pack(fill="both", expand=True)
        self._slot_list = tk.Listbox(
            list_f,
            bg=CLR["console_bg"],
            fg=CLR["console_fg"],
            font=(MONO_FONT, 9),
            selectmode="browse",
            height=5,
            relief="flat",
            bd=0,
        )
        lsb = linked_scrollbar(list_f, self._slot_list)
        self._slot_list.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

        out_lf = tk.LabelFrame(
            self,
            text=f"  {t('collage.output_settings_section')}  ",
            padx=px(15),
            pady=px(6),
            font=(UI_FONT, 9, "bold"),
        )
        out_lf.pack(fill="x", padx=px(20), pady=px(4))
        or1 = tk.Frame(out_lf)
        or1.pack(fill="x", pady=px(2))
        tk.Label(or1, text=t("collage.output_resolution_label")).pack(side="left")
        self._out_w, self._out_h = tk.StringVar(value="1920"), tk.StringVar(value="1080")
        tk.Entry(or1, textvariable=self._out_w, width=6).pack(side="left", padx=px(4))
        tk.Label(or1, text="×").pack(side="left")
        tk.Entry(or1, textvariable=self._out_h, width=6).pack(side="left", padx=px(4))
        for w, h, lbl in [
            (1920, 1080, "1080p"),
            (1280, 720, "720p"),
            (3840, 2160, "4K"),
            (1080, 1920, "9:16"),
            (1080, 1080, "1:1"),
        ]:
            tk.Button(
                or1,
                text=lbl,
                relief="flat",
                width=5,
                command=partial(self._set_out_size, w, h),
            ).pack(side="left", padx=px(2))
        for v in (self._out_w, self._out_h):
            on_write(v, lambda: self._changed())
        or2 = tk.Frame(out_lf)
        or2.pack(fill="x", pady=px(2))
        tk.Label(or2, text=t("collage.grid_padding_label")).pack(side="left")
        self._pad_var = tk.StringVar(value="4")
        tk.Entry(or2, textvariable=self._pad_var, width=4).pack(side="left", padx=px(4))
        on_write(self._pad_var, lambda: self._changed())
        tk.Label(or2, text=t("collage.px_background")).pack(side="left")
        self._bg_color = tk.StringVar(value=BACKGROUNDS.label("Black"))
        ttk.Combobox(
            or2, textvariable=self._bg_color, width=9, values=list(BACKGROUNDS), state="readonly"
        ).pack(side="left", padx=px(4))
        return or2, out_lf

    def _build_audio_mode(self, or2: tk.Frame, out_lf: tk.LabelFrame) -> None:
        tk.Label(or2, text=t("collage.sound")).pack(side="left")
        self._audio_mode = tk.StringVar(value=AUDIO_MODES["first"])
        ttk.Combobox(
            or2,
            textvariable=self._audio_mode,
            values=list(AUDIO_MODES.values()),
            state="readonly",
            width=16,
        ).pack(side="left", padx=px(4))
        or3 = tk.Frame(out_lf)
        or3.pack(fill="x", pady=px(2))
        tk.Label(or3, text=t("collage.length")).pack(side="left")
        self._length_var = tk.StringVar(value=LENGTHS["longest"])
        lc = ttk.Combobox(
            or3,
            textvariable=self._length_var,
            values=list(LENGTHS.values()),
            state="readonly",
            width=28,
        )
        lc.pack(side="left", padx=px(4))
        lc.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self._custom_len = tk.StringVar(value="30")
        tk.Entry(or3, textvariable=self._custom_len, width=7).pack(side="left")
        or4 = tk.Frame(out_lf)
        or4.pack(fill="x", pady=px(2))
        self._loop_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            or4,
            text=t("collage.loop_shorter_clips"),
            variable=self._loop_var,
            command=self._changed,
        ).pack(side="left")
        tk.Label(or4, text=t("collage.otherwise_when_a_clip_ends")).pack(side="left")
        self._end_var = tk.StringVar(value=ENDS["hold"])
        self._end_cb = ttk.Combobox(
            or4, textvariable=self._end_var, values=list(ENDS.values()), state="readonly", width=24
        )
        self._end_cb.pack(side="left", padx=px(4))
        self._end_cb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1"), show_ten_bit=False)
        self.encoder.pack(anchor="w", pady=px(4, 0))

        self._out_var = tk.StringVar()
        SaveRow(self, t("common.output_file"), self._out_var).pack(
            fill="x", padx=px(20), pady=px(4)
        )
        self.btn_render = self.make_render_btn(
            self, t("collage.build_collage"), self._render, width=26
        )
        self.btn_render.pack(pady=px(6))
        self.console = self.make_console_block(self, height=4, pady=px(2, 10))

    # ── State helpers ─────────────────────────────────────────────────
    def _layout_key(self) -> str:
        return LAYOUT_LABELS.code_of(self._layout_var.get(), "auto")

    def _key(self, table: Mapping[str, str], var: tk.StringVar, default: str) -> str:
        return next((k for k, v in table.items() if v == var.get()), default)

    def _canvas_size(self) -> tuple[int, int]:
        try:
            return (
                int(ff_number(self._out_w.get(), 64, 8192)) // 2 * 2,
                int(ff_number(self._out_h.get(), 64, 8192)) // 2 * 2,
            )
        except ValueError:
            return 1920, 1080

    def _gap(self) -> int:
        try:
            return int(ff_number(self._pad_var.get() or "0", 0, 200))
        except ValueError:
            return 0

    def _clips_for_layout(self) -> list[_Slot]:
        key = self._layout_key()
        spec = LAYOUTS[key]
        if spec.auto:
            return self._slots[:MAX_CLIPS]
        return self._slots[: spec.slots]

    def _layout(self, aspects: list[float]) -> grid.Layout:
        key = self._layout_key()
        spec = LAYOUTS[key]
        w, h = self._canvas_size()
        mode = self._mode_var.get()
        if spec.auto:
            return grid.best_layout(aspects, w, h, gap=self._gap(), mode=mode)
        return grid.uniform(aspects, w, h, spec.cols, self._gap(), mode == "fill")

    def _changed(self) -> None:
        loop = self._loop_var.get()
        self._end_cb.config(state="disabled" if loop else "readonly")
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(150, self._draw_grid_preview)

    def _draw_grid_preview(self) -> None:
        self._job = None
        c = self._grid_canvas
        c.delete("all")
        key = self._layout_key()
        spec = LAYOUTS[key]
        W, H = self._canvas_size()
        cw, ch = px(320), px(180)
        scale = min(cw / W, ch / H)
        ox, oy = (cw - W * scale) / 2, (ch - H * scale) / 2
        c.create_rectangle(ox, oy, ox + W * scale, oy + H * scale, outline="#444444", width=px(1))
        if spec.pip:
            self._layout_lbl.config(text=t("collage.main_video_full_screen_the_others_in"))
            return
        clips = self._clips_for_layout()
        aspects = [s.aspect for s in clips] or [16 / 9] * (4 if spec.auto else spec.slots)
        if not spec.auto and len(aspects) < spec.slots:
            aspects += [16 / 9] * (spec.slots - len(aspects))
        try:
            lay = self._layout(aspects)
        except ValueError:
            return
        colors = [
            CLR["accent"],
            CLR["green"],
            CLR["orange"],
            CLR.get("pink", "#E91E63"),
            "#9C27B0",
            "#00BCD4",
            "#FF5722",
            "#8BC34A",
            "#FFC107",
        ]
        for i, (x, y, w, h) in enumerate(lay.rects):
            x1, y1 = ox + x * scale, oy + y * scale
            c.create_rectangle(
                x1,
                y1,
                x1 + w * scale,
                y1 + h * scale,
                fill=colors[i % len(colors)],
                outline="#222222",
                width=px(1),
            )
            name = clips[i].name[:10] if i < len(clips) else f"#{i + 1}"
            c.create_text(
                x1 + w * scale / 2,
                y1 + h * scale / 2,
                text=name,
                fill="white",
                font=(UI_FONT, 7, "bold"),
            )
        extra = ""
        if spec.auto and len(self._slots) > 16:
            extra = "\nMany clips play at once - rendering will be slow."
        self._layout_lbl.config(text=lay.describe() + extra)

    # ── Clips ─────────────────────────────────────────────────────────
    def load_file(self, path: str) -> bool:
        if len(self._slots) >= MAX_CLIPS:
            self.log(self.console, t("collage.at_most_max_clips_clips", max_clips=MAX_CLIPS))
            return True
        info = ff.try_probe(path)
        if info is None or not info.has_video:
            self.log(self.console, t("collage.path_no_video_skipped", path=os.path.basename(path)))
            return True
        w, h = info.require_video().display_size
        sar = info.require_video().sar or "1:1"
        try:
            num, den = (int(x) for x in sar.split(":"))
            sar_f = num / den if num and den else 1.0
        except ValueError:
            sar_f = 1.0
        dur = (
            info.require_video().duration if info.require_video().duration > 0.05 else info.duration
        )
        self._slots.append(
            _Slot(path, os.path.basename(path), dur, (w * sar_f) / max(1, h), info.has_audio)
        )
        self._refresh_list()
        return True

    def _add_videos(self) -> None:
        for p in dialogs.ask_open_many("video", title=t("collage.add_clips"), parent=self):
            self.load_file(p)

    def _remove_selected(self) -> None:
        sel = selected_rows(self._slot_list)
        if sel:
            self._slots.pop(sel[0])
            self._refresh_list()

    def _move_up(self) -> None:
        sel = selected_rows(self._slot_list)
        if sel and sel[0] > 0:
            i = sel[0]
            self._slots[i - 1], self._slots[i] = self._slots[i], self._slots[i - 1]
            self._refresh_list()
            self._slot_list.selection_set(i - 1)

    def _move_down(self) -> None:
        sel = selected_rows(self._slot_list)
        if sel and sel[0] < len(self._slots) - 1:
            i = sel[0]
            self._slots[i + 1], self._slots[i] = self._slots[i], self._slots[i + 1]
            self._refresh_list()
            self._slot_list.selection_set(i + 1)

    def _clear_all(self) -> None:
        self._slots.clear()
        self._refresh_list()

    def _refresh_list(self) -> None:
        self._slot_list.delete(0, "end")
        for i, s in enumerate(self._slots):
            self._slot_list.insert("end", f"  #{i + 1}   {s.name:<40s}  {_fmt(s.duration)}")
        self._slot_count_lbl.config(text=t("collage.slots_clip_s", slots=len(self._slots)))
        self._changed()

    # ── Render ────────────────────────────────────────────────────────
    def _length(self, clips: list[_Slot], length_key: str) -> float | None:
        """The collage's length: the longest clip, the shortest, or the typed one (None: bad number)."""
        durs = [s.duration for s in clips]
        if length_key == "longest":
            return max(durs)
        if length_key == "shortest":
            return min(durs)
        try:
            return common.number(self._custom_len.get(), t("intro_maker.length"), 0.5, 24 * 3600)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return None

    def _graph(
        self,
        spec: _Layout,
        clips: list[_Slot],
        geometry: tuple[int, int, str],
        total: float,
        length_key: str,
    ) -> tuple[str, bool]:
        """(filter graph, has sound): picture-in-picture, or the grid (fixed or automatic)."""
        W, H, fps = geometry
        loop = self._loop_var.get()
        end = self._key(ENDS, self._end_var, "hold")
        audio = self._key(AUDIO_MODES, self._audio_mode, "first")
        bg = BACKGROUNDS.get(self._bg_color.get(), "black")
        if spec.pip:
            canvas = grid.Canvas(W, H, fps=fps, gap=self._gap(), background=bg)
            has_audio = [s.has_audio for s in clips]
            return grid.pip_filter(
                has_audio, canvas, shortest=length_key == "shortest", audio=audio
            )
        gclips = [grid.Clip(i, s.aspect, s.duration, s.has_audio) for i, s in enumerate(clips)]
        layouts: dict[grid.LayoutKey, grid.Layout] | None = (
            None if spec.auto else {"all": self._layout([c.aspect for c in gclips])}
        )
        if not spec.auto and not loop and end == "reflow":
            end = "hold"  # fixed grids keep their cells
        canvas = grid.Canvas(
            W, H, fps=fps, gap=self._gap(), mode=self._mode_var.get(), background=bg
        )
        return grid.collage_filter(
            gclips, canvas, grid.Timing(total, loop=loop, end=end, audio=audio), layouts
        )

    def _render(self) -> None:
        key = self._layout_key()
        spec = LAYOUTS[key]
        clips = self._clips_for_layout()
        need = 2 if spec.auto else spec.slots
        if len(clips) < need:
            messagebox.showwarning(
                t("collage.not_enough_title"),
                t(
                    "collage.this_layout_needs_need_clips_slots_added",
                    need=need,
                    slots=len(self._slots),
                ),
                parent=self,
            )
            return
        W, H = self._canvas_size()
        length_key = self._key(LENGTHS, self._length_var, "longest")
        total = self._length(clips, length_key)
        if total is None:
            return
        paths = [s.path for s in clips]
        out = self._out_var.get().strip() or self.suggest_output(paths[0], "collage")
        out = self.confirm_output(out, paths)
        if not out:
            return
        self._out_var.set(out)
        loop = self._loop_var.get()
        fps_info = ff.try_probe(paths[0])
        fps = frame_rate(fps_info) if fps_info is not None else "30"
        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        graph, has_a = self._graph(spec, clips, (W, H, fps), total, length_key)
        looped = loop and not spec.pip
        cmd = grid.collage_cmd(paths, graph, venc, container, total, out, audio=has_a, loop=looped)
        what = spec.label if not spec.auto else self._layout([c.aspect for c in clips]).describe()
        self.log(
            self.console,
            t(
                "collage.clips_clips_what_w_h_total",
                clips=len(clips),
                what=what,
                w=W,
                h=H,
                total=_fmt(total),
            ),
        )
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=total,
            output=out,
            inputs=paths,
            name="Collage",
        )


def ff_number(text: str, lo: float, hi: float) -> float:
    v = float(str(text).strip())
    if not lo <= v <= hi:
        raise ValueError(f"must be between {lo:g} and {hi:g}")
    return v
