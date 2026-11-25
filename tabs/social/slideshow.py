"""
tabs/social/slideshow.py  -  Slideshow Maker: photos, animated GIFs, video clips
and title cards into one video, with transitions, captions and music.

* Slides: photos (Ken Burns motion), GIFs (they play, looping to fill their
  time), video clips (any part, with or without their own sound) and title
  or ending cards.  Each slide can have its own time, transition, motion and
  caption; the rest follow the slideshow's settings.  Sort by name or date,
  shuffle, reorder.
* Transitions: all 61 of core/ops/transitions.py, grouped (fades, wipes,
  slides, soft edges, shapes, slices, effects such as fading squares and
  swirl), a cut, or a random everyday one per slide; "Preview transition"
  plays the selected slide into the next.
* Music: any number of tracks one after another (repeated if needed),
  starting part-way into the first, faded in and out, quieter while a clip's
  own sound plays; the slideshow can be fitted to the music, and every
  change can land on a beat.

The work is core/ops/slideshow.py.
"""

from __future__ import annotations

import contextlib
import os
import random
import re
import shutil
import tkinter as tk
from collections.abc import Iterable
from dataclasses import dataclass, replace
from functools import partial
from tkinter import messagebox, ttk
from typing import TypeVar

from core import ass, proc, tasks
from core import beats as beats_core
from core import encoders as enc
from core import ffmpeg as ff
from core.hardware import launch_preview
from core.i18n import t
from core.ops import slideshow as ss
from core.ops import transitions as tr
from core.paths import temp_dir
from core.queue_manager import TaskContext
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, BaseTab, add_tooltip
from tabs.kit.time_fields import MomentPicker, RangePicker
from tabs.kit.tktyped import linked_scrollbar, selected_rows
from tabs.kit.widgets import ColourButton
from tabs.widgets import output_section, scroll_body

RANDOM = "random"
SAME = "(as the slideshow)"  # a slide's choice that follows the slideshow's
MOTIONS = {
    "zoom_in": "Zoom in",
    "zoom_out": "Zoom out",
    "pan_right": "Pan right",
    "pan_left": "Pan left",
    "pan_up": "Pan up",
    "pan_down": "Pan down",
    RANDOM: "Random (varies per slide)",
    "static": "None (still)",
}
FITS = {
    "fill": "Fill the frame (crop edges)",
    "blur": "Fit with blurred background",
    "bars": "Fit with plain bars",
}
RESOLUTIONS = {
    "1920×1080 (YouTube)": (1920, 1080),
    "3840×2160 (4K)": (3840, 2160),
    "1280×720 (HD)": (1280, 720),
    "1080×1920 (Reels / Shorts)": (1080, 1920),
    "1080×1350 (Instagram portrait)": (1080, 1350),
    "1080×1080 (square)": (1080, 1080),
}
CAPTION_POS = {"bottom": "Bottom", "top": "Top", "lower_third": "Lower third (left)"}
KIND_NAMES = {
    ss.Kind.PHOTO: "Photo",
    ss.Kind.ANIMATION: "GIF",
    ss.Kind.VIDEO: "Video",
    ss.Kind.CARD: "Card",
}
#: the transition drop-downs: what they show -> the transition
TRANSITIONS = {
    "Random (an everyday one per slide)": RANDOM,
    "None (cut)": ss.CUT,
    **{
        f"{group}: {label}": name
        for group, names in tr.GROUPS.items()
        for name, label in names.items()
    },
}
TRANSITION_NAMES = {name: label for label, name in TRANSITIONS.items()}
#: what the drop-downs show -> the setting
MOTION_KEYS = {label: key for key, label in MOTIONS.items()}
FIT_KEYS = {label: key for key, label in FITS.items()}
CAPTION_KEYS = {label: key for key, label in CAPTION_POS.items()}


@dataclass
class Slide:
    """A slide as set on the page (None: as the slideshow)."""

    path: str
    kind: ss.Kind
    caption: str = ""
    seconds: float | None = None
    transition: str | None = None  # into the next slide
    motion: str | None = None
    start: float = 0.0  # videos: the part shown
    end: float | None = None
    sound: bool = True  # videos: keep their sound
    length: float | None = None  # videos and GIFs: the file's length (None: not read yet)
    has_audio: bool = False
    card: ss.Card | None = None

    @property
    def name(self) -> str:
        if self.card is not None:
            return self.card.title or "(card)"
        return os.path.basename(self.path)


def natural_key(path: str) -> list[int | str]:
    return [
        int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", os.path.basename(path))
    ]


