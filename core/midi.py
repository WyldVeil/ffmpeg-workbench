"""
core/midi.py  -  Audio -> MIDI transcription and MIDI rendering helpers

Transcription needs no Python packages: FFmpeg runs a bank of 84 semitone
band-pass filters (C1-B7) in one pass and prints each band's level for every
23 ms frame; the piano roll is then thresholded and peak-picked here and
written as a Standard MIDI File by the small writer below.

Rendering uses FluidSynth (bundled on Windows, the system package on Linux)
with a General MIDI SoundFont - GeneralUser GS is bundled with the app, and
system SoundFonts (FluidR3, TimGM6mb...) are found on Linux.
"""

from __future__ import annotations

import glob
import logging
import math
import os
import shutil
import sys

from core import ffmpeg as ff
from core.hardware import bundled_bin_dirs, find_binary

logger = logging.getLogger("crossfader.midi")

FIRST_NOTE = 24  # C1
N_BINS = 84  # 7 octaves -> B7
RATE = 11025
HOP = 256  # 23.2 ms frames

GM_PROGRAMS = [
    "Acoustic Grand Piano",
    "Bright Acoustic Piano",
    "Electric Grand Piano",
    "Honky-tonk Piano",
    "Electric Piano 1",
    "Electric Piano 2",
    "Harpsichord",
    "Clavinet",
    "Celesta",
    "Glockenspiel",
    "Music Box",
    "Vibraphone",
    "Marimba",
    "Xylophone",
    "Tubular Bells",
    "Dulcimer",
    "Drawbar Organ",
    "Percussive Organ",
    "Rock Organ",
    "Church Organ",
    "Reed Organ",
    "Accordion",
    "Harmonica",
    "Tango Accordion",
    "Acoustic Guitar (nylon)",
    "Acoustic Guitar (steel)",
    "Electric Guitar (jazz)",
    "Electric Guitar (clean)",
    "Electric Guitar (muted)",
    "Overdriven Guitar",
    "Distortion Guitar",
    "Guitar Harmonics",
    "Acoustic Bass",
    "Electric Bass (finger)",
    "Electric Bass (pick)",
    "Fretless Bass",
    "Slap Bass 1",
    "Slap Bass 2",
    "Synth Bass 1",
    "Synth Bass 2",
    "Violin",
    "Viola",
    "Cello",
    "Contrabass",
    "Tremolo Strings",
    "Pizzicato Strings",
    "Orchestral Harp",
    "Timpani",
    "String Ensemble 1",
    "String Ensemble 2",
    "Synth Strings 1",
    "Synth Strings 2",
    "Choir Aahs",
    "Voice Oohs",
    "Synth Choir",
    "Orchestra Hit",
    "Trumpet",
    "Trombone",
    "Tuba",
    "Muted Trumpet",
    "French Horn",
    "Brass Section",
    "Synth Brass 1",
    "Synth Brass 2",
    "Soprano Sax",
    "Alto Sax",
    "Tenor Sax",
    "Baritone Sax",
    "Oboe",
    "English Horn",
    "Bassoon",
    "Clarinet",
    "Piccolo",
    "Flute",
    "Recorder",
    "Pan Flute",
    "Blown Bottle",
    "Shakuhachi",
    "Whistle",
    "Ocarina",
    "Lead 1 (square)",
    "Lead 2 (sawtooth)",
    "Lead 3 (calliope)",
    "Lead 4 (chiff)",
    "Lead 5 (charang)",
    "Lead 6 (voice)",
    "Lead 7 (fifths)",
    "Lead 8 (bass + lead)",
    "Pad 1 (new age)",
    "Pad 2 (warm)",
    "Pad 3 (polysynth)",
    "Pad 4 (choir)",
    "Pad 5 (bowed)",
    "Pad 6 (metallic)",
    "Pad 7 (halo)",
    "Pad 8 (sweep)",
]


def note_hz(note: int) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


