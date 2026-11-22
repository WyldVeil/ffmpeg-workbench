"""
core/ops/karaoke.py - the Karaoke Maker (tabs/audio/karaoke.py): lyrics heard by
whisper.cpp, timed word by word into an ASS file that fills in as they're sung,
burnt over the (darkened) video or a plain background, vocals optionally removed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from core import ass, whisper
from core import ffmpeg as ff
from core.queue_manager import TaskContext


@dataclass(frozen=True, slots=True)
class Word:
    word: str  # with its leading space, as heard
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class Line:
    start: float
    end: float
    text: str
    words: Sequence[Word]


ASS_COLOURS = {
    "Yellow": "&H0000FFFF",  # #FFFF00
    "White": "&H00FFFFFF",  # #FFFFFF
    "Cyan": "&H00FFFF00",  # #00FFFF
    "Green": "&H0000FF00",  # #00FF00
    "Pink": "&H008080FF",  # #FF8080 - soft pink, readable
    "Orange": "&H000080FF",  # #FF8000
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Karaoke:
    model: str  # core.whisper model key
    language: str = "auto"
    darkness: float = 0.6  # how bright the video stays under the lyrics (0..1)
    font_size: int = 64  # at 1080p
    sung: str = ASS_COLOURS["Green"]
    waiting: str = ASS_COLOURS["Yellow"]
    remove_vocals: bool = False


def _ass_header(font_size: int, primary: str, secondary: str, res_x: int, res_y: int) -> str:
    # Bold=-1 (yes), Alignment=2 (bottom-centre), Outline=3, Shadow=1
    return (
        "[Script Info]\nScriptType: v4.00+\nCollisions: Normal\n"
        f"PlayResX: {res_x}\nPlayResY: {res_y}\nTimer: 100.0\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Karaoke,Montserrat,{font_size},{primary},{secondary},&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,3,1,2,10,10,40,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _sweep(line: Line) -> str:
    """The line's \\kf chain (centiseconds per word), with an empty timed token
    for each pause so the fill stays in time (the words carry their own spaces)."""
    parts: list[str] = []
    prev_end = line.words[0].start
    for n, w in enumerate(line.words):
        gap_cs = max(0, round((w.start - prev_end) * 100))
        if gap_cs > 0:
            parts.append(f"{{\\kf{gap_cs}}}")
        text = ass.escape(w.word.lstrip() if n == 0 else w.word)
        parts.append(f"{{\\kf{max(1, round((w.end - w.start) * 100))}}}{text}")
        prev_end = w.end
    return "".join(parts)


def build_ass(
    lines: Sequence[Line],
    path: str,
    font_size: int,
    primary: str,
    secondary: str,
    res_x: int = 1920,
    res_y: int = 1080,
) -> int:
    """Write an ASS karaoke file; how many lines.  *primary* fills in as a word is
    sung, *secondary* is the colour of words waiting: each word sweeps left to
    right (\\kf), as on a karaoke machine."""
    events: list[str] = []
    for line in lines:
        if line.words:
            a, b, body = line.words[0].start, line.words[-1].end, _sweep(line)
        elif line.text.strip():  # no word times: the whole line as one sweep
            a, b = line.start, line.end
            body = f"{{\\kf{max(1, round((b - a) * 100))}}}{ass.escape(line.text.strip())}"
        else:
            continue
        events.append(f"Dialogue: 0,{ass.ts(a)},{ass.ts(b)},Karaoke,,0,0,0,,{body}\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_ass_header(font_size, primary, secondary, res_x, res_y))
        fh.writelines(events)
    return len(events)


def _words(tokens: Sequence[dict[str, Any]]) -> list[Word]:
    """whisper.cpp tokens joined into words (a token without a leading space continues the word)."""
    words: list[Word] = []
    for tok in tokens:
        text = str(tok.get("text", ""))
        if not text.strip() or text.startswith("[_"):
            continue
        off = tok.get("offsets", {})
        a, b = off.get("from", 0) / 1000.0, off.get("to", 0) / 1000.0
        if words and not text.startswith(" "):
            last = words[-1]
            words[-1] = Word(last.word + text, last.start, max(last.end, b))
        else:
            words.append(Word(text, a, max(b, a + 0.05)))
    return words


def segments_from_whispercpp(path: str) -> list[Line]:
    """whisper.cpp full JSON (-ojf) -> the lines that have words."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        data = json.load(fh)
    lines: list[Line] = []
    for seg in data.get("transcription", []):
        words = _words(seg.get("tokens", []))
        off = seg.get("offsets", {})
        if words:
            lines.append(
                Line(
                    off.get("from", 0) / 1000.0,
                    off.get("to", 0) / 1000.0,
                    str(seg.get("text", "")).strip(),
                    words,
                )
            )
    return lines


