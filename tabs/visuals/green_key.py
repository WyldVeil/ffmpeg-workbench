"""
tabs/visuals/green_key.py  -  Green Screen Keyer

Remove a green/blue (or any colour, or white/black) background and put the
subject over a video, an image, a solid colour - or keep it transparent
(ProRes 4444 .mov or VP9 .webm with alpha).

* The key colour is sampled automatically from the frame edges, and can be
  picked with the eyedropper by clicking the "Before" preview.
* Chroma keys add colour-spill suppression (despill).
* The background is scaled to cover the frame without distortion and runs
  at the foreground's frame rate; the result lasts as long as the
  foreground.
"""

from __future__ import annotations

import os
import tkinter as tk
from functools import partial
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import tasks
from core.i18n import t
from core.ops import green_key as gk
from core.ops.shared import RenderJob, frame_rate
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.kit.widgets import ColourButton
from tabs.widgets import CompareView, FileRow, output_section, scroll_body

METHODS = {
    "chroma": "Chroma key (green / blue screens)",
    "color": "Colour key (any colour)",
    "luma_white": "Luma key - remove white",
    "luma_black": "Luma key - remove black",
}
BACKGROUNDS = {
    "file": "Video or image file",
    "color": "Solid colour",
    "alpha": "Transparent (keep alpha channel)",
}


class GreenKeyerTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info: ff.MediaInfo | None = None
        self._job: str | None = None
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        body, opts = self._build_header()
        self._build_output(body, opts)
        self._build_encoder(body)

    def _build_header(self) -> tuple[tk.Frame, tk.LabelFrame]:
        self.make_header(self, t("tab.green_screen_keyer"), t("green_key.subtitle"), icon="🟩")
        body = scroll_body(self)

        pick = tk.LabelFrame(body, text=f"  {t('section.input_files')}  ", padx=px(12), pady=px(8))
        pick.pack(fill="x", padx=px(20), pady=px(10, 4))
        self.src_row = FileRow(
            pick, t("green_key.foreground_label"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", pady=px(2))
        brow = tk.Frame(pick)
        brow.pack(fill="x", pady=px(2))
        tk.Label(
            brow, text=t("green_key.background"), width=16, anchor="w", font=(UI_FONT, 10, "bold")
        ).pack(side="left")
        self.bg_mode = tk.StringVar(value=BACKGROUNDS["file"])
        bm = ttk.Combobox(
            brow,
            textvariable=self.bg_mode,
            values=list(BACKGROUNDS.values()),
            state="readonly",
            width=32,
        )
        bm.pack(side="left", padx=px(6))
        bm.bind("<<ComboboxSelected>>", lambda _e: self._bg_mode_changed())
        self.bg_swatch = ColourButton(
            brow,
            "#202020",
            text=t("green_key.colour"),
            width=10,
            on_change=lambda _c: self._schedule(),
        )
        self.bg_row = FileRow(
            pick,
            t("green_key.background_label"),
            kind="visual",
            on_change=lambda _p: self._schedule(),
        )
        self.bg_row.pack(fill="x", pady=px(2))
        self.loop_var = tk.BooleanVar(value=True)
        self.loop_chk = tk.Checkbutton(
            pick, text=t("green_key.loop_bg_checkbox"), variable=self.loop_var
        )
        self.loop_chk.pack(anchor="w")

        self.compare = CompareView(
            body,
            400,
            225,
            before_label="Before - click to pick the key colour",
            after_label="Result",
        )
        self.compare.pack(pady=px(6))
        self.compare.view_before.canvas.bind("<Button-1>", self._eyedropper)
        self.compare.view_before.canvas.config(cursor="crosshair")

        opts = tk.LabelFrame(
            body, text=f"  {t('green_key.key_options_section')}  ", padx=px(12), pady=px(8)
        )
        opts.pack(fill="x", padx=px(20), pady=px(4))
        return body, opts

    def _build_output(self, body: tk.Frame, opts: tk.LabelFrame) -> None:
        r0 = tk.Frame(opts)
        r0.pack(fill="x", pady=px(2))
        tk.Label(r0, text=t("sync.method_label"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self.method_var = tk.StringVar(value=METHODS["chroma"])
        mb = ttk.Combobox(
            r0,
            textvariable=self.method_var,
            values=list(METHODS.values()),
            state="readonly",
            width=34,
        )
        mb.pack(side="left", padx=px(6))
        mb.bind("<<ComboboxSelected>>", lambda _e: self._schedule())
        tk.Label(r0, text=t("green_key.key_colour_label"), font=(UI_FONT, 10, "bold")).pack(
            side="left", padx=px(16, 4)
        )
        # typical chroma green until sampled
        self.swatch = ColourButton(
            r0, "#00B140", width=10, show_hex=True, on_change=lambda _c: self._schedule()
        )
        self.swatch.pack(side="left")
        tk.Button(
            r0,
            text=t("green_key.auto_detect"),
            relief="flat",
            cursor="hand2",
            command=lambda: self._auto_sample(force=True),
        ).pack(side="left", padx=px(6))

        self.sim_var = tk.DoubleVar(value=0.12)
        self.blend_var = tk.DoubleVar(value=0.08)
        for label, var, hi, hint in (
            (t("green_key.similarity_label"), self.sim_var, 0.6, t("green_key.similarity_hint")),
            (t("green_key.blend_label"), self.blend_var, 0.5, t("green_key.blend_hint")),
        ):
            row = tk.Frame(opts)
            row.pack(fill="x", pady=px(2))
            tk.Label(row, text=label, width=14, anchor="w").pack(side="left")
            tk.Scale(
                row,
                variable=var,
                from_=0.0,
                to=hi,
                resolution=0.005,
                orient="horizontal",
                length=px(260),
                command=lambda _v: self._schedule(),
            ).pack(side="left", padx=px(6))
            tk.Label(row, text=hint, fg=CLR["fgdim"], font=(UI_FONT, 8)).pack(side="left")
        self.despill_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            opts,
            text=t("green_key.remove_colour_spill_from_the_subject_green"),
            variable=self.despill_var,
            command=self._schedule,
        ).pack(anchor="w")

    def _build_encoder(self, body: tk.Frame) -> None:
        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9", "prores", "dnxhr"),
            suggest=lambda: self._suggest(self.src_row.path),
        )
        self.alpha_note = tk.Label(  # shown under the save row for transparent output
            self.out_row.master,
            text=t("green_key.transparent_output_save_as_mov_prores_4444"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        )

        self.btn_render = self.make_render_btn(
            body, t("green_key.composite_button"), self._render, width=28
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=6)

    # ── Settings → filters ───────────────────────────────────────────────────

    def method(self) -> str:
        label = self.method_var.get()
        return next((k for k, v in METHODS.items() if v == label), "chroma")

    def bg_kind(self) -> str:
        label = self.bg_mode.get()
        return next((k for k, v in BACKGROUNDS.items() if v == label), "file")

    def keyer(self) -> str:
        return gk.key_chain(
            self.method(),
            self.key_colour,
            self.sim_var.get(),
            self.blend_var.get(),
            self.despill_var.get(),
        )

    def preview_chain(self) -> str:
        info = self._info
        if info is None or not info.has_video:
            return ""
        kind, bg = self.bg_kind(), self.bg_row.path
        if kind == "file" and not (bg and os.path.isfile(bg)):
            kind = "alpha"  # no background yet: show the transparency
        return gk.preview_graph(
            kind, self.keyer(), common.even_size(info), frame_rate(info), bg, self.bg_colour
        )

    def _suggest(self, src: str) -> str:
        return self.suggest_output(src, "keyed", ".mov" if self.bg_kind() == "alpha" else ".mp4")

    # ── Behaviour ────────────────────────────────────────────────────────────

    def _source_changed(self, path: str) -> None:
        self._info = ff.try_probe(path)
        self.compare.load(path)
        self._auto_sample()
        if not self.out_row.path:
            self.out_row.set(self._suggest(path))

    def _auto_sample(self, force: bool = False) -> None:
        path = self.src_row.path
        if not path or not os.path.isfile(path):
            return
        at = (self._info.duration * 0.1) if self._info else 0.0

        def done(colour: str | None) -> None:
            if colour and (force or gk.spill_type(colour)):
                self._set_key(colour)
                self.log(
                    self.console,
                    t("green_key.key_colour_sampled_from_the_frame_edges", colour=colour),
                )
            elif force:
                self.log(
                    self.console,
                    t("green_key.could_not_detect_a_backdrop_colour_pick"),
                )
            self._schedule()

        tasks.submit(partial(gk.sample_border_colour, path, at), on_done=done, name="KeySample")

    def _eyedropper(self, event: tk.Event[tk.Misc]) -> None:
        info, path = self._info, self.src_row.path
        if info is None or not info.has_video or not path:
            return
        vw, vh = info.require_video().display_size
        pw, ph = 400, 225
        scale = min(pw / vw, ph / vh)
        iw, ih = vw * scale, vh * scale
        x = int((event.x - (pw - iw) / 2) / scale)
        y = int((event.y - (ph - ih) / 2) / scale)
        if not (0 <= x < vw and 0 <= y < vh):
            return
        x, y = min(max(x, 2), vw - 3), min(max(y, 2), vh - 3)

        def done(colour: str | None) -> None:
            if colour:
                self._set_key(colour)
                self._schedule()

        tasks.submit(
            partial(gk.sample_point, path, self.compare.position, x, y),
            on_done=done,
            name="KeyEyedropper",
        )

    @property
    def key_colour(self) -> str:
        """The colour keyed out, RRGGBB."""
        return self.swatch.rgb

    @property
    def bg_colour(self) -> str:
        return self.bg_swatch.rgb

    def _set_key(self, colour: str) -> None:
        self.swatch.set(colour)

    def _bg_mode_changed(self) -> None:
        kind = self.bg_kind()
        self.bg_row.pack_forget()
        self.loop_chk.pack_forget()
        self.bg_swatch.pack_forget()
        self.alpha_note.pack_forget()
        if kind == "file":
            self.bg_row.pack(fill="x", pady=px(2))
            self.loop_chk.pack(anchor="w")
        elif kind == "color":
            self.bg_swatch.pack(side="left", padx=px(6))
        else:
            self.alpha_note.pack(anchor="w", pady=px(4, 0))
        out = self.out_row.path
        if out:
            base, ext = os.path.splitext(out)
            if kind == "alpha" and ext.lower() not in (".mov", ".webm", ".mkv"):
                self.out_row.set(base + ".mov")
            elif kind != "alpha" and ext.lower() == ".mov" and base.endswith("_keyed"):
                self.out_row.set(base + ".mp4")
        self._schedule()

    def _schedule(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(200, self._refresh)

    def _refresh(self) -> None:
        self._job = None
        self.compare.set_filter(self.preview_chain())

    # ── Render ───────────────────────────────────────────────────────────────

    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src, what=t("green_key.foreground_label"))
        if info is None:
            return
        kind = self.bg_kind()
        bg = self.bg_row.path
        if kind == "file":
            if common.check_source(self, bg, what=t("green_key.background_label")) is None:
                return
        out = common.output_for(
            self, self.out_row, src, "keyed", inputs=[bg] if kind == "file" else []
        )
        if not out:
            return
        keyer = self.keyer()
        container = common.container_of(out)
        if kind == "alpha":
            if container not in ("mov", "webm", "mkv"):
                messagebox.showwarning(
                    t("common.warning"),
                    t("green_key.transparent_video_needs_a_mov_prores_4444"),
                    parent=self,
                )
                return
            self.log(self.console, t("green_key.keying_to_a_transparent_video"))
            self.run_ffmpeg(
                gk.transparent_cmd(src, out, keyer, info),
                self.console,
                on_done=lambda rc: self.show_result(rc, out),
                btn=self.btn_render,
                duration=info.duration or None,
                output=out,
                inputs=[src],
                name="Green screen",
            )
            return
        backdrop = gk.Backdrop(
            kind=kind, colour=self.bg_colour, path=bg, still=False, loop=self.loop_var.get()
        )
        if kind == "file":
            bg_info = ff.try_probe(bg)
            still = bool(bg_info and bg_info.is_image) or bg.lower().endswith(gk.IMAGE_EXTS)
            backdrop = gk.Backdrop(
                kind=kind, colour=self.bg_colour, path=bg, still=still, loop=self.loop_var.get()
            )
        graph, extra_inputs = gk.composite_graph(
            backdrop, keyer, common.even_size(info), frame_rate(info)
        )
        self.log(
            self.console,
            t(
                "green_key.keying_key_colour_method_var",
                key_colour=self.key_colour,
                method_var=self.method_var.get(),
            ),
        )
        common.render(
            self,
            RenderJob(
                src=src,
                info=info,
                out=out,
                graph=graph,
                extra_inputs=extra_inputs,
                name="Green screen",
                inputs=[bg] if kind == "file" else [],
            ),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
        )
