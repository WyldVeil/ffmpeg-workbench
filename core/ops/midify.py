"""
core/ops/midify.py - the MIDIfier (tabs/audio/midifier.py): notes found in a
recording (or a MIDI file as it is), played with FluidSynth, encoded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from core import ffmpeg as ff
from core import midi
from core.ops.shared import audio_encoder_args
from core.queue_manager import TaskContext


@dataclass(frozen=True, slots=True)
class Settings:
    threshold: float
    min_dur: float
    gain: float
    rate: int
    program: int


def encode_cmd(wav: str, out: str, length: float | None) -> list[str]:
    """The rendered *wav* encoded for *out*: cut to *length* with a one-second
    fade (FluidSynth adds a few seconds of release tail)."""
    cmd = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-i", wav]
    if length:
        cmd += ["-t", f"{length + 1.0:.3f}", "-af", f"afade=t=out:st={length:.3f}:d=1"]
    mp3 = out.lower().endswith(".mp3")
    return [
        *cmd,
        *(["-c:a", "libmp3lame", "-b:a", "256k"] if mp3 else audio_encoder_args(out)),
        out,
    ]


@dataclass(frozen=True, slots=True)
class MidifyJob:
    """Find the notes (unless it's MIDI already), play them with FluidSynth, encode."""

    src: str
    out: str
    is_midi: bool
    save_midi: bool
    fluidsynth: str
    soundfont: str
    length: float | None
    settings: Settings

    def run(self, _p: object, _c: object, ctx: TaskContext) -> int:
        job = ctx.tempdir()
        midi_path = self.src if self.is_midi else self._transcribe(ctx, job)
        ctx.log(f"Stage 2/3: playing it with {os.path.basename(self.soundfont)}…")
        wav = os.path.join(job, "rendered.wav")
        s = self.settings
        ctx.span(0.5, 0.8).run(
            midi.render_cmd(
                self.fluidsynth, self.soundfont, midi_path, wav, rate=s.rate, gain=s.gain
            )
        )
        if not os.path.isfile(wav) or os.path.getsize(wav) < 1000:
            raise RuntimeError("FluidSynth produced no audio.")
        ctx.log("Stage 3/3: encoding…")
        cmd = encode_cmd(wav, ctx.output(self.out), self.length)
        ctx.span(0.8, 1.0).run(cmd, duration=self.length)
        return 0

    def _transcribe(self, ctx: TaskContext, job: str) -> str:
        """Stage 1: the notes, as a MIDI file in *job* (and next to the output when asked)."""
        ctx.log("Stage 1/3: finding the notes…")
        with open(os.path.join(job, "graph.txt"), "w", encoding="utf-8") as fh:
            fh.write(midi.analysis_graph())
        ctx.span(0.0, 0.5).run(
            midi.analysis_cmd(self.src, "graph.txt"), duration=self.length, cwd=job
        )
        s = self.settings
        notes = midi.transcribe(midi.read_levels(job), threshold=s.threshold, min_dur=s.min_dur)
        if not notes:
            raise RuntimeError("No notes were found - try a lower note threshold.")
        midi_path = os.path.join(job, "transcription.mid")
        midi.write_midi(midi_path, notes, program=s.program)
        ctx.log(f"  {len(notes)} notes")
        if self.save_midi:
            final_mid = os.path.splitext(self.out)[0] + ".mid"
            midi.write_midi(ctx.output(final_mid), notes, program=s.program)
            ctx.log(f"  MIDI saved: {os.path.basename(final_mid)}")
        return midi_path
