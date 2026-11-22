"""
tabs/transcoder/downsizer.py  -  Universal Downsizer

Make any video fit a file-size limit (Discord, email, upload caps...) at the
best quality that fits.

* The bitrate budget comes from the target size and the length (with a
  margin for the container); audio gets a share that suits the budget.
* Resolution and frame rate are lowered only as far as needed - judged by
  bits per pixel for the chosen codec - and only when allowed.
* "Best quality" encodes two-pass on the CPU (accurate size); "Fast" uses the
  GPU encoder when there is one.  If the file still comes out over the
  limit it is re-encoded at a lower bitrate automatically, so the result is
  never over the limit.
* HDR sources are tone-mapped to SDR for H.264 (and kept HDR for HEVC/AV1).
* Optional start/end trim for clips that are too long to fit nicely.
"""
from __future__ import annotations

import os
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import FileRow, add_pickers, scroll_body

TARGETS = [("8 MB", 8), ("10 MB - Discord", 10), ("18 MB - safe for email", 18), ("25 MB", 25),
           ("50 MB - Discord Nitro Basic", 50), ("100 MB", 100), ("500 MB - Discord Nitro", 500),
           ("Custom size", 0)]
CODECS = {"h264": "H.264 - plays everywhere (recommended)", "hevc": "HEVC (H.265) - smaller, newer devices",
          "av1": "AV1 - smallest, newest devices"}
AUDIO_MODES = {"auto": "Re-encode to fit (AAC)", "copy": "Keep the original audio", "none": "Remove the audio"}
#: acceptable bits per pixel per frame - below this the picture falls apart
MIN_BPP = {"h264": 0.045, "hevc": 0.03, "av1": 0.025}
LADDER = (2160, 1440, 1080, 900, 720, 540, 480, 360, 240, 180)
MARGIN = 0.97          # container overhead + encoder slack


@dataclass
class Plan:
    video_kbps: int
    audio_kbps: int
    mono: bool
    width: int
    height: int
    fps: float
    notes: list


def plan(duration: float, target_bytes: int, size: tuple, fps: float, *, codec: str = "h264",
         has_audio: bool = True, audio_mode: str = "auto", audio_src_kbps: int = 0,
         allow_res: bool = True, allow_fps: bool = True) -> Plan:
    """Work out bitrates, size and frame rate for *target_bytes*.  Raises
    ValueError when the target cannot hold a watchable video."""
    if duration <= 0:
        raise ValueError("The video's length is unknown.")
    budget = target_bytes * 8 / 1000 / duration * MARGIN            # kbit/s for everything
    notes = []
    mono = False
    if not has_audio or audio_mode == "none":
        a_kbps = 0
    elif audio_mode == "copy":
        a_kbps = audio_src_kbps or 320
        if not audio_src_kbps:
            notes.append("The original audio bitrate is unknown - 320 kb/s is reserved for it.")
    else:
        a_kbps = 128 if budget >= 2000 else 96 if budget >= 800 else 64 if budget >= 350 else 48
        mono = a_kbps <= 48
    v_kbps = int(budget - a_kbps)
    if v_kbps < 40:
        raise ValueError(f"{target_bytes / 1e6:g} MB is too small for {ff.format_time(duration)} of video "
                         f"(only {max(0, v_kbps)} kb/s would be left for the picture). Trim the video, "
                         "remove the audio or choose a bigger size.")
    w, h = size
    out_fps = fps if fps and fps > 0 else 30.0
    min_bpp = MIN_BPP.get(codec, 0.045)

    def bpp(ww, hh, ff_):
        return v_kbps * 1000 / max(1, ww * hh * ff_)

    if allow_fps and out_fps > 31 and bpp(w, h, out_fps) < min_bpp * 1.5:
        out_fps = out_fps / 2 if out_fps / 2 >= 23.9 else 30.0
        notes.append(f"Frame rate lowered to {out_fps:g} fps.")
    out_w, out_h = w, h
    if allow_res and bpp(w, h, out_fps) < min_bpp:
        short = min(w, h)
        for rung in LADDER:
            if rung >= short:
                continue
            scale = rung / short
            cw, ch = max(2, round(w * scale / 2) * 2), max(2, round(h * scale / 2) * 2)
            out_w, out_h = cw, ch
            if bpp(cw, ch, out_fps) >= min_bpp:
                break
        if (out_w, out_h) != (w, h):
            notes.append(f"Resolution lowered to {out_w}×{out_h} so the picture stays clean.")
    if bpp(out_w, out_h, out_fps) < min_bpp * 0.6:
        notes.append("Expect visible blockiness - a shorter clip or a bigger size would look better.")
    return Plan(v_kbps, a_kbps, mono, out_w, out_h, out_fps, notes)


