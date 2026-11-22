"""
tabs/cutting/smart_reframe.py  -  Smart Reframe

Turn landscape video into vertical / square (or any aspect) while the crop
window follows the action.

1. Analysis: FFmpeg reduces every frame to a small motion-energy map
   (frame differences at 8 fps); the centre of motion is tracked with a
   dead zone, smoothing and a gentle pull back to the centre, and snaps at
   scene cuts.
2. Render: the crop position is updated on every output frame (sendcmd),
   so the camera glides instead of jumping.

"Fit with blurred background" keeps the whole picture instead.
"""

from __future__ import annotations

import itertools
import os
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import proc as _proc
from core.i18n import t
from core.ops.shared import frame_rate, keep_audio_args
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, scroll_body

RATIOS = {
    "9:16": ("9:16 - Shorts, Reels, TikTok", (9, 16), (1080, 1920)),
    "1:1": ("1:1 - square", (1, 1), (1080, 1080)),
    "4:5": ("4:5 - Instagram portrait", (4, 5), (1080, 1350)),
    "16:9": ("16:9 - widescreen", (16, 9), (1920, 1080)),
    "4:3": ("4:3 - classic", (4, 3), (1440, 1080)),
}
MODES = {
    "follow": "Follow the action (responsive)",
    "smooth": "Smooth drift (calm, centred)",
    "centre": "Fixed centre (no tracking)",
    "fit": "Fit whole picture with blurred background",
}
GRID_W, GRID_H, RATE = 64, 36, 8


@dataclass
class Tuning:
    alpha: float  # EMA weight per analysis sample
    dead_zone: float  # ignore target moves smaller than this (fraction of frame)
    centre_pull: float  # bias of the target towards the centre


TUNING = {"follow": Tuning(0.22, 0.03, 0.05), "smooth": Tuning(0.07, 0.07, 0.25)}


def crop_box(w: int, h: int, rw: int, rh: int) -> tuple:
    """Largest rw:rh window inside w x h (even sizes)."""
    if w / h > rw / rh:
        ch = h
        cw = round(h * rw / rh)
    else:
        cw = w
        ch = round(w * rh / rw)
    return min(cw, w) // 2 * 2, min(ch, h) // 2 * 2


def track(frames, tuning: Tuning, cut_factor: float = 6.0) -> list:
    """Smoothed (cx, cy) in 0..1 for each motion-energy grid (bytes, GRID_W x GRID_H).

    Raw targets (centre of motion, with a dead zone and a pull towards the
    centre) are smoothed forwards and backwards - zero-phase, so the virtual
    camera does not lag behind the action - separately within each scene."""
    targets, cuts, recent = [], [], []
    target = None
    for grid in frames:
        rows = [sum(grid[y * GRID_W : (y + 1) * GRID_W]) for y in range(GRID_H)]
        cols = [sum(grid[x::GRID_W]) for x in range(GRID_W)]
        total = sum(rows)
        mean = sum(recent) / len(recent) if recent else 0
        cuts.append(bool(recent) and total > cut_factor * max(mean, 50))
        recent = ([*recent, total])[-24:]
        if total > GRID_W * GRID_H * 1.5:  # enough motion to trust
            cx = sum(c * (i + 0.5) for i, c in enumerate(cols)) / total / GRID_W
            cy = sum(r * (i + 0.5) for i, r in enumerate(rows)) / total / GRID_H
            raw = [cx + (0.5 - cx) * tuning.centre_pull, cy + (0.5 - cy) * tuning.centre_pull]
            if target is None or cuts[-1]:
                target = raw
            else:
                target = [
                    r if abs(r - tg) > tuning.dead_zone else tg
                    for r, tg in zip(raw, target, strict=False)
                ]
        targets.append(tuple(target) if target is not None else None)
    first = next((tg for tg in targets if tg is not None), (0.5, 0.5))
    filled, last = [], first
    for tg in targets:
        last = tg if tg is not None else last
        filled.append(last)
    out = list(filled)
    starts = [0] + [i for i, c in enumerate(cuts) if c and i > 0] + [len(filled)]
    for a0, b0 in itertools.pairwise(starts):
        seg = filled[a0:b0]
        if not seg:
            continue
        fwd, cur = [], list(seg[0])
        for p in seg:
            cur = [c + (v - c) * tuning.alpha for c, v in zip(cur, p, strict=False)]
            fwd.append(tuple(cur))
        cur = list(fwd[-1])
        for i in range(len(fwd) - 1, -1, -1):
            cur = [c + (v - c) * tuning.alpha for c, v in zip(cur, fwd[i], strict=False)]
            out[a0 + i] = tuple(cur)
    return out


