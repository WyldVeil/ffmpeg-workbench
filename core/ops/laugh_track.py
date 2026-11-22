"""
core/ops/laugh_track.py - the Laugh Track Remover (tabs/audio/laugh_track.py):
the FFmpeg engine's filter chain and commands, and the AI engine's job
(audio-separator splits the sound; the dialogue stem goes back in).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from core import ffmpeg as ff
from core.queue_manager import TaskContext


@dataclass(frozen=True, slots=True, kw_only=True)
class Cleanup:
    """The FFmpeg engine's four steps, each on or off."""

    profile: bool = True  # noise reduction (afftdn)
    reduction: float = 12
    noise_floor: float = -40
    centre: bool = True  # keep the dialogue: the centre channel / dialoguenhance
    centre_mix: float = 0.5
    channels: int = 2
    eq: bool = True  # dip the band laughter lives in
    eq_depth: float = 6
    gate: bool = False  # duck what's below a threshold
    gate_threshold: float = -30
    gate_ratio: float = 4


def filter_chain(c: Cleanup) -> str | None:
    """The -af chain (None: no step is on)."""
    filters: list[str] = []
    if c.profile:
        filters.append(f"afftdn=nr={c.reduction:.0f}:nf={c.noise_floor:.0f}:tn=1")
    if c.centre:
        # 5.1/7.1 mixes carry the dialogue in the centre channel; on stereo,
        # dialoguenhance pulls the centre-panned voice forward
        if c.channels >= 6:
            filters.append("pan=stereo|c0=FC+0.2*FL+0.1*SL|c1=FC+0.2*FR+0.1*SR")
        elif c.channels == 2:
            filters.append(
                f"dialoguenhance=original={max(0.0, 1.0 - c.centre_mix):.2f}:enhance=2:voice=4"
            )
    if c.eq:
        filters.append(f"equalizer=f=2650:t=h:w=4700:g=-{c.eq_depth:.0f}")
    if c.gate:
        filters.append(
            f"acompressor=threshold={c.gate_threshold:.0f}dB:ratio={c.gate_ratio:.1f}:attack=5:release=200:makeup=2"
        )
    return ",".join(filters) if filters else None


def preview_cmd(src: str, af: str, title: str) -> list[str]:
    """The first 30 s through the chain, in a small player window."""
    return [
        ff.ffplay_path(),
        "-i",
        src,
        "-af",
        af,
        "-t",
        "30",
        "-window_title",
        title,
        "-x",
        "800",
        "-y",
        "450",
    ]


def _sound_out(video: bool, sound: str) -> list[str]:
    if video:
        return ["-map", "0:v:0", "-map", sound, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    return ["-map", sound, "-c:a", "pcm_s16le"]


def render_cmd(src: str, af: str, out: str, *, video: bool) -> list[str]:
    """The cleaned sound - with the untouched picture (*video*), or as a WAV."""
    tail = _sound_out(True, "0:a:0") if video else ["-map", "0:a:0", "-vn", "-c:a", "pcm_s16le"]
    return [ff.ffmpeg_path(), "-y", "-i", src, "-af", af, *tail, out]


_DIALOGUE_TAGS = ("(other)", "(vocals)", "(no crowd)", "(no_crowd)", "(speech)", "(dialog")
_CROWD_TAGS = ("(crowd)", "(laugh", "(instrumental)")


def dialogue_stem(stems: Sequence[str]) -> str | None:
    """Which of the separated files is the dialogue (by the model's naming), if it can tell."""
    pick = next((f for f in stems if any(tag in f.lower() for tag in _DIALOGUE_TAGS)), None)
    if pick is None and len(stems) == 2:
        pick = next((f for f in stems if not any(tag in f.lower() for tag in _CROWD_TAGS)), None)
    return pick


@dataclass(frozen=True, slots=True, kw_only=True)
class AiSeparation:
    """The AI engine: extract the sound, separate it with *model*, put the dialogue back."""

    src: str
    cli: str  # the audio-separator program
    model: str
    video: bool
    duration: float | None

    def run(self, ctx: TaskContext, out: str) -> int:
        job = ctx.tempdir()  # private: nothing outside it is ever touched
        wav = os.path.join(job, "input.wav")
        ctx.log("Step 1: extracting the audio…")
        extract = [
            ff.ffmpeg_path(),
            "-y",
            "-i",
            self.src,
            "-map",
            "0:a:0",
            "-vn",
            "-c:a",
            "pcm_s16le",
            wav,
        ]
        ctx.span(0.0, 0.1).run(extract, duration=self.duration)
        ctx.log(f"Step 2: separating with {self.model} (this can take a while)…")
        separate = [
            self.cli,
            wav,
            "--model_filename",
            self.model,
            "--output_dir",
            job,
            "--output_format",
            "WAV",
        ]
        res = ff.run(separate, on_line=ctx.log, cancel=ctx.cancelled)
        if res.cancelled:
            raise ff.CancelledError("cancelled")
        if res.returncode != 0:
            raise RuntimeError("The AI separator failed:\n" + "\n".join(res.log_tail[-6:]))
        stems = [f for f in os.listdir(job) if f.lower().endswith(".wav") and f != "input.wav"]
        pick = dialogue_stem(stems)
        if pick is None:
            raise RuntimeError(
                "Could not tell which separated stem is the dialogue: " + ", ".join(stems)
            )
        ctx.log(f"Step 3: building the final file from {pick}…")
        final = [
            ff.ffmpeg_path(),
            "-y",
            "-i",
            self.src,
            "-i",
            os.path.join(job, pick),
            *_sound_out(self.video, "1:a:0"),
        ]
        ctx.span(0.95, 1.0).run([*final, ctx.output(out)], duration=self.duration)
        return 0
