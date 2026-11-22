"""Upscaling shaders (FSR, NIS, ArtCNN, Anime4K) in the Resolution Scaler: graphs, colours, tags."""

import json
import subprocess

import pytest

from core import ffmpeg as ff
from core import filters as fl
from core import upscale
from tabs.transcoder import scaler as sc

needs_gpu = pytest.mark.skipif(
    not (fl.vulkan_ok() and upscale.available()),
    reason="needs a Vulkan GPU and the bundled shaders",
)


def test_plane_graph_keeps_colour_out_of_libplacebo():
    g = upscale.plane_graph(
        (1280, 720), tags={"range": "tv", "primaries": "bt709", "trc": "bt709", "space": "bt709"}
    )
    assert g.startswith("format=yuv420p,extractplanes=y+u+v[y][u][v];")
    assert "[y]setparams=range=pc,libplacebo=w=1280:h=720" in g and ":range=pc:format=gray[Y]" in g
    assert "[u]scale=640:360:flags=lanczos[U]" in g and "mergeplanes=0x001020:yuv420p" in g
    assert g.endswith("setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709")
    deep = upscale.plane_graph((1920, 1080), deep=True)
    assert (
        "format=gray10le[Y]" in deep
        and "yuv420p10le" in deep
        and deep.endswith(
            "setparams=range=unknown:color_primaries=unknown:color_trc=unknown:colorspace=unknown"
        )
    )


def test_scale_chain_choices(monkeypatch):
    monkeypatch.setattr(
        upscale, "shader_path", lambda key: f"/s/{key}.glsl" if key in upscale.SHADERS else None
    )
    up = sc.scale_chain((640, 360), (1280, 720), "fit", "auto", gpu=True, sharpen=0.5)
    assert "custom_shader_path=" in up and "fsr" in up and "cas=" not in up  # FSR sharpens already
    assert "libplacebo" not in sc.scale_chain((640, 360), (1280, 720), "fit", "auto", gpu=False)
    cpu = sc.scale_chain((640, 360), (1280, 720), "fit", "lanczos", sharpen=0.5)
    assert cpu.startswith("scale=1280:720:flags=lanczos") and "cas=strength=0.50" in cpu
    down = sc.scale_chain((1920, 1080), (1280, 720), "fit", "anime4k", gpu=True)
    assert "libplacebo" in down and "custom_shader_path" not in down  # shaders only upscale
    assert sc.scale_chain((640, 360), (1080, 1080), "pad", "nis", gpu=True).endswith(
        "pad=1080:1080:(ow-iw)/2:(oh-ih)/2:color=black"
    )


@pytest.fixture(scope="module")
def small(media, tmp_path_factory):
    """The 1280x720 test pattern made 640x360, one copy untagged and one tagged BT.709."""
    d = tmp_path_factory.mktemp("small")
    plain, tagged = str(d / "small.mp4"), str(d / "small709.mp4")
    ff.check(
        [
            ff.ffmpeg_path(),
            "-y",
            "-v",
            "error",
            "-i",
            media["h264"],
            "-t",
            "2",
            "-vf",
            "scale=640:360:flags=area",
            "-c:v",
            "libx264",
            "-crf",
            "8",
            "-c:a",
            "aac",
            plain,
        ]
    )
    ff.check(
        [
            ff.ffmpeg_path(),
            "-y",
            "-v",
            "error",
            "-i",
            plain,
            "-c",
            "copy",
            "-bsf:v",
            "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1",
            tagged,
        ]
    )
    return {"plain": plain, "tagged": tagged}


def _flat_yuv(path, x=1100, y=400):
    r = subprocess.run(
        [
            ff.ffmpeg_path(),
            "-v",
            "error",
            "-i",
            path,
            "-frames:v",
            "1",
            "-vf",
            f"crop=32:32:{x}:{y},signalstats,metadata=print:file=-",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    vals = {}
    for key in ("YAVG", "UAVG", "VAVG"):
        vals[key] = float(r.stdout.split(f"lavfi.signalstats.{key}=")[1].split()[0])
    return vals


def _tags(path):
    r = subprocess.run(
        [
            ff.ffprobe_path(),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=color_space,color_primaries,color_transfer",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(r.stdout)["streams"][0]


@needs_gpu
@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("key", list(upscale.SHADERS))
def test_shader_upscale_keeps_colours_and_tags(ui, small, media, outdir, key):
    if not upscale.shader_path(key):
        pytest.skip(f"{key} shader not installed")
    ui.reset()
    tab = ui.open("Resolution Scaler")
    src = small["tagged"] if key == "fsr" else small["plain"]
    tab.src_row.set(src)
    tab._source_changed(src)
    tab.size_var.set("Twice the size (×2)")
    tab.method_var.set(sc.METHODS[key])
    out = str(outdir / f"up_{key}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    assert not ui.dialogs.errors() and not ui.dialogs.warnings(), (
        ui.dialogs.errors(),
        ui.dialogs.warnings(),
    )
    info = ff.probe(out, use_cache=False)
    assert (info.require_video().width, info.require_video().height) == (
        1280,
        720,
    ) and info.has_audio
    want, got = _flat_yuv(media["h264"]), _flat_yuv(out)
    for k in want:  # a flat, very saturated colour comes through unchanged
        assert abs(got[k] - want[k]) <= 2.5, (key, want, got)
    tags = _tags(out)
    if key == "fsr":
        assert tags.get("color_space") == "bt709" and tags.get("color_primaries") == "bt709"
    else:
        assert tags.get("color_space", "unknown") == "unknown"


@needs_gpu
@pytest.mark.ffmpeg
def test_cli_scale_with_shader(capsys, small, outdir):
    from core import cli

    assert (
        cli.main(
            [
                "scale",
                small["plain"],
                "--factor",
                "2",
                "--method",
                "nis",
                "-o",
                str(outdir / "nis.mp4"),
                "--json",
            ]
        )
        == 0
    )
    res = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "1280x720" in res["video"]
