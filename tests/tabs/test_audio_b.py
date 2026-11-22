"""Audio tools, part 2: Dynamics, Mixer, Ducker, Voice Isolation, Laugh Track, TTS."""

import os
import shutil
import subprocess

import pytest

from core import ffmpeg as ff


@pytest.fixture(scope="module")
def ui():
    from tests.gui_harness import Harness

    return Harness.instance()


def _ok(ui):
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    assert not ui.dialogs.warnings(), ui.dialogs.warnings()


def _volume(path):
    """(mean, max) dB of the first audio stream."""
    r = subprocess.run(
        [
            ff.ffmpeg_path(),
            "-hide_banner",
            "-nostats",
            "-i",
            path,
            "-map",
            "0:a:0",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    mean = float(r.stderr.split("mean_volume:")[1].split("dB")[0])
    peak = float(r.stderr.split("max_volume:")[1].split("dB")[0])
    return mean, peak


def test_pure_helpers(tmp_path):
    from core import tts
    from tabs.audio import karaoke, voice_isolation
    from tabs.audio.tts import _shift_srt

    assert (
        _shift_srt("1\n00:00:01,500 --> 00:00:59,900\nHi\n", 0.25)
        == "1\n00:00:01,750 --> 00:01:00,150\nHi\n"
    )
    assert tts._pct("+15%") == 15 and tts._pct("-4Hz") == -4
    f = voice_isolation.denoise_filter("neural", model="/m/sh.rnnn", blend=0.25)
    assert f.startswith("highpass=f=80,arnndn=model=") and f.endswith(":mix=0.75")
    assert (
        voice_isolation.denoise_filter("fft", floor_db=-40, reduction_db=20, rumble=False)
        == "afftdn=nf=-40:nr=20:tn=1"
    )
    for name in voice_isolation.MODELS:
        assert os.path.isfile(os.path.join(voice_isolation.models_dir(), name)), name
    assert "[side][bass]amix" in karaoke.vocal_remove_filter()


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_dynamics_default_chain(ui, media, outdir):
    ui.reset()
    tab = ui.open("Audio Dynamics")
    tab.src_var.set(media["h264"])
    out = str(outdir / "dyn.mp4")
    tab.out_var.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.has_video and info.has_audio
    assert info.duration == pytest.approx(ff.duration_of(media["h264"]), abs=0.15)


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("fmt", ["AAC 256k  (keep video)", "WAV  (audio only)"])
def test_mixer_two_tracks(ui, media, outdir, fmt):
    ui.reset()
    tab = ui.open("Audio Mixer")
    tab.vid_var.set(media["h264"])
    while len(tab.tracks) < 2:
        tab._add_track()
    a, b = tab.tracks[:2]
    a.path_var.set(media["mp3"])
    a.offset_var.set("1.5")
    a.fadein_var.set("0.5")
    a.pan_var.set(-0.5)
    b.path_var.set(media["wav_silence"])
    b.loop_var.set(True)
    for extra in tab.tracks[2:]:
        extra.enabled.set(False)
    tab.out_fmt_var.set(fmt)
    out = str(outdir / ("mix.mp4" if "keep" in fmt else "mix.wav"))
    tab.out_var.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.has_audio and info.require_audio().channels == 2
    assert info.has_video == ("keep" in fmt)
    assert info.duration == pytest.approx(ff.duration_of(media["h264"]), abs=0.15)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_ducker_keeps_stereo_music(ui, media, outdir):
    ui.reset()
    tab = ui.open("Music Ducker")
    tab._src_var.set(media["h264"])
    tab._music_var.set(media["mp3"])
    out = str(outdir / "ducked.mp4")
    tab._out_var.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.require_audio().channels == 2
    assert info.duration == pytest.approx(ff.duration_of(media["h264"]), abs=0.15)


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("method", ["neural", "fft"])
def test_voice_isolation(ui, media, outdir, method):
    ui.reset()
    tab = ui.open("Voice Isolation")
    tab.src_var.set(media["wav_silence"])
    tab.method_var.set(method)
    out = str(outdir / f"clean_{method}.wav")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.has_audio and not info.has_video
    assert info.duration == pytest.approx(ff.duration_of(media["wav_silence"]), abs=0.1)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_laugh_track_ffmpeg_engine(ui, media, outdir):
    ui.reset()
    tab = ui.open("Laugh Track Remover")
    tab._src_var.set(media["h264"])
    tab._engine_var.set("ffmpeg")
    out = str(outdir / "nolaughs.mp4")
    tab._out_var.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.has_video and info.has_audio


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_tts_build_cmd_video_keeps_length(ui, media, outdir, tmp_path):
    """The spoken track is placed at the start time and the video keeps its full length."""
    ui.reset()
    tab = ui.open("TTS Voice-Over")
    speech = str(tmp_path / "speech.wav")
    subprocess.run(
        [
            ff.ffmpeg_path(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=f=440:d=1.5",
            "-ac",
            "1",
            speech,
        ],
        check=True,
    )
    tab._vid_path = media["h264"]
    info = ff.probe(media["h264"])
    for mix in ("mix", "replace"):
        out = str(outdir / f"tts_{mix}.mp4")
        cmd, _dur = tab._build_cmd(
            speech,
            out,
            info,
            {
                "mode": "video",
                "pads": (0.0, 0.3),
                "offset": 2.0,
                "vol": 1.0,
                "normalise": False,
                "fmt": "",
                "mix": mix,
                "orig_vol": 0.5,
                "vid_fmt": "Copy video stream",
            },
        )
        subprocess.run(cmd, check=True, capture_output=True)
        res = ff.probe(out, use_cache=False)
        assert res.duration == pytest.approx(info.duration, abs=0.15)
        # silent before the 2 s start in replace mode
        if mix == "replace":
            head = str(tmp_path / "head.wav")
            subprocess.run(
                [ff.ffmpeg_path(), "-y", "-loglevel", "error", "-i", out, "-t", "1.8", "-vn", head],
                check=True,
            )
            assert _volume(head)[1] < -60


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.skipif(
    not (shutil.which("espeak-ng") or shutil.which("espeak")), reason="no offline voice"
)
def test_tts_offline_standalone(ui, outdir):
    from tabs.audio import tts as tts_tab

    ui.reset()
    tab = ui.open("TTS Voice-Over")
    tab._engine_var.set(tts_tab.ENGINE_SYSTEM)
    tab.text_box.delete("1.0", "end")
    tab.text_box.insert("1.0", "Testing the offline voice. One, two, three.")
    tab.mode_var.set("standalone")
    tab.audio_fmt_var.set("WAV")
    out = str(outdir / "speech.wav")
    tab.out_var.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.has_audio and info.duration > 1.5


def test_converter_helpers():
    from tabs.audio import exporter

    log = [
        "[silencedetect @ 0x1] silence_start: 0",
        "[silencedetect @ 0x1] silence_end: 1.25 | silence_duration: 1.25",
        "[silencedetect @ 0x1] silence_start: 4.5",
        "[silencedetect @ 0x1] silence_end: 5 | silence_duration: 0.5",
        "[silencedetect @ 0x1] silence_start: 8.75",
    ]
    assert exporter.silence_bounds(log, 10.0) == (1.25, 8.75)
    assert exporter.silence_bounds(["silence_start: 0", "silence_end: 10"], 10.0) == (0.0, 10.0)
    assert exporter.parse_replaygain(
        [
            "[Parsed_replaygain_0 @ 0x1] track_gain = +9.89 dB",
            "[Parsed_replaygain_0 @ 0x1] track_peak = 0.060887",
        ]
    ) == (9.89, 0.060887)
    cfg = {"fmt": "FLAC", "depth": "s32:24", "compression": "8"}
    assert exporter.AudioExporterTab.codec_args(cfg, 10) == [
        "-c:a",
        "flac",
        "-sample_fmt",
        "s32",
        "-bits_per_raw_sample",
        "24",
        "-compression_level",
        "8",
    ]
    assert exporter.AudioExporterTab.codec_args({"fmt": "MP3", "size_bytes": 1_000_000}, 60) == [
        "-c:a",
        "libmp3lame",
        "-b:a",
        "129k",
    ]
    with pytest.raises(ValueError):
        exporter.AudioExporterTab.codec_args({"fmt": "MP3", "size_bytes": 1000}, 600)
    chain = exporter.AudioExporterTab.base_chain(
        {"volume": 0, "fade_in": 1, "fade_out": 2}, 10.0, (1.0, 9.0)
    )
    assert chain[-1] == "afade=t=out:st=6.000:d=2.000"


def _converter(ui, src, fmt, out, **opts):
    ui.reset()
    tab = ui.open("Audio Converter")
    tab.src_var.set(src)
    tab.fmt_var.set(fmt)
    tab._on_format_change()
    for name, value in opts.items():
        getattr(tab, name).set(value)
    tab.out_var.set(out)
    tab._export()
    ui.wait_idle(120)
    _ok(ui)
    return ff.probe(out, use_cache=False)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_converter_trim_is_start_to_end(ui, media, outdir):
    info = _converter(
        ui, media["h264"], "WAV", str(outdir / "trim.wav"), trim_start="1", trim_end="3"
    )
    assert info.duration == pytest.approx(2.0, abs=0.05) and not info.has_video


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    "fmt,ext,want", [("FLAC", ".flac", ("s32", 24)), ("ALAC (M4A)", ".m4a", ("s32p", 24))]
)
def test_converter_24_bit(ui, media, outdir, fmt, ext, want):
    out = str(outdir / f"deep{ext}")
    _converter(ui, media["mp3"], fmt, out, depth_var="24-bit")
    r = subprocess.run(
        [
            ff.ffprobe_path(),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_fmt,bits_per_raw_sample",
            "-of",
            "csv=p=0",
            out,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    fmt_name, bits = r.stdout.strip().split(",")[:2]
    assert (fmt_name, int(bits)) == want


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_converter_keeps_cover_and_rate_when_normalising(ui, media, outdir, tmp_path):
    src = str(tmp_path / "with_cover.mp3")
    subprocess.run(
        [
            ff.ffmpeg_path(),
            "-y",
            "-loglevel",
            "error",
            "-i",
            media["mp3"],
            "-i",
            media["png"],
            "-map",
            "0:a",
            "-map",
            "1:v",
            "-c:a",
            "copy",
            "-c:v",
            "mjpeg",
            "-disposition:v",
            "attached_pic",
            "-metadata",
            "title=Song",
            src,
        ],
        check=True,
    )
    rate = ff.probe(src).require_audio().sample_rate
    out = str(outdir / "norm.flac")
    info = _converter(ui, src, "FLAC", out, norm_var="EBU R128 (loudnorm)")
    assert info.require_audio().sample_rate == rate  # no silent 192 kHz upsample
    assert any(s.attached_pic for s in info.streams)  # cover art kept
    peak_out = str(outdir / "peak.wav")
    _converter(ui, src, "WAV", peak_out, norm_var="Peak normalize", tp_var="-3")
    assert _volume(peak_out)[1] == pytest.approx(-3.0, abs=0.3)


def test_midi_writer_and_transcriber(tmp_path):
    from core import midi

    path = str(tmp_path / "t.mid")
    midi.write_midi(path, [(60, 0.0, 0.5, 100), (64, 0.5, 1.0, 90)], program=40)
    data = open(path, "rb").read()
    assert (
        data[:4] == b"MThd"
        and data[14:18] == b"MTrk"
        and b"\xc0\x28" in data
        and data.endswith(b"\xff\x2f\x00")
    )
    levels = [[-100.0] * 40 for _ in range(midi.N_BINS)]
    for f in range(10, 30):
        levels[36][f] = -10.0  # C4 = bin 36
        levels[35][f] = levels[37][f] = -30.0  # leakage into the neighbours
    notes = midi.transcribe(levels, threshold=0.5, min_dur=0.05)
    assert [(n[0], round(n[1], 3)) for n in notes] == [(60, round(10 * midi.HOP / midi.RATE, 3))]


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_midifier_end_to_end(ui, outdir, tmp_path):
    from core import midi

    if not midi.find_fluidsynth() or not midi.find_soundfonts():
        pytest.skip("FluidSynth or a SoundFont is not available")
    src = str(tmp_path / "arp.wav")
    subprocess.run(
        [
            ff.ffmpeg_path(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc='0.5*sin(2*PI*if(lt(t,0.6),261.63,if(lt(t,1.2),329.63,392))*t)':d=1.8:s=44100",
            src,
        ],
        check=True,
    )
    ui.reset()
    tab = ui.open("MIDIfier")
    ui.wait_idle(30)
    tab.src_var.set(src)
    tab.save_midi_var.set(True)
    out = str(outdir / "arp_midified.mp3")
    tab.out_var.set(out)
    tab._run()
    ui.wait_idle(120)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.has_audio and info.duration == pytest.approx(2.8, abs=0.2)
    assert os.path.getsize(os.path.splitext(out)[0] + ".mid") > 30


def test_waveform_region_filter():
    from tabs.audio import waveform

    f = waveform.region_filter([(1.0, 2.0, "mute", 0.0), (3.0, 4.0, "boost", 6.0)])
    assert f.startswith(
        "asetnsamples=n=256:p=0,volume='1+(0.000000-1)*clip(min(t-1.000,2.000-t)/0.02,0,1)'"
    )
    assert "1.995262" in f  # +6 dB


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_waveform_editor(ui, media, outdir):
    ui.reset()
    tab = ui.open("Waveform Editor")
    tab.src_row.set(media["h264"])
    ui.pump(0.3)
    ui.wait_idle(60)
    for _ in range(100):
        if tab._peaks:
            break
        ui.pump(0.1)
    assert len(tab._peaks) >= 200 and max(tab._peaks) > 0.01
    tab._regions.append((0.5, 1.5, "mute", 0.0))
    tab._export_mode.set("audio")
    out = str(outdir / "wave.wav")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    seg = str(outdir / "muted_part.wav")
    subprocess.run(
        [ff.ffmpeg_path(), "-y", "-loglevel", "error", "-ss", "0.6", "-t", "0.8", "-i", out, seg],
        check=True,
    )
    assert _volume(seg)[1] < -60  # the region is silent
    # loading another file clears the old regions
    tab.src_row.set(media["mp3"])
    ui.pump(0.5)
    assert tab._regions == []
