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
from pathlib import Path
from tkinter import colorchooser, messagebox, ttk

from core import ass
from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, FramePreview, SaveRow, scroll_body

logger = logging.getLogger("crossfader.social.hard_subber")

POSITIONS = {
    "bottom": ("Bottom centre", 2),
    "top": ("Top centre", 8),
    "middle": ("Middle", 5),
    "bottom_left": ("Bottom left", 1),
    "bottom_right": ("Bottom right", 3),
}
_EXTERNAL = "External subtitle file"


def to_utf8_copy(path: str, folder: str) -> str:
    """Copy a subtitle file into *folder* as UTF-8 with a safe name."""
    raw = Path(path).read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        try:
            from charset_normalizer import from_bytes  # optional, better guesses

            best = from_bytes(raw).best()
            text = str(best) if best is not None else None
        except Exception:
            logger.debug("to_utf8_copy: best-effort step failed", exc_info=True)
            text = None
    if text is None:
        text = raw.decode("cp1252", errors="replace")
    ext = os.path.splitext(path)[1].lower() or ".srt"
    out = os.path.join(folder, f"subs{ext}")
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.replace("\r\n", "\n"))
    return out


def force_style(
    *,
    font: str,
    size: int,
    colour: str,
    outline_colour: str,
    outline: float,
    box: bool,
    position: str,
    margin: int,
) -> str:
    align = POSITIONS.get(position, POSITIONS["bottom"])[1]
    parts = [
        f"FontName={font}",
        f"FontSize={size}",
        f"PrimaryColour={ass.colour(colour)}",
        f"OutlineColour={ass.colour(outline_colour)}",
        f"Alignment={align}",
        f"MarginV={margin}",
        "Bold=0",
    ]
    if box:
        parts += [
            "BorderStyle=3",
            f"Outline={max(1.0, outline):g}",
            "Shadow=0",
            f"BackColour={ass.colour(outline_colour, 0.35)}",
        ]
    else:
        parts += [
            "BorderStyle=1",
            f"Outline={outline:g}",
            "Shadow=1",
            f"BackColour={ass.colour('#000000', 0.5)}",
        ]
    return ",".join(parts)


class HardSubberTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self.colour = "#FFFFFF"
        self.outline_colour = "#000000"
        self._info = None
        self._tracks: list = []
        self._work = None
        self._job = None
        self._build_ui()

    def _build_ui(self):
        body, r0, st = self._build_header()
        out_lf = self._build_output(body, r0, st)
        self._build_out(body, out_lf)

    def _build_header(self):
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
        tk.Label(srow, text="Subtitles:", width=16, anchor="w", font=(UI_FONT, 10, "bold")).pack(
            side="left"
        )
        self.track_var = tk.StringVar(value=_EXTERNAL)
        self.track_box = ttk.Combobox(
            srow, textvariable=self.track_var, values=[_EXTERNAL], state="readonly", width=40
        )
        self.track_box.pack(side="left", padx=px(6))
        self.track_box.bind("<<ComboboxSelected>>", lambda _e: self._schedule())
        self.sub_row = FileRow(
            body, "Subtitle file:", kind="subtitle", on_change=lambda _p: self._subs_changed()
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
            body, text="  Style (SRT / VTT, or override ASS)  ", padx=px(10), pady=px(6)
        )
        st.pack(fill="x", padx=px(20), pady=px(4))
        r0 = tk.Frame(st)
        r0.pack(fill="x", pady=px(2))
        tk.Label(r0, text="Font:").pack(side="left")
        self.font_var = tk.StringVar(value="Open Sans")
        ttk.Combobox(
            r0, textvariable=self.font_var, values=ass.families(["Arial", "DejaVu Sans"]), width=16
        ).pack(side="left", padx=px(6))
        tk.Label(r0, text="Size:").pack(side="left", padx=px(8, 0))
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

    def _build_output(self, body, r0, st):
        self.col_btn = tk.Button(
            r0, text="Text", relief="flat", command=lambda: self._pick("colour")
        )
        self.col_btn.pack(side="left", padx=px(4))
        self.out_btn = tk.Button(
            r0, text="Outline / box", relief="flat", command=lambda: self._pick("outline_colour")
        )
        self.out_btn.pack(side="left", padx=px(4))
        r1 = tk.Frame(st)
        r1.pack(fill="x", pady=px(2))
        tk.Label(r1, text="Outline:").pack(side="left")
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
            r1, text="Background box", variable=self.box_var, command=self._schedule
        ).pack(side="left", padx=px(8))
        tk.Label(r1, text="Position:").pack(side="left")
        self.position_var = tk.StringVar(value=POSITIONS["bottom"][0])
        pb = ttk.Combobox(
            r1,
            textvariable=self.position_var,
            values=[v[0] for v in POSITIONS.values()],
            state="readonly",
            width=14,
        )
        pb.pack(side="left", padx=px(4))
        pb.bind("<<ComboboxSelected>>", lambda _e: self._schedule())
        tk.Label(r1, text="Margin:").pack(side="left", padx=px(8, 0))
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
            text="Apply this style to .ass subtitles too (replaces their styling)",
            variable=self.override_var,
            command=self._schedule,
        ).pack(anchor="w")
        for var in (self.font_var,):
            var.trace_add("write", lambda *_a: self._schedule())

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9"))
        return out_lf

    def _build_out(self, body, out_lf):
        self.encoder.pack(anchor="w")
        self.out_row = SaveRow(
            out_lf,
            t("common.output_file"),
            suggest=lambda: self.suggest_output(self.src_row.path, "subbed"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(
            body,
            text="🔥  BURN SUBTITLES",
            font=(UI_FONT, 12, "bold"),
            bg=CLR["pink"],
            fg="white",
            width=24,
            cursor="hand2",
            command=self._render,
        )
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self._paint()

    # ── state ────────────────────────────────────────────────────────────────

    def _paint(self):
        self.col_btn.config(bg=self.colour, fg="black")
        self.out_btn.config(bg=self.outline_colour, fg="white")

    def _pick(self, attr):
        res = colorchooser.askcolor(getattr(self, attr), parent=self)
        if res and res[1]:
            setattr(self, attr, res[1].upper())
            self._paint()
            self._schedule()

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        info = self._info
        self._tracks = []
        values = [_EXTERNAL]
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
        self.track_var.set(values[1] if len(values) > 1 and not self.sub_row.path else _EXTERNAL)
        dur = info.duration if info else 0.0
        self.slider.configure(to=max(dur, 0.01))
        self.pos_var.set(min(dur * 0.2, 30.0))
        self.preview.load(path, dur, float(self.pos_var.get()))
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "subbed"))
        self._schedule()

    def _subs_changed(self):
        self.track_var.set(_EXTERNAL)
        self._schedule()

    def _style(self) -> str:
        key = next((k for k, v in POSITIONS.items() if v[0] == self.position_var.get()), "bottom")
        return force_style(
            font=self.font_var.get() or "Open Sans",
            size=int(self.size_var.get()),
            colour=self.colour,
            outline_colour=self.outline_colour,
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

    def _schedule(self):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(300, self._refresh)

    def _refresh(self):
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

    def _render(self):
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

        def done(rc):
            shutil.rmtree(work, ignore_errors=True)
            self.show_result(rc, out)

        common.render(
            self,
            common.RenderJob(src=src, info=info, out=out, chain=chain, name="Hard-Subber"),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
            on_done=done,
        )

    def on_app_closing(self):
        if self._work:
            shutil.rmtree(self._work, ignore_errors=True)
            self._work = None  # a new one is made if the page is used again
