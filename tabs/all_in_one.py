"""
tabs/all_in_one.py - the All-in-One Builder: line up any tools, in any order,
as many times as wanted, and render the lot in one go.

"+ Add a step" lists every tool that works on one video (tabs/builder/catalog.py)
and the builder's own "Quick adjustments".  Adding one opens that tool, whole,
in a window over the builder (tabs/builder/step_mode.py): set it up as usual,
then "Save and return".  A step's ⚙ opens it again; ↑ ↓ move it, ⧉ copies it
and ✕ removes it.  A step after the first works on the video as the steps
before it leave it - its window shows exactly that.

Rendering joins the steps into as few FFmpeg runs as it can (core/ops/chain.py)
and says how many above the Render button; files passed between runs are
lossless, so the picture is compressed once.  A step list can be saved as a
recipe and used again, on this video or another.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import threading
import tkinter as tk
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from tkinter import messagebox
from typing import Any

from core import encoders, paths, tasks
from core import ffmpeg as ff
from core.i18n import t
from core.ops import chain, chain_run
from core.scaling import px
from tabs.base_tab import CLR, UI_FONT, BaseTab, add_tooltip
from tabs.builder import catalog
from tabs.builder.step_mode import Captured, StepHost
from tabs.kit import dialogs
from tabs.widgets import FileRow, output_section, scroll_body

logger = logging.getLogger("crossfader.builder")

RECIPE_VERSION = 1
PREVIEW_ENCODE = chain.Encode(tuple(chain.PREVIEW))
#: Filters too ordinary to describe a step by.
_PLAIN = frozenset({"null", "anull", "setsar", "format", "setpts", "asetpts", "split", "asplit"})


@dataclass(eq=False)
class Step:
    """One step of the list."""

    kind: str
    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    host: StepHost | None = None
    cmds: list[list[str]] | None = None  # the tool's commands, as a template
    run: Any = None  # or its Python job
    out_ext: str = ".mp4"
    in_path: str = ""  # the file it was set up on
    out_path: str = ""  # where its Python job writes
    state: dict[str, Any] = field(default_factory=dict[str, Any])
    sig: str = ""  # the steps before it when it was set up
    check: bool = False  # an earlier step changed since
    stale: bool = False  # set up on another video (or loaded): capture again before rendering
    problem: str = ""

    @property
    def ready(self) -> bool:
        return (self.cmds is not None or self.run is not None) and not self.stale


class AllInOneTab(BaseTab):
    accepted_kinds = ("video",)

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.steps: list[Step] = []
        self.workdir = paths.temp_dir("builder_")
        self._previews: dict[str, str] = {}  # signature of the steps before -> preview video
        self._info: ff.MediaInfo | None = None
        self._busy = False  # a step window is open, or a preview is being made
        self._build_ui()
        self._refresh()

    # ── Building ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        self.make_header(self, t("tab.all_in_one"), t("builder.subtitle"), icon="🎛")
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), kind="video", on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        box = tk.LabelFrame(body, text=f"  {t('builder.steps')}  ", padx=px(12), pady=px(8))
        box.pack(fill="x", padx=px(20), pady=px(6))
        self.rows = tk.Frame(box)
        self.rows.pack(fill="x")
        self.empty = tk.Label(
            box, text=t("builder.empty"), fg=CLR["fgdim"], justify="left", anchor="w"
        )
        controls = tk.Frame(box)
        controls.pack(fill="x", pady=px(8, 0))
        self.add_btn = tk.Menubutton(
            controls, text="＋ " + t("builder.add_step"), font=(UI_FONT, 10, "bold"), bg=CLR["accent"],
            fg="white", activebackground=CLR["accent"], activeforeground="white", relief="flat",
            padx=px(12), pady=px(4), cursor="hand2",
        )  # fmt: skip
        self.add_btn.pack(side="left")
        self.add_btn.configure(menu=self._add_menu(self.add_btn))
        for text, cmd in (
            (t("builder.open_recipe"), self._open_recipe),
            (t("builder.save_recipe"), self._save_recipe),
        ):
            tk.Button(controls, text=text, relief="flat", cursor="hand2", command=cmd).pack(
                side="right", padx=px(4, 0)
            )
        self.plan_lbl = tk.Label(body, text="", anchor="w", justify="left", font=(UI_FONT, 9))
        self.plan_lbl.pack(fill="x", padx=px(22), pady=px(2))
        self.plan_lbl.bind(
            "<Configure>", lambda e: self.plan_lbl.config(wraplength=max(200, e.width - 10))
        )
        self.encoder, self.out_row = output_section(
            body, t("common.output_file"), ("h264", "hevc", "av1", "vp9", "prores"),
            ext=self._out_ext, suggest=self._suggest,
        )  # fmt: skip
        self.format_note = tk.Label(
            self.out_row.master, text="", fg=CLR["fgdim"], anchor="w", justify="left"
        )
        self.btn_render = self.make_render_btn(body, t("builder.render"), self._render, width=22)
        self.btn_render.pack(pady=px(8))
        show = tk.Frame(body)
        show.pack(fill="x", padx=px(20))
        self.cmd_btn = tk.Button(
            show, text=t("builder.show_commands"), relief="flat", command=self._toggle_commands
        )
        self.cmd_btn.pack(side="left")
        self.cmd_box = tk.Frame(body)  # shown on request
        self.cmd_text = self.make_console_block(self.cmd_box, height=8, padx=0)
        self.cmd_text.config(wrap="word")
        self.console = self.make_console_block(body, height=5)

    def _add_menu(self, parent: tk.Misc) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(
            label=f"{catalog.icon(catalog.QUICK)}  {catalog.name(catalog.QUICK)}",
            command=partial(self._add, catalog.QUICK),
        )
        menu.add_separator()
        for heading, kinds in catalog.menu():
            sub = tk.Menu(menu, tearoff=False)
            for kind in kinds:
                sub.add_command(
                    label=f"{catalog.icon(kind)}  {catalog.name(kind)}",
                    command=partial(self._add, kind),
                )
            menu.add_cascade(label=heading, menu=sub)
        return menu

    # ── The source ────────────────────────────────────────────────────
    def load_file(self, path: str) -> bool:
        if dialogs.kind_of(path) != "video":
            return False
        self.src_row.set(path)
        self._source_changed(path)
        return True

    @property
    def src(self) -> str:
        return self.src_row.path

    def _source_changed(self, path: str) -> None:
        self._info = ff.try_probe(path) if path else None
        if self.steps:
            for s in self.steps:
                s.stale = True
                if catalog.one_video(s.kind):
                    s.problem = t("builder.set_up_again")
            self.log(self.console, t("builder.new_video"))
        self.out_row.set(self._suggest())
        self._refresh()

    def _suggest(self) -> str:
        return self.suggest_output(self.src, "edited", self._out_ext()) if self.src else ""

    def _out_ext(self) -> str:
        last = self.steps[-1] if self.steps else None
        if last is not None and self._decides(last):
            return last.out_ext
        return (
            ".mov"
            if self.encoder.codec == "prores"
            else (".webm" if self.encoder.codec == "vp9" else ".mp4")
        )

    @staticmethod
    def _decides(step: Step) -> bool:
        return catalog.own_format(step.kind) or step.out_ext.lower() not in chain.VIDEO_EXTS

    # ── Signatures: what a step's input is made of ────────────────────
    def _sig(self, index: int) -> str:
        h = hashlib.sha1(self.src.encode())
        for s in self.steps[:index]:
            h.update(
                json.dumps(s.cmds).encode()
                if s.cmds is not None
                else f"{s.kind}:{s.uid}:{s.state}".encode()
            )
        return h.hexdigest()[:12]

    def _flag_later(self) -> None:
        """Steps whose input changed since they were set up: positions and times
        may need a look (and a Python job must be captured again)."""
        for i, s in enumerate(self.steps):
            if s.sig and s.sig != self._sig(i):
                if catalog.placed(s.kind):
                    s.check = True
                if s.run is not None:
                    s.stale = True

    # ── Adding and editing ────────────────────────────────────────────
    def _add(self, kind: str) -> None:
        if not self._can_edit():
            return
        step = Step(kind)
        self._open_step(step, len(self.steps), new=True)

    def _can_edit(self) -> bool:
        if self._busy:
            return False
        if not self.src or not os.path.isfile(self.src):
            messagebox.showinfo(t("tab.all_in_one"), t("builder.choose_video_first"), parent=self)
            return False
        return True

    def _settings(self, step: Step) -> None:
        if self._can_edit():
            self._open_step(step, self.steps.index(step), new=False)

    def _open_step(self, step: Step, index: int, *, new: bool) -> None:
        """Show *step*'s tool, on the video as the steps before it leave it."""
        self._busy = True

        def show(path: str) -> None:
            host = step.host
            if host is None:
                host = step.host = StepHost(self, step.kind, self.workdir, step.uid)
            if host.input != path:
                host.load(
                    path,
                    lambda: (step.state and host.restore(step.state), self._show(step, index, new)),
                )
            else:
                self._show(step, index, new)

        self._input_for(index, step, show, lambda: setattr(self, "_busy", False))

    def _show(self, step: Step, index: int, new: bool) -> None:
        host = step.host
        assert host is not None
        title = t("builder.step_title", n=index + 1, tool=catalog.name(step.kind))
        banner = t("builder.banner_first") if index == 0 else t("builder.banner_later", n=index)
        host.show(
            title,
            f"{catalog.icon(step.kind)}  {title}\n{banner}",
            on_save=partial(self._save_step, step, index, new),
            on_close=partial(self._close_step, step, new),
        )

    def _save_step(self, step: Step, index: int, new: bool) -> None:
        host = step.host
        assert host is not None
        host.busy(True)

        def captured(found: Captured | None, said: str) -> None:
            host.busy(False)
            if found is None:
                if not said:
                    messagebox.showwarning(
                        t("tab.all_in_one"), t("builder.nothing_to_do"), parent=host.win
                    )
                return
            self._take(step, index, found)
            host.hide()
            if new:
                self.steps.insert(index, step)
            self._busy = False
            self._flag_later()
            self._refresh()

        host.capture(captured)

    def _take(self, step: Step, index: int, found: Captured) -> None:
        host = step.host
        assert host is not None
        step.in_path = host.input
        step.out_path = found.output
        step.out_ext = os.path.splitext(found.output)[1].lower() or ".mp4"
        if found.cmds is not None:
            step.cmds, step.run = chain.templated(found.cmds, host.input, found.output), None
        else:
            step.cmds, step.run = None, found.run
        step.state = host.snapshot()
        step.sig = self._sig(index)
        step.check = step.stale = False
        step.problem = ""

    def _close_step(self, step: Step, new: bool) -> None:
        host = step.host
        if host is not None:
            if new:
                host.destroy()
                step.host = None
            else:
                host.hide()
        self._busy = False

    # ── The video a step works on ─────────────────────────────────────
    def _input_for(
        self, index: int, step: Step, then: Callable[[str], object], failed: Callable[[], object]
    ) -> None:
        """The file step *index* reads: the video itself for the first step, else
        a preview made of the steps before it (kept, and linked as the step's own
        input so its Python job can be pointed at the real thing later)."""
        if index == 0:
            then(self.src)
            return
        sig = self._sig(index)
        before = self.steps[:index]
        ext = before[-1].out_ext if self._decides(before[-1]) else ".mkv"
        mine = os.path.join(self.workdir, f"{step.uid}-in{ext}")

        def link(preview: str) -> None:
            with contextlib.suppress(OSError):
                os.remove(mine)
            try:
                os.link(preview, mine)
            except OSError:
                shutil.copyfile(preview, mine)
            then(mine)

        have = self._previews.get(sig)
        if have and os.path.exists(have):
            link(have)
            return
        out = os.path.join(self.workdir, f"preview-{sig}{ext}")
        self._make_preview(
            before, out, index, lambda: (self._previews.__setitem__(sig, out), link(out)), failed
        )

    def _make_preview(
        self,
        before: list[Step],
        out: str,
        index: int,
        done: Callable[[], object],
        failed: Callable[[], object],
    ) -> None:
        stale = [i for i, s in enumerate(before) if not s.ready]
        if stale:
            self._recapture(
                stale, lambda: self._make_preview(before, out, index, done, failed), failed
            )
            return
        win = _Progress(self, t("builder.preparing", n=index + 1))
        src, has_audio = self.src, bool(self._info and self._info.has_audio)
        duration = self._info.duration if self._info else None

        def plan(alone: set[int]) -> list[chain.Pass]:
            return self._plan(before, src, out, PREVIEW_ENCODE, has_audio, alone)

        def work() -> int:
            ctx = chain_run.LocalContext(win.fraction, win.cancelled)
            try:
                return chain_run.execute(
                    plan, ctx, src=src, final=PREVIEW_ENCODE, duration=duration
                )
            finally:
                ctx.cleanup()

        def finished(rc: int) -> None:
            win.close()
            if rc == 0 and os.path.exists(out):
                done()
            else:
                failed()

        def broke(exc: BaseException) -> None:
            win.close()
            if not isinstance(exc, ff.CancelledError):
                messagebox.showerror(
                    t("tab.all_in_one"),
                    t("builder.preview_failed", error=str(exc)[-600:]),
                    parent=self,
                )
            failed()

        tasks.submit(work, on_done=finished, on_error=broke, name="builder preview")

    def _plan(
        self,
        steps: list[Step],
        src: str,
        out: str,
        encode: chain.Encode,
        has_audio: bool,
        alone: set[int],
    ) -> list[chain.Pass]:
        plan_steps = [
            chain.Step(
                catalog.name(s.kind),
                s.cmds,
                run=s.run,
                out_ext=s.out_ext,
                in_path=s.in_path,
                out_path=s.out_path,
                alone=i in alone,
                own_format=catalog.own_format(s.kind),
            )
            for i, s in enumerate(steps)
        ]
        alpha = bool(
            self._info
            and self._info.video
            and "a" in (self._info.video.pix_fmt or "").replace("yuv", "")
        )
        return chain.plan(
            plan_steps,
            src,
            out,
            final=encode,
            has_audio=has_audio,
            workdir=self.workdir,
            alpha=alpha,
        )

    # ── Capturing again (new video, recipes, moved Python steps) ──────
    def _recapture(
        self, indices: list[int], done: Callable[[], object], failed: Callable[[], object]
    ) -> None:
        """Set steps *indices* up again, quietly, from their saved settings, in order."""
        if not indices:
            done()
            return
        i, rest = indices[0], indices[1:]
        step = self.steps[i]
        if step.problem:
            messagebox.showwarning(
                t("tab.all_in_one"), t("builder.step_problem", n=i + 1, tool=catalog.name(step.kind), problem=step.problem),
                parent=self,
            )  # fmt: skip
            failed()
            return
        self.log(self.console, t("builder.updating", n=i + 1, tool=catalog.name(step.kind)))

        def captured(found: Captured | None, said: str) -> None:
            if found is None:
                step.problem = said or t("builder.nothing_to_do")
                self._refresh()
                self._recapture([i], done, failed)  # says what went wrong
                return
            self._take(step, i, found)
            self._refresh()
            self._recapture(rest, done, failed)

        def loaded() -> None:
            host = step.host
            assert host is not None
            if step.state:
                host.restore(step.state)
            host.capture(captured, quiet=True)

        def got(path: str) -> None:
            if step.host is None:
                step.host = StepHost(self, step.kind, self.workdir, step.uid)
            step.host.load(path, loaded)

        self._input_for(i, step, got, failed)

    # ── The list ──────────────────────────────────────────────────────
    def _move(self, step: Step, by: int) -> None:
        i = self.steps.index(step)
        j = i + by
        if self._busy or not 0 <= j < len(self.steps):
            return
        self.steps[i], self.steps[j] = self.steps[j], self.steps[i]
        self._flag_later()
        self._refresh()

    def _copy(self, step: Step) -> None:
        if self._busy:
            return
        i = self.steps.index(step)
        twin = Step(step.kind, cmds=[list(c) for c in step.cmds] if step.cmds else None, out_ext=step.out_ext,
                    state=json.loads(json.dumps(step.state)), stale=step.run is not None or step.stale)  # fmt: skip
        self.steps.insert(i + 1, twin)
        self._flag_later()
        if catalog.placed(step.kind):
            twin.check = True
        self._refresh()

    def _remove(self, step: Step) -> None:
        if self._busy:
            return
        if step.host is not None:
            step.host.destroy()
        self.steps.remove(step)
        self._flag_later()
        self._refresh()

    def _summary(self, step: Step) -> str:
        if step.problem:
            return step.problem
        if step.stale:
            return t("builder.will_update")
        if step.run is not None:
            return t("builder.own_job")
        shown = [n for n in chain.filter_names(step.cmds or []) if n not in _PLAIN]
        return (
            ", ".join(shown[:6]) + (" …" if len(shown) > 6 else "")
            if shown
            else t("builder.no_filters")
        )

    def _refresh(self) -> None:
        for child in self.rows.winfo_children():
            child.destroy()
        if self.steps:
            self.empty.pack_forget()
        else:
            self.empty.pack(fill="x", before=self.rows.master.winfo_children()[-1])
        for i, step in enumerate(self.steps):
            self._step_row(i, step)
        self._update_output()
        self._update_plan()

    def _step_row(self, i: int, step: Step) -> None:
        row = tk.Frame(self.rows, pady=px(2))
        row.pack(fill="x")
        tk.Label(row, text=f"{i + 1}", width=3, font=(UI_FONT, 11, "bold"), fg=CLR["accent"]).pack(
            side="left"
        )
        tk.Label(row, text=f"{catalog.icon(step.kind)}  {catalog.name(step.kind)}", font=(UI_FONT, 10, "bold"),
                 anchor="w").pack(side="left")  # fmt: skip
        colour = CLR["red"] if step.problem else CLR["fgdim"]
        tk.Label(row, text=self._summary(step), fg=colour, font=(UI_FONT, 9), anchor="w").pack(
            side="left", padx=px(10)
        )
        buttons = (
            ("✕", partial(self._remove, step), t("builder.tip_remove")),
            ("⧉", partial(self._copy, step), t("builder.tip_copy")),
            ("↓", partial(self._move, step, 1), t("builder.tip_down")),
            ("↑", partial(self._move, step, -1), t("builder.tip_up")),
        )
        for text, cmd, tip in buttons:
            b = tk.Button(row, text=text, width=2, relief="flat", cursor="hand2", command=cmd)
            b.pack(side="right", padx=px(1))
            add_tooltip(b, tip)
        tk.Button(row, text="⚙ " + t("builder.settings"), relief="flat", cursor="hand2",
                  command=partial(self._settings, step)).pack(side="right", padx=px(6))  # fmt: skip
        if step.check and not step.problem:
            badge = tk.Label(
                row, text="⚠ " + t("builder.check"), fg=CLR["orange"], font=(UI_FONT, 9, "bold")
            )
            badge.pack(side="right", padx=px(6))
            add_tooltip(badge, t("builder.check_tip"))

    def _update_output(self) -> None:
        last = self.steps[-1] if self.steps else None
        if last is not None and self._decides(last):
            self.encoder.pack_forget()
            self.format_note.config(
                text=t("builder.format_by_last", tool=catalog.name(last.kind), ext=last.out_ext)
            )
            self.format_note.pack(anchor="w", before=self.out_row)
        else:
            self.format_note.pack_forget()
            if not self.encoder.winfo_manager():
                self.encoder.pack(anchor="w", before=self.out_row)
        path = self.out_row.path
        if (
            path
            and os.path.splitext(path)[1].lower() != self._out_ext()
            and path.startswith(os.path.splitext(self._suggest())[0])
        ):
            self.out_row.set(self._suggest())

    def _final_encode(self, out: str) -> chain.Encode:
        container = os.path.splitext(out)[1].lower().lstrip(".") or "mp4"
        venc = self.encoder.video_encode(container)
        has_audio = bool(self._info and self._info.has_audio)
        audio = encoders.default_audio(container) if has_audio else []
        return chain.Encode((*venc.args, *audio), tuple(venc.pre_input), venc.filter_suffix)

    def _passes(self, out: str) -> list[chain.Pass] | None:
        if (
            not self.steps
            or not self.src
            or any(s.cmds is None and s.run is None for s in self.steps)
        ):
            return None
        has_audio = bool(self._info and self._info.has_audio)
        try:
            return self._plan(self.steps, self.src, out, self._final_encode(out), has_audio, set())
        except Exception:
            logger.exception("planning failed")
            return None

    def _update_plan(self) -> None:
        n = len(self.steps)
        if not n:
            self.plan_lbl.config(text="", fg=CLR["fgdim"])
            self._show_commands([])
            return
        out = (
            self.out_row.path
            or self._suggest()
            or os.path.join(self.workdir, "out" + self._out_ext())
        )
        passes = self._passes(out)
        if passes is None:
            self.plan_lbl.config(text=t("builder.plan_pending"), fg=CLR["fgdim"])
            self._show_commands([])
            return
        if len(passes) == 1:
            text = t("builder.plan_one", n=n) if n > 1 else t("builder.plan_single")
        else:
            alone = [catalog.name(self.steps[p.steps[0]].kind) for p in passes if p.note]
            text = t(
                "builder.plan_more", runs=len(passes), steps=", ".join(dict.fromkeys(alone)) or "-"
            )
        self.plan_lbl.config(text="✔ " + text, fg=CLR["green"])
        self._show_commands(passes)

    # ── Commands ──────────────────────────────────────────────────────
    def _toggle_commands(self) -> None:
        if self.cmd_box.winfo_manager():
            self.cmd_box.pack_forget()
            self.cmd_btn.config(text=t("builder.show_commands"))
        else:
            self.cmd_box.pack(fill="x", padx=px(20), pady=px(4), after=self.cmd_btn.master)
            self.cmd_btn.config(text=t("builder.hide_commands"))
        self._update_plan()

    def _show_commands(self, passes: list[chain.Pass]) -> None:
        if not self.cmd_box.winfo_manager():
            return
        lines: list[str] = []
        for p in passes:
            names = ", ".join(f"{k + 1}. {catalog.name(self.steps[k].kind)}" for k in p.steps)
            lines.append(f"# {names}")
            if p.run is not None:
                lines.append("#   " + t("builder.own_job"))
            lines += [ff.format_cmd(c) for c in p.cmds]
            lines.append("")
        self.cmd_text.config(state="normal")
        self.cmd_text.delete("1.0", "end")
        self.cmd_text.insert("1.0", "\n".join(lines))
        self.cmd_text.config(state="disabled")

    # ── Rendering ─────────────────────────────────────────────────────
    def render(self) -> None:
        if not self._busy:
            super().render()

    def _render(self) -> None:
        if self._busy:
            return
        if not self.src or self._info is None:
            messagebox.showwarning(
                t("common.warning"), t("builder.choose_video_first"), parent=self
            )
            return
        if not self.steps:
            messagebox.showwarning(t("common.warning"), t("builder.no_steps"), parent=self)
            return
        bad = next((i for i, s in enumerate(self.steps) if s.problem), None)
        if bad is not None:
            s = self.steps[bad]
            messagebox.showwarning(
                t("common.warning"), t("builder.step_problem", n=bad + 1, tool=catalog.name(s.kind), problem=s.problem),
                parent=self,
            )  # fmt: skip
            return
        if any(s.check for s in self.steps) and not messagebox.askyesno(
            t("tab.all_in_one"), t("builder.render_with_checks"), parent=self
        ):
            return
        stale = [i for i, s in enumerate(self.steps) if not s.ready or self._moved_python(i, s)]
        self._busy = True
        self._recapture(stale, self._queue, lambda: setattr(self, "_busy", False))

    def _moved_python(self, i: int, s: Step) -> bool:
        """A Python job reads the file it was set up on: the video for a first
        step, its own linked input for the others - moved, it must be set up again."""
        if s.run is None:
            return False
        first = os.path.abspath(s.in_path) == os.path.abspath(self.src)
        return first != (i == 0)

    def _queue(self) -> None:
        self._busy = False
        out = self.out_row.path or self._suggest()
        out = self.confirm_output(out, [self.src], default_ext=self._out_ext())
        if not out:
            return
        self.out_row.set(out)
        steps, src = list(self.steps), self.src
        has_audio = bool(self._info and self._info.has_audio)
        final = self._final_encode(out)
        duration = self._info.duration if self._info else None

        def plan(alone: set[int]) -> list[chain.Pass]:
            return self._plan(steps, src, out, final, has_audio, alone)

        def work(_progress: object, _cancelled: object, ctx: Any) -> int:
            return chain_run.execute(plan, ctx, src=src, final=final, duration=duration)

        from tabs import common

        self.log(self.console, t("builder.rendering", n=len(steps)))
        common.queue_job(
            self,
            common.Job("All-in-One Builder", out, work, duration, (src,)),
            btn=self.btn_render,
            console=self.console,
        )

    # ── Recipes ───────────────────────────────────────────────────────
    def _save_recipe(self) -> None:
        if not self.steps:
            messagebox.showinfo(t("tab.all_in_one"), t("builder.no_steps"), parent=self)
            return
        base = os.path.splitext(os.path.basename(self.src))[0] if self.src else "recipe"
        path = dialogs.ask_save(
            f"{base} recipe.json", kind="recipe", defaultextension=".json", parent=self
        )
        if not path:
            return
        data = recipe_data(self.steps, self.src, self.encoder.codec)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1, ensure_ascii=False)
        except OSError as exc:
            messagebox.showerror(t("common.error"), str(exc), parent=self)
            return
        self.log(self.console, t("builder.recipe_saved", path=path))

    def _open_recipe(self) -> None:
        if self._busy:
            return
        path = dialogs.ask_open("recipe", parent=self)
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                steps = steps_from_recipe(json.load(fh), self.src)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            messagebox.showerror(
                t("common.error"), t("builder.recipe_bad", error=str(exc)), parent=self
            )
            return
        if self.steps and not messagebox.askyesno(
            t("tab.all_in_one"), t("builder.replace_steps"), parent=self
        ):
            return
        for s in self.steps:
            if s.host is not None:
                s.host.destroy()
        self.steps = steps
        self._refresh()
        self.log(self.console, t("builder.recipe_opened", n=len(steps)))

    def on_app_closing(self) -> None:
        for s in self.steps:
            if s.host is not None:
                s.host.destroy()
        shutil.rmtree(self.workdir, ignore_errors=True)


