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

import os
import shutil
import tkinter as tk
from dataclasses import dataclass
from tkinter import colorchooser, messagebox, ttk

from core import ass
from core import ffmpeg as ff
from core.i18n import t
from core.scaling import px
from tabs import framestep
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, FramePreview, scroll_body

STYLES = {
    "lower_third": "Lower third (sliding bar)",
    "chapter": "Chapter title (dimmed frame)",
    "name_tag": "Name tag (corner box)",
    "kinetic": "Kinetic pop (word by word)",
    "strip": "Subtitle strip (bottom banner)",
}
ACCENTS = {"YouTube red": "#E53935", "Electric blue": "#1565C0", "Neon green": "#00C853",
           "Gold": "#FFB300", "Purple": "#7B1FA2", "Charcoal": "#263238"}
SIZES = {"Small": 0.8, "Medium": 1.0, "Large": 1.25}


@dataclass
class Card:
    style: str = "lower_third"
    line1: str = "Title Text"
    line2: str = ""
    start: float = 1.0
    duration: float = 4.0
    accent: str = "#1565C0"


def build_script(cards, width: int, height: int, *, font: str = "Montserrat",
                 scale: float = 1.0, anim: float = 0.35) -> ass.Script:
    """ASS script with every card laid out for a width x height video."""
    sc = ass.Script(width, height)
    u = min(width, height) / 1080 * scale
    W, H = width, height
    sc.style(ass.Style("Box", font=font, size=20, bold=False))
    sc.style(ass.Style("Title", font=font, size=round(54 * u), bold=True, shadow=2 * u,
                       back_colour="#000000", back_alpha=0.5))
    sc.style(ass.Style("Sub", font=font, size=round(34 * u), colour="#EEEEEE", shadow=1.5 * u,
                       back_colour="#000000", back_alpha=0.5))
    a_ms = int(anim * 1000)
    for c in cards:
        s, e = c.start, c.start + c.duration
        if c.duration <= 0 or not (c.line1.strip() or c.line2.strip()):
            continue
        fin, fout = min(a_ms, int(c.duration * 300)), min(a_ms, int(c.duration * 300))
        l1, l2 = ass.escape(c.line1), ass.escape(c.line2)
        if c.style == "chapter":
            s1, s2 = round(84 * u), round(40 * u)
            sc.add(s, e, ass.box(W, H, x=0, y=0, fill="#000000", alpha=0.45,
                                 extra=f"\\fad({fin},{fout})"), "Box", 0)
            ty = H / 2 - (s2 * 0.9 if c.line2 else 0)
            sc.add(s, e, f"{{\\an5\\pos({W / 2:.0f},{ty:.0f})\\fs{s1}\\fsp{2 * u:.1f}"
                         f"\\fad({fin},{fout})\\fscx92\\fscy92\\t(0,{a_ms * 2},\\fscx100\\fscy100)}}{l1}",
                   "Title", 2)
            line_w = min(W * 0.5, max(ass.text_width(c.line1, s1) * 0.6, 120 * u))
            sc.add(s, e, f"{{\\an8\\pos({W / 2:.0f},{ty + s1 * 0.62:.0f})\\bord0\\shad0"
                         f"\\1c{ass.colour(c.accent)}\\fad({fin},{fout})\\fscx0"
                         f"\\t(0,{a_ms * 2},\\fscx100)\\p1}}{ass.rect(line_w, max(3, 6 * u))}{{\\p0}}",
                   "Box", 1)
            if c.line2:
                sc.add(s, e, f"{{\\an8\\pos({W / 2:.0f},{ty + s1 * 0.62 + 18 * u:.0f})\\fs{s2}"
                             f"\\fad({fin + 150},{fout})}}{l2}", "Sub", 2)
        elif c.style == "kinetic":
            s1, s2 = round(104 * u), round(44 * u)
            ty = H / 2 - (s2 if c.line2 else 0)
            sc.add(s, e, f"{{\\an5\\pos({W / 2:.0f},{ty:.0f})\\fs{s1}\\bord{5 * u:.1f}"
                         f"\\3c{ass.colour(c.accent)}\\fad(0,{fout})\\fscx0\\fscy0"
                         f"\\t(0,180,\\fscx115\\fscy115)\\t(180,320,\\fscx100\\fscy100)}}{l1}",
                   "Title", 2)
            if c.line2:
                sc.add(s, e, f"{{\\an8\\pos({W / 2:.0f},{ty + s1 * 0.62:.0f})\\fs{s2}"
                             f"\\fad(0,{fout})}}{ass.word_reveal(c.line2, 350, 170)}", "Sub", 2)
        elif c.style == "strip":
            s1 = round(40 * u)
            lines = 2 if c.line2 else 1
            bh = s1 * (1.0 + 1.25 * lines)
            sc.add(s, e, ass.box(W, bh, x=0, y=H - bh, fill="#000000", alpha=0.35,
                                 extra=f"\\fad({fin},{fout})"), "Box", 0)
            text = l1 + (f"\\N{{\\fs{round(s1 * 0.8)}\\b0}}{l2}" if c.line2 else "")
            sc.add(s, e, f"{{\\an5\\pos({W / 2:.0f},{H - bh / 2:.0f})\\fs{s1}\\fad({fin},{fout})}}{text}",
                   "Title", 2)
        else:  # lower_third / name_tag
            tag = c.style == "name_tag"
            s1 = round((40 if tag else 54) * u)
            s2 = round((26 if tag else 34) * u)
            pad = (18 if tag else 28) * u
            stripe = (10 if tag else 0) * u
            text_w = max(ass.text_width(c.line1, s1), ass.text_width(c.line2, s2, bold=False))
            bw = min(W * 0.9, max(W * (0.18 if tag else 0.3), text_w + 2 * pad + stripe))
            bh = pad * 1.3 + s1 * 1.15 + (s2 * 1.3 if c.line2 else 0)
            x0 = W * 0.04
            y0 = H * 0.06 if tag else H - bh - H * 0.08
            fill, falpha = ("#111111", 0.15) if tag else (c.accent, 0.08)
            sc.add(s, e, f"{{\\an7\\move({-bw:.0f},{y0:.0f},{x0:.0f},{y0:.0f},0,{a_ms})\\bord0\\shad0"
                         f"\\1c{ass.colour(fill)}\\1a{ass.alpha_tag(falpha)}\\fad(0,{fout})\\p1}}"
                         f"{ass.rect(bw, bh)}{{\\p0}}", "Box", 0)
            if stripe:
                sc.add(s, e, f"{{\\an7\\move({-bw:.0f},{y0:.0f},{x0:.0f},{y0:.0f},0,{a_ms})\\bord0"
                             f"\\shad0\\1c{ass.colour(c.accent)}\\fad(0,{fout})\\p1}}"
                             f"{ass.rect(stripe, bh)}{{\\p0}}", "Box", 1)
            tx = x0 + pad + stripe
            ty = y0 + pad * 0.65
            sc.add(s, e, f"{{\\an7\\move({tx - 60 * u:.0f},{ty:.0f},{tx:.0f},{ty:.0f},80,{a_ms + 80})"
                         f"\\fs{s1}\\fad({fin},{fout})}}{l1}", "Title", 2)
            if c.line2:
                ty2 = ty + s1 * 1.18
                sc.add(s, e, f"{{\\an7\\move({tx - 60 * u:.0f},{ty2:.0f},{tx:.0f},{ty2:.0f},160,"
                             f"{a_ms + 160})\\fs{s2}\\fad({fin + 120},{fout})}}{l2}", "Sub", 2)
    return sc


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
        self.src_row = FileRow(body, t("common.source_video"), self.src_var,
                               on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))

        pv = tk.Frame(body)
        pv.pack(pady=px(4))
        self.preview = FramePreview(pv, 480, 270)
        self.preview.pack()
        self.pos_var = tk.DoubleVar(value=0.0)
        self.slider = ttk.Scale(pv, from_=0, to=1, variable=self.pos_var, length=px(480),
                                command=lambda _v: self.preview.show(float(self.pos_var.get())))
        self.slider.pack(fill="x")
        framestep.slider(self.slider, self.preview)      # , and . step a frame

        look = tk.Frame(body)
        look.pack(fill="x", padx=px(20), pady=px(4))
        tk.Label(look, text="Font:").pack(side="left")
        self.font_var = tk.StringVar(value="Montserrat")
        fb = ttk.Combobox(look, textvariable=self.font_var, values=ass.families(), width=18,
                          state="readonly")
        fb.pack(side="left", padx=px(6))
        fb.bind("<<ComboboxSelected>>", lambda _e: self._schedule())
        tk.Label(look, text="Text size:").pack(side="left", padx=px(12, 0))
        self.size_var = tk.StringVar(value="Medium")
        sb = ttk.Combobox(look, textvariable=self.size_var, values=list(SIZES), width=8,
                          state="readonly")
        sb.pack(side="left", padx=px(6))
        sb.bind("<<ComboboxSelected>>", lambda _e: self._schedule())
        tk.Label(look, text="Animation (s):").pack(side="left", padx=px(12, 0))
        self.anim_var = tk.StringVar(value="0.35")
        tk.Entry(look, textvariable=self.anim_var, width=5).pack(side="left", padx=px(6))
        self.anim_var.trace_add("write", lambda *_a: self._schedule())

        cards_hdr = tk.Frame(body)
        cards_hdr.pack(fill="x", padx=px(20), pady=px(8, 0))
        tk.Label(cards_hdr, text=t("titles.title_cards"), font=(UI_FONT, 9, "bold"),
                 fg=CLR["fgdim"]).pack(side="left")
        tk.Button(cards_hdr, text=f"➕ {t('titles.add_card_button')}", relief="flat",
                  cursor="hand2", command=lambda: self.add_card()).pack(side="right")
        self.cards_frame = tk.Frame(body)
        self.cards_frame.pack(fill="x", padx=px(20))

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9", "prores"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.src_row.path, "titled"))
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(body, text=t("titles.render_button"), font=(UI_FONT, 12, "bold"),
                                    bg="#AD1457", fg="white", width=26, cursor="hand2",
                                    command=self._render)
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

        self.add_card(Card("lower_third", "Jane Smith", "Director of Photography", 1.0, 4.0))
        self.add_card(Card("chapter", "Chapter One", "The beginning", 6.0, 4.0, "#FFB300"))

    def add_card(self, card: Card | None = None):
        card = card or Card(start=(self.rows[-1]["vars"]["start"].get() if self.rows else 1.0))
        if not isinstance(card.start, (int, float)):
            try:
                card.start = ff.parse_time(card.start) + 5
            except ValueError:
                card.start = 1.0
        frame = tk.LabelFrame(self.cards_frame, text=f"  Card {len(self.rows) + 1}  ", padx=px(8), pady=px(4))
        frame.pack(fill="x", pady=px(3))
        v = {"style": tk.StringVar(value=STYLES.get(card.style, STYLES["lower_third"])),
             "line1": tk.StringVar(value=card.line1), "line2": tk.StringVar(value=card.line2),
             "start": tk.StringVar(value=f"{card.start:g}"),
             "duration": tk.StringVar(value=f"{card.duration:g}")}
        row = {"frame": frame, "vars": v, "accent": card.accent}
        r1 = tk.Frame(frame)
        r1.pack(fill="x")
        ttk.Combobox(r1, textvariable=v["style"], values=list(STYLES.values()), state="readonly",
                     width=30).pack(side="left")
        tk.Label(r1, text=t("titles.start_label")).pack(side="left", padx=px(10, 2))
        tk.Entry(r1, textvariable=v["start"], width=8).pack(side="left")
        tk.Label(r1, text=t("titles.duration_label")).pack(side="left", padx=px(10, 2))
        tk.Entry(r1, textvariable=v["duration"], width=6).pack(side="left")
        swatch = tk.Button(r1, text="  ", bg=card.accent, relief="flat", width=3,
                           command=lambda r=row: self._pick_accent(r))
        swatch.pack(side="left", padx=px(10))
        row["swatch"] = swatch
        tk.Button(r1, text="👁", relief="flat", cursor="hand2",
                  command=lambda r=row: self._show_card(r)).pack(side="left")
        tk.Button(r1, text="✕", relief="flat", cursor="hand2",
                  command=lambda r=row: self._remove(r)).pack(side="right")
        r2 = tk.Frame(frame)
        r2.pack(fill="x", pady=px(3, 0))
        tk.Label(r2, text=t("titles.line_1_label"), width=7, anchor="w").pack(side="left")
        tk.Entry(r2, textvariable=v["line1"], width=34).pack(side="left", padx=px(0, 10))
        tk.Label(r2, text=t("titles.line_2_label"), width=7, anchor="w").pack(side="left")
        tk.Entry(r2, textvariable=v["line2"], width=34).pack(side="left")
        for var in v.values():
            var.trace_add("write", lambda *_a: self._schedule())
        self.rows.append(row)
        self._schedule()
        return row

    def _remove(self, row):
        if row in self.rows:
            self.rows.remove(row)
            row["frame"].destroy()
            for i, r in enumerate(self.rows, 1):
                r["frame"].config(text=f"  Card {i}  ")
            self._schedule()

    def _pick_accent(self, row):
        res = colorchooser.askcolor(row["accent"], parent=self)
        if res and res[1]:
            row["accent"] = res[1]
            row["swatch"].config(bg=res[1])
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
                raise ValueError(f"Card {i}: start time '{v['start'].get()}' is not a time.") from None
            dur = common.number(v["duration"].get(), f"Card {i} duration", 0.2, 3600)
            style = next((k for k, lbl in STYLES.items() if lbl == v["style"].get()), "lower_third")
            out.append(Card(style, v["line1"].get(), v["line2"].get(), start, dur, r["accent"]))
        return out

    def script(self, width: int, height: int) -> ass.Script:
        try:
            anim = common.number(self.anim_var.get() or "0.35", "Animation", 0.05, 3)
        except ValueError:
            anim = 0.35
        return build_script(self.cards(), width, height, font=self.font_var.get(),
                            scale=SIZES.get(self.size_var.get(), 1.0), anim=anim)

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
            try:
                os.remove(old)
            except OSError:
                pass
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
            messagebox.showwarning(t("common.warning"),
                                   f"Card {late[0]} starts after the video ends "
                                   f"({ff.format_time(info.duration)}).", parent=self)
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

        common.render(self, src=src, info=info, out=out, chain=chain, encoder=self.encoder,
                      console=self.console, btn=self.btn_render,
                      btn_label=t("titles.render_button"), name="Animated Titles", on_done=done)

    def on_app_closing(self):
        if self._preview_dir:
            shutil.rmtree(self._preview_dir, ignore_errors=True)