# ── Analysis ──────────────────────────────────────────────────────────────────


def analysis_graph(
    stream: int = 0,
    *,
    n_bins: int = N_BINS,
    first: int = FIRST_NOTE,
    rate: int = RATE,
    hop: int = HOP,
) -> str:
    """Filter graph writing band levels to b00.txt ... (run with cwd = job folder)."""
    labels = "".join(f"[s{i}]" for i in range(n_bins))
    parts = [
        f"[0:a:{stream}]aformat=sample_fmts=dbl:channel_layouts=mono,aresample={rate},"
        f"asetnsamples=n={hop}:p=0,asplit={n_bins}{labels}"
    ]
    for i in range(n_bins):
        bp = f"bandpass=f={note_hz(first + i):.3f}:width_type=q:w=17:precision=f64"
        parts.append(
            f"[s{i}]{bp},{bp},astats=metadata=1:reset=1:measure_perchannel=none:"
            f"measure_overall=RMS_level,ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:"
            f"file=b{i:02d}.txt" + (",anullsink" if i < n_bins - 1 else "[out]")
        )
    return ";\n".join(parts)


def analysis_cmd(src: str, graph_file: str) -> list:
    return [
        ff.ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        src,
        "-/filter_complex",
        graph_file,
        "-map",
        "[out]",
        "-f",
        "null",
        "-",
    ]


