"""
core/ops/voiceover.py - the TTS Voice-Over's mix (tabs/audio/tts.py): the spoken
track placed, levelled and saved alone, or laid over (or replacing) a video's sound.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum

from core import ffmpeg as ff
from core import tts as tts_core
from core.ops.shared import audio_encoder_args
from core.queue_manager import TaskContext


class SpeechFormat(StrEnum):
    MP3_320 = "mp3_320"
    MP3_192 = "mp3_192"
    WAV = "wav"
    AAC = "aac"
    FLAC = "flac"


SPEECH_EXT = {
    SpeechFormat.MP3_320: ".mp3",
    SpeechFormat.MP3_192: ".mp3",
    SpeechFormat.WAV: ".wav",
    SpeechFormat.AAC: ".m4a",
    SpeechFormat.FLAC: ".flac",
}
MP3_RATES = {SpeechFormat.MP3_320: "320k", SpeechFormat.MP3_192: "192k"}


class VideoOut(StrEnum):
    COPY = "copy"  # keep the video as it is
    CRF18 = "crf18"
    CRF23 = "crf23"


@dataclass(frozen=True, slots=True, kw_only=True)
class Placement:
    """Where the speech goes: alone (*video* False, saved as *fmt*) or into the
    video (mixed with its sound at *original_volume*, or *replace* it)."""

    video: bool
    pads: tuple[float, float]  # silence before and after
    offset: float  # extra delay (s)
    volume: float
    normalise: bool
    fmt: SpeechFormat = SpeechFormat.MP3_320
    replace: bool = False
    original_volume: float = 0.3
    video_out: VideoOut = VideoOut.COPY


def speech_chain(p: Placement) -> list[str]:
    """The spoken track: stereo 48 kHz, level, loudness, padding and delay."""
    pad_b, pad_a = p.pads
    chain = ["aformat=sample_rates=48000:channel_layouts=stereo", f"volume={p.volume:.3f}"]
    if p.normalise:
        chain += ["loudnorm=I=-16:TP=-1.5:LRA=11", "aresample=48000", "asetnsamples=n=4096:p=0"]
    delay = pad_b + p.offset
    if delay > 0:
        chain.append(f"adelay=delays={round(delay * 1000)}:all=1")
    if pad_a > 0:
        chain.append(f"apad=pad_dur={pad_a:.3f}")
    return chain


def voiceover_cmd(
    speech: str, out: str, video: str, info: ff.MediaInfo | None, p: Placement
) -> tuple[list[str], float | None]:
    """(command, length): the speech alone, or the *video* with it (length then the video's)."""
    chain = speech_chain(p)
    if not p.video or info is None:
        cmd = [ff.ffmpeg_path(), "-y", "-i", speech, "-af", ",".join(chain), "-vn"]
        if out.lower().endswith(".mp3"):
            cmd += ["-c:a", "libmp3lame", "-b:a", MP3_RATES.get(p.fmt, "320k")]
        else:
            cmd += audio_encoder_args(out)
        return [*cmd, out], None
    dur = info.duration
    tts = "[1:a:0]" + ",".join(chain) + f",apad,atrim=duration={dur:.3f}"
    if p.replace or not info.has_audio:
        graph = f"{tts}[aout]"
    else:
        graph = (
            f"[0:a:0]aformat=sample_rates=48000:channel_layouts=stereo,volume={p.original_volume:.3f},"
            f"apad,atrim=duration={dur:.3f}[orig];{tts}[tts];"
            "[orig][tts]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.97[aout]"
        )
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        "-i",
        video,
        "-i",
        speech,
        "-filter_complex",
        graph,
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
    ]
    if p.video_out is VideoOut.COPY:
        cmd += ["-c:v", "copy"]
    else:
        crf = "18" if p.video_out is VideoOut.CRF18 else "23"
        cmd += ["-c:v", "libx264", "-crf", crf, "-preset", "medium", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-b:a", "256k", "-t", f"{dur:.3f}"]
    if out.lower().endswith((".mp4", ".mov", ".m4v")):
        cmd += ["-movflags", "+faststart"]
    return [*cmd, out], dur


def shift_srt(srt: str, seconds: float) -> str:
    """Move every SRT cue later by *seconds*."""

    def fix(m: re.Match[str]) -> str:
        h, mi, se, ms = (int(g) for g in m.groups())
        total = max(0, (h * 3600 + mi * 60 + se) * 1000 + ms + round(seconds * 1000))
        h, rem = divmod(total, 3_600_000)
        mi, rem = divmod(rem, 60_000)
        se, ms = divmod(rem, 1000)
        return f"{h:02d}:{mi:02d}:{se:02d},{ms:03d}"

    return re.sub(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", fix, srt)


@dataclass(frozen=True, slots=True, kw_only=True)
class VoiceoverJob:
    """Speak *text* with *voice*, then place it (alone or into *video*); with
    *subtitles*, an .srt beside the output, moved to where the speech starts."""

    text: str
    voice: tts_core.Voice
    placement: Placement
    video: str
    info: ff.MediaInfo | None
    subtitles: bool

    def run(self, _p: object, _c: object, ctx: TaskContext, *, out: str) -> int:
        speech = os.path.join(
            ctx.tempdir(), "speech" + (".mp3" if self.voice.engine == "edge" else ".wav")
        )
        ctx.log("Step 1/2: speaking the script…")
        srt = tts_core.synthesize(
            self.text, speech, self.voice, subtitles=self.subtitles, cancelled=ctx.cancelled
        )
        ctx.progress(0.3)
        ctx.log("Step 2/2: building the output…")
        cmd, dur = voiceover_cmd(speech, ctx.output(out), self.video, self.info, self.placement)
        ctx.span(0.3, 1.0).run(cmd, duration=dur)
        if self.subtitles and srt:
            shift = self.placement.pads[0] + self.placement.offset
            srt_path = os.path.splitext(out)[0] + ".srt"
            with open(srt_path, "w", encoding="utf-8") as fh:
                fh.write(shift_srt(srt, shift) if shift else srt)
            ctx.log(f"Subtitles saved: {os.path.basename(srt_path)}")
        return 0
