"""
tabs/system/metadata_cleaner.py  -  Metadata Cleaner

Remove what a video or audio file says about you before sharing it: GPS
location, phone/camera model, dates, names and comments, software, chapter
titles, cover pictures and hidden telemetry tracks (GoPro / drone GPS logs).
Nothing is re-encoded - the picture and sound are copied as they are - and
rotation, HDR information and (optionally) track languages are kept.  Each
result is checked again afterwards.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import dialogs
from core import ffmpeg as ff
from core import metaclean as mc
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab


class MetadataCleanerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.files: list = []
        self.findings: dict = {}
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, "Metadata Cleaner", "Remove location, device, dates and names from files - "
                         "without re-encoding.", icon="🧹")
        top = tk.Frame(self)
        top.pack(fill="x", padx=20, pady=(10, 4))
        for label, cmd in (("Add files…", self._add), ("Remove", self._remove), ("Clear", self._clear)):
            tk.Button(top, text=label, command=cmd, relief="flat", cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Label(top, text="(or drop files here)", fg=CLR["fgdim"]).pack(side="left", padx=6)

        mid = tk.Frame(self)
        mid.pack(fill="both", expand=True, padx=20, pady=4)
        self.tree = ttk.Treeview(mid, columns=("what",), show="tree headings", height=12)
        self.tree.heading("#0", text="File / what it contains")
        self.tree.heading("what", text="Value")
        self.tree.column("#0", width=420)
        self.tree.column("what", width=420)
        sb = ttk.Scrollbar(mid, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        opt = tk.LabelFrame(self, text="  Keep  ", padx=12, pady=6)
        opt.pack(fill="x", padx=20, pady=4)
        self.keep_lang = tk.BooleanVar(value=True)
        self.keep_subs = tk.BooleanVar(value=True)
        self.keep_chapters = tk.BooleanVar(value=False)
        self.keep_cover = tk.BooleanVar(value=False)
        self.deep = tk.BooleanVar(value=False)
        for text, var in (("Track languages (which audio is English…)", self.keep_lang),
                          ("Subtitles", self.keep_subs), ("Chapters and their titles", self.keep_chapters),
                          ("Cover picture", self.keep_cover)):
            tk.Checkbutton(opt, text=text, variable=var).pack(side="left", padx=(0, 12))
        tk.Checkbutton(self, text="Deep clean: also remove encoder notes inside the video stream (not for HDR "
                                  "video, which needs them)", variable=self.deep).pack(anchor="w", padx=20)
        out = tk.Frame(self)
        out.pack(fill="x", padx=20, pady=4)
        self.where_var = tk.StringVar(value="next")
        tk.Radiobutton(out, text="Save next to each original as “name (clean)”", variable=self.where_var,
                       value="next").pack(side="left")
        tk.Radiobutton(out, text="Into folder:", variable=self.where_var, value="folder").pack(side="left", padx=(12, 0))
        self.folder_var = tk.StringVar()
        tk.Entry(out, textvariable=self.folder_var, width=34).pack(side="left", padx=4)
        tk.Button(out, text="…", relief="flat", command=lambda: self.folder_var.set(
            dialogs.ask_directory(parent=self) or self.folder_var.get())).pack(side="left")
        self.btn = tk.Button(self, text="🧹  CLEAN FILES", font=(UI_FONT, 12, "bold"), bg=CLR["green"], fg="white",
                             width=24, cursor="hand2", command=self._clean)
        self.btn.pack(pady=8)
        cf = tk.Frame(self)
        cf.pack(fill="both", expand=False, padx=20, pady=(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    # ── Files ─────────────────────────────────────────────────────────
    def load_file(self, path) -> bool:
        if not os.path.isfile(path) or path in self.files:
            return True
        info = ff.try_probe(path)
        if info is None:
            self.log(self.console, f"Can't read {os.path.basename(path)} - skipped.")
            return True
        self.files.append(path)
        found = mc.inspect(info) + mc.stream_notes(path)
        self.findings[path] = found
        node = self.tree.insert("", "end", iid=path, text=os.path.basename(path),
                                values=(f"{len(found)} item(s) found" if found else "nothing personal found",),
                                open=True)
        for f in found:
            self.tree.insert(node, "end", text=f"{mc.CATEGORY_TITLES.get(f.category, f.category)} · {f.name}",
                             values=(f.value[:120],))
        return True

    def _add(self):
        for p in dialogs.ask_open_many("media", title="Files to clean", parent=self):
            self.load_file(p)

    def _remove(self):
        for iid in self.tree.selection():
            root = iid if iid in self.files else self.tree.parent(iid)
            if root in self.files:
                self.files.remove(root)
                self.findings.pop(root, None)
                self.tree.delete(root)

    def _clear(self):
        self.files.clear()
        self.findings.clear()
        self.tree.delete(*self.tree.get_children())

    # ── Clean ─────────────────────────────────────────────────────────
    def _clean(self):
        if not self.files:
            messagebox.showwarning("Nothing to clean", "Add some files first.", parent=self)
            return
        folder = None
        if self.where_var.get() == "folder":
            folder = self.folder_var.get().strip()
            if not folder:
                messagebox.showwarning("Folder", "Choose the folder for the cleaned files.", parent=self)
                return
            os.makedirs(folder, exist_ok=True)
        opts = dict(keep_language=self.keep_lang.get(), keep_subtitles=self.keep_subs.get(),
                    keep_chapters=self.keep_chapters.get(), keep_cover=self.keep_cover.get(), deep=self.deep.get())
        files = list(self.files)
        jobs = []
        taken = set()
        for src in files:
            out = mc.free_output(src, folder)
            while out in taken:
                base, ext = os.path.splitext(out)
                out = f"{base}_{len(taken)}{ext}"
            taken.add(out)
            jobs.append((src, out))

        def work(_p, _c, ctx):
            failed, notes_total = [], 0
            for i, (src, out) in enumerate(jobs):
                ctx.span(i / len(jobs), (i + 1) / len(jobs))
                ctx.log(f"[{i + 1}/{len(jobs)}] {os.path.basename(src)}")
                part = f"{os.path.splitext(out)[0]}.part{os.path.splitext(out)[1]}"
                try:
                    info = ff.probe(src)
                    argv, notes = mc.clean_command(info, src, part, **opts)
                    for n in notes:
                        ctx.log("  " + n)
                    ctx.run(argv, duration=info.duration or None)
                    os.replace(part, out)
                    after = mc.inspect(ff.probe(out, use_cache=False))
                    if opts["deep"]:
                        after += mc.stream_notes(out)
                    left = mc.leftovers(after, keep_chapters=opts["keep_chapters"], keep_cover=opts["keep_cover"],
                                        keep_subtitles=opts["keep_subtitles"])
                    if left:
                        notes_total += len(left)
                        for f in left:
                            ctx.log("  still there: " + f.line())
                    ctx.log(f"  ✓ {os.path.basename(out)} - {len(self.findings.get(src, []))} item(s) removed")
                except ff.CancelledError:
                    if os.path.exists(part):
                        os.remove(part)
                    raise
                except Exception as exc:
                    if os.path.exists(part):
                        os.remove(part)
                    reason = (str(exc).strip().splitlines() or [type(exc).__name__])[-1]
                    failed.append((src, reason))
                    ctx.log(f"  ✖ {reason}")
            if failed:
                raise RuntimeError(f"{len(failed)} of {len(jobs)} file(s) failed:\n" +
                                   "\n".join(f"• {os.path.basename(s)}: {r}" for s, r in failed))
            return 0

        out_label = jobs[0][1] if len(jobs) == 1 else os.path.dirname(jobs[0][1])
        common.queue_job(self, name=f"Metadata Cleaner ({len(jobs)} file(s))", out=out_label, work=work,
                         btn=self.btn, btn_label="🧹  CLEAN FILES", console=self.console, inputs=files)
