"""
tabs/visuals/scopes.py  -  Video Scopes

Live waveform, RGB parade, histogram, vectorscope and false-colour views of
any frame (scrub with the slider), PNG snapshots, and export of a "scope
video" with the scope beside or below the picture, or a 2x2 grid of all
four scopes for colourist-style review.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from core import ffmpeg as ff
from core import tasks
from core.i18n import t
from core.ops import scopes as sc
from core.ops.shared import RenderJob
from core.proc import ProcResult
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, FramePreview, SaveRow, scroll_body

LAYOUTS = {
    "side": "Scope to the right of the picture",
    "below": "Scope below the picture",
    "grid": "Picture + all four scopes (2×2 grid)",
    "only": "Scope only",
}


class VideoScopesTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info: ff.MediaInfo | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.make_header(self, t("tab.video_scopes"), t("scopes.subtitle"), icon="📊")
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        self._build_scope_picker(body)
        self._build_views(body)
        self._build_scope_video(body)
        self.console = self.make_console_block(body, height=5)
        self._scope_changed()

    def _build_scope_picker(self, body: tk.Frame) -> None:
        """Which scope, and what it shows."""
        pick = tk.Frame(body, bg=CLR["bg"])
        pick.pack(fill="x", padx=px(20), pady=px(4))
        tk.Label(pick, text="Scope:", bg=CLR["bg"], fg=CLR["fg"], font=(UI_FONT, 10, "bold")).pack(
            side="left"
        )
        self.scope_var = tk.StringVar(value="waveform")
        for key, (label, *_rest) in sc.SCOPES.items():
            tk.Radiobutton(
                pick,
                text=label,
                value=key,
                variable=self.scope_var,
                bg=CLR["bg"],
                fg=CLR["fg"],
                selectcolor=CLR["input_bg"],
                activebackground=CLR["bg"],
                command=self._scope_changed,
            ).pack(side="left", padx=px(4))
        self.help_lbl = tk.Label(
            body,
            text="",
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            font=(UI_FONT, 9),
            wraplength=px(820),
            justify="left",
        )
        self.help_lbl.pack(anchor="w", padx=px(24))

    def _build_views(self, body: tk.Frame) -> None:
        """The picture beside its scope, and a slider through the video."""
        views = tk.Frame(body, bg=CLR["bg"])
        views.pack(pady=px(6))
        self.picture = FramePreview(views, 400, 225, show_time=False)
        self.picture.pack(side="left", padx=px(4))
        self.scope_view = FramePreview(views, 400, 225, show_time=False)
        self.scope_view.pack(side="left", padx=px(4))
        bar = tk.Frame(body, bg=CLR["bg"])
        bar.pack(fill="x", padx=px(24))
        self.pos_var = tk.DoubleVar(value=0.0)
        self.slider = ttk.Scale(
            bar, from_=0.0, to=1.0, variable=self.pos_var, command=lambda _v: self._moved()
        )
        self.slider.pack(side="left", fill="x", expand=True)
        framestep.slider(self.slider, lambda: framestep.fps_of(self.src_row.path))
        self.time_lbl = tk.Label(bar, text="", width=12, bg=CLR["bg"], fg=CLR["fgdim"])
        self.time_lbl.pack(side="left", padx=px(6))
        tk.Button(
            bar, text="Save snapshot…", relief="flat", cursor="hand2", command=self._snapshot
        ).pack(side="left")

    def _build_scope_video(self, body: tk.Frame) -> None:
        """Render the scope as a video: layout, encoder and where it goes."""
        vid = tk.LabelFrame(
            body, text=f"  {t('scopes.scope_video_section')}  ", padx=px(12), pady=px(8)
        )
        vid.pack(fill="x", padx=px(20), pady=px(8))
        row = tk.Frame(vid)
        row.pack(fill="x", pady=px(2))
        tk.Label(row, text=t("scopes.layout_label"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self.layout_var = tk.StringVar(value=LAYOUTS["side"])
        ttk.Combobox(
            row,
            textvariable=self.layout_var,
            values=list(LAYOUTS.values()),
            state="readonly",
            width=38,
        ).pack(side="left", padx=px(8))
        self.encoder = EncoderPanel(vid, codecs=("h264", "hevc", "prores"), show_ten_bit=False)
        self.encoder.pack(anchor="w", pady=px(6, 0))
        self.out_row = SaveRow(
            vid,
            t("common.output_file"),
            suggest=lambda: self.suggest_output(self.src_row.path, "scopes"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_vid = tk.Button(
            vid,
            text=t("scopes.scope_video_button"),
            bg="#7B1FA2",
            fg="white",
            font=(UI_FONT, 10, "bold"),
            cursor="hand2",
            command=self._render_video,
        )
        self.btn_vid.pack(pady=px(6))

    # ── Behaviour ────────────────────────────────────────────────────────────

    def _source_changed(self, path: str) -> None:
        self._info = ff.try_probe(path)
        dur = self._info.duration if self._info else 0.0
        self.slider.configure(to=max(dur, 0.01))
        self.pos_var.set(dur * 0.25)
        self.picture.load(path, dur, dur * 0.25)
        self.scope_view.set_filter(sc.SCOPES[self.scope_var.get()][1])
        self.scope_view.load(path, dur, dur * 0.25)
        self.time_lbl.config(text=ff.format_time(dur * 0.25))

    def _scope_changed(self) -> None:
        key = self.scope_var.get()
        self.help_lbl.config(text="ℹ  " + sc.SCOPES[key][3])
        self.scope_view.set_filter(sc.SCOPES[key][1])

    def _moved(self) -> None:
        pos = float(self.pos_var.get())
        self.time_lbl.config(text=ff.format_time(pos))
        self.picture.show(pos)
        self.scope_view.show(pos)

    def _layout_key(self) -> str:
        label = self.layout_var.get()
        return next((k for k, v in LAYOUTS.items() if v == label), "side")

    def _snapshot(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        from tabs.kit import dialogs

        base = os.path.splitext(os.path.basename(src))[0]
        out = dialogs.ask_save(
            f"{base}_{self.scope_var.get()}.png", kind="image", defaultextension=".png", parent=self
        )
        if not out:
            return
        w, h = common.even_size(info)
        layout = "grid" if self._layout_key() == "grid" else "side"
        graph = sc.export_graph(self.scope_var.get(), layout, w, h)
        cmd = sc.snapshot_cmd(src, float(self.pos_var.get()), graph, out)

        def work() -> ProcResult[str]:
            return ff.capture(cmd, timeout=120)

        def done(res: ProcResult[str]) -> None:
            if res.returncode == 0 and os.path.isfile(out):
                self.log_tagged(self.console, f"Snapshot saved: {out}", "success")
            else:
                self.log_tagged(
                    self.console,
                    "Snapshot failed: "
                    + ff.summarize_error((res.stderr or "").splitlines(), res.returncode),
                    "error",
                )

        tasks.submit(work, on_done=done, name="ScopeSnapshot")

    def _render_video(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        w, h = common.even_size(info)
        out = common.output_for(self, self.out_row, src, "scopes")
        if not out:
            return
        graph = sc.export_graph(self.scope_var.get(), self._layout_key(), w, h)
        common.render(
            self,
            RenderJob(src=src, info=info, out=out, graph=graph, name="Scope video"),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_vid,
        )