def recipe_data(steps: list[Step], src: str, codec: str) -> dict[str, Any]:
    """A step list as a recipe file's contents."""
    return {
        "recipe": "ffmpeg-workbench all-in-one",
        "version": RECIPE_VERSION,
        "video": os.path.basename(src),
        "video_id": _video_id(src),
        "codec": codec,
        "steps": [
            {
                "kind": s.kind,
                "state": s.state,
                "cmds": s.cmds,
                "out_ext": s.out_ext,
                "python": s.run is not None,
            }
            for s in steps
        ],
    }


def steps_from_recipe(data: dict[str, Any], src: str) -> list[Step]:
    """The steps of a recipe; set up again from their settings before rendering
    unless the recipe was made on this very video (and they are FFmpeg commands)."""
    if data.get("version") != RECIPE_VERSION or not isinstance(data.get("steps"), list):
        raise ValueError("not a recipe")
    same = bool(src) and data.get("video_id") == _video_id(src)
    out: list[Step] = []
    for item in data["steps"]:
        kind = str(item["kind"])
        if not catalog.known(kind):
            raise ValueError(f"unknown step {kind!r}")
        cmds = item.get("cmds")
        step = Step(kind, cmds=[[str(a) for a in c] for c in cmds] if cmds else None,
                    out_ext=str(item.get("out_ext") or ".mp4"), state=dict(item.get("state") or {}))  # fmt: skip
        step.stale = not same or bool(item.get("python")) or step.cmds is None
        if not same and catalog.one_video(kind):
            step.problem = t("builder.set_up_again")
        out.append(step)
    return out


