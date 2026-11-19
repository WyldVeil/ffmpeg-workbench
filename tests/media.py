"""
tests/media.py - Synthetic media fixtures generated with FFmpeg's lavfi sources.

Every test that needs real media uses these files, so nothing copyrighted or
large is ever committed.  Files are cached per directory; set the environment
variable ``CROSSFADER_TEST_MEDIA`` to reuse a pre-built directory between runs.
"""
from __future__ import annotations

import os
import shutil
import subprocess

# name -> (ffmpeg arguments placed after "-y", output file name)
_RECIPES: dict[str, tuple[list[str], str]] = {
    # 6 s 1280x720 30 fps H.264 + 440 Hz AAC stereo - the general-purpose clip
    "h264": ([
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-shortest",
    ], "h264_720p30_aac.mp4"),
    # second clip with different tone, for joins / crossfades
    "h264_b": ([
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000",
        "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-shortest",
    ], "h264_720p30_b.mp4"),
    # different resolution / frame rate / sample rate
    "h264_1080p25": ([
        "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=550:sample_rate=44100",
        "-t", "5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac", "2", "-shortest",
    ], "h264_1080p25_aac.mp4"),
    # vertical, 60 fps, NO audio stream
    "vertical_noaudio": ([
        "-f", "lavfi", "-i", "smptehdbars=size=1080x1920:rate=60",
        "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
    ], "vertical_1080x1920_60_noaudio.mp4"),
    # 10-bit HEVC + Opus in Matroska
    "hevc10": ([
        "-f", "lavfi", "-i", "mandelbrot=size=1280x720:rate=24",
        "-f", "lavfi", "-i", "anoisesrc=color=pink:sample_rate=48000:amplitude=0.3",
        "-t", "5", "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
        "-x265-params", "log-level=error",
        "-c:a", "libopus", "-b:a", "96k", "-shortest",
    ], "hevc10_720p24_opus.mkv"),
    # VP9 + Opus WebM at NTSC 29.97
    "vp9": ([
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30000/1001",
        "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000",
        "-t", "5", "-c:v", "libvpx-vp9", "-b:v", "300k", "-deadline", "realtime",
        "-c:a", "libopus", "-shortest",
    ], "vp9_360p2997_opus.webm"),
    # stereo MP3 music bed
    "mp3": ([
        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100",
        "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100",
        "-filter_complex", "[0][1]amerge=inputs=2,volume=0.5[a]", "-map", "[a]",
        "-t", "8", "-c:a", "libmp3lame", "-b:a", "192k",
    ], "tone_stereo.mp3"),
    # WAV with a 2 s silent gap in the middle (silence-removal tests)
    "wav_silence": ([
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2,apad=pad_dur=2",
        "-f", "lavfi", "-i", "sine=frequency=550:sample_rate=48000:duration=2",
        "-filter_complex", "[0][1]concat=n=2:v=0:a=1[a]", "-map", "[a]",
        "-c:a", "pcm_s16le",
    ], "speech_with_silence.wav"),
    # still image
    "png": ([
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=1",
        "-frames:v", "1",
    ], "still_1280x720.png"),
    # green-screen plate with a foreground patch
    "greenscreen": ([
        "-f", "lavfi", "-i", "color=c=0x00FF00:size=1280x720:rate=30",
        "-f", "lavfi", "-i", "testsrc2=size=400x300:rate=30",
        "-filter_complex", "[0][1]overlay=(W-w)/2:(H-h)/2[v]", "-map", "[v]",
        "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
    ], "greenscreen_720p30.mp4"),
}

_SRT = (
    "1\n00:00:00,500 --> 00:00:02,000\nHello <b>world</b> — ünïcödé\n\n"
    "2\n00:00:02,500 --> 00:00:04,000\nSecond line, with comma: 50% off!\n"
)


def _write_cube(path: str, size: int = 17) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('TITLE "warm"\nLUT_3D_SIZE %d\n' % size)
        for b in range(size):
            for g in range(size):
                for r in range(size):
                    fh.write("%.6f %.6f %.6f\n" % (
                        min(1.0, r / (size - 1) * 1.05), g / (size - 1),
                        b / (size - 1) * 0.95))


def ffmpeg_exe() -> str | None:
    return shutil.which("ffmpeg")


def build_media(directory: str) -> dict[str, str]:
    """Create (or reuse) every fixture in *directory*; return name -> path."""
    os.makedirs(directory, exist_ok=True)
    ff = ffmpeg_exe()
    out: dict[str, str] = {}
    for name, (args, fname) in _RECIPES.items():
        path = os.path.join(directory, fname)
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            if ff is None:
                raise RuntimeError("ffmpeg is required to build test media")
            subprocess.run(
                [ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                 *args, path],
                check=True, stdin=subprocess.DEVNULL)
        out[name] = path

    srt = os.path.join(directory, "subs.srt")
    if not os.path.isfile(srt):
        with open(srt, "w", encoding="utf-8") as fh:
            fh.write(_SRT)
    out["srt"] = srt

    cube = os.path.join(directory, "warm.cube")
    if not os.path.isfile(cube):
        _write_cube(cube)
    out["cube"] = cube

    # A copy whose name contains every character that breaks naive quoting
    # in shell strings, concat lists and filtergraph arguments.
    nasty = os.path.join(directory, "name with spaces & 'quotes' [x], 50%.mp4")
    if not os.path.isfile(nasty):
        shutil.copyfile(out["h264"], nasty)
    out["nasty"] = nasty
    return out


def default_media_dir() -> str:
    env = os.environ.get("CROSSFADER_TEST_MEDIA")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".media_cache")