def crop_commands(path, w, h, cw, ch, fps: float, duration: float) -> tuple:
    """sendcmd script placing the crop window on every output frame + start x/y."""

    def at(tm):
        if not path:
            return 0.5, 0.5
        i = tm * RATE
        a = int(min(max(i, 0), len(path) - 1))
        b = min(a + 1, len(path) - 1)
        f = min(max(i - a, 0.0), 1.0)
        return (
            path[a][0] + (path[b][0] - path[a][0]) * f,
            path[a][1] + (path[b][1] - path[a][1]) * f,
        )

    def xy(tm):
        cx, cy = at(tm)
        x = int(min(max(cx * w - cw / 2, 0), w - cw)) // 2 * 2
        y = int(min(max(cy * h - ch / 2, 0), h - ch)) // 2 * 2
        return x, y

    lines, last = [], None
    n = int(duration * fps) + 2
    for k in range(n):
        tm = k / fps
        pos = xy(tm)
        if pos != last:
            lines.append(f"{tm:.4f} crop@rf x {pos[0]}, crop@rf y {pos[1]};")
            last = pos
    return "\n".join(lines) + "\n", xy(0.0)


class SmartReframeTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self,
            t("tab.smart_reframe"),
            "Landscape to vertical or square, with the crop following the action.",
            icon="🎯",
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.info_lbl = tk.Label(body, text="", fg=CLR["fgdim"])
        self.info_lbl.pack(anchor="w", padx=px(24))

        opt = tk.LabelFrame(body, text="  Reframe  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(6))
        self.ratio_var = tk.StringVar(value=RATIOS["9:16"][0])
        self.mode_var = tk.StringVar(value=MODES["follow"])
        r0 = tk.Frame(opt)
        r0.pack(fill="x", pady=px(2))
        tk.Label(r0, text="Shape:", width=12, anchor="w").pack(side="left")
        rb = ttk.Combobox(
            r0,
            textvariable=self.ratio_var,
            values=[v[0] for v in RATIOS.values()],
            state="readonly",
            width=30,
        )
        rb.pack(side="left")
        rb.bind("<<ComboboxSelected>>", lambda _e: self._ratio_changed())
        tk.Label(r0, text="Output size:").pack(side="left", padx=px(12, 2))
        self.ow_var, self.oh_var = tk.StringVar(value="1080"), tk.StringVar(value="1920")
        tk.Entry(r0, textvariable=self.ow_var, width=6).pack(side="left")
        tk.Label(r0, text="×").pack(side="left")
        tk.Entry(r0, textvariable=self.oh_var, width=6).pack(side="left")
        r1 = tk.Frame(opt)
        r1.pack(fill="x", pady=px(2))
        tk.Label(r1, text="Camera:", width=12, anchor="w").pack(side="left")
        ttk.Combobox(
            r1, textvariable=self.mode_var, values=list(MODES.values()), state="readonly", width=40
        ).pack(side="left")
        tk.Label(
            opt,
            text="  ℹ  Tracking analyses the whole clip first (about 10-20× faster than "
            "real time), then renders.",
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w")

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(
            out_lf,
            t("common.output_file"),
            save=True,
            suggest=lambda: self.suggest_output(self.src_row.path, "reframed"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(
            body,
            text="🎯  REFRAME",
            font=(UI_FONT, 12, "bold"),
            bg=CLR["green"],
            fg="white",
            width=22,
            cursor="hand2",
            command=self._render,
        )
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=6)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def _ratio_key(self):
        return next((k for k, v in RATIOS.items() if v[0] == self.ratio_var.get()), "9:16")

    def _ratio_changed(self):
        ow, oh = RATIOS[self._ratio_key()][2]
        self.ow_var.set(str(ow))
        self.oh_var.set(str(oh))

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        if self._info is not None and self._info.has_video:
            w, h = self._info.require_video().display_size
            self.info_lbl.config(text=f"{w}×{h}, {ff.format_time(self._info.duration)}")
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "reframed"))

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        try:
            ow = int(common.number(self.ow_var.get(), "Width", 16, 8192)) // 2 * 2
            oh = int(common.number(self.oh_var.get(), "Height", 16, 8192)) // 2 * 2
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "reframed")
        if not out:
            return
        mode = next((k for k, v in MODES.items() if v == self.mode_var.get()), "follow")
        rw, rh = RATIOS[self._ratio_key()][1]
        w, h = common.even_size(info)
        cw, ch = crop_box(w, h, rw, rh)
        fps_str = frame_rate(info)
        fps = info.require_video().fps or 30.0
        dur = info.duration
        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        audio = keep_audio_args(info, container)
        if mode == "fit":
            chain = (
                f"split[a][b];[a]scale={ow // 8}:{oh // 8}:force_original_aspect_ratio=increase,"
                f"crop={ow // 8}:{oh // 8},gblur=sigma=4,eq=brightness=-0.1,scale={ow}:{oh}[bg];"
                f"[b]scale={ow}:{oh}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
            )
            common.render(
                self,
                src=src,
                info=info,
                out=out,
                chain=chain,
                encoder=venc,
                console=self.console,
                btn=self.btn_render,
                name="Smart Reframe",
            )
            return

        def work(_p, cancelled, ctx):
            path = []
            if mode != "centre" and (cw < w or ch < h):
                ctx.log("Analysing motion…")
                path = self._analyse(src, dur, TUNING.get(mode, TUNING["follow"]), cancelled, ctx)
                moves = max((abs(a[0] - b[0]) for a, b in itertools.pairwise(path)), default=0)
                ctx.log(
                    f"Tracked {len(path)} samples (largest step {moves * 100:.1f}% of the frame)."
                )
            tmp = ctx.tempdir()
            script, (x0, y0) = crop_commands(path, w, h, cw, ch, fps, dur)
            with open(os.path.join(tmp, "crop.cmd"), "w", encoding="ascii") as fh:
                fh.write(script)
            graph = (
                f"[0:v]sendcmd=f=crop.cmd,crop@rf=w={cw}:h={ch}:x={x0}:y={y0},"
                f"scale={ow}:{oh}:flags=lanczos,setsar=1,fps={fps_str}[v]"
            )
            vmap = "[v]"
            if venc.filter_suffix:
                graph += f";[v]{venc.filter_suffix}[venc]"
                vmap = "[venc]"
            cmd = [
                ff.ffmpeg_path(),
                "-y",
                *venc.pre_input,
                "-i",
                src,
                "-filter_complex",
                graph,
                "-map",
                vmap,
                *audio,
                *venc.args,
                ctx.output(),
            ]
            ctx.span(0.35 if path else 0.0, 1.0).run(cmd, duration=dur, cwd=tmp)
            return 0

        self.log(self.console, f"Reframing {w}×{h} → {cw}×{ch} window → {ow}×{oh}")
        common.queue_job(
            self,
            name="Smart Reframe",
            out=out,
            work=work,
            btn=self.btn_render,
            console=self.console,
            duration=dur,
            inputs=[src],
        )

    @staticmethod
    def _analyse(src, duration, tuning, cancelled, ctx) -> list:
        cmd = [
            ff.ffmpeg_path(),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            src,
            "-an",
            "-sn",
            "-vf",
            f"fps={RATE},scale={GRID_W * 2}:{GRID_H * 2}:flags=area,format=gray,"
            f"tblend=all_mode=difference,scale={GRID_W}:{GRID_H}:flags=area",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ]
        proc = _proc.spawn(cmd, stdout=_proc.PIPE, stderr=_proc.DEVNULL)
        out = proc.stdout
        assert out is not None  # started with stdout=PIPE
        size = GRID_W * GRID_H
        frames, buf = [], b""
        expected = max(1, int(duration * RATE))
        try:
            while True:
                if cancelled():
                    ff.kill_tree(proc)
                    raise ff.CancelledError("cancelled")
                chunk = out.read(size * 16)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= size:
                    frames.append(buf[:size])
                    buf = buf[size:]
                ctx.progress(0.35 * min(1.0, len(frames) / expected))
        finally:
            proc.wait()
        # first sample has no predecessor in tblend: repeat it so samples line up with time
        path = track(frames, tuning)
        return path[:1] + path if path else path