def vocal_remove_filter() -> str:
    """Centre-panned vocals out, bass (usually centred too) kept, same polarity on both sides."""
    return (
        "asplit[ka][kb];[ka]pan=stereo|c0=c0-c1|c1=c0-c1[side];"
        "[kb]lowpass=f=180,pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1[bass];"
        "[side][bass]amix=inputs=2:normalize=0,alimiter=limit=0.95"
    )


def transcribe(ctx: TaskContext, src: str, info: ff.MediaInfo, k: Karaoke) -> list[Line]:
    """The lyric lines with word times (downloading the model the first time)."""
    tmp = ctx.tempdir()
    if not whisper.is_installed(k.model):
        ctx.log(f"Downloading the {whisper.MODELS[k.model].label} model (one time)…")
        whisper.download_model(k.model, lambda f: ctx.progress(0.2 * f), ctx.cancelled)
    wav = os.path.join(tmp, "audio.wav")
    ctx.log("Step 1/3: transcribing the lyrics…")
    ctx.span(0.2, 0.25).run(whisper.audio_extract_cmd(src, wav), duration=info.duration or None)
    base = os.path.join(tmp, "words")
    cmd = whisper.transcribe_cmd(
        wav, whisper.model_path(k.model), base, language=k.language, max_len=40, formats=("json",)
    )

    def progress(line: str) -> None:
        f = whisper.parse_progress(line)
        if f is not None:
            ctx.progress(0.25 + 0.45 * f)

    res = ff.run(cmd, on_line=progress, cancel=ctx.cancelled)
    if res.cancelled:
        raise ff.CancelledError("cancelled")
    if res.returncode != 0 or not os.path.isfile(base + ".json"):
        raise RuntimeError("Speech recognition failed:\n" + "\n".join(res.log_tail[-6:]))
    segments = segments_from_whispercpp(base + ".json")
    if not segments:
        raise RuntimeError("No sung or spoken words were recognised.")
    return segments


def render_cmd(src: str, subs: str, out: str, info: ff.MediaInfo, k: Karaoke) -> list[str]:
    """The lyrics *subs* over the darkened video, or over a dark background for sound alone."""
    if info.has_video:
        cmd = [ff.ffmpeg_path(), "-y", "-i", src]
        vf = f"curves=all='0/0 1/{k.darkness:.2f}',{ass.subtitles_filter(subs)},format=yuv420p"
        a_in = "0:a:0"
    else:
        w, h = 1920, 1080
        cmd = [
            ff.ffmpeg_path(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x101018:s={w}x{h}:r=30",
            "-i",
            src,
            "-shortest",
        ]
        vf = f"{ass.subtitles_filter(subs)},format=yuv420p"
        a_in = "1:a:0"
    cmd += ["-map", "0:v:0", "-map", a_in, "-vf", vf]
    if k.remove_vocals:
        cmd += ["-af", vocal_remove_filter()]
    cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"]
    return [*cmd, "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", out]


def make_karaoke(src: str, out: str, k: Karaoke, _p: object, _c: object, ctx: TaskContext) -> int:
    """The queued job: transcribe, time the words, render."""
    info = ff.probe(src)
    segments = transcribe(ctx, src, info, k)
    ctx.log(f"Step 2/3: {len(segments)} lyric lines.")
    w, h = info.require_video().display_size if info.has_video else (1920, 1080)
    subs = os.path.join(ctx.tempdir(), "karaoke.ass")
    build_ass(
        segments, subs, round(k.font_size * min(w, h) / 1080), k.sung, k.waiting, res_x=w, res_y=h
    )
    ctx.log("Step 3/3: rendering the karaoke video…")
    ctx.span(0.7, 1.0).run(
        render_cmd(src, subs, ctx.output(out), info, k), duration=info.duration or None
    )
    return 0