def read_levels(folder: str, n_bins: int = N_BINS) -> list:
    """Per-band lists of dB levels written by the analysis pass."""
    levels = []
    for i in range(n_bins):
        vals = []
        with open(os.path.join(folder, f"b{i:02d}.txt"), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("lavfi.astats.Overall.RMS_level="):
                    try:
                        v = float(line.split("=", 1)[1])
                    except ValueError:
                        v = -120.0
                    vals.append(-120.0 if math.isinf(v) or math.isnan(v) else max(-120.0, v))
        levels.append(vals)
    return levels


def transcribe(
    levels: list,
    *,
    hop_s: float = HOP / RATE,
    threshold: float = 0.5,
    min_dur: float = 0.05,
    first: int = FIRST_NOTE,
) -> list:
    """Piano roll -> [(pitch, start_s, end_s, velocity)].

    Levels are scaled to 0..1 over an 80 dB range below the loudest band;
    a band sounds a note where it is above *threshold* and is the local
    maximum (within 0.05) of its +-2 semitone neighbourhood.  Frames where
    more than a quarter of the bands are lit (clicks, drum hits) are skipped."""
    n_frames = min((len(r) for r in levels), default=0)
    if not n_frames:
        return []
    top = max(max(r[:n_frames]) for r in levels)
    norm = [[min(1.0, max(0.0, (v - top + 80.0) / 80.0)) for v in r[:n_frames]] for r in levels]
    n_bins = len(norm)
    on = [bytearray(n_frames) for _ in range(n_bins)]
    broadband = max(12, n_bins // 4)  # clicks / drum hits light up many bands at once
    for f in range(n_frames):
        col = [norm[p][f] for p in range(n_bins)]
        if sum(1 for v in col if v >= threshold) > broadband:
            continue
        for p in range(n_bins):
            v = col[p]
            if v >= threshold and v >= max(col[max(0, p - 2) : p + 3]) - 0.05:
                on[p][f] = 1
    min_frames = max(1, round(min_dur / hop_s))
    notes = []
    for p in range(n_bins):
        pitch = first + p
        if pitch > 127:
            break
        row, f = on[p], 0
        while f < n_frames:
            if row[f]:
                g = f
                while g < n_frames and row[g]:
                    g += 1
                if g - f >= min_frames:
                    energy = sum(norm[p][f:g]) / (g - f)
                    notes.append(
                        (pitch, f * hop_s, g * hop_s, int(min(127, max(30, energy * 127))))
                    )
                f = g
            else:
                f += 1
    notes.sort(key=lambda n: (n[1], n[0]))
    return notes


# ── Standard MIDI File writer ─────────────────────────────────────────────────


def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def write_midi(path: str, notes, *, program: int = 0, bpm: float = 120.0, ppq: int = 480) -> None:
    """Single-track (format 0) MIDI file on channel 1."""
    ticks_per_s = ppq * bpm / 60.0
    events = []
    for pitch, start, end, vel in notes:
        a = round(start * ticks_per_s)
        b = max(a + 1, round(end * ticks_per_s))
        events.append((b, 0, bytes([0x80, pitch, 0])))  # offs before ons at the same tick
        events.append((a, 1, bytes([0x90, pitch, max(1, min(127, vel))])))
    events.sort(key=lambda e: (e[0], e[1]))
    track = bytearray()
    track += _vlq(0) + b"\xff\x51\x03" + round(60_000_000 / bpm).to_bytes(3, "big")
    track += _vlq(0) + bytes([0xC0, max(0, min(127, program))])
    last = 0
    for tick, _order, data in events:
        track += _vlq(tick - last) + data
        last = tick
    track += _vlq(0) + b"\xff\x2f\x00"
    with open(path, "wb") as fh:
        fh.write(
            b"MThd"
            + (6).to_bytes(4, "big")
            + (0).to_bytes(2, "big")
            + (1).to_bytes(2, "big")
            + ppq.to_bytes(2, "big")
        )
        fh.write(b"MTrk" + len(track).to_bytes(4, "big") + bytes(track))


# ── FluidSynth + SoundFonts ───────────────────────────────────────────────────


def find_fluidsynth() -> str | None:
    found = find_binary("fluidsynth")
    if found:
        return found
    exe = "fluidsynth.exe" if sys.platform == "win32" else "fluidsynth"
    for d in bundled_bin_dirs():
        for cand in (os.path.join(d, "fluidsynth", exe), os.path.join(d, "fluidsynth", "bin", exe)):
            if os.path.isfile(cand):
                return cand
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", "")):
            cand = os.path.join(base, "FluidSynth", "bin", exe)
            if base and os.path.isfile(cand):
                return cand
    return shutil.which("fluidsynth")


def soundfont_dirs() -> list:
    dirs = [os.path.join(d, "soundfonts") for d in bundled_bin_dirs()]
    from core.hardware import APP_ROOT

    dirs.append(os.path.join(APP_ROOT, "soundfonts"))
    try:
        from core.paths import data_dir

        dirs.append(os.path.join(data_dir(), "soundfonts"))  # user-added SoundFonts
    except Exception:
        logger.debug("soundfont_dirs: best-effort step failed", exc_info=True)
    if sys.platform == "win32":
        dirs.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "soundfonts"))
    else:
        dirs += [
            os.path.expanduser("~/.local/share/soundfonts"),
            os.path.expanduser("~/.local/share/sounds/sf2"),
            "/usr/share/soundfonts",
            "/usr/share/sounds/sf2",
            "/usr/local/share/soundfonts",
            "/usr/share/sounds/sf3",
        ]
    seen, out = set(), []
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def find_soundfonts() -> list:
    """All .sf2/.sf3 files found, best General MIDI banks first."""
    files = []
    for d in soundfont_dirs():
        for pattern in ("*.sf2", "*.sf3", "*.SF2"):
            files += glob.glob(os.path.join(d, pattern))

    def rank(p):
        n = os.path.basename(p).lower()
        return (
            0
            if "generaluser" in n
            else 1
            if "fluidr3" in n
            else 2
            if "musescore" in n
            else 3
            if "timgm" in n
            else 4,
            n,
        )

    return sorted(dict.fromkeys(files), key=rank)


def render_cmd(
    fluidsynth: str, soundfont: str, midi: str, wav: str, *, rate: int = 44100, gain: float = 0.6
) -> list:
    return [fluidsynth, "-ni", "-q", "-g", f"{gain:g}", "-r", str(rate), "-F", wav, soundfont, midi]
