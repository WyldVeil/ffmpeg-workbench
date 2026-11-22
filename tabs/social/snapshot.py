"""
tabs/social/snapshot.py  -  Snapshot: save any frame of a video as a picture.

Step to the exact frame (slider, ◀ ▶ buttons, or , and . on the keyboard,
Shift for a second), see on the right exactly how the picture will look, and
save it as a PNG at the video's own resolution.

The defaults are the ones that make a still look its best, and each can be
switched off or tuned:

* colours converted with the video's own colour matrix and full-range RGB, with
  full-quality chroma (FFmpeg's plain conversion assumes the SD matrix, which
  shifts HD colours slightly, and makes blocky colour edges);
* the pixel shape corrected for anamorphic video;
* HDR tone-mapped to normal (SDR) colours, as the HDR to SDR tool does;
* interlacing combs removed when the video is interlaced;
* optional: black bars cropped, a little sharpening or noise reduction,
  brightness / contrast / saturation, half or double size, 16-bit PNG.
"""
from __future__ import annotations

import os
import subprocess
import tkinter as tk
from tkinter import ttk

from core import ffmpeg as ff
from core.hardware import CREATE_NO_WINDOW
from core.procenv import child_env
from core import filters as fl
from core import hdr
from core.i18n import t
from core.scaling import px
from tabs import framestep
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab, add_tooltip, reveal_file
from tabs.widgets import FileRow, FramePreview, scroll_body

SIZES = {"own": "The video's own size", "half": "Half size", "double": "Double size (upscaled)"}
SHARPEN = {0.0: "Off", 0.3: "Subtle", 0.6: "Medium", 0.9: "Strong"}
DENOISE = {0: "Off", 2: "Light", 4: "Medium", 7: "Strong"}
LOOK = {"sharpen": 0.0, "denoise": 0, "brightness": 0.0, "contrast": 1.0, "saturation": 1.0}


def colour_matrix(info: ff.MediaInfo) -> str:
    """The matrix to turn this video's YUV into RGB (FFmpeg assumes BT.601 when
    the file doesn't say, which is wrong for HD)."""
    v = info.video
    cs = (v.color_space or "").lower() if v else ""
    if cs in ("bt709",):
        return "bt709"
    if cs in ("bt470bg", "smpte170m", "bt601"):
        return "bt601"
    if cs.startswith("bt2020"):
        return "bt2020"
    if cs == "smpte240m":
        return "smpte240m"
    return "bt709" if (v and v.height >= 720) else "bt601"


def _sar(v) -> float:
    try:
        num, den = (int(x) for x in (v.sar or "1:1").replace("/", ":").split(":"))
        return num / den if num > 0 and den > 0 else 1.0
    except ValueError:
        return 1.0


def picture_size(v, square_pixels: bool = True, crop=None) -> tuple:
    """Width, height of the frame as stored (or of *crop*), with the pixel shape
    corrected when *square_pixels*, and turned like the video (FFmpeg turns
    rotated phone video the right way up when decoding)."""
    w, h = (crop[2], crop[3]) if crop else (v.width, v.height)
    if square_pixels:
        sar = _sar(v)
        if sar > 1:
            w = w * sar
        elif sar < 1:
            h = h / sar
    if v.rotation in (90, 270):
        w, h = h, w
    return w, h


def out_size(info: ff.MediaInfo, size: str = "own", square_pixels: bool = True, crop=None) -> tuple:
    """Width, height of the picture (even numbers)."""
    v = info.video
    if v is None:
        return 2, 2
    w, h = picture_size(v, square_pixels, crop)
    k = {"half": 0.5, "double": 2.0}.get(size, 1.0)
    return max(2, int(round(w * k / 2)) * 2), max(2, int(round(h * k / 2)) * 2)