def retry_kbps(v_kbps: int, target_bytes: int, actual_bytes: int, audio_bytes: int = 0) -> int:
    """Lower video bitrate after an attempt came out over the limit."""
    video_room = max(1, target_bytes - audio_bytes)
    video_used = max(1, actual_bytes - audio_bytes)
    return max(20, int(v_kbps * video_room / video_used * 0.96))


class UniversalDownsizerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self._info = None
        self._job = None
        self._build_ui()
        add_pickers(self, [self.start_var, self.end_var], lambda: self.src_row.path)   # 🎞 next to the trim

    def _build_ui(self):
        self.make_header(self, t("tab.universal_downsizer"), "Shrink a video to fit a size limit, at the best "
                         "quality that fits.", icon="🗜")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self._src_var = self.src_row.var
        self.info_lbl = tk.Label(body, text="", fg=CLR["fgdim"], anchor="w")
        self.info_lbl.pack(fill="x", padx=px(20))

        opt = tk.LabelFrame(body, text="  Target  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(6))

        def row(r, label, *widgets):
            tk.Label(opt, text=label, width=14, anchor="w").grid(row=r, column=0, sticky="w", pady=px(3))
            for i, w in enumerate(widgets):
                w.grid(row=r, column=1 + i, sticky="w", padx=px(0, 6), pady=px(3))

        self.target_var = tk.StringVar(value=TARGETS[1][0])
        tb = ttk.Combobox(opt, textvariable=self.target_var, values=[x[0] for x in TARGETS], state="readonly",
                          width=28)
        self._target_mb = tk.StringVar(value="10")
        self.custom_entry = tk.Entry(opt, textvariable=self._target_mb, width=7)
        row(0, "Size limit:", tb, self.custom_entry, tk.Label(opt, text="MB"))
        self.codec_var = tk.StringVar(value=CODECS["h264"])
        cb = ttk.Combobox(opt, textvariable=self.codec_var, values=list(CODECS.values()), state="readonly", width=40)
        row(1, "Codec:", cb)
        self.speed_var = tk.StringVar(value="quality")
        sp = tk.Frame(opt)
        tk.Radiobutton(sp, text="Best quality (two-pass, CPU)", variable=self.speed_var, value="quality",
                       command=self._changed).pack(side="left")
        tk.Radiobutton(sp, text="Fast (GPU when available)", variable=self.speed_var, value="fast",
                       command=self._changed).pack(side="left", padx=px(10))
        row(2, "Encoding:", sp)
        self.audio_var = tk.StringVar(value=AUDIO_MODES["auto"])
        ab = ttk.Combobox(opt, textvariable=self.audio_var, values=list(AUDIO_MODES.values()), state="readonly",
                          width=28)
        row(3, "Audio:", ab)
        self._allow_res = tk.BooleanVar(value=True)
        self._allow_fps = tk.BooleanVar(value=True)
        perm = tk.Frame(opt)
        tk.Checkbutton(perm, text="May lower the resolution", variable=self._allow_res,
                       command=self._changed).pack(side="left")
        tk.Checkbutton(perm, text="May lower the frame rate (above 30 fps only)", variable=self._allow_fps,
                       command=self._changed).pack(side="left", padx=px(10))
        row(4, "Allowed:", perm)
        self.start_var, self.end_var = tk.StringVar(), tk.StringVar()
        tr = tk.Frame(opt)
        tk.Entry(tr, textvariable=self.start_var, width=10).pack(side="left")
        tk.Label(tr, text=" to ").pack(side="left")
        tk.Entry(tr, textvariable=self.end_var, width=10).pack(side="left")
        tk.Label(tr, text="  (optional - leave empty for the whole video)", fg=CLR["fgdim"]).pack(side="left")
        row(5, "Trim:", tr)
        for w in (tb, cb, ab):
            w.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        for v in (self._target_mb, self.start_var, self.end_var):
            v.trace_add("write", lambda *_a: self._changed())
        self.plan_lbl = tk.Label(opt, text="", fg=CLR["accent"], justify="left", anchor="w", wraplength=px(640))
        self.plan_lbl.grid(row=6, column=0, columnspan=6, sticky="w", pady=px(6, 0))

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True, ext=".mp4",
                               suggest=lambda: self.suggest_output(self.src_row.path, "small", ".mp4"))
        self.out_row.pack(fill="x")
        self._out_var = self.out_row.var
        self.btn_render = tk.Button(body, text=t("downsizer.render_button"), font=(UI_FONT, 12, "bold"),
                                     bg=CLR["green"], fg="white", width=30, cursor="hand2", command=self._start_render)
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=7)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self._changed()

    # ── State ─────────────────────────────────────────────────────────
    def _key(self, table, var, default):
        return next((k for k, v in table.items() if v == var.get()), default)

    def _target_bytes(self) -> int:
        mb = dict(TARGETS).get(self.target_var.get(), 0)
        if not mb:
            mb = common.number(self._target_mb.get(), "Size limit", 0.1, 100000)
        return int(mb * 1_000_000)          # decimal MB: also fits limits counted in MiB

    def _trim(self, info):
        a = ff.parse_time(self.start_var.get()) if self.start_var.get().strip() else 0.0
        b = ff.parse_time(self.end_var.get()) if self.end_var.get().strip() else info.duration
        b = min(b, info.duration)
        if b - a < 0.5:
            raise ValueError("The trimmed part must be at least half a second long.")
        return a, b

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        info = self._info
        if info is None or not info.has_video:
            self.info_lbl.config(text="")
        else:
            v = info.video
            size_mb = os.path.getsize(path) / 1e6 if os.path.isfile(path) else 0
            hdr = "  HDR" if v.is_hdr else ""
            self.info_lbl.config(text=f"{ff.format_time(info.duration)}  ·  {v.width}×{v.height}  ·  "
                                      f"{v.fps:.3g} fps  ·  {v.codec}{hdr}  ·  {size_mb:.1f} MB now")
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "small", ".mp4"))
        self._changed()

    def _plan(self) -> tuple:
        info = self._info
        if info is None or not info.has_video:
            raise ValueError("Choose a video first.")
        a, b = self._trim(info)
        codec = self._key(CODECS, self.codec_var, "h264")
        audio_mode = self._key(AUDIO_MODES, self.audio_var, "auto")
        src_kbps = int((info.audio.bit_rate or 0) / 1000) if info.has_audio else 0
        p = plan(b - a, self._target_bytes(), common.even_size(info), info.video.fps, codec=codec,
                 has_audio=info.has_audio, audio_mode=audio_mode, audio_src_kbps=src_kbps,
                 allow_res=self._allow_res.get(), allow_fps=self._allow_fps.get())
        return p, (a, b), codec, audio_mode

    def _changed(self):
        self.custom_entry.config(state="normal" if dict(TARGETS).get(self.target_var.get(), 0) == 0
                                 else "disabled")
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(150, self._show_plan)

    def _show_plan(self):
        self._job = None
        if self._info is None:
            self.plan_lbl.config(text="")
            return
        try:
            p, (a, b), _codec, _am = self._plan()
        except ValueError as exc:
            self.plan_lbl.config(text=f"⚠  {exc}", fg=CLR["orange"])
            return
        cur = os.path.getsize(self.src_row.path) if os.path.isfile(self.src_row.path) else 0
        if cur and cur <= self._target_bytes() and (a, b) == (0.0, self._info.duration):
            msg = "The file is already under this size - it will still be re-encoded."
        else:
            audio = f" + audio {p.audio_kbps} kb/s{' mono' if p.mono else ''}" if p.audio_kbps else ""
            msg = (f"Plan: {p.width}×{p.height} at {p.fps:.3g} fps, video {p.video_kbps:,} kb/s{audio}"
                   + ("\n" + "\n".join(p.notes) if p.notes else ""))
        self.plan_lbl.config(text=msg, fg=CLR["accent"])

    # ── Render ────────────────────────────────────────────────────────
    def _start_render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        self._info = info
        try:
            p, (start, end), codec, audio_mode = self._plan()
            target = self._target_bytes()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = self.out_row.path or self.suggest_output(src, "small", ".mp4")
        if not out.lower().endswith((".mp4", ".mkv", ".mov")):
            out = os.path.splitext(out)[0] + ".mp4"
        out = self.confirm_output(out, [src], default_ext=".mp4")
        if not out:
            return
        self.out_row.set(out)
        fast = self.speed_var.get() == "fast"
        self.log(self.console, f"Target {target / 1e6:g} MB · {p.width}×{p.height} @ {p.fps:.3g} fps · "
                               f"video {p.video_kbps} kb/s · audio {p.audio_kbps} kb/s")
        for note in p.notes:
            self.log(self.console, "  " + note)
        length = end - start

        def work(_p, _c, ctx):
            def runner(cmd, duration, lo, hi):
                ctx.span(lo, hi).run(cmd, duration=duration)
            return encode_to_size(src, ctx.output(), info, p, target, codec=codec, fast=fast,
                                  audio_mode=audio_mode, start=start, end=end, tmp=ctx.tempdir(),
                                  run=runner, log=ctx.log)

        common.queue_job(self, name="Downsizer", out=out, work=work, btn=self.btn_render,
                         btn_label=t("downsizer.render_button"), console=self.console, duration=length,
                         inputs=[src])


