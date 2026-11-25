"""
tabs/transcoder/converter.py  -  Format Converter

Convert any number of files to another container or audio format.

Smart mode keeps every stream that the new format can hold exactly as it is
(no quality loss, very fast) and converts only what it cannot hold - e.g.
MKV(H.264 + DTS + SRT) -> MP4 copies the video, converts DTS to AAC and SRT to
MP4 text subtitles.  "Always re-encode" converts everything.

All audio tracks, subtitles, chapters and metadata are kept when the format
allows; outputs never overwrite anything (a free name is chosen) and each
file's result is checked - failures are reported, not counted as done.
"""

from __future__ import annotations

import logging
import os
import tkinter as tk
from tkinter import messagebox, ttk

from core.i18n import t
from core.ops.converter import FORMATS, ConvertBatch
from core.outputs import free_name
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit import dialogs
from tabs.kit.tktyped import linked_scrollbar, selected_rows
from tabs.widgets import FolderRow

logger = logging.getLogger("crossfader.transcoder.converter")


class FormatConverterTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.file_paths: list[str] = []
        self._build_ui()

    def _build_ui(self) -> None:
        self.make_header(self, t("tab.format_converter"), t("converter.desc_subtitle"), icon="🔄")
        lf = tk.LabelFrame(self, text=t("section.input_files"), padx=px(10), pady=px(6))
        lf.pack(fill="both", expand=True, padx=px(20), pady=px(8))
        self.listbox = tk.Listbox(lf, height=7, font=(MONO_FONT, 10), selectmode="extended")
        sb = linked_scrollbar(lf, self.listbox)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        btn_f = tk.Frame(self)
        btn_f.pack(pady=px(4))
        for label, cmd in (
            (t("converter.btn_add_files"), self._add),
            (t("converter.btn_remove"), self._remove),
            (t("converter.btn_clear"), self._clear),
        ):
            tk.Button(btn_f, text=label, command=cmd, cursor="hand2", relief="flat").pack(
                side="left", padx=px(4)
            )
        tk.Label(btn_f, text=t("converter.or_drop_files_here"), fg=CLR["fgdim"]).pack(
            side="left", padx=px(6)
        )

        opts = tk.LabelFrame(
            self, text=t("converter.sect_conversion_options"), padx=px(15), pady=px(8)
        )
        opts.pack(fill="x", padx=px(20), pady=px(5))
        r0 = tk.Frame(opts)
        r0.pack(fill="x", pady=px(4))
        tk.Label(r0, text=t("converter.lbl_output_format"), font=(UI_FONT, 10, "bold")).pack(
            side="left"
        )
        self.fmt_var = tk.StringVar(value=FORMATS["mp4"][0])
        ttk.Combobox(
            r0,
            textvariable=self.fmt_var,
            values=[v[0] for v in FORMATS.values()],
            state="readonly",
            width=30,
        ).pack(side="left", padx=px(8))
        self.copy_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            opts,
            text=t("converter.smart_keep_streams_the_new_format_can"),
            variable=self.copy_var,
        ).pack(anchor="w")
        self.out_dir_var = tk.StringVar()
        out_row = FolderRow(opts, t("common.output_folder"), self.out_dir_var, width=50)
        out_row.pack(fill="x", pady=px(4))
        tk.Label(out_row, text=t("converter.lbl_blank_hint"), fg=CLR["fgdim"]).pack(
            side="left", padx=px(6)
        )
        self.btn_render = self.make_render_btn(
            self, t("converter.btn_convert_all"), self._render, width=28
        )
        self.btn_render.pack(pady=px(10))
        self.console = self.make_console_block(self, height=6, padx=px(20), pady=px(4))

    def load_file(self, path: str) -> bool:
        if os.path.isfile(path) and path not in self.file_paths:
            self.file_paths.append(path)
            self.listbox.insert(tk.END, f"  {os.path.basename(path)}")
        return True

    def _add(self) -> None:
        for p in dialogs.ask_open_many("media", title=t("converter.add_files"), parent=self):
            self.load_file(p)

    def _remove(self) -> None:
        for i in reversed(selected_rows(self.listbox)):
            self.file_paths.pop(i)
            self.listbox.delete(i)

    def _clear(self) -> None:
        self.file_paths.clear()
        self.listbox.delete(0, tk.END)

    def _fmt(self) -> str:
        return next((k for k, v in FORMATS.items() if v[0] == self.fmt_var.get()), "mp4")

    def _render(self) -> None:
        if not self.file_paths:
            messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=self)
            return
        fmt = self._fmt()
        out_dir = self.out_dir_var.get().strip()
        if out_dir and not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except OSError as exc:
                messagebox.showwarning(
                    t("common.warning"),
                    t("converter.can_t_use_that_folder_exc", exc=exc),
                    parent=self,
                )
                return
        smart = self.copy_var.get()
        paths = self.file_paths[:]
        taken: set[str] = set()
        jobs: list[tuple[str, str]] = []
        for src in paths:
            base = os.path.splitext(os.path.basename(src))[0]
            out = free_name(
                os.path.join(out_dir or os.path.dirname(src), f"{base}.{fmt}"),
                taken | {os.path.normcase(os.path.abspath(p)) for p in paths},
            )
            taken.add(os.path.normcase(out))
            jobs.append((src, out))

        batch = ConvertBatch(jobs=jobs, fmt=fmt, smart=smart)
        out_label = jobs[0][1] if len(jobs) == 1 else os.path.dirname(jobs[0][1])
        common.queue_job(
            self,
            common.Job(
                name=f"Convert {len(jobs)} file(s) to {fmt.upper()}",
                out=out_label,
                work=batch.run,
                inputs=paths,
            ),
            btn=self.btn_render,
            console=self.console,
        )
