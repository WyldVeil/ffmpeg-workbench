"""
tabs/social/titles.py  -  Animated Titles

Burn animated title cards onto footage: lower thirds, chapter titles, name
tags, kinetic pop-ups and subtitle strips.  Any number of cards, each with
its own timing and accent colour, with a live preview.

Titles are rendered by libass from a generated ASS script (see core/ass.py):
real bold fonts from the bundled set, vector bars, smooth slides / fades /
pops, and any characters in the text.  Layout scales with the video size,
so 720p, 4K and vertical videos all look right.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tkinter as tk
from tkinter import messagebox, ttk

from core import ass
from core import ffmpeg as ff
from core.i18n import t
from core.ops.shared import RenderJob
from core.ops.titles import Card, build_script
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, UI_FONT, BaseTab, add_tooltip
from tabs.kit.time_fields import MomentPicker
from tabs.kit.widgets import ColourButton
from tabs.widgets import EncoderPanel, FileRow, FramePreview, SaveRow, scroll_body

STYLES = {
    "lower_third": "Lower third (sliding bar)",
    "chapter": "Chapter title (dimmed frame)",
    "name_tag": "Name tag (corner box)",
    "kinetic": "Kinetic pop (word by word)",
    "strip": "Subtitle strip (bottom banner)",
}
ACCENTS = {
    "YouTube red": "#E53935",
    "Electric blue": "#1565C0",
    "Neon green": "#00C853",
    "Gold": "#FFB300",
    "Purple": "#7B1FA2",
    "Charcoal": "#263238",
}
SIZES = {"Small": 0.8, "Medium": 1.0, "Large": 1.25}


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


class TitlesGeneratorTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self.rows: list = []
        self._info = None
        self._job = None
        self._preview_dir = None
        self._preview_n = 0
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.make_header(self, t("tab.animated_titles"), t("titles.subtitle"), icon="🎬")
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))

        pv = tk.Frame(body)
        pv.pack(pady=px(4))
        self.preview = FramePreview(pv, 480, 270)
        self.preview.pack()
        self.pos_var = tk.DoubleVar(value=0.0)
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

        look = tk.Frame(body)
        look.pack(fill="x", padx=px(20), pady=px(4))
        tk.Label(look, text="Font:").pack(side="left")
        self.font_var = tk.StringVar(value="Montserrat")
        fb = ttk.Combobox(
            look, textvariable=self.font_var, values=ass.families(), width=18, state="readonly"
        )
        fb.pack(side="left", padx=px(6))
        fb.bind("<<ComboboxSelected>>", lambda _e: self._schedule())
        tk.Label(look, text="Text size:").pack(side="left", padx=px(12, 0))
        self.size_var = tk.StringVar(value="Medium")
        sb = ttk.Combobox(
            look, textvariable=self.size_var, values=list(SIZES), width=8, state="readonly"
        )
        sb.pack(side="left", padx=px(6))
        sb.bind("<<ComboboxSelected>>", lambda _e: self._schedule())
        tk.Label(look, text="Animation (s):").pack(side="left", padx=px(12, 0))
        self.anim_var = tk.StringVar(value="0.35")
        tk.Entry(look, textvariable=self.anim_var, width=5).pack(side="left", padx=px(6))
        self.anim_var.trace_add("write", lambda *_a: self._schedule())

        cards_hdr = tk.Frame(body)
        cards_hdr.pack(fill="x", padx=px(20), pady=px(8, 0))
        tk.Label(
            cards_hdr, text=t("titles.title_cards"), font=(UI_FONT, 9, "bold"), fg=CLR["fgdim"]
        ).pack(side="left")
        tk.Button(
            cards_hdr,
            text=f"➕ {t('titles.add_card_button')}",
            relief="flat",
            cursor="hand2",
            command=lambda: self.add_card(),
        ).pack(side="right")
        self.cards_frame = tk.Frame(body)
        self.cards_frame.pack(fill="x", padx=px(20))

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9", "prores"))
        self.encoder.pack(anchor="w")
        self.out_row = SaveRow(
            out_lf,
            t("common.output_file"),
            suggest=lambda: self.suggest_output(self.src_row.path, "titled"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = self.make_render_btn(
            body, t("titles.render_button"), self._render, width=26
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

        self.add_card(Card("lower_third", "Jane Smith", "Director of Photography", 1.0, 4.0))
        self.add_card(Card("chapter", "Chapter One", "The beginning", 6.0, 4.0, "#FFB300"))

    def add_card(self, card: Card | None = None):
        card = card or Card(start=(self.rows[-1]["vars"]["start"].get() if self.rows else 1.0))
        if not isinstance(card.start, (int, float)):
            try:
                card.start = ff.parse_time(card.start) + 5
            except ValueError:
                card.start = 1.0
        frame = tk.LabelFrame(
            self.cards_frame, text=f"  Card {len(self.rows) + 1}  ", padx=px(8), pady=px(4)
        )
        frame.pack(fill="x", pady=px(3))
        v = {
            "style": tk.StringVar(value=STYLES.get(card.style, STYLES["lower_third"])),
            "line1": tk.StringVar(value=card.line1),
            "line2": tk.StringVar(value=card.line2),
            "start": tk.StringVar(value=f"{card.start:g}"),
            "duration": tk.StringVar(value=f"{card.duration:g}"),
        }
        row = {"frame": frame, "vars": v}
        r1 = tk.Frame(frame)
        r1.pack(fill="x")
        ttk.Combobox(
            r1, textvariable=v["style"], values=list(STYLES.values()), state="readonly", width=30
        ).pack(side="left")
        tk.Label(r1, text=t("titles.start_label")).pack(side="left", padx=px(10, 2))
        start = MomentPicker(
            r1,
            value=v["start"].get(),
            width=8,
            here=lambda: float(self.pos_var.get()),
            on_change=lambda r=row: self._use(r),
            tip="Start at the frame shown (i)",
        )
        start.pack(side="left")
        v["start"] = start.var
        tk.Label(r1, text=t("titles.duration_label")).pack(side="left", padx=px(10, 2))
        tk.Entry(r1, textvariable=v["duration"], width=6).pack(side="left")
        end_btn = tk.Button(
            r1,
            text="◉",
            relief="flat",
            width=2,
            cursor="hand2",
            command=lambda r=row: self._end_here(r),
        )
        end_btn.pack(side="left")
        add_tooltip(end_btn, "End at the frame shown (o) - sets the duration")
        swatch = ColourButton(r1, card.accent, width=3, on_change=lambda _c: self._schedule())
        swatch.pack(side="left", padx=px(10))
        row["swatch"] = swatch
        tk.Button(
            r1, text="👁", relief="flat", cursor="hand2", command=lambda r=row: self._show_card(r)
        ).pack(side="left")
        tk.Button(
            r1, text="✕", relief="flat", cursor="hand2", command=lambda r=row: self._remove(r)
        ).pack(side="right")
        r2 = tk.Frame(frame)
        r2.pack(fill="x", pady=px(3, 0))
        tk.Label(r2, text=t("titles.line_1_label"), width=7, anchor="w").pack(side="left")
        tk.Entry(r2, textvariable=v["line1"], width=34).pack(side="left", padx=px(0, 10))
        tk.Label(r2, text=t("titles.line_2_label"), width=7, anchor="w").pack(side="left")
        tk.Entry(r2, textvariable=v["line2"], width=34).pack(side="left")
        for var in v.values():
            var.trace_add("write", lambda *_a: self._schedule())
        for w in _walk(frame):
            if isinstance(w, tk.Entry):
                w.bind("<FocusIn>", lambda _e, r=row: self._use(r), add=True)
        self.rows.append(row)
        self._active = row
        self._schedule()
        return row

    # ── i / o and the ◉ buttons: a card starts / ends at the frame shown ──
    def _use(self, row):
        self._active = row

    def _card(self):
        row = getattr(self, "_active", None)
        return row if row in self.rows else (self.rows[-1] if self.rows else None)

    def _end_here(self, row):
        self._use(row)
        try:
            start = ff.parse_time(row["vars"]["start"].get())
        except ValueError:
            return
        length = float(self.pos_var.get()) - start
        if length > 0:
            row["vars"]["duration"].set(f"{length:.3f}".rstrip("0").rstrip("."))

    def mark_in(self):
        row = self._card()
        if row is not None:
            row["vars"]["start"].set(ff.format_time(float(self.pos_var.get())))

    def mark_out(self):
        row = self._card()
        if row is not None:
            self._end_here(row)

    def _remove(self, row):
        if row in self.rows:
            self.rows.remove(row)
            row["frame"].destroy()
            for i, r in enumerate(self.rows, 1):
                r["frame"].config(text=f"  Card {i}  ")
            self._schedule()

    def _show_card(self, row):
        try:
            start = ff.parse_time(row["vars"]["start"].get())
            dur = float(row["vars"]["duration"].get())
        except ValueError:
            return
        at = start + min(1.0, dur / 2)
        self.pos_var.set(at)
        self.preview.show(at)

    # ── Model ────────────────────────────────────────────────────────────────

    def cards(self) -> list:
        """Cards from the UI; raises ValueError naming the bad field."""
        out = []
        for i, r in enumerate(self.rows, 1):
            v = r["vars"]
            try:
                start = ff.parse_time(v["start"].get())
            except ValueError:
                raise ValueError(
                    f"Card {i}: start time '{v['start'].get()}' is not a time."
                ) from None
            dur = common.number(v["duration"].get(), f"Card {i} duration", 0.2, 3600)
            style = next((k for k, lbl in STYLES.items() if lbl == v["style"].get()), "lower_third")
            out.append(
                Card(style, v["line1"].get(), v["line2"].get(), start, dur, r["swatch"].colour)
            )
        return out

    def script(self, width: int, height: int) -> ass.Script:
        try:
            anim = common.number(self.anim_var.get() or "0.35", "Animation", 0.05, 3)
        except ValueError:
            anim = 0.35
        return build_script(
            self.cards(),
            width,
            height,
            font=self.font_var.get(),
            scale=SIZES.get(self.size_var.get(), 1.0),
            anim=anim,
        )

    # ── Preview ──────────────────────────────────────────────────────────────

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        dur = self._info.duration if self._info else 0.0
        self.slider.configure(to=max(dur, 0.01))
        first = self.rows[0] if self.rows else None
        self.preview.load(path, dur, 0.0)
        if first is not None:
            self._show_card(first)
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "titled"))
        self._schedule()

    def _schedule(self):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(250, self._refresh)

    def _refresh(self):
        self._job = None
        info = self._info
        if info is None or not info.has_video:
            return
        try:
            w, h = common.even_size(info)
            sc = self.script(w, h)
        except ValueError:
            return
        if self._preview_dir is None:
            from core.paths import temp_dir

            self._preview_dir = temp_dir("titles_preview_")
        self._preview_n += 1
        path = os.path.join(self._preview_dir, f"p{self._preview_n}.ass")
        sc.write(path)
        old = os.path.join(self._preview_dir, f"p{self._preview_n - 3}.ass")
        if os.path.exists(old):
            with contextlib.suppress(OSError):
                os.remove(old)
        self.preview.set_filter(ass.subtitles_filter(path), keep_time=True)

    # ── Render ───────────────────────────────────────────────────────────────

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        try:
            cards = self.cards()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        if not cards:
            messagebox.showwarning(t("common.warning"), "Add at least one title card.", parent=self)
            return
        late = [i for i, c in enumerate(cards, 1) if c.start >= info.duration > 0]
        if late:
            messagebox.showwarning(
                t("common.warning"),
                f"Card {late[0]} starts after the video ends ({ff.format_time(info.duration)}).",
                parent=self,
            )
            return
        out = common.output_for(self, self.out_row, src, "titled")
        if not out:
            return
        from core.paths import temp_dir

        work = temp_dir("titles_")
        w, h = common.even_size(info)
        path = self.script(w, h).write(os.path.join(work, "titles.ass"))
        chain = ass.subtitles_filter(path)

        def done(rc):
            shutil.rmtree(work, ignore_errors=True)
            self.show_result(rc, out)

        common.render(
            self,
            RenderJob(src=src, info=info, out=out, chain=chain, name="Animated Titles"),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
            on_done=done,
        )

    def on_app_closing(self):
        if self._preview_dir:
            shutil.rmtree(self._preview_dir, ignore_errors=True)
            self._preview_dir = None  # a new one is made if the page is used again
