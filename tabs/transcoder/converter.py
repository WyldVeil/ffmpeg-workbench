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

import contextlib
import logging
import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops.converter import FORMATS, stream_args
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit import dialogs

logger = logging.getLogger("crossfader.transcoder.converter")


def free_name(path: str, taken: set) -> str:
    base, ext = os.path.splitext(path)
    cand, n = path, 2
    while os.path.exists(cand) or os.path.normcase(cand) in taken:
        cand = f"{base} ({n}){ext}"
        n += 1
    return cand


class FormatConverterTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_paths = []
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.format_converter"), t("converter.desc_subtitle"), icon="🔄")
        lf = tk.LabelFrame(self, text=t("section.input_files"), padx=px(10), pady=px(6))
        lf.pack(fill="both", expand=True, padx=px(20), pady=px(8))
        self.listbox = tk.Listbox(lf, height=7, font=(MONO_FONT, 10), selectmode="extended")
        sb = ttk.Scrollbar(lf, command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
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
        tk.Label(btn_f, text="(or drop files here)", fg=CLR["fgdim"]).pack(side="left", padx=px(6))

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
            text="Smart: keep streams the new format can hold as they are (fast, no quality "
            "loss) and convert only the rest",
            variable=self.copy_var,
        ).pack(anchor="w")
        r2 = tk.Frame(opts)
        r2.pack(fill="x", pady=px(4))
        tk.Label(r2, text=t("common.output_folder")).pack(side="left")
        self.out_dir_var = tk.StringVar()
        tk.Entry(r2, textvariable=self.out_dir_var, width=50).pack(side="left", padx=px(6))
        tk.Button(r2, text=t("btn.browse"), command=self._browse_dir, relief="flat").pack(
            side="left"
        )
        tk.Label(r2, text=t("converter.lbl_blank_hint"), fg=CLR["fgdim"]).pack(
            side="left", padx=px(6)
        )
        self.btn_render = tk.Button(
            self,
            text=t("converter.btn_convert_all"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["green"],
            fg="white",
            height=2,
            width=28,
            command=self._render,
        )
        self.btn_render.pack(pady=px(10))
        cf = tk.Frame(self)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(4))
        self.console, csb = self.make_console(cf, height=6)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def load_file(self, path) -> bool:
        if os.path.isfile(path) and path not in self.file_paths:
            self.file_paths.append(path)
            self.listbox.insert(tk.END, f"  {os.path.basename(path)}")
        return True

    def _add(self):
        for p in dialogs.ask_open_many("media", title="Add files", parent=self):
            self.load_file(p)

    def _remove(self):
        for i in reversed(self.listbox.curselection()):
            self.file_paths.pop(i)
            self.listbox.delete(i)

    def _clear(self):
        self.file_paths.clear()
        self.listbox.delete(0, tk.END)

    def _browse_dir(self):
        p = dialogs.ask_directory(parent=self)
        if p:
            self.out_dir_var.set(p)

    def _fmt(self) -> str:
        return next((k for k, v in FORMATS.items() if v[0] == self.fmt_var.get()), "mp4")

    def _render(self):
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
                    t("common.warning"), f"Can't use that folder:\n{exc}", parent=self
                )
                return
        smart = self.copy_var.get()
        paths = self.file_paths[:]
        taken: set = set()
        jobs = []
        for src in paths:
            base = os.path.splitext(os.path.basename(src))[0]
            out = free_name(
                os.path.join(out_dir or os.path.dirname(src), f"{base}.{fmt}"),
                taken | {os.path.normcase(os.path.abspath(p)) for p in paths},
            )
            taken.add(os.path.normcase(out))
            jobs.append((src, out))

        def work(_p, _c, ctx):
            done, failed = [], []
            for i, (src, out) in enumerate(jobs):
                ctx.span(i / len(jobs), (i + 1) / len(jobs))
                ctx.log(f"[{i + 1}/{len(jobs)}] {os.path.basename(src)} → {os.path.basename(out)}")
                part = f"{os.path.splitext(out)[0]}.part{os.path.splitext(out)[1]}"
                try:
                    info = ff.probe(src)
                    pre, args, notes, summary = stream_args(info, fmt, smart)
                    for note in notes:
                        ctx.log("  " + note)
                    ctx.log(f"  {summary}")
                    ctx.run(
                        [
                            ff.ffmpeg_path(),
                            "-hide_banner",
                            "-nostdin",
                            "-y",
                            *pre,
                            "-i",
                            src,
                            *args,
                            part,
                        ],
                        duration=info.duration or None,
                    )
                    os.replace(part, out)
                    done.append(out)
                except ff.CancelledError:
                    _remove(part)
                    raise
                except Exception as exc:  # one bad file must not stop the batch
                    logger.debug("work: best-effort step failed", exc_info=True)
                    _remove(part)
                    reason = (
                        str(exc).strip().splitlines()[-1]
                        if str(exc).strip()
                        else type(exc).__name__
                    )
                    if reason == "no audio":
                        reason = "it has no audio"
                    failed.append((src, reason))
                    ctx.log(f"  ✖ failed: {reason}")
            ctx.log(f"Converted {len(done)} of {len(jobs)} file(s).")
            if failed:
                raise RuntimeError(
                    f"{len(failed)} of {len(jobs)} file(s) failed:\n"
                    + "\n".join(f"• {os.path.basename(s)}: {r}" for s, r in failed)
                )
            return 0

        out_label = jobs[0][1] if len(jobs) == 1 else os.path.dirname(jobs[0][1])
        common.queue_job(
            self,
            name=f"Convert {len(jobs)} file(s) to {fmt.upper()}",
            out=out_label,
            work=work,
            btn=self.btn_render,
            btn_label=t("converter.btn_convert_all"),
            console=self.console,
            inputs=paths,
        )


def _remove(path):
    with contextlib.suppress(OSError):
        os.remove(path)
