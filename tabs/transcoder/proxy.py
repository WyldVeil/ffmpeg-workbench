"""
tabs/transcoder/proxy.py  -  Proxy Generator

Lightweight copies of camera files for smooth editing, made the way editors
expect them: short GOP or intra-frame codecs, even sizes, every audio track
as PCM in MOV, timecode and metadata kept, names that relink easily
(same name + suffix, or the same name in a "Proxy" folder).  Each file is
checked; failures are reported and never counted as done.
"""

from __future__ import annotations

import logging
import os
import tkinter as tk
from tkinter import messagebox, ttk

from core.i18n import t
from core.ops import proxy as px_ops
from core.outputs import free_name
from core.scaling import px
from tabs import common
from tabs.base_tab import MONO_FONT, UI_FONT, BaseTab
from tabs.kit import dialogs
from tabs.widgets import FolderRow

logger = logging.getLogger("crossfader.transcoder.proxy")

VIDEO_EXTS = (
    ".mp4",
    ".mov",
    ".mkv",
    ".mxf",
    ".avi",
    ".mts",
    ".m2ts",
    ".braw",
    ".r3d",
    ".mpg",
    ".webm",
    ".m4v",
)


class ProxyGenTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_paths = []
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.proxy_generator"), t("proxy.subtitle"), icon="⚙")
        lf = tk.LabelFrame(self, text=t("section.input_files"), padx=px(10), pady=px(6))
        lf.pack(fill="both", expand=True, padx=px(20), pady=px(8))
        self.listbox = tk.Listbox(lf, height=7, font=(MONO_FONT, 10), selectmode="extended")
        sb = ttk.Scrollbar(lf, command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        bf = tk.Frame(self)
        bf.pack(pady=px(4))
        for label, cmd in (
            (t("converter.btn_add_files"), self._add),
            ("Add a folder…", self._add_folder),
            (t("converter.btn_remove"), self._remove),
            (t("converter.btn_clear"), self._clear),
        ):
            tk.Button(bf, text=label, command=cmd, relief="flat", cursor="hand2").pack(
                side="left", padx=px(4)
            )
        self._build_options()

    def _build_options(self):
        """Proxy format, size, sound and where the proxies go."""
        opt = tk.LabelFrame(
            self, text=f"  {t('proxy.proxy_options_section')}  ", padx=px(15), pady=px(8)
        )
        opt.pack(fill="x", padx=px(20), pady=px(5))
        r0 = tk.Frame(opt)
        r0.pack(fill="x", pady=px(3))
        tk.Label(r0, text=t("proxy.preset_label"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self.preset_var = tk.StringVar(value=px_ops.PRESETS["prores"][0])
        ttk.Combobox(
            r0,
            textvariable=self.preset_var,
            values=[v[0] for v in px_ops.PRESETS.values()],
            state="readonly",
            width=50,
        ).pack(side="left", padx=px(8))
        r1 = tk.Frame(opt)
        r1.pack(fill="x", pady=px(3))
        tk.Label(r1, text="Size:").pack(side="left")
        self.size_var = tk.StringVar(value=px_ops.SIZES["half"][0])
        ttk.Combobox(
            r1,
            textvariable=self.size_var,
            values=[v[0] for v in px_ops.SIZES.values()],
            state="readonly",
            width=12,
        ).pack(side="left", padx=px(8))
        self.audio_var = tk.BooleanVar(value=True)
        tk.Checkbutton(r1, text="Include every audio track", variable=self.audio_var).pack(
            side="left", padx=px(12)
        )
        r2 = tk.Frame(opt)
        r2.pack(fill="x", pady=px(3))
        self.place_var = tk.StringVar(value="folder")
        tk.Radiobutton(
            r2,
            text="Same name in a “Proxy” folder next to each file (easy relinking)",
            variable=self.place_var,
            value="folder",
        ).pack(anchor="w")
        r3 = tk.Frame(r2)
        r3.pack(anchor="w")
        tk.Radiobutton(
            r3,
            text="Next to the originals, with the suffix",
            variable=self.place_var,
            value="suffix",
        ).pack(side="left")
        self.suffix_var = tk.StringVar(value="_proxy")
        tk.Entry(r3, textvariable=self.suffix_var, width=12).pack(side="left", padx=px(4))
        r4 = tk.Frame(r2)
        r4.pack(anchor="w")
        tk.Radiobutton(r4, text="In this folder:", variable=self.place_var, value="dir").pack(
            side="left"
        )
        self.out_dir_var = tk.StringVar()
        FolderRow(r4, "", self.out_dir_var, width=44, label_width=0).pack(side="left")
        self.btn_render = self.make_render_btn(
            self, t("proxy.render_button"), self._render, width=28
        )
        self.btn_render.pack(pady=px(10))
        self.console = self.make_console_block(self, height=6, padx=px(20), pady=px(4))

    def load_file(self, path) -> bool:
        if os.path.isfile(path) and path not in self.file_paths:
            self.file_paths.append(path)
            self.listbox.insert(tk.END, f"  {os.path.basename(path)}")
        return True

    def _add(self):
        for p in dialogs.ask_open_many("video", title="Add camera files", parent=self):
            self.load_file(p)

    def _add_folder(self):
        folder = dialogs.ask_directory(parent=self)
        if not folder:
            return
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if (
                name.lower().endswith(VIDEO_EXTS)
                and not name.lower().startswith(".")
                and os.path.isfile(path)
            ):
                self.load_file(path)

    def _remove(self):
        for i in reversed(self.listbox.curselection()):
            self.file_paths.pop(i)
            self.listbox.delete(i)

    def _clear(self):
        self.file_paths.clear()
        self.listbox.delete(0, tk.END)

    def _target(self, src: str, ext: str) -> str:
        base = os.path.splitext(os.path.basename(src))[0]
        place = self.place_var.get()
        if place == "folder":
            return os.path.join(os.path.dirname(src), "Proxy", base + ext)
        if place == "dir":
            folder = self.out_dir_var.get().strip()
            if not folder:
                raise ValueError("Choose the folder for the proxies.")
            return os.path.join(folder, base + ext)
        suffix = self.suffix_var.get().strip()
        if not suffix:
            raise ValueError(
                "Enter a suffix (or choose another place) so the originals are never replaced."
            )
        return os.path.join(os.path.dirname(src), base + suffix + ext)

    def _render(self):
        if not self.file_paths:
            messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=self)
            return
        preset = next(
            (k for k, v in px_ops.PRESETS.items() if v[0] == self.preset_var.get()), "prores"
        )
        size_key = next((k for k, v in px_ops.SIZES.items() if v[0] == self.size_var.get()), "half")
        ext = px_ops.PRESETS[preset][1]
        sources = {os.path.normcase(os.path.abspath(p)) for p in self.file_paths}
        jobs, taken = [], set()
        try:
            for src in self.file_paths:
                out = free_name(self._target(src, ext), taken | sources)
                taken.add(os.path.normcase(out))
                jobs.append((src, out))
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        batch = px_ops.ProxyBatch(
            jobs=jobs, preset=preset, size=size_key, audio=self.audio_var.get()
        )

        common.queue_job(
            self,
            common.Job(
                name=f"Proxies ({len(jobs)} files)",
                out=os.path.dirname(jobs[0][1]),
                work=batch.run,
                inputs=list(self.file_paths),
            ),
            btn=self.btn_render,
            console=self.console,
        )
