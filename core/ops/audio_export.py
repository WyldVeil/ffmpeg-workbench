"""
core/ops/audio_export.py - the Audio Converter (tabs/audio/exporter.py): formats,
codec arguments, the filter chain (trim, silence, volume, fades, ReplayGain,
loudness or peak normalisation) and the export job.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from core import ffmpeg as ff
from core.queue_manager import TaskContext


class AudioFormat(StrEnum):
    MP3 = "mp3"
    AAC = "aac"
    VORBIS = "vorbis"
    OPUS = "opus"
    WAV = "wav"
    FLAC = "flac"
    ALAC = "alac"
    AIFF = "aiff"
    WMA = "wma"
    AC3 = "ac3"


@dataclass(frozen=True, slots=True, kw_only=True)
class FormatDef:
    """What the converter offers for one output format."""

    label: str
    ext: str
    codec: tuple[str, ...]  # empty: set from the bit depth (WAV, AIFF)
    lossy: bool
    sample_rates: tuple[int, ...]
    bitrates: tuple[str, ...] = ()  # lossy formats
    default_br: str = ""
    vbr_flag: str | None = None  # the encoder's quality option, if it has one
    vbr_range: tuple[int, int] = (0, 9)
    vbr_default: int = 0
    bit_depths: Mapping[str, str] = field(
        default_factory=dict[str, str]
    )  # lossless: label -> sample format
    default_depth: str = "16-bit"
    compression_range: tuple[int, int] = (0, 12)  # FLAC
    compression_default: int = 5


FORMAT_DEFS: dict[AudioFormat, FormatDef] = {
    AudioFormat.MP3: FormatDef(
        label="MP3",
        ext=".mp3",
        codec=("-c:a", "libmp3lame"),
        lossy=True,
        sample_rates=(8000, 11025, 16000, 22050, 32000, 44100, 48000),
        bitrates=("64k", "96k", "128k", "160k", "192k", "224k", "256k", "320k"),
        default_br="320k",
        vbr_flag="-q:a",
        vbr_range=(0, 9),
        vbr_default=2,
    ),
    AudioFormat.AAC: FormatDef(
        label="AAC (M4A)",
        ext=".m4a",
        codec=("-c:a", "aac"),
        lossy=True,
        sample_rates=(22050, 32000, 44100, 48000, 96000),
        bitrates=("64k", "96k", "128k", "160k", "192k", "256k", "320k"),
        default_br="256k",
    ),
    AudioFormat.VORBIS: FormatDef(
        label="OGG Vorbis",
        ext=".ogg",
        codec=("-c:a", "libvorbis"),
        lossy=True,
        sample_rates=(8000, 11025, 16000, 22050, 32000, 44100, 48000),
        bitrates=("64k", "96k", "128k", "160k", "192k", "256k", "320k"),
        default_br="192k",
        vbr_flag="-q:a",
        vbr_range=(0, 10),
        vbr_default=6,
    ),
    AudioFormat.OPUS: FormatDef(
        label="Opus",
        ext=".opus",
        codec=("-c:a", "libopus"),
        lossy=True,
        sample_rates=(8000, 12000, 16000, 24000, 48000),
        bitrates=(
            "32k",
            "48k",
            "64k",
            "96k",
            "128k",
            "160k",
            "192k",
            "256k",
            "320k",
            "450k",
            "510k",
        ),
        default_br="128k",
    ),
    AudioFormat.WAV: FormatDef(
        label="WAV",
        ext=".wav",
        codec=(),
        lossy=False,
        sample_rates=(8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000),
        bit_depths={
            "16-bit": "pcm_s16le",
            "24-bit": "pcm_s24le",
            "32-bit": "pcm_s32le",
            "32-bit float": "pcm_f32le",
        },
        default_depth="16-bit",
    ),
    AudioFormat.FLAC: FormatDef(
        label="FLAC",
        ext=".flac",
        codec=("-c:a", "flac"),
        lossy=False,
        sample_rates=(8000, 16000, 22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000),
        bit_depths={"16-bit": "s16", "24-bit": "s32:24", "32-bit": "s32"},
        default_depth="16-bit",
        compression_range=(0, 12),
        compression_default=5,
    ),
    AudioFormat.ALAC: FormatDef(
        label="ALAC (M4A)",
        ext=".m4a",
        codec=("-c:a", "alac"),
        lossy=False,
        sample_rates=(44100, 48000, 88200, 96000, 176400, 192000),
        bit_depths={"16-bit": "s16p", "24-bit": "s32p:24"},
        default_depth="16-bit",
    ),
    AudioFormat.AIFF: FormatDef(
        label="AIFF",
        ext=".aiff",
        codec=(),
        lossy=False,
        sample_rates=(22050, 32000, 44100, 48000, 88200, 96000),
        bit_depths={"16-bit": "pcm_s16be", "24-bit": "pcm_s24be", "32-bit": "pcm_s32be"},
        default_depth="16-bit",
    ),
    AudioFormat.WMA: FormatDef(
        label="WMA",
        ext=".wma",
        codec=("-c:a", "wmav2"),
        lossy=True,
        sample_rates=(22050, 32000, 44100, 48000),
        bitrates=("64k", "96k", "128k", "160k", "192k", "256k", "320k"),
        default_br="192k",
    ),
    AudioFormat.AC3: FormatDef(
        label="AC3",
        ext=".ac3",
        codec=("-c:a", "ac3"),
        lossy=True,
        sample_rates=(32000, 44100, 48000),
        bitrates=("128k", "192k", "224k", "256k", "320k", "384k", "448k", "640k"),
        default_br="384k",
    ),
}

#: what the drop-down shows for each format
FORMATS = {fmt: spec.label for fmt, spec in FORMAT_DEFS.items()}


class Normalise(StrEnum):
    NONE = "none"
    LOUDNESS = "loudness"
    PEAK = "peak"


NORMALISE = {
    Normalise.NONE: "None",
    Normalise.LOUDNESS: "EBU R128 (loudnorm)",
    Normalise.PEAK: "Peak normalize",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Export:
    """Everything the export needs, read from the page once.  One of
    *size_bytes* (a size limit), *vbr* (a VBR quality) or *bitrate* is set for
    lossy formats; *depth* (a sample format, "s32:24") for lossless ones."""

    fmt: AudioFormat
    start: float = 0.0
    end: float | None = None
    volume: float = 0.0  # dB
    fade_in: float = 0.0
    fade_out: float = 0.0
    norm: Normalise = Normalise.NONE
    lufs: float = -16.0
    tp: float = -1.0  # true peak (loudness) or the peak level (peak)
    strip_silence: bool = False
    replay_gain: bool = False
    strip_meta: bool = False
    strip_art: bool = False
    tags: Mapping[str, str] = field(default_factory=dict[str, str])
    sample_rate: str = ""  # "" keeps the source's
    channels: str = ""
    size_bytes: float | None = None
    vbr: str | None = None
    bitrate: str | None = None
    depth: str = ""
    compression: str = "5"


def codec_args(e: Export, seconds: float) -> list[str]:
    fmt = e.fmt
    fd = FORMAT_DEFS[fmt]
    if fd.lossy:
        args = list(fd.codec)
        if e.size_bytes is not None:
            lo, hi = BITRATE_LIMITS_K.get(fmt, (8, 640))
            kbps = int(e.size_bytes * 8 * 0.97 / max(seconds, 0.1) / 1000)
            if kbps < lo:
                raise ValueError(
                    f"{FORMATS[fmt]} can't get this short enough for that size "
                    f"(it would need {kbps} kbps; the minimum is {lo})."
                )
            args += ["-b:a", f"{min(kbps, hi)}k"]
        elif e.vbr is not None and fd.vbr_flag:
            args += [fd.vbr_flag, e.vbr]
        else:
            args += ["-b:a", e.bitrate or "192k"]
        return args
    depth = e.depth
    if fmt in (AudioFormat.WAV, AudioFormat.AIFF):
        return ["-c:a", depth or ("pcm_s16le" if fmt is AudioFormat.WAV else "pcm_s16be")]
    args = list(fd.codec)
    if depth:
        sample_fmt, _, bits = depth.partition(":")
        args += ["-sample_fmt", sample_fmt] + (["-bits_per_raw_sample", bits] if bits else [])
    if fmt is AudioFormat.FLAC:
        args += ["-compression_level", e.compression]
    return args


def base_chain(e: Export, length: float, keep: tuple[float, float] | None = None) -> list[str]:
    """Filters before normalisation: silence trim, volume, fades (no full-file buffering)."""
    chain: list[str] = []
    a, b = keep if keep else (0.0, length)
    if keep:
        chain += [f"atrim=start={a:.3f}:end={b:.3f}", "asetpts=PTS-STARTPTS"]
    final = max(0.01, b - a)
    if e.volume:
        chain.append(f"volume={e.volume:g}dB")
    if e.fade_in > 0:
        chain.append(f"afade=t=in:st=0:d={min(e.fade_in, final):.3f}")
    if e.fade_out > 0:
        d = min(e.fade_out, final)
        chain.append(f"afade=t=out:st={final - d:.3f}:d={d:.3f}")
    return chain


BITRATE_LIMITS_K = {
    AudioFormat.MP3: (8, 320),
    AudioFormat.AAC: (16, 512),
    AudioFormat.VORBIS: (45, 500),
    AudioFormat.OPUS: (6, 510),
    AudioFormat.WMA: (24, 320),
    AudioFormat.AC3: (32, 640),
}
COVER_ART_EXTS = (".mp3", ".flac", ".m4a")


def _lines(log: Sequence[str] | str) -> list[str]:
    """A log as lines, whether given as lines or as one text."""
    return log.splitlines() if isinstance(log, str) else list(log)


def silence_bounds(
    log: Sequence[str] | str, length: float, edge: float = 0.05
) -> tuple[float, float]:
    """(start, end) to keep after dropping leading/trailing silence found by silencedetect."""
    starts: list[float] = []
    ends: list[float] = []
    for line in _lines(log):
        if "silence_start:" in line:
            starts.append(float(line.split("silence_start:")[1].split()[0]))
        elif "silence_end:" in line:
            ends.append(float(line.split("silence_end:")[1].split()[0].rstrip("|")))
    a, b = 0.0, length
    if starts and starts[0] <= edge and ends:
        a = ends[0]
    if starts and (len(ends) < len(starts) or ends[-1] >= length - edge):
        b = starts[-1]
    if b - a < 0.05:  # all silent: keep everything rather than nothing
        return 0.0, length
    return a, b


def parse_replaygain(log: Sequence[str] | str) -> tuple[float, float]:
    gain, peak = 0.0, 0.0
    for line in _lines(log):
        if "track_gain =" in line:
            gain = float(line.split("track_gain =")[1].split("dB")[0])
        elif "track_peak =" in line:
            peak = float(line.split("track_peak =")[1].split()[0])
    return gain, peak


def parse_max_volume(log: Sequence[str] | str) -> float:
    for line in reversed(_lines(log)):
        if "max_volume:" in line:
            return float(line.split("max_volume:")[1].split("dB")[0])
    raise RuntimeError("Could not measure the peak level.")


# ── the export job's steps ────────────────────────────────────────────────────


def _input_args(src: str, start: float, end: float | None) -> list[str]:
    inp = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y"]
    inp += ["-ss", f"{start:.3f}"] if start > 0 else []
    inp += ["-to", f"{end:.3f}"] if end is not None else []
    return [*inp, "-i", src]


def _analysis(
    ctx: TaskContext, inp: list[str], af: str, length: float, span: tuple[float, float]
) -> list[str]:
    """Run the audio through *af* without writing anything; its log."""
    cmd = [*inp, "-map", "0:a:0", "-af", af, "-f", "null", "-"]
    return ctx.span(*span).run(cmd, duration=length, tail=200000).log_tail


def _silence_kept(ctx: TaskContext, inp: list[str], length: float) -> tuple[float, float]:
    ctx.log("Finding leading and trailing silence…")
    keep = silence_bounds(
        _analysis(ctx, inp, "silencedetect=n=-50dB:d=0.1", length, (0, 0.2)), length
    )
    ctx.log(f"Keeping {keep[0]:.2f}s → {keep[1]:.2f}s")
    return keep


def _replay_gain(ctx: TaskContext, inp: list[str], chain: list[str], length: float) -> str:
    ctx.log("Measuring ReplayGain…")
    gain_db, peak = parse_replaygain(
        _analysis(ctx, inp, ",".join([*chain, "replaygain"]), length, (0.2, 0.4))
    )
    if peak > 0:  # never push the peak above -1 dBFS
        gain_db = min(gain_db, -20 * math.log10(peak) - 1.0)
    ctx.log(f"ReplayGain: {gain_db:+.2f} dB")
    return f"volume={gain_db:.2f}dB"


def _levelling(
    ctx: TaskContext,
    inp: list[str],
    chain: list[str],
    e: Export,
    length: float,
    rate: int,
) -> str | None:
    """The loudness (EBU R128, two passes) or peak normalisation filter, if asked for."""
    from core import loudness

    if e.norm is Normalise.LOUDNESS:
        ctx.log("Measuring loudness (pass 1 of 2)…")
        probe = f"loudnorm=I={e.lufs}:TP={e.tp}:LRA=11:print_format=json"
        meas = loudness.parse_measurement(
            _analysis(ctx, inp, ",".join([*chain, probe]), length, (0.4, 0.6))
        )
        return loudness.normalise_filter(e.lufs, e.tp, 11, meas, rate)
    if e.norm is Normalise.PEAK:
        ctx.log("Measuring the peak level…")
        peak = parse_max_volume(
            _analysis(ctx, inp, ",".join([*chain, "volumedetect"]), length, (0.4, 0.6))
        )
        ctx.log(f"Peak {peak:.1f} dBFS → {e.tp:.1f} dBFS")
        return f"volume={e.tp - peak:.2f}dB"
    return None


def _output_options(e: Export, chain: list[str], out: str) -> list[str]:
    """Sample rate, channels, filters, tags and container flags."""
    cmd = ["-ar", e.sample_rate] if e.sample_rate else []
    cmd += ["-ac", e.channels] if e.channels else []
    cmd += ["-af", ",".join(chain)] if chain else []
    cmd += ["-map_metadata", "-1"] if e.strip_meta else []
    for key, val in e.tags.items():
        cmd += ["-metadata", f"{key}={val}"]
    return cmd + (["-movflags", "+faststart"] if out.lower().endswith(".m4a") else [])


@dataclass(frozen=True, slots=True, kw_only=True)
class ExportJob:
    """The queued export: analyse (silence, ReplayGain, loudness) when asked, then encode."""

    src: str
    export: Export
    end: float | None
    length: float
    cover: int | None  # the attached picture's stream, kept with the sound
    rate: int  # output sample rate, for the loudness filter

    def run(self, _p: object, _c: object, ctx: TaskContext, *, out: str) -> int:
        e = self.export
        inp = _input_args(self.src, e.start, self.end)
        keep = _silence_kept(ctx, inp, self.length) if e.strip_silence else None
        seg_len = keep[1] - keep[0] if keep else self.length
        chain = base_chain(e, self.length, keep)
        if e.replay_gain:
            chain.append(_replay_gain(ctx, inp, chain, self.length))
        level = _levelling(ctx, inp, chain, e, self.length, self.rate)
        chain += [level] if level else []
        cmd = [*inp, "-map", "0:a:0"]
        if self.cover is not None:
            cmd += ["-map", f"0:{self.cover}", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
        cmd += [*codec_args(e, seg_len), *_output_options(e, chain, out)]
        ctx.log("Encoding…")
        analysed = e.norm is not Normalise.NONE or e.replay_gain or e.strip_silence
        ctx.span(0.6 if analysed else 0.0, 1.0).run([*cmd, ctx.output(out)], duration=seg_len)
        return 0
