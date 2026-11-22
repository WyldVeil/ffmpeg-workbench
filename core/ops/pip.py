"""
core/ops/pip.py - Picture-in-Picture (tabs/visuals/pip.py): the inset's size and
place, its look and entrance, and the sound (either video's, or both mixed).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PipSpec:
    """An inset: *pct* of the background's width at *place* (or *custom* %),
    with a border, rounded corners, opacity, a time span and an entrance."""

    bg_size: tuple[int, int]
    fg_size: tuple[int, int]
    pct: float = 30.0
    place: str = "br"
    custom: tuple[float, float] = (50.0, 50.0)
    margin_pct: float = 3.0
    border: int = 0
    border_colour: str = "FFFFFF"
    radius: int = 0
    opacity: float = 1.0
    start: float = 0.0
    end: float | None = None
    anim: str = "none"
    anim_dur: float = 0.5

    def inset_size(self) -> tuple[int, int]:
        bw, _bh = self.bg_size
        fw, fh = self.fg_size
        w = max(16, int(bw * self.pct / 100) // 2 * 2)
        h = max(16, round(w * fh / max(1, fw) / 2) * 2)
        return w, h

    def target_xy(self) -> tuple[int, int]:
        bw, bh = self.bg_size
        w, h = self.inset_size()
        w, h = w + 2 * self.border, h + 2 * self.border
        m = int(min(bw, bh) * self.margin_pct / 100)
        if self.place == "custom":
            return int((bw - w) * self.custom[0] / 100), int((bh - h) * self.custom[1] / 100)
        col = {"l": m, "c": (bw - w) // 2, "r": bw - w - m}
        row = {"t": m, "m": (bh - h) // 2, "b": bh - h - m}
        key = {"c": "mc"}.get(self.place, self.place)
        return col[key[1]], row[key[0]]


def pip_graph(spec: PipSpec, bg: str = "[0:v]", fg: str = "[1:v]", out: str = "[v]") -> str:
    w, h = spec.inset_size()
    x, y = spec.target_xy()
    bw, bh = spec.bg_size
    fgc = [
        f"setpts=PTS-STARTPTS+{spec.start:.3f}/TB",
        f"scale={w}:{h}:flags=lanczos",
        "setsar=1",
        "format=yuva420p",
    ]
    if spec.radius > 0:
        r = min(spec.radius, w // 2, h // 2)
        fgc.append(
            f"geq=lum='p(X,Y)':cb='p(X,Y)':cr='p(X,Y)':"
            f"a='255*lte(hypot(max(0,abs(X-W/2)-(W/2-{r})),max(0,abs(Y-H/2)-(H/2-{r}))),{r})'"
        )
    if spec.border > 0:
        b = spec.border
        fgc.append(f"pad={w + 2 * b}:{h + 2 * b}:{b}:{b}:color=0x{spec.border_colour}")
    if spec.opacity < 0.999:
        fgc.append(f"colorchannelmixer=aa={spec.opacity:.3f}")
    end = spec.end
    d = max(0.05, spec.anim_dur)
    if spec.anim == "fade":
        fgc.append(f"fade=t=in:st={spec.start:.3f}:d={d:.2f}:alpha=1")
        if end is not None:
            fgc.append(f"fade=t=out:st={max(spec.start, end - d):.3f}:d={d:.2f}:alpha=1")
    xe, ye = str(x), str(y)
    if spec.anim in ("left", "right", "top", "bottom"):
        ow, oh = w + 2 * spec.border, h + 2 * spec.border
        p = f"min(1,max(0,(t-{spec.start:.3f})/{d:.3f}))"
        ease = f"(1-pow(1-{p},3))"  # ease-out cubic
        if spec.anim == "left":
            xe = f"{-ow}+({x}+{ow})*{ease}"
        elif spec.anim == "right":
            xe = f"{bw}-({bw}-{x})*{ease}"
        elif spec.anim == "top":
            ye = f"{-oh}+({y}+{oh})*{ease}"
        else:
            ye = f"{bh}-({bh}-{y})*{ease}"
    enable = (
        f"between(t,{spec.start:.3f},{end:.3f})" if end is not None else f"gte(t,{spec.start:.3f})"
    )
    return (
        f"{fg}{','.join(fgc)}[pipfg];{bg}[pipfg]overlay=x='{xe}':y='{ye}':eof_action=pass:format=auto:"
        f"enable='{enable}',format=yuv420p{out}"
    )


def pip_audio(
    mode: str, start: float, duration: float, *, fg_audio: bool, bg_audio: bool
) -> tuple[str, list[str] | None]:
    """(graph steps to add, audio arguments): *mode* "fg" (the inset's sound from
    when it appears), "mix", "none", or "bg" (None: keep the background's as is)."""
    st = "aresample=48000,aformat=channel_layouts=stereo"
    inset = (
        f"[1:a:0]{st},adelay=delays={round(start * 1000)}:all=1,apad,atrim=duration={duration:.3f}"
    )
    mixed = ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    if mode == "fg" and fg_audio:
        return f";{inset}[a]", mixed
    if mode == "mix" and fg_audio and bg_audio:
        both = f";{inset}[fa];[0:a:0]{st}[ba];[ba][fa]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.97[a]"
        return both, mixed
    if mode == "none" or not bg_audio:
        return "", ["-an"]
    return "", None
