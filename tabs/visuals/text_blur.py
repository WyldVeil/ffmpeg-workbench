"""
tabs/visuals/text_blur.py - Text Blurrer: finds the text laid over a video
(names, handles, captions, phone numbers, logos) and blurs the ones you pick.

Detection (core/ops/text_find.py) starts as soon as a video is chosen.  Each
find is outlined on the preview while it's on screen; click it to blur it,
click again to leave it.  A chosen box can be moved or resized with its
handles, and dragging on an empty part of the picture adds a box by hand for
anything the finder missed.  Rendering reuses Privacy Blur's engine
(core/ops/region_blur.py).
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import tasks, ui
from core.i18n import t
from core.ops import text_find as tf
from core.ops.region_blur import Region, blur_graph
from core.ops.shared import RenderJob
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.kit import boxedit
from tabs.kit.widgets import ChoiceBox
from tabs.widgets import FileRow, FramePreview, output_section, scroll_body

PW, PH = 560, 315


class Hide(StrEnum):
    """How a chosen box is hidden (core/ops/region_blur.KINDS)."""

    BLUR = "blur"
    PIXELATE = "pixelate"
    BOX = "box"


@dataclass(eq=False)
class Item:
    """One thing to maybe blur: its box (fractions, already with a margin
    added at render), when it's on screen, and whether it's chosen."""

    x: float
    y: float
    w: float
    h: float
    start: float
    end: float | None
    textlike: bool
    sure: bool
    by_hand: bool = False
    chosen: tk.BooleanVar = field(default_factory=lambda: tk.BooleanVar(value=False))

    def box(self) -> boxedit.Rect:
        return (self.x, self.y, self.w, self.h)

    def on_screen(self, at: float) -> bool:
        return self.start - 0.05 <= at and (self.end is None or at <= self.end + 0.05)


def _clock(seconds: float) -> str:
    """0:07, 1:23, 1:02:03 - to the second, like a video player shows."""
    s = round(seconds)
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def where(x: float, y: float, w: float, h: float) -> str:
    """Where on the picture, in words: "top left", "middle", "bottom right"..."""
    cx, cy = x + w / 2, y + h / 2
    row = "top" if cy < 1 / 3 else ("bottom" if cy > 2 / 3 else "middle")
    col = "left" if cx < 1 / 3 else ("right" if cx > 2 / 3 else "centre")
    if row == "middle" and col == "centre":
        return t("text_blur.where.middle")
    return t(f"text_blur.where.{row}_{col}")


@dataclass
class _Press:
    """A press on the preview: on item *index* (part = boxedit.hit), or drawing a new box."""

    at: tuple[int, int]
    index: int | None = None
    part: str = ""
    before: boxedit.Rect = (0.0, 0.0, 0.0, 0.0)
    moved: bool = False
    rect: tuple[int, int, int, int] | None = None  # a new box being drawn


class TextBlurTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info: ff.MediaInfo | None = None
        self.items: list[Item] = []
        self._active: int | None = None  # the item whose handles show
        self._press: _Press | None = None
        self._hovered: int | None = None  # the item a click would pick
        self._search = 0  # bumps with every new search, so a stale one is ignored
        self._build_ui()

    # ── Building ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        self.make_header(self, t("tab.text_blurrer"), t("text_blur.subtitle"), icon="🔤")
        body = scroll_body(self)
        self.src_row = FileRow(
            body,
            t("common.source_video"),
            self.src_var,
            kind="video",
            on_change=self._source_changed,
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        self.status = tk.Label(
            body, text=t("text_blur.choose_a_video"), fg=CLR["fgdim"], font=(UI_FONT, 9), anchor="w"
        )
        self.status.pack(fill="x", padx=px(20))
        top = tk.Frame(body)
        top.pack(fill="x", padx=px(20), pady=px(4))
        self._build_preview(top)
        self._build_list(top)
        self._build_style(body)
        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9"),
            suggest=lambda: self.suggest_output(self.src_row.path, "blurred"),
        )
        self.btn_render = self.make_render_btn(
            body, t("text_blur.blur_chosen"), self._render, width=22
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

    def _build_preview(self, top: tk.Frame) -> None:
        left = tk.Frame(top)
        left.pack(side="left", anchor="n")
        self.preview = FramePreview(left, PW, PH)
        self.preview.pack()
        self.preview.after_draw = self._draw
        c = self.preview.canvas
        c.config(cursor="crosshair")
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Motion>", self._on_hover)
        c.bind("<Leave>", self._on_leave)
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
        framestep.slider(self.slider, self.preview)
        tk.Label(
            left, text=t("text_blur.click_hint"), fg=CLR["fgdim"], font=(UI_FONT, 8), justify="left"
        ).pack(anchor="w")

    def _build_list(self, top: tk.Frame) -> None:
        right = tk.Frame(top)
        right.pack(side="left", fill="both", expand=True, padx=px(12, 0))
        tk.Label(right, text=t("text_blur.found"), font=(UI_FONT, 10, "bold")).pack(anchor="w")
        holder = tk.Frame(right, relief="sunken", bd=1)
        holder.pack(fill="x")
        self.rows = scroll_body(holder, height=230)
        self.maybe_var = tk.BooleanVar(value=False)
        self.maybe_box = tk.Checkbutton(
            right, text=t("text_blur.show_less_certain", n=0), variable=self.maybe_var,
            command=self._relist,
        )  # fmt: skip
        self.maybe_box.pack(anchor="w")
        buttons = tk.Frame(right)
        buttons.pack(fill="x", pady=px(2))
        for label, cmd in (
            (t("text_blur.choose_all"), partial(self._choose_all, True)),
            (t("text_blur.choose_none"), partial(self._choose_all, False)),
            (t("text_blur.find_again"), self._find),
        ):
            tk.Button(buttons, text=label, relief="flat", command=cmd).pack(
                side="left", padx=px(0, 4)
            )

    def _build_style(self, body: tk.Frame) -> None:
        box = tk.LabelFrame(body, text=t("text_blur.how_to_hide"), padx=px(8), pady=px(4))
        box.pack(fill="x", padx=px(20), pady=px(6))
        self.kind = ChoiceBox(
            box, {k: t(f"text_blur.kind.{k.value}") for k in Hide}, Hide.BLUR, width=18,
            on_change=lambda _k: self._refresh_result(),
        )  # fmt: skip
        self.kind.grid(row=0, column=0, sticky="w")
        self.strength_var = tk.DoubleVar(value=0.7)
        tk.Scale(
            box, variable=self.strength_var, from_=0.2, to=1.0, resolution=0.05,
            orient="horizontal", length=px(150), label=t("text_blur.strength"),
            command=lambda _v: self._refresh_result(),
        ).grid(row=0, column=1, sticky="w", padx=px(10))  # fmt: skip
        self.margin_var = tk.DoubleVar(value=0.3)
        tk.Scale(
            box, variable=self.margin_var, from_=0.0, to=1.0, resolution=0.05,
            orient="horizontal", length=px(150), label=t("text_blur.margin"),
            command=lambda _v: self._refresh_result(),
        ).grid(row=0, column=2, sticky="w", padx=px(10))  # fmt: skip
        self.result_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            box, text=t("text_blur.show_result"), variable=self.result_var,
            command=self._refresh_result,
        ).grid(row=1, column=0, columnspan=3, sticky="w")  # fmt: skip

    # ── Source and finding ────────────────────────────────────────────
    def load_file(self, path: str) -> bool:
        """A dropped video becomes the source."""
        from tabs.kit import dialogs

        if dialogs.kind_of(path) != "video":
            return False
        self.src_var.set(path)
        return True

    def _source_changed(self, path: str) -> None:
        self._info = info = ff.try_probe(path) if path else None
        self.items.clear()
        self._active = None
        self._relist()
        if info is None or not info.has_video:
            self.preview.clear()
            self.status.config(text=t("text_blur.not_a_video"), fg=CLR["red"])
            return
        self.slider.configure(to=max(info.duration, 0.01))
        self.pos_var.set(0)
        self.preview.load(path, info.duration, 0.0)
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "blurred"))
        self._find()

    def _find(self) -> None:
        info, src = self._info, self.src_row.path
        if info is None or not src or not info.has_video:
            return
        self._search += 1
        search = self._search
        w, h = info.require_video().display_size
        dur = max(info.duration, 0.1)
        self.status.config(text=t("text_blur.looking", percent=0), fg=CLR["fgdim"])

        def progress(share: float) -> None:
            ui.call_soon(self._progress, search, share)

        def work() -> list[tf.Found]:
            return tf.find(tf.sample(src, w, h, dur, progress=progress), w, h, dur)

        tasks.submit(
            work,
            on_done=partial(self._found, search),
            on_error=partial(self._find_failed, search),
            name="TextFind",
        )

    def _progress(self, search: int, share: float) -> None:
        if search == self._search:
            self.status.config(text=t("text_blur.looking", percent=round(share * 100)))

    def _find_failed(self, search: int, exc: BaseException) -> None:
        if search == self._search:
            self.status.config(text=t("text_blur.find_failed", error=str(exc)), fg=CLR["red"])

    def _found(self, search: int, found: list[tf.Found]) -> None:
        if search != self._search:
            return
        by_hand = [i for i in self.items if i.by_hand]  # boxes drawn by hand survive a new search
        self.items = [
            Item(f.x, f.y, f.w, f.h, f.start, f.end, f.textlike, f.sure) for f in found
        ] + by_hand
        self._active = None
        sure = sum(1 for f in found if f.sure)
        if not found:
            self.status.config(text=t("text_blur.found_none"), fg=CLR["fgdim"])
        else:
            self.status.config(
                text=t("text_blur.found_n", n=sure, maybe=len(found) - sure), fg=CLR["fg"]
            )
        self._relist()

    # ── The list ──────────────────────────────────────────────────────
    def _shown(self) -> list[int]:
        """Items listed and outlined: the sure ones (and the rest when asked)."""
        show_all = self.maybe_var.get()
        return [i for i, it in enumerate(self.items) if it.sure or it.by_hand or show_all]

    def _describe(self, n: int, it: Item) -> str:
        what = t("text_blur.what.text") if it.textlike else t("text_blur.what.logo")
        if it.by_hand:
            what = t("text_blur.what.by_hand")
        end = _clock(it.end) if it.end is not None else t("text_blur.to_the_end")
        return t(
            "text_blur.row",
            n=n,
            what=what,
            start=_clock(it.start),
            end=end,
            where=where(it.x, it.y, it.w, it.h),
        )

    def _relist(self) -> None:
        for child in self.rows.winfo_children():
            child.destroy()
        maybe = sum(1 for it in self.items if not it.sure and not it.by_hand)
        self.maybe_box.config(text=t("text_blur.show_less_certain", n=maybe))
        for n, i in enumerate(self._shown(), 1):
            it = self.items[i]
            row = tk.Frame(self.rows)
            row.pack(fill="x", anchor="w")
            tk.Checkbutton(row, variable=it.chosen, command=self._changed).pack(side="left")
            label = tk.Label(
                row,
                text=self._describe(n, it),
                anchor="w",
                cursor="hand2",
                font=(UI_FONT, 9, "bold" if it.chosen.get() else "normal"),
                fg=CLR["fg"] if it.sure or it.by_hand else CLR["fgdim"],
            )
            label.pack(side="left", fill="x")
            label.bind("<Button-1>", partial(self._go_to, i))
        self._draw()

    def _go_to(self, index: int, _e: object = None) -> None:
        """Show the frame where item *index* first appears, with its handles."""
        it = self.items[index]
        self._active = index
        at = min(max(it.start + 0.1, 0.0), self._duration())
        self.pos_var.set(at)
        self.preview.show(at)
        self._draw()

    def _choose_all(self, on: bool) -> None:
        for i in self._shown():
            self.items[i].chosen.set(on)
        self._changed()

    def _changed(self) -> None:
        self._relist()
        self._refresh_result()

    def _duration(self) -> float:
        return self._info.duration if self._info else 0.0

    # ── The preview ───────────────────────────────────────────────────
    def _regions(self) -> list[Region]:
        kind = self.kind.value.value
        strength = float(self.strength_var.get())
        pad = float(self.margin_var.get())
        out: list[Region] = []
        for it in self.items:
            if not it.chosen.get():
                continue
            box = tf.Found(it.x, it.y, it.w, it.h, it.start, it.end).padded(pad)
            out.append(Region(box.x, box.y, box.w, box.h, kind, strength, box.start, box.end))
        return out

    def _size(self) -> tuple[int, int]:
        return common.even_size(self._info) if self._info and self._info.has_video else (1920, 1080)

    def _refresh_result(self) -> None:
        regions = self._regions()
        if self.result_var.get() and regions:
            w, h = self._size()
            self.preview.set_filter(blur_graph(regions, w, h, "[in]", ""), keep_time=True)
        else:
            self.preview.set_filter("", keep_time=True)
        self._draw()

    def _on_canvas(self, it: Item) -> boxedit.Rect | None:
        box = self.preview.image_box()
        if box is None:
            return None
        x0, y0, bw, bh = box
        return (x0 + it.x * bw, y0 + it.y * bh, it.w * bw, it.h * bh)

    def _draw(self) -> None:
        c = self.preview.canvas
        c.delete("find")
        at = float(self.pos_var.get())
        for n, i in enumerate(self._shown(), 1):
            it = self.items[i]
            rect = self._on_canvas(it)
            if rect is None or not it.on_screen(at):
                continue
            x, y, w, h = rect
            chosen = it.chosen.get()
            hovered = i == self._hovered
            colour = (
                CLR["accent"]
                if chosen
                else (CLR["orange"] if it.sure or it.by_hand else CLR["fgdim"])
            )
            if hovered:
                colour = "#FFFFFF"
            if chosen and not self.result_var.get():  # no live result: show what will be hidden
                c.create_rectangle(
                    x, y, x + w, y + h, fill=colour, stipple="gray25", outline="", tags="find"
                )
            c.create_rectangle(
                x, y, x + w, y + h, outline=colour, width=px(3 if hovered else 2 if chosen else 1),
                dash=() if chosen or hovered else px(4, 3), tags="find",
            )  # fmt: skip
            c.create_text(
                x + px(3), y - px(1), text=str(n), anchor="sw", fill=colour,
                font=(UI_FONT, 9, "bold"), tags="find",
            )  # fmt: skip
            if hovered and i != self._active:  # say what a click does, inside the picture
                tip = t("text_blur.click_to_unblur") if chosen else t("text_blur.click_to_blur")
                ty = y + h + px(4) if y + h + px(20) < c.winfo_height() else y - px(18)
                label = c.create_text(
                    x, ty, text=tip, anchor="nw", fill="#FFFFFF", font=(UI_FONT, 9, "bold"),
                    tags="find",
                )  # fmt: skip
                bx = c.bbox(label)
                if bx:
                    back = c.create_rectangle(
                        bx[0] - px(3), bx[1] - px(1), bx[2] + px(3), bx[3] + px(1),
                        fill="#000000", outline="", tags="find",
                    )  # fmt: skip
                    c.tag_lower(back, label)
            if i == self._active and chosen:
                boxedit.draw(c, x, y, x + w, y + h, colour, px(5), tag="find")
        if self._press and self._press.rect:
            a, b, cc, d = self._press.rect
            c.create_rectangle(
                a, b, cc, d, outline="white", width=px(1), dash=px(3, 3), tags="find"
            )

    def _under(self, ex: int, ey: int) -> tuple[int, str] | None:
        """The item (and part of it) under the pointer: the active item's handles
        first, then the inside of any item on screen now (smallest first)."""
        at = float(self.pos_var.get())
        if self._active is not None and self._active < len(self.items):
            it = self.items[self._active]
            rect = self._on_canvas(it)
            if rect and it.chosen.get() and it.on_screen(at):
                part = boxedit.hit(rect, ex, ey, px(7))
                if part:
                    return self._active, part
        hits: list[tuple[float, int]] = []
        for i in self._shown():
            it = self.items[i]
            rect = self._on_canvas(it)
            if rect and it.on_screen(at) and boxedit.hit(rect, ex, ey, 0) == "move":
                hits.append((it.w * it.h, i))
        return (min(hits)[1], "move") if hits else None

    def _on_hover(self, ev: tk.Event[tk.Misc]) -> None:
        if self._press is None:
            under = self._under(ev.x, ev.y)
            hovered = under[0] if under else None
            if hovered != self._hovered:
                self._hovered = hovered
                self._draw()
            part = under[1] if under else None
            if under and part == "move" and under[0] != self._active:
                part = "hand"  # a click chooses it
            boxedit.set_cursor(self.preview.canvas, part if part != "hand" else None, "crosshair")
            if part == "hand":
                with contextlib.suppress(tk.TclError):
                    self.preview.canvas.configure(cursor="hand2")

    def _on_leave(self, _ev: tk.Event[tk.Misc]) -> None:
        if self._hovered is not None and self._press is None:
            self._hovered = None
            self._draw()

    def _on_press(self, ev: tk.Event[tk.Misc]) -> None:
        if self.preview.image_box() is None:
            return
        under = self._under(ev.x, ev.y)
        if under is None:
            self._press = _Press((ev.x, ev.y))
            return
        index, part = under
        self._press = _Press((ev.x, ev.y), index, part, self.items[index].box())

    def _on_drag(self, ev: tk.Event[tk.Misc]) -> None:
        p, box = self._press, self.preview.image_box()
        if p is None or box is None:
            return
        sx, sy = p.at
        if abs(ev.x - sx) + abs(ev.y - sy) > px(4):
            p.moved = True
        if not p.moved:
            return
        if p.index is None:
            p.rect = (min(sx, ev.x), min(sy, ev.y), max(sx, ev.x), max(sy, ev.y))
        else:
            _x0, _y0, bw, bh = box
            it = self.items[p.index]
            it.chosen.set(True)  # moving or resizing it means it's wanted
            self._active = p.index
            it.x, it.y, it.w, it.h = boxedit.drag(
                p.part, p.before, (ev.x - sx) / bw, (ev.y - sy) / bh, (1.0, 1.0), minimum=0.01
            )
        self._draw()

    def _on_release(self, _ev: tk.Event[tk.Misc]) -> None:
        p, self._press = self._press, None
        if p is None:
            return
        if p.index is not None:
            if not p.moved:  # a click: choose it, or leave it
                it = self.items[p.index]
                it.chosen.set(not it.chosen.get())
                self._active = p.index if it.chosen.get() else None
            self._changed()
            return
        if p.rect:
            self._add_by_hand(p.rect)
        self._draw()

    def _add_by_hand(self, rect: tuple[int, int, int, int]) -> None:
        box = self.preview.image_box()
        if box is None:
            return
        x0, y0, bw, bh = box
        a, b, c, d = rect
        x, y = max(0.0, (a - x0) / bw), max(0.0, (b - y0) / bh)
        w, h = min(1.0, (c - x0) / bw) - x, min(1.0, (d - y0) / bh) - y
        if w < 0.01 or h < 0.01:
            return
        it = Item(x, y, w, h, 0.0, None, textlike=True, sure=True, by_hand=True)
        it.chosen.set(True)
        self.items.append(it)
        self._active = len(self.items) - 1
        self._changed()

    # ── Render ────────────────────────────────────────────────────────
    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        regions = self._regions()
        if not regions:
            messagebox.showwarning(t("common.warning"), t("text_blur.nothing_chosen"), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "blurred")
        if not out:
            return
        w, h = common.even_size(info)
        common.render(
            self,
            RenderJob(
                src=src, info=info, out=out, graph=blur_graph(regions, w, h), name="Text Blurrer"
            ),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
        )
