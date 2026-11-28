"""HDR sources stay right whatever tool re-encodes them: kept as 10-bit HDR
when the output codec can carry it, converted to SDR properly otherwise -
never an 8-bit file labelled HDR (which is what every tool used to write)."""

from __future__ import annotations

import subprocess

import pytest

from core import ffmpeg as ff
from core import hdr
from core.hdr_keep import adapt


def _pq(_path: str) -> str | None:
    return "smpte2084"


def _sdr(_path: str) -> str | None:
    return None


def _cmd(*middle: str) -> list[str]:
    return ["-i", "in.mov", *middle, "out.mp4"]


def test_hevc_output_is_switched_to_ten_bit_and_tagged():
    out = adapt(_cmd("-vf", "crop=100:100", "-c:v", "libx265", "-pix_fmt", "yuv420p"), _pq)
    assert out[out.index("-pix_fmt") + 1] == "yuv420p10le"
    assert out[out.index("-color_trc") + 1] == "smpte2084"
    assert out[-1] == "out.mp4"


def test_nvenc_hevc_gets_p010():
    out = adapt(_cmd("-c:v", "hevc_nvenc", "-pix_fmt", "yuv420p"), _pq)
    assert out[out.index("-pix_fmt") + 1] == "p010le"


def test_h264_with_a_filter_chain_gets_tone_mapped_at_the_end():
    out = adapt(_cmd("-vf", "crop=100:100", "-c:v", "libx264", "-pix_fmt", "yuv420p"), _pq)
    vf = out[out.index("-vf") + 1]
    assert vf.startswith("crop=100:100,") and "tonemap" in vf
    assert out[out.index("-color_trc") + 1] == "bt709"


def test_h264_without_filters_gets_one():
    out = adapt(_cmd("-c:v", "libx264"), _pq)
    assert "tonemap" in out[out.index("-vf") + 1]


def test_filter_complex_output_is_rerouted_through_the_tone_mapping():
    out = adapt(
        _cmd(
            "-filter_complex",
            "[0:v]crop=10:10[v]",
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
        ),
        _pq,
    )
    graph = out[out.index("-filter_complex") + 1]
    assert graph.startswith("[0:v]crop=10:10[v];[v]") and graph.endswith("[v_sdr]")
    assert out[out.index("-map") + 1] == "[v_sdr]"


def test_vaapi_upload_stays_last():
    out = adapt(_cmd("-vf", "scale=640:-2,format=nv12,hwupload", "-c:v", "h264_vaapi"), _pq)
    vf = out[out.index("-vf") + 1]
    assert vf.endswith("format=nv12,hwupload") and "tonemap" in vf


def test_vaapi_hevc_uploads_ten_bit():
    out = adapt(_cmd("-vf", "format=nv12,hwupload", "-c:v", "hevc_vaapi"), _pq)
    assert out[out.index("-vf") + 1] == "format=p010,hwupload"


@pytest.mark.parametrize(
    "middle",
    [
        ("-c:v", "copy"),  # a stream copy
        ("-vn", "-c:a", "aac"),  # audio only
        ("-vf", "zscale=t=linear,tonemap=hable", "-c:v", "libx264"),  # handles HDR itself
        ("-c:v", "libx265", "-pix_fmt", "yuv420p10le"),  # already 10-bit
        ("-c:v", "prores_ks"),  # an intermediate codec keeps what it gets
    ],
)
def test_left_alone(middle):
    cmd = _cmd(*middle)
    assert adapt(list(cmd), _pq) == cmd


def test_sdr_sources_are_untouched():
    cmd = _cmd("-vf", "crop=100:100", "-c:v", "libx264")
    assert adapt(list(cmd), _sdr) == cmd


def test_idempotent():
    once = adapt(_cmd("-c:v", "libx264"), _pq)
    assert adapt(list(once), _pq) == once
    once = adapt(_cmd("-c:v", "libx265"), _pq)
    assert adapt(list(once), _pq) == once


def test_generated_inputs_are_not_probed():
    seen: list[str] = []

    def spy(path: str) -> str | None:
        seen.append(path)
        return None

    adapt(["-f", "lavfi", "-i", "color=black", "-i", "real.mov", "-c:v", "libx264", "o.mp4"], spy)
    assert seen == ["real.mov"]


def test_real_hdr_clip_through_an_ordinary_tool(tmp_path):
    """An HDR10 clip cropped as H.264 comes out SDR; as HEVC, 10-bit HDR10 with
    its mastering information."""
    if (
        "libx265"
        not in subprocess.run(
            [ff.ffmpeg_path(), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    ):
        pytest.skip("no libx265 in this FFmpeg")
    src = str(tmp_path / "hdr10.mp4")
    x265 = (
        "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:hdr10=1:log-level=error:"
        "master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):"
        "max-cll=1000,400"
    )
    subprocess.run(
        [
            ff.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=320x240:r=25,format=yuv420p10le", "-t", "1",
            "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", x265,
            "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc", src,
        ],
        check=True,
    )  # fmt: skip
    for codec, want_hdr in (("libx264", False), ("libx265", True)):
        out = str(tmp_path / f"{codec}.mp4")
        cmd = [ff.ffmpeg_path(), "-y", "-i", src, "-vf", "crop=160:120", "-c:v", codec]
        res = ff.run([*cmd, "-preset", "ultrafast", "-pix_fmt", "yuv420p", out])
        assert res.ok, res.error_summary
        info = ff.probe(out, use_cache=False)
        video = info.require_video()
        if want_hdr:
            assert video.pix_fmt == "yuv420p10le" and video.color_transfer == "smpte2084"
            assert hdr.inspect(info, out).mastering_nits == 1000
        else:
            assert video.pix_fmt == "yuv420p" and video.color_transfer == "bt709"
            assert not info.is_hdr
