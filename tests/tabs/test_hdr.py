"""HDR to SDR: detection, both engines, and no HDR metadata left in the result."""

import json
import subprocess

import pytest

from core import ffmpeg as ff
from core import filters as fl
from core import hdr

X265_HDR10 = (
    "log-level=error:hdr10=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:"
    "master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400"
)


def _make(path, transfer, x265, seconds=1.5):
    ff.check(
        [
            ff.ffmpeg_path(),
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=24,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:"
            "range=tv",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            str(seconds),
            "-vf",
            f"zscale=t={transfer}:p=bt2020:m=bt2020nc:r=tv:npl=1000,format=yuv420p10le",
            "-c:v",
            "libx265",
            "-x265-params",
            x265,
            "-color_primaries",
            "bt2020",
            "-color_trc",
            transfer,
            "-colorspace",
            "bt2020nc",
            "-c:a",
            "aac",
            path,
        ]
    )
    return path


@pytest.fixture(scope="module")
def hdr10(tmp_path_factory):
    return _make(str(tmp_path_factory.mktemp("hdr") / "hdr10.mkv"), "smpte2084", X265_HDR10)


@pytest.fixture(scope="module")
def hlg(tmp_path_factory):
    return _make(str(tmp_path_factory.mktemp("hlg") / "hlg.mp4"), "arib-std-b67", "log-level=error")


def _hdr_leftovers(path):
    """HDR descriptions still in the file (first frame side data + container)."""
    r = subprocess.run(
        [
            ff.ffprobe_path(),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-read_intervals",
            "%+#1",
            "-show_frames",
            "-show_streams",
            "-show_entries",
            "frame=side_data_list:stream=side_data_list,color_transfer",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(r.stdout)
    kinds = [
        sd.get("side_data_type", "")
        for part in ("frames", "streams")
        for item in data.get(part, [])
        for sd in item.get("side_data_list") or []
    ]
    return [
        k
        for k in kinds
        if k.startswith(("Mastering", "Content light", "HDR Dynamic", "DOVI", "Dolby"))
    ]


@pytest.mark.ffmpeg
def test_detects_kinds(hdr10, hlg, media):
    h = hdr.inspect(ff.probe(hdr10), hdr10)
    assert (h.kind, h.mastering_nits, h.max_cll, h.max_fall) == ("HDR10", 1000.0, 1000, 400)
    assert "HDR10" in h.label() and "1000 nits" in h.label()
    assert hdr.inspect(ff.probe(hlg), hlg).kind == "HLG"
    assert not hdr.inspect(ff.probe(media["h264"]), media["h264"]).is_hdr


def test_chains_strip_hdr_descriptions():
    for gpu in (False, True):
        chain = hdr.sdr_chain(gpu=gpu)
        assert chain.endswith(fl.STRIP_HDR)
    assert "libplacebo=tonemapping=bt.2390" in hdr.sdr_chain(gpu=True)
    assert hdr.sdr_chain(gpu=False, assume="hlg").startswith(
        "setparams=color_primaries=bt2020:color_trc=arib-std-b67"
    )
    # the Downsizer and Codec Cruncher use the same CPU chain
    assert fl.hdr_to_sdr_chain().endswith(fl.STRIP_HDR)


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("engine,container", [("cpu", "mp4"), ("gpu", "mkv")])
def test_converts_and_leaves_no_hdr_metadata(ui, hdr10, outdir, engine, container):
    if engine == "gpu" and not fl.vulkan_ok():
        pytest.skip("no Vulkan here")
    ui.reset()
    tab = ui.open("HDR to SDR")
    tab.load_file(hdr10)
    ui.wait_for(lambda: tab._inspecting is None, 30)
    assert "HDR10" in tab.kind_lbl.cget("text")
    tab.engine_var.set(engine)
    tab._changed()
    out = str(outdir / f"sdr_{engine}.{container}")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    info = ff.probe(out, use_cache=False)
    v = info.require_video()
    assert (v.color_transfer, v.color_primaries) == ("bt709", "bt709") and not v.is_hdr
    assert info.has_audio and info.duration == pytest.approx(1.5, abs=0.1)
    assert _hdr_leftovers(out) == []
    # the picture is SDR-bright, not the dim/grey look of untouched PQ values
    rgb = subprocess.run(
        [
            ff.ffmpeg_path(),
            "-v",
            "error",
            "-ss",
            "0.5",
            "-i",
            out,
            "-frames:v",
            "1",
            "-vf",
            "scale=1:1:flags=area,format=rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    assert sum(rgb[:3]) / 3 > 60


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_hlg_and_untagged(ui, hlg, media, outdir):
    ui.reset()
    tab = ui.open("HDR to SDR")
    tab.engine_var.set("cpu")
    tab.load_file(hlg)
    ui.wait_for(lambda: tab._inspecting is None, 30)
    assert "HLG" in tab.kind_lbl.cget("text")
    out = str(outdir / "hlg_sdr.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    assert ff.probe(out, use_cache=False).require_video().color_transfer == "bt709"
    # an SDR file: nothing to convert, explained
    ui.reset()
    tab.load_file(media["h264_b"])
    ui.wait_for(lambda: tab._inspecting is None, 30)
    tab.out_row.set(str(outdir / "nope.mp4"))
    tab._render()
    assert ui.dialogs.warnings() and "isn't tagged as HDR" in ui.dialogs.warnings()[0][2]


@pytest.mark.ffmpeg
def test_cli_uses_the_same_conversion(capsys, hdr10, outdir):
    import json as _json

    from core import cli

    assert (
        cli.main(["hdr-to-sdr", hdr10, "--gpu", "off", "-o", str(outdir / "c.mkv"), "--json"]) == 0
    )
    res = _json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert res["ok"] and res["engine"] == "CPU" and res["source"].startswith("HDR10")
    assert _hdr_leftovers(res["output"]) == []