def _probe(path: str) -> tuple[float, bool]:
    info = ff.try_probe(path)
    return (info.duration, info.has_audio) if info else (0.0, False)


V = TypeVar("V", bound=tk.Variable)


class SlideshowMakerTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.slides: list[Slide] = []
        self.tracks: list[tuple[str, float]] = []  # music: (file, length)
        self._loading = False  # filling the slide editor: its traces must not write back
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.make_header(
            self,
            t("tab.slideshow_maker"),
            t("slideshow.photos_gifs_video_clips_and_title_cards"),
            icon="🖼",
        )
        body = scroll_body(self)
        self._build_list(body)
        self._build_editor(body)
        self._build_settings(body)
        self._build_music(body)
        self._build_output(body)
        self._selected()

    def _build_list(self, body: tk.Frame) -> None:
        top = tk.Frame(body)
        top.pack(fill="x", padx=px(20), pady=px(10, 4))
        left = tk.Frame(top)
        left.pack(side="left", fill="both", expand=True)
        cols = (("n", "#", 36), ("file", "Slide", 220), ("kind", "Kind", 60), ("time", "Time", 70))
        cols += (("trans", "Transition after", 170), ("caption", "Caption", 180))
        self.tree = ttk.Treeview(
            left, columns=[c[0] for c in cols], show="headings", height=9, selectmode="browse"
        )
        for c, label, w in cols:
            self.tree.heading(c, text=label)
            self.tree.column(c, width=px(w), anchor="w", stretch=c in ("file", "caption"))
        self.tree.pack(side="left", fill="both", expand=True)
        sb = linked_scrollbar(left, self.tree)
        sb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._selected)
        btns = tk.Frame(top)
        btns.pack(side="left", fill="y", padx=px(8, 0))
        for text, cmd in (
            ("➕ Add photos / videos", self._add_files),
            ("📁 Add folder", self._add_folder),
            ("🅰 Add title card", self._add_card),
            ("⬆ Up", partial(self._move, -1)),
            ("⬇ Down", partial(self._move, 1)),
            ("🗑 Remove", self._remove),
            ("🧹 Clear", self._clear),
            ("🔤 Sort by name", self._sort_name),
            ("🕑 Sort by date", self._sort_date),
            ("🔀 Shuffle", self._shuffle),
        ):
            tk.Button(
                btns, text=text, relief="flat", anchor="w", width=20, cursor="hand2", command=cmd
            ).pack(fill="x", pady=px(1))
        self.count_lbl = tk.Label(btns, text=t("slideshow.0_slides"), fg=CLR["fgdim"])
        self.count_lbl.pack(pady=px(6, 0))

    def _build_editor(self, body: tk.Frame) -> None:
        self.editor = tk.LabelFrame(
            body, text=t("slideshow.selected_slide"), padx=px(10), pady=px(6)
        )
        self.editor.pack(fill="x", padx=px(20), pady=px(4))
        row = tk.Frame(self.editor)
        row.pack(fill="x")
        tk.Label(row, text=t("slideshow.caption"), width=12, anchor="w").pack(side="left")
        self.cap_var = self._traced(tk.StringVar())
        tk.Entry(row, textvariable=self.cap_var, width=40).pack(side="left", fill="x", expand=True)
        row = tk.Frame(self.editor)
        row.pack(fill="x", pady=px(3, 0))
        tk.Label(row, text=t("slideshow.time_s"), width=12, anchor="w").pack(side="left")
        self.slide_secs_var = self._traced(tk.StringVar())
        secs = tk.Entry(row, textvariable=self.slide_secs_var, width=6)
        secs.pack(side="left")
        add_tooltip(secs, t("slideshow.empty_as_the_slideshow_videos_the_length"))
        tk.Label(row, text=t("slideshow.transition_after")).pack(side="left", padx=px(12, 4))
        self.slide_trans_var = self._traced(tk.StringVar(value=SAME))
        ttk.Combobox(
            row,
            textvariable=self.slide_trans_var,
            values=[SAME, *TRANSITIONS],
            state="readonly",
            width=34,
        ).pack(side="left")
        tk.Button(
            row, text=t("youtube.preview"), relief="flat", command=self._preview_transition
        ).pack(side="left", padx=px(6, 0))
        self._build_kind_rows()

    def _build_kind_rows(self) -> None:
        """The rows that only some kinds of slide have (shown for those)."""
        self.motion_row = tk.Frame(self.editor)
        tk.Label(self.motion_row, text=t("slideshow.motion"), width=12, anchor="w").pack(
            side="left"
        )
        self.slide_motion_var = self._traced(tk.StringVar(value=SAME))
        ttk.Combobox(
            self.motion_row,
            textvariable=self.slide_motion_var,
            values=[SAME, *MOTIONS.values()],
            state="readonly",
            width=26,
        ).pack(side="left")
        self.video_row = tk.Frame(self.editor)
        tk.Label(self.video_row, text=t("slideshow.part"), width=12, anchor="w").pack(side="left")
        self.part = RangePicker(
            self.video_row, start="0", end_hint="(empty = to the end)", on_change=self._edited
        )
        self.part.pack(side="left")
        self.sound_var = self._traced(tk.BooleanVar(value=True))
        tk.Checkbutton(
            self.video_row, text=t("slideshow.keep_its_sound"), variable=self.sound_var
        ).pack(side="left", padx=px(12, 0))
        self.card_row = tk.Frame(self.editor)
        tk.Label(self.card_row, text=t("chapter_markers.title"), width=12, anchor="w").pack(
            side="left"
        )
        self.card_title_var = self._traced(tk.StringVar())
        tk.Entry(self.card_row, textvariable=self.card_title_var, width=26).pack(side="left")
        tk.Label(self.card_row, text=t("slideshow.subtitle_2")).pack(side="left", padx=px(10, 4))
        self.card_sub_var = self._traced(tk.StringVar())
        tk.Entry(self.card_row, textvariable=self.card_sub_var, width=26).pack(side="left")
        self.card_bg_btn = ColourButton(
            self.card_row,
            "#101010",
            text=t("intro_maker.background_section"),
            width=10,
            on_change=lambda _c: self._edited(),
        )
        self.card_bg_btn.pack(side="left", padx=px(10, 0))

    def _combo(
        self, parent: tk.Misc, var: tk.StringVar, values: Iterable[str], width: int
    ) -> ttk.Combobox:
        return ttk.Combobox(
            parent, textvariable=var, values=list(values), state="readonly", width=width
        )

    def _build_settings(self, body: tk.Frame) -> None:
        opt = tk.LabelFrame(body, text=t("slideshow.slideshow"), padx=px(10), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(6))
        self.dur_var, self.trans_dur_var = tk.StringVar(value="4"), tk.StringVar(value="1.0")
        self.form_row(
            opt,
            0,
            t("slideshow.seconds_per_slide"),
            tk.Entry(opt, textvariable=self.dur_var, width=6),
            tk.Label(opt, text=t("slideshow.transition_s")),
            tk.Entry(opt, textvariable=self.trans_dur_var, width=6),
            label_width=16,
            pady=2,
        )
        self.trans_var = tk.StringVar(value=TRANSITION_NAMES["fade"])
        self.motion_var = tk.StringVar(value=MOTIONS[RANDOM])
        self.form_row(
            opt,
            1,
            t("slideshow.transition"),
            self._combo(opt, self.trans_var, TRANSITIONS, 34),
            tk.Label(opt, text=t("slideshow.motion")),
            self._combo(opt, self.motion_var, MOTIONS.values(), 24),
            label_width=16,
            pady=2,
        )
        self.fit_var, self.zoom_var = tk.StringVar(value=FITS["blur"]), tk.DoubleVar(value=1.2)
        zoom = tk.Scale(
            opt,
            variable=self.zoom_var,
            from_=1.05,
            to=1.6,
            resolution=0.05,
            orient="horizontal",
            length=px(120),
        )
        self.form_row(
            opt,
            2,
            t("slideshow.framing"),
            self._combo(opt, self.fit_var, FITS.values(), 28),
            tk.Label(opt, text=t("slideshow.motion_amount")),
            zoom,
            label_width=16,
            pady=2,
        )
        self.bars_btn = ColourButton(opt, "#000000", text=t("slideshow.bars_colour"), width=10)
        self.bars_btn.grid(row=2, column=5, sticky="w")
        self.res_var, self.fps_var = (
            tk.StringVar(value=next(iter(RESOLUTIONS))),
            tk.StringVar(value="30"),
        )
        self.form_row(
            opt,
            3,
            t("hard_subber.size_label"),
            self._combo(opt, self.res_var, RESOLUTIONS, 30),
            tk.Label(opt, text="FPS:"),
            self._combo(opt, self.fps_var, ("24", "25", "30", "50", "60"), 5),
            label_width=16,
            pady=2,
        )
        self.cap_on, self.cap_font = tk.BooleanVar(value=True), tk.StringVar(value="Montserrat")
        self.cap_pos = tk.StringVar(value=CAPTION_POS["bottom"])
        self.form_row(
            opt,
            4,
            t("slideshow.captions"),
            tk.Checkbutton(opt, text=t("slideshow.show"), variable=self.cap_on),
            self._combo(opt, self.cap_font, ass.families(), 14),
            self._combo(opt, self.cap_pos, CAPTION_POS.values(), 16),
            label_width=16,
            pady=2,
        )

    def _build_music(self, body: tk.Frame) -> None:
        box = tk.LabelFrame(body, text=t("slideshow.music_optional"), padx=px(10), pady=px(6))
        box.pack(fill="x", padx=px(20), pady=px(4))
        top = tk.Frame(box)
        top.pack(fill="x")
        self.music_list = tk.Listbox(top, height=3, activestyle="none")
        self.music_list.pack(side="left", fill="x", expand=True)
        btns = tk.Frame(top)
        btns.pack(side="left", padx=px(6, 0))
        for text, cmd in (
            ("➕ Add music", self._add_music),
            ("⬆", partial(self._move_track, -1)),
            ("⬇", partial(self._move_track, 1)),
            ("🗑", self._remove_track),
        ):
            tk.Button(btns, text=text, relief="flat", command=cmd).pack(side="left", padx=px(1))
        row = tk.Frame(box)
        row.pack(fill="x", pady=px(4, 0))
        tk.Label(row, text=t("slideshow.start_the_first_track_at")).pack(side="left")
        self.music_start = MomentPicker(row, value="0", width=8)
        self.music_start.pack(side="left", padx=px(4, 12))
        self.music_vol = tk.DoubleVar(value=0.9)
        tk.Label(row, text=t("slideshow.volume_label")).pack(side="left")
        tk.Scale(
            row,
            variable=self.music_vol,
            from_=0.1,
            to=1.5,
            resolution=0.05,
            orient="horizontal",
            length=px(120),
        ).pack(side="left")
        self.fade_in_var, self.fade_out_var = tk.StringVar(value="0.5"), tk.StringVar(value="3")
        for label, var in (
            ("Fade in (s):", self.fade_in_var),
            ("Fade out (s):", self.fade_out_var),
        ):
            tk.Label(row, text=label).pack(side="left", padx=px(10, 2))
            tk.Entry(row, textvariable=var, width=4).pack(side="left")
        row = tk.Frame(box)
        row.pack(fill="x", pady=px(2, 0))
        self.duck_var, self.fit_music_var, self.on_beat_var = (
            tk.BooleanVar(value=True),
            tk.BooleanVar(value=False),
            tk.BooleanVar(value=False),
        )
        for text, var, tip in (
            (
                "Quieter while a video clip speaks",
                self.duck_var,
                "The music dips under the clips' own sound",
            ),
            (
                "Fit the slideshow to the music",
                self.fit_music_var,
                "Photos, GIFs and cards share the music's length (slides with their own time keep it)",
            ),
            (
                "Change slides on the beat",
                self.on_beat_var,
                "Every cut or transition starts on the nearest beat of the first track",
            ),
        ):
            cb = tk.Checkbutton(row, text=text, variable=var)
            cb.pack(side="left", padx=px(0, 12))
            add_tooltip(cb, tip)

    def _build_output(self, body: tk.Frame) -> None:
        self.encoder, self.out_row = output_section(
            body, t("common.output_file"), ("h264", "hevc", "av1", "vp9"), suggest=self._suggest
        )
        self.btn_render = self.make_render_btn(
            body, t("slideshow.create_slideshow"), self._render, width=24
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

    # ── The slide list ───────────────────────────────────────────────────────

    def _time_text(self, s: Slide) -> str:
        if s.seconds is not None:
            return f"{s.seconds:g}s"
        if s.kind is ss.Kind.VIDEO:
            return "…" if s.length is None else f"{self._part_length(s):.1f}s"
        return "default"

    def _refresh_list(self, select: int | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(self.slides):
            trans = (
                ""
                if i == len(self.slides) - 1
                else TRANSITION_NAMES.get(s.transition or "", "default")
            )
            values = (i + 1, s.name, KIND_NAMES[s.kind], self._time_text(s), trans, s.caption)
            self.tree.insert("", "end", iid=str(i), values=values)
        if select is not None and 0 <= select < len(self.slides):
            self.tree.selection_set(str(select))
            self.tree.see(str(select))
        n = len(self.slides)
        self.count_lbl.config(text=t("slideshow.n_slide_value", n=n, value="s" if n != 1 else ""))

    def _index(self) -> int | None:
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def add_paths(self, paths: list[str]) -> int:
        new = [
            Slide(p, kind)
            for p in paths
            if os.path.isfile(p) and (kind := ss.kind_of(p)) is not None
        ]
        self.slides += new
        for s in new:
            if s.kind in (ss.Kind.VIDEO, ss.Kind.ANIMATION):
                tasks.submit(
                    partial(_probe, s.path), on_done=partial(self._probed, s), name="SlideshowProbe"
                )
        self._refresh_list(len(self.slides) - 1 if new else None)
        return len(new)

    def _probed(self, slide: Slide, facts: tuple[float, bool]) -> None:
        slide.length, slide.has_audio = facts
        slide.sound = slide.sound and slide.has_audio
        if self.winfo_exists():
            self._refresh_list(self._index())

    def load_file(self, path: str) -> bool:
        return self.add_paths([path]) > 0

    def _add_files(self) -> None:
        self.add_paths(self.pick_sources("media"))

    def _add_folder(self) -> None:
        from tabs.kit import dialogs

        d = dialogs.ask_directory(parent=self)
        if d:
            files = sorted((os.path.join(d, f) for f in os.listdir(d)), key=natural_key)
            if not self.add_paths(files):
                messagebox.showinfo(
                    t("common.warning"),
                    t("slideshow.no_photos_gifs_or_videos_found_in"),
                    parent=self,
                )

    def _add_card(self) -> None:
        i = self._index()
        at = 0 if i is None else i + 1
        self.slides.insert(at, Slide("", ss.Kind.CARD, card=ss.Card("Title", "")))
        self._refresh_list(at)

    def _move(self, delta: int) -> None:
        i = self._index()
        if i is None or not 0 <= i + delta < len(self.slides):
            return
        self.slides[i], self.slides[i + delta] = self.slides[i + delta], self.slides[i]
        self._refresh_list(i + delta)

    def _remove(self) -> None:
        i = self._index()
        if i is not None:
            del self.slides[i]
            self._refresh_list(min(i, len(self.slides) - 1))

    def _clear(self) -> None:
        self.slides.clear()
        self._refresh_list()

    def _sort_name(self) -> None:
        self.slides.sort(key=_name_key)
        self._refresh_list()

    def _sort_date(self) -> None:
        self.slides.sort(key=_date_key)
        self._refresh_list()

    def _shuffle(self) -> None:
        random.shuffle(self.slides)
        self._refresh_list()

    # ── The selected slide ───────────────────────────────────────────────────

    def _traced(self, var: V) -> V:
        var.trace_add("write", self._edited)
        return var

    def _selected(self, *_: tk.Event[tk.Misc]) -> None:
        i = self._index()
        slide = self.slides[i] if i is not None else None
        self._loading = True
        try:
            self._fill_editor(slide)
        finally:
            self._loading = False
        for row, kinds in (
            (self.motion_row, (ss.Kind.PHOTO,)),
            (self.video_row, (ss.Kind.VIDEO,)),
            (self.card_row, (ss.Kind.CARD,)),
        ):
            if slide is not None and slide.kind in kinds:
                row.pack(fill="x", pady=px(3, 0))
            else:
                row.pack_forget()

    def _fill_editor(self, s: Slide | None) -> None:
        self.cap_var.set(s.caption if s else "")
        self.slide_secs_var.set(f"{s.seconds:g}" if s and s.seconds is not None else "")
        self.slide_trans_var.set(TRANSITION_NAMES.get(s.transition or "", SAME) if s else SAME)
        self.slide_motion_var.set(MOTIONS.get(s.motion or "", SAME) if s else SAME)
        self.part.set(s.start if s else 0.0, s.end if s else None)
        self.sound_var.set(s.sound if s else True)
        card = s.card if s else None
        self.card_title_var.set(card.title if card else "")
        self.card_sub_var.set(card.subtitle if card else "")
        self.card_bg_btn.set(card.background if card else "#101010")

    def _edited(self, *_: str) -> None:
        """A field of the selected slide changed: store it (bad numbers wait for render)."""
        i = self._index()
        if self._loading or i is None:
            return
        s = self.slides[i]
        s.caption = self.cap_var.get()
        text = self.slide_secs_var.get().strip()
        s.seconds = float(text) if _is_number(text) else None
        s.transition = TRANSITIONS.get(self.slide_trans_var.get())
        s.motion = MOTION_KEYS.get(self.slide_motion_var.get())
        s.sound = self.sound_var.get()
        with contextlib.suppress(ValueError):  # a bad part is explained when rendering
            s.start, s.end = self.part.span()
        if s.card is not None:
            s.card = ss.Card(
                self.card_title_var.get(), self.card_sub_var.get(), self.card_bg_btn.colour
            )
        self._refresh_row(i)

    def _refresh_row(self, i: int) -> None:
        s = self.slides[i]
        trans = (
            "" if i == len(self.slides) - 1 else TRANSITION_NAMES.get(s.transition or "", "default")
        )
        self.tree.item(
            str(i), values=(i + 1, s.name, KIND_NAMES[s.kind], self._time_text(s), trans, s.caption)
        )

    # ── Music ────────────────────────────────────────────────────────────────

    def _add_music(self) -> None:
        for path in self.pick_sources("audio"):
            tasks.submit(
                partial(_probe, path),
                on_done=partial(self._music_probed, path),
                name="SlideshowMusic",
            )

    def _music_probed(self, path: str, facts: tuple[float, bool]) -> None:
        length, _audio = facts
        if length > 0:
            self.tracks.append((path, length))
            self._refresh_tracks(len(self.tracks) - 1)

    def _refresh_tracks(self, select: int | None = None) -> None:
        self.music_list.delete(0, tk.END)
        for path, length in self.tracks:
            self.music_list.insert(
                tk.END, f"{os.path.basename(path)}   ({ff.format_time(length, ms=False)})"
            )
        if select is not None and 0 <= select < len(self.tracks):
            self.music_list.selection_set(select)

    def _move_track(self, delta: int) -> None:
        sel = selected_rows(self.music_list)
        if sel and 0 <= sel[0] + delta < len(self.tracks):
            i = sel[0]
            self.tracks[i], self.tracks[i + delta] = self.tracks[i + delta], self.tracks[i]
            self._refresh_tracks(i + delta)

    def _remove_track(self) -> None:
        sel = selected_rows(self.music_list)
        if sel:
            del self.tracks[sel[0]]
            self._refresh_tracks()

    def set_music(self, paths_and_lengths: list[tuple[str, float]]) -> None:
        """Use these (file, length) tracks (tests, and drops onto the page)."""
        self.tracks = list(paths_and_lengths)
        self._refresh_tracks()

    # ── Planning ─────────────────────────────────────────────────────────────

    def _suggest(self) -> str:
        first = next((s.path for s in self.slides if s.path), "")
        if first:
            return os.path.join(os.path.dirname(first), "slideshow.mp4")
        from core.paths import user_videos_dir

        return os.path.join(user_videos_dir(), "slideshow.mp4")

    def _part_length(self, s: Slide) -> float:
        end = s.end if s.end is not None else (s.length or 0.0)
        return max(0.1, min(end, s.length or end) - s.start)

    def _look(self) -> ss.Look:
        w, h = RESOLUTIONS.get(self.res_var.get(), (1920, 1080))
        fit = FIT_KEYS.get(self.fit_var.get(), "blur")
        return ss.Look(
            w, h, self.fps_var.get() or "30", fit, float(self.zoom_var.get()), self.bars_btn.colour
        )

    def _lengths(self) -> list[float | None]:
        """Each slide's own length: its own time, a video's part, else None (the slideshow's)."""
        out: list[float | None] = []
        for i, s in enumerate(self.slides):
            if s.seconds is not None and s.seconds <= 0.2:
                raise ValueError(f"Slide {i + 1}: the time must be more than 0.2 seconds.")
            if s.kind is ss.Kind.VIDEO and s.seconds is None:
                if s.length is None:
                    raise ValueError(
                        f"Slide {i + 1}: still reading the video - try again in a moment."
                    )
                if s.end is not None and s.end <= s.start:
                    raise ValueError(f"Slide {i + 1}: the part's end must be after its start.")
                out.append(self._part_length(s))
            else:
                out.append(s.seconds)
        return out

    def _joins(self, rng: random.Random) -> list[ss.Join]:
        default = TRANSITIONS.get(self.trans_var.get(), "fade")
        secs = common.number(self.trans_dur_var.get(), t("slideshow.transition_section"), 0.1, 10)
        joins: list[ss.Join] = []
        for s in self.slides[:-1]:
            name = s.transition or default
            name = rng.choice(tr.EVERYDAY) if name == RANDOM else name
            joins.append(ss.Join(name, 0.0 if name == ss.CUT else secs))
        return joins

    def _music(self) -> ss.Music | None:
        if not self.tracks:
            return None
        return ss.Music(
            tracks=tuple(p for p, _l in self.tracks),
            lengths=tuple(length for _p, length in self.tracks),
            offset=self.music_start.seconds(0.0, t("slideshow.music_start")) or 0.0,
            volume=float(self.music_vol.get()),
            fade_in=common.number(self.fade_in_var.get() or "0", t("pip.fade_in"), 0, 30),
            fade_out=common.number(self.fade_out_var.get() or "0", t("exporter.fade_out_2"), 0, 60),
            duck=self.duck_var.get(),
        )

    def _plan(self) -> tuple[list[ss.Slide], list[ss.Join], ss.Music | None]:
        """The slideshow as set on the page (ValueError: a sentence saying what's wrong)."""
        default = common.number(self.dur_var.get(), t("slideshow.seconds_per_slide_2"), 0.5, 600)
        rng = random.Random(len(self.slides))
        joins = self._joins(rng)
        lengths, music = self._lengths(), self._music()
        if music is not None and self.fit_music_var.get():
            fixed = [
                None if s.kind is not ss.Kind.VIDEO and s.seconds is None else n
                for s, n in zip(self.slides, lengths, strict=True)
            ]
            span = sum(music.lengths) - music.offset
            seconds = ss.fit_to_music(
                fixed, joins, span, shortest=2 * max([j.overlap for j in joins] or [0]) + 0.5
            )
        else:
            seconds = [default if n is None else n for n in lengths]
        ss.check_joins(seconds, joins)
        default_motion = MOTION_KEYS.get(self.motion_var.get(), RANDOM)
        moves = [m for m in MOTIONS if m not in (RANDOM, "static")]
        slides = [
            ss.Slide(
                kind=s.kind,
                path=s.path,
                seconds=n,
                motion=_motion(s.motion or default_motion, moves, rng),
                caption=s.caption if self.cap_on.get() else "",
                start=s.start,
                sound=s.kind is ss.Kind.VIDEO and s.sound and s.has_audio,
                card=s.card,
            )
            for s, n in zip(self.slides, seconds, strict=True)
        ]
        return slides, joins, music

    # ── Render and preview ───────────────────────────────────────────────────

    def _render(self) -> None:
        if not self.slides:
            messagebox.showwarning(
                t("common.warning"),
                t("slideshow.add_some_photos_gifs_or_videos_first"),
                parent=self,
            )
            return
        missing = [
            s.path for s in self.slides if s.kind is not ss.Kind.CARD and not os.path.isfile(s.path)
        ]
        if missing:
            messagebox.showwarning(
                t("common.warning"),
                t("slideshow.missing_file_missing", missing=missing[0]),
                parent=self,
            )
            return
        try:
            slides, joins, music = self._plan()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        inputs = [s.path for s in slides if s.path] + [p for p, _l in self.tracks]
        out = self.out_row.path or self._suggest()
        out = self.confirm_output(out, inputs)
        if not out:
            return
        self.out_row.set(out)
        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        encode = ss.Encode(
            video=tuple(venc.args),
            pre_input=tuple(venc.pre_input),
            filter_suffix=venc.filter_suffix,
            audio=tuple(enc.default_audio(container)),
        )
        caption_style = (
            self.cap_font.get(),
            CAPTION_KEYS.get(self.cap_pos.get(), "bottom"),
        )
        job = _SlideshowJob(
            slides=slides,
            joins=joins,
            look=self._look(),
            music=music,
            on_beat=bool(music) and self.on_beat_var.get(),
            caption_style=caption_style,
            encode=encode,
        )
        total = ss.timeline([s.seconds for s in slides], joins)[1]
        look = job.look
        self.log(
            self.console,
            t(
                "slideshow.slides_slides_total_width_height_fps_fps",
                slides=len(slides),
                total=ff.format_time(total),
                width=look.width,
                height=look.height,
                fps=look.fps,
            ),
        )
        common.queue_job(
            self,
            common.Job(name="Slideshow", out=out, work=job.run, duration=total, inputs=inputs),
            btn=self.btn_render,
            console=self.console,
        )

    def _preview_transition(self) -> None:
        """Play the selected slide into the next with its transition (small and quick)."""
        i = self._index()
        if i is None or i + 1 >= len(self.slides):
            messagebox.showinfo(
                t("btn.preview"), t("slideshow.select_a_slide_that_has_another_after"), parent=self
            )
            return
        try:
            slides, joins, _music = self._plan()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        pair = [
            replace(s, seconds=min(s.seconds, 2.5), caption="", sound=False)
            for s in slides[i : i + 2]
        ]
        join = replace(joins[i], seconds=min(joins[i].seconds, 1.2))
        look = ss.Look(
            640,
            360 if self._look().width >= self._look().height else 640,
            "25",
            self._look().fit,
            self._look().zoom,
            self.bars_btn.colour,
        )
        self.log(self.console, t("slideshow.preview_name", name=tr.LABELS.get(join.name, "a cut")))
        tasks.submit(
            partial(_make_preview, pair, join, look),
            on_done=launch_preview,
            on_error=self._preview_failed,
            name="SlideshowPreview",
        )

    def _preview_failed(self, exc: BaseException) -> None:
        self.log(self.console, t("slideshow.preview_failed_exc", exc=exc))


def _name_key(s: Slide) -> list[int | str]:
    return natural_key(s.path or s.name)


def _date_key(s: Slide) -> float:
    try:
        return os.path.getmtime(s.path)
    except OSError:
        return 0.0


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _motion(name: str, moves: list[str], rng: random.Random) -> str:
    return rng.choice(moves) if name == RANDOM else name


def _make_preview(pair: list[ss.Slide], join: ss.Join, look: ss.Look) -> str:
    """Two slides and their join as a small video (worker thread); its path."""
    folder = temp_dir("slideshow_preview_")
    clips: list[str] = []
    for k, slide in enumerate(pair):
        clip = ss.clip_name(folder, k, slide)
        card = (
            ss.card_script(slide.card, look).write(os.path.join(folder, f"c{k}.ass"))
            if slide.card
            else ""
        )
        res = proc.run(ss.slide_cmd(slide, look, clip, card), timeout=300)
        if not res.ok:
            shutil.rmtree(folder, ignore_errors=True)
            raise RuntimeError(res.last_error_line() or "a slide couldn't be made")
        clips.append(clip)
    out = os.path.join(folder, "preview.mp4")
    res = proc.run(ss.join_cmd(clips, pair, [join], out, enc=ss.PREVIEW_ENCODE), timeout=300)
    if not res.ok:
        raise RuntimeError(res.last_error_line() or "the preview couldn't be made")
    return out


@dataclass(frozen=True, kw_only=True)
class _SlideshowJob:
    """Every slide into a clip, then one pass joining them (transitions,
    captions, sound); with *on_beat*, the changes first move onto the beats."""

    slides: list[ss.Slide]
    joins: list[ss.Join]
    look: ss.Look
    music: ss.Music | None
    on_beat: bool
    caption_style: tuple[str, str]
    encode: ss.Encode

    def _beat_slides(self, ctx: TaskContext) -> list[ss.Slide]:
        if not (self.on_beat and self.music):
            return self.slides
        ctx.log(t("slideshow.finding_the_beats_in_the_music"))
        found, bpm = beats_core.find_beats(self.music.tracks[0])
        ctx.log(t("slideshow.bpm_0f_bpm", bpm=bpm))
        offset = self.music.offset
        times = [b - offset for b in found if b >= offset]
        seconds = ss.on_beats([s.seconds for s in self.slides], self.joins, times)
        return [replace(s, seconds=n) for s, n in zip(self.slides, seconds, strict=True)]

    def run(self, _p: object, _c: object, ctx: TaskContext) -> int:
        tmp = ctx.tempdir()
        slides = self._beat_slides(ctx)
        n = len(slides)
        clips: list[str] = []
        for i, slide in enumerate(slides):
            ctx.log(
                t(
                    "slideshow.slide_i_n_value",
                    i=i + 1,
                    n=n,
                    value=os.path.basename(slide.path) or "card",
                )
            )
            clip = ss.clip_name(tmp, i, slide)
            card = (
                ss.card_script(slide.card, self.look).write(os.path.join(tmp, f"c{i}.ass"))
                if slide.card
                else ""
            )
            ctx.span(0.7 * i / n, 0.7 * (i + 1) / n).run(
                ss.slide_cmd(slide, self.look, clip, card), duration=slide.seconds
            )
            clips.append(clip)
        captions = ""
        if any(s.caption.strip() for s in slides):
            script = ss.caption_script(slides, self.joins, self.look, self.caption_style)
            captions = script.write(os.path.join(tmp, "captions.ass"))
        total = ss.timeline([s.seconds for s in slides], self.joins)[1]
        cmd = ss.join_cmd(
            clips,
            slides,
            self.joins,
            ctx.output(),
            enc=self.encode,
            music=self.music,
            captions_ass=captions,
        )
        ctx.span(0.7, 1.0).run(cmd, duration=total)
        return 0
