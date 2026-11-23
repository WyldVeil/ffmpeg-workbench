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

import logging
import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import metaclean as mc
from core.queue_manager import TaskContext
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, BaseTab
from tabs.kit import dialogs
from tabs.kit.tktyped import linked_scrollbar
from tabs.widgets import FolderRow

logger = logging.getLogger("crossfader.system.metadata_cleaner")


def _outputs(files: list[str], folder: str | None) -> list[tuple[str, str]]:
    """(source, cleaned copy) for every file, no two copies with the same name."""
    jobs: list[tuple[str, str]] = []
    taken: set[str] = set()
    for src in files:
        out = mc.free_output(src, folder)
        while out in taken:
            base, ext = os.path.splitext(out)
            out = f"{base}_{len(taken)}{ext}"
        taken.add(out)
        jobs.append((src, out))
    return jobs


def _clean_one(
    ctx: TaskContext, src: str, out: str, opts: dict[str, bool], removed: int
) -> str | None:
    """Clean *src* into *out*, then check what's left; why it failed, or None."""
    part = f"{os.path.splitext(out)[0]}.part{os.path.splitext(out)[1]}"
    try:
        info = ff.probe(src)
        argv, notes = mc.clean_command(info, src, part, **opts)
        for n in notes:
            ctx.log("  " + n)
        ctx.run(argv, duration=info.duration or None)
        os.replace(part, out)
        after = mc.inspect(ff.probe(out, use_cache=False))
        after += mc.stream_notes(out) if opts["deep"] else []
        kept = {k: opts[k] for k in ("keep_chapters", "keep_cover", "keep_subtitles")}
        for f in mc.leftovers(after, **kept):
            ctx.log("  still there: " + f.line())
        ctx.log(f"  ✓ {os.path.basename(out)} - {removed} item(s) removed")
        return None
    except ff.CancelledError:
        if os.path.exists(part):
            os.remove(part)
        raise
    except Exception as exc:
        logger.debug("work: best-effort step failed", exc_info=True)
        if os.path.exists(part):
            os.remove(part)
        reason = (str(exc).strip().splitlines() or [type(exc).__name__])[-1]
        ctx.log(f"  ✖ {reason}")
        return reason


class MetadataCleanerTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.files: list[str] = []
        self.findings: dict[str, list[mc.Finding]] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        self.make_header(
            self,
            "Metadata Cleaner",
            "Remove location, device, dates and names from files - without re-encoding.",
            icon="🧹",
        )
        top = tk.Frame(self)
        top.pack(fill="x", padx=px(20), pady=px(10, 4))
        for label, cmd in (
            ("Add files…", self._add),
            ("Remove", self._remove),
            ("Clear", self._clear),
        ):
            tk.Button(top, text=label, command=cmd, relief="flat", cursor="hand2").pack(
                side="left", padx=px(0, 6)
            )
        tk.Label(top, text="(or drop files here)", fg=CLR["fgdim"]).pack(side="left", padx=px(6))

        mid = tk.Frame(self)
        mid.pack(fill="both", expand=True, padx=px(20), pady=px(4))
        self.tree = ttk.Treeview(mid, columns=("what",), show="tree headings", height=12)
        self.tree.heading("#0", text="File / what it contains")
        self.tree.heading("what", text="Value")
        self.tree.column("#0", width=px(420))
        self.tree.column("what", width=px(420))
        sb = linked_scrollbar(mid, self.tree)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        opt = tk.LabelFrame(self, text="  Keep  ", padx=px(12), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.keep_lang = tk.BooleanVar(value=True)
        self.keep_subs = tk.BooleanVar(value=True)
        self.keep_chapters = tk.BooleanVar(value=False)
        self.keep_cover = tk.BooleanVar(value=False)
        self.deep = tk.BooleanVar(value=False)
        for text, var in (
            ("Track languages (which audio is English…)", self.keep_lang),
            ("Subtitles", self.keep_subs),
            ("Chapters and their titles", self.keep_chapters),
            ("Cover picture", self.keep_cover),
        ):
            tk.Checkbutton(opt, text=text, variable=var).pack(side="left", padx=px(0, 12))
        tk.Checkbutton(
            self,
            text="Deep clean: also remove encoder notes inside the video stream (not for HDR "
            "video, which needs them)",
            variable=self.deep,
        ).pack(anchor="w", padx=px(20))
        out = tk.Frame(self)
        out.pack(fill="x", padx=px(20), pady=px(4))
        self.where_var = tk.StringVar(value="next")
        tk.Radiobutton(
            out,
            text="Save next to each original as “name (clean)”",
            variable=self.where_var,
            value="next",
        ).pack(side="left")
        tk.Radiobutton(out, text="Into folder:", variable=self.where_var, value="folder").pack(
            side="left", padx=px(12, 0)
        )
        self.folder_var = tk.StringVar()
        FolderRow(out, "", self.folder_var, width=34, label_width=0).pack(side="left")
        self.btn = self.make_render_btn(self, "🧹  CLEAN FILES", self._clean, width=24)
        self.btn.pack(pady=px(8))
        self.console = self.make_console_block(self, height=5, expand=False)

    # ── Files ─────────────────────────────────────────────────────────
    def load_file(self, path: str) -> bool:
        if not os.path.isfile(path) or path in self.files:
            return True
        info = ff.try_probe(path)
        if info is None:
            self.log(self.console, f"Can't read {os.path.basename(path)} - skipped.")
            return True
        self.files.append(path)
        found = mc.inspect(info) + mc.stream_notes(path)
        self.findings[path] = found
        node = self.tree.insert(
            "",
            "end",
            iid=path,
            text=os.path.basename(path),
            values=(f"{len(found)} item(s) found" if found else "nothing personal found",),
            open=True,
        )
        for f in found:
            self.tree.insert(
                node,
                "end",
                text=f"{mc.CATEGORY_TITLES.get(f.category, f.category)} · {f.name}",
                values=(f.value[:120],),
            )
        return True

    def _add(self) -> None:
        for p in dialogs.ask_open_many("media", title="Files to clean", parent=self):
            self.load_file(p)

    def _remove(self) -> None:
        for iid in self.tree.selection():
            root = iid if iid in self.files else self.tree.parent(iid)
            if root in self.files:
                self.files.remove(root)
                self.findings.pop(root, None)
                self.tree.delete(root)

    def _clear(self) -> None:
        self.files.clear()
        self.findings.clear()
        self.tree.delete(*self.tree.get_children())

    # ── Clean ─────────────────────────────────────────────────────────
    def _clean(self) -> None:
        if not self.files:
            messagebox.showwarning("Nothing to clean", "Add some files first.", parent=self)
            return
        folder = None
        if self.where_var.get() == "folder":
            folder = self.folder_var.get().strip()
            if not folder:
                messagebox.showwarning(
                    "Folder", "Choose the folder for the cleaned files.", parent=self
                )
                return
            os.makedirs(folder, exist_ok=True)
        opts: dict[str, bool] = dict(
            keep_language=self.keep_lang.get(),
            keep_subtitles=self.keep_subs.get(),
            keep_chapters=self.keep_chapters.get(),
            keep_cover=self.keep_cover.get(),
            deep=self.deep.get(),
        )
        files = list(self.files)
        jobs = _outputs(files, folder)
        removed = {src: len(self.findings.get(src, [])) for src in files}

        def work(_p: object, _c: object, ctx: TaskContext) -> int:
            failed: list[tuple[str, str]] = []
            for i, (src, out) in enumerate(jobs):
                ctx.span(i / len(jobs), (i + 1) / len(jobs))
                ctx.log(f"[{i + 1}/{len(jobs)}] {os.path.basename(src)}")
                reason = _clean_one(ctx, src, out, opts, removed[src])
                if reason:
                    failed.append((src, reason))
            if failed:
                raise RuntimeError(
                    f"{len(failed)} of {len(jobs)} file(s) failed:\n"
                    + "\n".join(f"• {os.path.basename(s)}: {r}" for s, r in failed)
                )
            return 0

        out_label = jobs[0][1] if len(jobs) == 1 else os.path.dirname(jobs[0][1])
        common.queue_job(
            self,
            common.Job(
                name=f"Metadata Cleaner ({len(jobs)} file(s))",
                out=out_label,
                work=work,
                inputs=files,
            ),
            btn=self.btn,
            console=self.console,
        )