def encode_to_size(src: str, final: str, info, p: Plan, target: int, *, codec: str = "h264", fast: bool = False,
                   audio_mode: str = "auto", start: float = 0.0, end: float | None = None, tmp: str,
                   run, log=print) -> int:
    """Encode *src* into *final* under *target* bytes following plan *p*:
    two-pass on the CPU (or the GPU when *fast*), then re-encode at a lower
    bitrate until the file fits.  *run(cmd, duration, lo, hi)* runs one
    command (lo/hi = its share of the progress).  Shared by the tool and the
    command line."""
    end = info.duration if end is None else end
    length = end - start
    v = info.video
    hdr = v.is_hdr
    ten_bit = codec != "h264" and (v.bit_depth > 8 or hdr)
    chain = []
    if hdr and codec == "h264":
        chain.append(fl.hdr_to_sdr_chain())
    w0, h0 = v.display_size
    sw, sh = max(2, w0 // 2 * 2), max(2, h0 // 2 * 2)
    if (p.width, p.height) != (sw, sh) or (v.sar and v.sar not in ("1:1", "0:1")):
        chain.append(f"scale={p.width}:{p.height}:flags=lanczos,setsar=1")
    if v.fps and abs(p.fps - v.fps) > 0.01:
        chain.append(f"fps={p.fps:g}")
    container = os.path.splitext(final)[1].lstrip(".").lower() or "mp4"
    if container == "part":
        container = os.path.splitext(os.path.splitext(final)[0])[1].lstrip(".").lower() or "mp4"

    def video_args(kbps):
        return enc.video(codec, "high", speed="balanced", hw="auto" if fast else "off", ten_bit=ten_bit,
                         container=container, bitrate=f"{kbps}k", maxrate=f"{int(kbps * 1.5)}k" if fast else None,
                         gop_seconds=4, fps=p.fps)

    if audio_mode == "none" or not info.has_audio:
        audio = ["-an"]
    elif audio_mode == "copy":
        audio = ["-map", "0:a:0", "-c:a", "copy"]
    else:
        audio = ["-map", "0:a:0", "-c:a", "aac", "-b:a", f"{p.audio_kbps}k"] + (["-ac", "1"] if p.mono else [])
    inp = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y"]
    if start > 0:
        inp += ["-ss", f"{start:.3f}"]
    if end < info.duration - 0.01:
        inp += ["-to", f"{end:.3f}"]
    kbps = p.video_kbps
    ve = video_args(kbps)
    two_pass = not fast and ve.hw is None and bool(enc.two_pass_args(ve.encoder, 1, "x"))
    passlog = os.path.join(tmp, "pass")
    if two_pass:
        log(f"Pass 1 of 2 ({ve.label})…")
        run(ve.pre_input + inp + ["-i", src, "-map", "0:v:0"] + ve.vf_args(",".join(chain) or None) + ve.args
            + enc.two_pass_args(ve.encoder, 1, passlog) + ["-an", "-f", "null", "-"], length, 0.0, 0.4)
    lo = 0.4 if two_pass else 0.0
    for attempt in range(4):
        ve = video_args(kbps)
        log(f"Encoding with {ve.label}…" if attempt == 0 else f"Still over the limit - re-encoding at {kbps} kb/s…")
        run(ve.pre_input + inp + ["-i", src, "-map", "0:v:0"] + ve.vf_args(",".join(chain) or None) + ve.args
            + (enc.two_pass_args(ve.encoder, 2, passlog) if two_pass else []) + audio
            + ["-map_metadata", "0", "-map_chapters", "0", final], length,
            lo + (1 - lo) * attempt / 4, 1.0 if attempt == 0 else lo + (1 - lo) * (attempt + 1) / 4)
        size = os.path.getsize(final)
        log(f"  {size / 1e6:.2f} MB")
        if size <= target:
            return 0
        kbps = retry_kbps(kbps, target, size, int(p.audio_kbps * 1000 / 8 * length))
    raise RuntimeError("Could not get the video under the size limit - choose a bigger size, trim it, or allow a "
                       "lower resolution.")