def build_chain(info: ff.MediaInfo, *, accurate: bool = True, square_pixels: bool = True,
                tonemap: bool = True, deinterlace: bool = True, crop=None, size: str = "own",
                sharpen: float = 0.0, denoise: int = 0, brightness: float = 0.0, contrast: float = 1.0,
                saturation: float = 1.0, deep: bool = False, gpu: bool = False) -> str:
    """The FFmpeg filter chain that turns the frame into the picture."""
    v = info.video
    steps = []
    if deinterlace and v and v.field_order not in ("", "progressive", "unknown"):
        steps.append("bwdif=mode=send_frame:parity=auto:deint=all")
    hdr_done = False
    if tonemap and info.is_hdr:
        steps.append(hdr.sdr_chain(gpu=gpu, out_fmt="yuv444p16le" if deep else "yuv444p"))
        hdr_done = True
    if crop:
        x, y, w, h = crop
        steps.append(f"crop={w}:{h}:{x}:{y}")
    if denoise:
        steps.append(f"hqdn3d=luma_spatial={denoise}:chroma_spatial={denoise * 0.75:g}:luma_tmp=0:chroma_tmp=0")
    if (brightness, contrast, saturation) != (0.0, 1.0, 1.0):
        steps.append(f"eq=brightness={brightness:g}:contrast={contrast:g}:saturation={saturation:g}")
    w, h = out_size(info, size, square_pixels, crop)
    rgb = "gbrp16le" if deep else "gbrp"
    if accurate:
        matrix = "bt709" if hdr_done else colour_matrix(info)
        rng = "pc" if (v and (v.color_range or "").lower() in ("pc", "jpeg")) else "tv"
        steps.append(f"scale={w}:{h}:flags=lanczos+accurate_rnd+full_chroma_int+full_chroma_inp:"
                     f"in_color_matrix={matrix}:in_range={rng}:out_range=pc,format={rgb}")
    else:
        steps.append(f"scale={w}:{h},format={rgb}")
    if sharpen:
        steps.append(f"cas=strength={sharpen:g}")
    steps.append("format=rgb48be" if deep else "format=rgb24")
    return ",".join(steps)


def frame_name(path: str, at: float) -> str:
    """``meadow 00-01-02.345.png`` - the video's name and the frame's time."""
    ms = int(round(max(0.0, at) * 1000))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    stem = os.path.splitext(os.path.basename(path))[0]
    return f"{stem} {h:02d}-{m:02d}-{rest // 1000:02d}.{rest % 1000:03d}.png"


def save_command(path: str, at: float, chain: str, out: str) -> list:
    gpu = "libplacebo" in chain
    return [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            *(["-init_hw_device", "vulkan"] if gpu else []),
            "-ss", ff.ts(at), "-i", path, "-frames:v", "1", "-an", "-sn", "-dn",
            "-vf", chain, "-c:v", "png", "-pred", "mixed", out]


class SnapshotTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_path = ""
        self._info = None
        self._fps = framestep.DEFAULT_FPS
        self._bars = None
        self._job = None
        self._last_dir = ""
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.make_header(self, t("tab.snapshot"), "Save any frame as a full-size, high-quality picture.",
                         icon="📷")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), kind="video",
                               on_change=lambda p: None if self.load_file(p) else None)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.info_lbl = tk.Label(body, text="", fg=CLR["fgdim"], font=(MONO_FONT, 9))
        self.info_lbl.pack(anchor="w", padx=px(24))

        views = tk.Frame(body)
        views.pack(pady=px(6, 0))
        self.views = []
        for label in ("In the video", "Your screenshot"):
            col = tk.Frame(views)
            col.pack(side="left", padx=px(6))
            tk.Label(col, text=label, fg=CLR["fgdim"], font=(UI_FONT, 9, "bold")).pack(anchor="w")
            view = FramePreview(col, 460, 259, show_time=False)
            view.pack()
            self.views.append(view)
        self.view_frame, self.view_shot = self.views
        # The picture is drawn on the page colour with an outline, so its real
        # shape shows (a cropped or tall picture on black looked letterboxed)
        self.view_shot.canvas.configure(bg=CLR["bg"])
        self.view_shot.after_draw = self._outline_shot
        for view in self.views:
            view.canvas.bind("<Button-1>", lambda _e: self._focus_slider(), add=True)

        nav = tk.Frame(body)
        nav.pack(fill="x", padx=px(20), pady=px(6, 0))
        self.pos_var = tk.DoubleVar(value=0.0)
        self.slider = ttk.Scale(nav, from_=0.0, to=1.0, variable=self.pos_var, command=lambda _v: self._moved())
        self.slider.pack(fill="x")
        framestep.slider(self.slider, lambda: self._fps)
        row = tk.Frame(nav)
        row.pack(fill="x", pady=px(4, 0))
        for label, frames, seconds, tip in (("◀◀", 0, -1, "One second back (Shift+,)"),
                                            ("◀", -1, 0, "One frame back (,)"),
                                            ("▶", 1, 0, "One frame forward (.)"),
                                            ("▶▶", 0, 1, "One second forward (Shift+.)")):
            b = tk.Button(row, text=label, relief="flat", width=4,
                          command=lambda f=frames, s=seconds: framestep.step(self.slider, f, s))
            b.pack(side="left", padx=px(0, 2))
            add_tooltip(b, tip)
        self.pos_lbl = tk.Label(row, text="", fg=CLR["accent"], font=(MONO_FONT, 10, "bold"))
        self.pos_lbl.pack(side="left", padx=px(12, 0))
        tk.Label(row, text="Go to:").pack(side="left", padx=px(16, 4))
        self.goto_var = tk.StringVar()
        goto = tk.Entry(row, textvariable=self.goto_var, width=13, relief="flat")
        goto.pack(side="left")
        goto.bind("<Return>", lambda _e: self._goto())
        add_tooltip(goto, "A time (1:02.5) or a frame number (#1500), then Enter")
        tk.Label(row, text="Keys:  ,  and  .  one frame   ·   with Shift, one second", fg=CLR["fgdim"],
                 font=(UI_FONT, 8)).pack(side="right")

        pic = tk.LabelFrame(body, text="  Picture  ", padx=px(12), pady=px(8))
        pic.pack(fill="x", padx=px(20), pady=px(8, 4))
        self.accurate_var = tk.BooleanVar(value=True)
        self.square_var = tk.BooleanVar(value=True)
        self.tonemap_var = tk.BooleanVar(value=True)
        self.deint_var = tk.BooleanVar(value=True)
        self.crop_var = tk.BooleanVar(value=False)
        self.deep_var = tk.BooleanVar(value=False)
        checks = (
            (self.accurate_var, "Accurate colours", "Converts with the video's own colour standard and full-range "
                                                    "RGB, with full-detail colour edges"),
            (self.square_var, "Correct the pixel shape", "For anamorphic video, so circles stay round"),
            (self.tonemap_var, "HDR to normal colours", "Tone-maps HDR video the way the HDR to SDR tool does "
                                                        "(otherwise HDR stills look grey and flat)"),
            (self.deint_var, "Remove interlacing combs", "Only does anything on interlaced video"),
            (self.crop_var, "Crop black bars", "Finds letterbox / pillarbox bars and leaves them out"),
            (self.deep_var, "16-bit PNG", "Keeps every shade of 10-bit and HDR video (bigger file)"),
        )
        self._checks = {}
        grid = tk.Frame(pic)
        grid.pack(fill="x")
        for i, (var, text, tip) in enumerate(checks):
            cb = tk.Checkbutton(grid, text=text, variable=var, command=self._options_changed)
            cb.grid(row=i // 3, column=i % 3, sticky="w", padx=px(0, 18))
            add_tooltip(cb, tip)
            self._checks[text] = cb

        adj = tk.Frame(pic)
        adj.pack(fill="x", pady=px(8, 0))
        self.size_var = tk.StringVar(value=SIZES["own"])
        self.sharpen_var = tk.StringVar(value=SHARPEN[0.0])
        self.denoise_var = tk.StringVar(value=DENOISE[0])
        for label, var, values, width in (("Size:", self.size_var, list(SIZES.values()), 26),
                                          ("Sharpen:", self.sharpen_var, list(SHARPEN.values()), 9),
                                          ("Noise reduction:", self.denoise_var, list(DENOISE.values()), 9)):
            tk.Label(adj, text=label).pack(side="left", padx=px(0, 4))
            cb = ttk.Combobox(adj, textvariable=var, values=values, state="readonly", width=width)
            cb.pack(side="left", padx=px(0, 16))
            cb.bind("<<ComboboxSelected>>", lambda _e: self._options_changed())

        sliders = tk.Frame(pic)
        sliders.pack(fill="x", pady=px(6, 0))
        self.brightness_var = tk.DoubleVar(value=0.0)
        self.contrast_var = tk.DoubleVar(value=1.0)
        self.saturation_var = tk.DoubleVar(value=1.0)
        for label, var, lo, hi in (("Brightness", self.brightness_var, -0.3, 0.3),
                                   ("Contrast", self.contrast_var, 0.5, 1.5),
                                   ("Saturation", self.saturation_var, 0.0, 2.0)):
            tk.Label(sliders, text=label).pack(side="left")
            tk.Scale(sliders, variable=var, from_=lo, to=hi, resolution=0.01, orient="horizontal",
                     length=px(130), command=lambda _v: self._options_changed()).pack(side="left", padx=px(4, 14))
        tk.Button(sliders, text="Reset", relief="flat", command=self._reset_look).pack(side="left")

        out = tk.Frame(body)
        out.pack(fill="x", padx=px(20), pady=px(8, 4))
        self.btn_render = tk.Button(out, text="📷  SAVE SCREENSHOT…", font=(UI_FONT, 12, "bold"), bg=CLR["green"],
                                    fg="white", cursor="hand2", command=self._save_as)
        self.btn_render.pack(side="left")
        add_tooltip(self.btn_render, "Save this frame as a PNG (Ctrl+S)")
        self.btn_quick = tk.Button(out, text="Quick save", relief="flat", command=self._quick_save)
        self.btn_quick.pack(side="left", padx=px(10))
        add_tooltip(self.btn_quick, "Save straight into the last folder you used (or next to the video), "
                                    "named after the video and the frame's time")
        self.size_lbl = tk.Label(out, text="", fg=CLR["fgdim"])
        self.size_lbl.pack(side="left", padx=px(10))
        self.result_lbl = tk.Label(body, text="", fg=CLR["green"], cursor="hand2", anchor="w")
        self.result_lbl.pack(fill="x", padx=px(22), pady=px(0, 10))
        self.result_lbl.bind("<Button-1>", lambda _e: self._reveal())
        self._saved = ""

    # ── Loading ───────────────────────────────────────────────────────
    def load_file(self, path) -> bool:
        info = ff.try_probe(path) if path else None
        if info is None or not info.has_video or info.is_image:
            if path:
                self.info_lbl.config(text="Can't read this file as video.", fg=CLR["red"])
            return False
        if self.src_row.path != path:
            self.src_row.set(path)
        self.file_path = path
        self._info = info
        self._fps = framestep.fps_of(path)
        self._bars = None
        v = info.video
        frames = max(1, int(round(info.duration * self._fps)))
        self.info_lbl.config(text=f"{info.summary()}  ·  {frames:,} frames", fg=CLR["fgdim"])
        self._checks["HDR to normal colours"].config(state="normal" if info.is_hdr else "disabled")
        interlaced = v.field_order not in ("", "progressive", "unknown")
        self._checks["Remove interlacing combs"].config(state="normal" if interlaced else "disabled")
        self._checks["Correct the pixel shape"].config(state="normal" if _sar(v) != 1.0 else "disabled")
        self.deep_var.set(v.bit_depth > 8 or info.is_hdr)
        if self.crop_var.get():
            self._detect_bars()
        self.slider.configure(to=max(0.0, info.duration - 1.0 / self._fps))
        for view in self.views:
            view.load(path, info.duration, self._seek(0.0))
        self.pos_var.set(0.0)
        self._options_changed()
        self._moved()
        self._focus_slider()
        return True

    def _focus_slider(self):
        """Keyboard to the slider, so , and . step frames straight away."""
        self.slider.focus_set()
        framestep.touch(self.slider)

    def _outline_shot(self):
        c = self.view_shot.canvas
        c.delete("edge")
        box = self.view_shot.image_box()
        if box is not None:
            x, y, w, h = box
            c.create_rectangle(x, y, x + w, y + h, outline=CLR["border"], width=px(1), tags="edge")

    # ── Position ──────────────────────────────────────────────────────
    def _frame(self) -> int:
        return int(round(float(self.pos_var.get()) * self._fps))

    def _seek(self, t_: float) -> float:
        """Where to seek for the frame starting at *t_*: half a frame early, so
        FFmpeg's "first frame at or after" always lands on that frame."""
        return max(0.0, t_ - 0.5 / self._fps)

    def _moved(self):
        if self._info is None:
            return
        n = self._frame()
        at = n / self._fps
        self.pos_lbl.config(text=f"{ff.format_time(at)}   ·   frame {n + 1:,}")
        for view in self.views:
            view.show(self._seek(at))

    def _goto(self):
        text = self.goto_var.get().strip()
        if not text or self._info is None:
            return
        try:
            at = (int(text.lstrip("#")) - 1) / self._fps if text.startswith("#") else ff.parse_time(text)
        except ValueError:
            self.goto_var.set("")
            return
        self.slider.set(min(float(self.slider.cget("to")), max(0.0, framestep.snap(at, self._fps))))
        framestep.touch(self.slider)

    # ── Picture ───────────────────────────────────────────────────────
    def _reset_look(self):
        self.brightness_var.set(0.0)
        self.contrast_var.set(1.0)
        self.saturation_var.set(1.0)
        self.sharpen_var.set(SHARPEN[0.0])
        self.denoise_var.set(DENOISE[0])
        self._options_changed()

    def _key(self, table: dict, var: tk.StringVar):
        return next((k for k, v in table.items() if v == var.get()), next(iter(table)))

    def chain(self) -> str:
        return build_chain(self._info, accurate=self.accurate_var.get(), square_pixels=self.square_var.get(),
                           tonemap=self.tonemap_var.get(), deinterlace=self.deint_var.get(),
                           crop=self._bars if self.crop_var.get() else None,
                           size=self._key(SIZES, self.size_var), sharpen=self._key(SHARPEN, self.sharpen_var),
                           denoise=self._key(DENOISE, self.denoise_var),
                           brightness=round(self.brightness_var.get(), 2),
                           contrast=round(self.contrast_var.get(), 2),
                           saturation=round(self.saturation_var.get(), 2),
                           deep=self.deep_var.get(), gpu=self.tonemap_var.get() and self._info.is_hdr
                           and fl.vulkan_ok())

    def _options_changed(self):
        if self._info is None:
            return
        if self.crop_var.get() and self._bars is None:
            self._detect_bars()
            return
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(200, self._refresh)

    def _refresh(self):
        self._job = None
        if self._info is None:
            return
        self.view_shot.set_filter(self.chain())
        w, h = out_size(self._info, self._key(SIZES, self.size_var), self.square_var.get(),
                        self._bars if self.crop_var.get() and self._bars else None)
        bits = "16-bit" if self.deep_var.get() else "8-bit"
        self.size_lbl.config(text=f"{w}×{h} PNG, {bits}")

    def _detect_bars(self):
        from tabs.social.auto_cropper import detect_bars
        from core import ui
        path, info = self.file_path, self._info

        def done(box):
            if path != self.file_path:
                return
            v = info.video
            self._bars = box if box and (box[2], box[3]) != (v.width, v.height) else ()
            self._options_changed()

        self.size_lbl.config(text="Looking for black bars…")
        ui.run_in_thread(detect_bars, path, info.duration, on_done=done, on_error=lambda _e: done(None),
                         name="SnapshotBars")

    # ── Saving ────────────────────────────────────────────────────────
    def _save_as(self):
        if self._info is None:
            self.result_lbl.config(text="Choose a video first.", fg=CLR["orange"])
            return
        from core import dialogs
        at = self._frame() / self._fps
        start = self._last_dir or os.path.dirname(self.file_path)
        out = dialogs.ask_save(os.path.join(start, frame_name(self.file_path, at)), kind="image",
                               defaultextension=".png", title="Save screenshot", parent=self)
        if out:
            self._save(out)

    def _quick_save(self):
        if self._info is None:
            self.result_lbl.config(text="Choose a video first.", fg=CLR["orange"])
            return
        folder = self._last_dir if os.path.isdir(self._last_dir) else os.path.dirname(self.file_path)
        out = os.path.join(folder, frame_name(self.file_path, self._frame() / self._fps))
        base, n = out[:-4], 2
        while os.path.exists(out):
            out = f"{base} ({n}).png"
            n += 1
        self._save(out)

    def _save(self, out: str):
        if not out.lower().endswith(".png"):
            out += ".png"
        at = self._frame() / self._fps
        cmd = save_command(self.file_path, self._seek(at), self.chain(), out)
        self.result_lbl.config(text="Saving…", fg=CLR["fgdim"])
        self.btn_render.config(state="disabled")

        def work():
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                               stdin=subprocess.DEVNULL, env=child_env(), creationflags=CREATE_NO_WINDOW)
            if r.returncode != 0 or not os.path.isfile(out):
                raise RuntimeError((r.stderr or "FFmpeg failed").strip().splitlines()[-1][:200])
            return out

        def done(path):
            self.btn_render.config(state="normal")
            self._last_dir = os.path.dirname(path)
            self._saved = path
            mb = os.path.getsize(path) / 1e6
            self.result_lbl.config(text=f"✓  Saved {os.path.basename(path)}  ({mb:.1f} MB)  -  click to show it",
                                   fg=CLR["green"])

        def failed(exc):
            self.btn_render.config(state="normal")
            self.result_lbl.config(text=f"Couldn't save: {exc}", fg=CLR["red"])

        from core import ui
        ui.run_in_thread(work, on_done=done, on_error=failed, name="Snapshot")

    def _reveal(self):
        if self._saved and os.path.isfile(self._saved):
            reveal_file(self._saved)