def _video_id(path: str) -> str:
    """The same video, wherever it is: its name, size and length."""
    if not path or not os.path.isfile(path):
        return ""
    info = ff.try_probe(path)
    return f"{os.path.basename(path)}|{os.path.getsize(path)}|{info.duration if info else 0:.2f}"


class _Progress:
    """A small window while a step's video is made: a bar, and Cancel."""

    def __init__(self, owner: tk.Misc, text: str) -> None:
        self._stop = threading.Event()
        self.win = tk.Toplevel(owner)
        self.win.title(t("tab.all_in_one"))
        self.win.transient(owner.winfo_toplevel())  # pyright: ignore[reportArgumentType]
        self.win.resizable(False, False)
        tk.Label(self.win, text=text, padx=px(16), pady=px(10), font=(UI_FONT, 10)).pack()
        self.bar = tk.Canvas(
            self.win, width=px(320), height=px(12), highlightthickness=0, bg=CLR["panel"]
        )
        self.bar.pack(padx=px(16))
        tk.Button(self.win, text=t("btn.cancel"), command=self._stop.set, relief="flat").pack(
            pady=px(10)
        )
        self.win.protocol("WM_DELETE_WINDOW", self._stop.set)
        self.win.after(30, self._grab)

    def _grab(self) -> None:
        with contextlib.suppress(tk.TclError):
            self.win.grab_set()

    def fraction(self, f: float) -> None:
        from core import ui

        ui.call_soon(self._draw, f)

    def _draw(self, f: float) -> None:
        with contextlib.suppress(tk.TclError):
            self.bar.delete("all")
            self.bar.create_rectangle(
                0, 0, px(320) * max(0.0, min(f, 1.0)), px(12), fill=CLR["accent"], outline=""
            )

    def cancelled(self) -> bool:
        return self._stop.is_set()

    def close(self) -> None:
        with contextlib.suppress(tk.TclError):
            self.win.grab_release()
            self.win.destroy()
