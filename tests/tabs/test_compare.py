"""Compare & Quality: VMAF/SSIM/PSNR measurement, alignment, the page and the command line."""
import itertools
import json
import math
import os
import subprocess
import tempfile

import pytest

from core import ffmpeg as ff
from core import quality as q


def _encode(src, out, *extra):
    ff.check([ff.ffmpeg_path(), "-y", "-v", "error", *extra[:2], "-i", src, *extra[2:], "-an", out])
    return out


@pytest.fixture(scope="module")
def copies(media, tmp_path_factory):
    d = tmp_path_factory.mktemp("copies")
    src = media["h264"]
    return {
        "bad": _encode(src, str(d / "bad.mp4"), "-v", "error", "-c:v", "libx264", "-crf", "40"),
        "good": _encode(src, str(d / "good.mp4"), "-v", "error", "-c:v", "libx264", "-crf", "16"),
        "small": _encode(src, str(d / "small.mkv"), "-v", "error", "-vf", "scale=640:360", "-c:v", "libx264",
                         "-crf", "20"),
        "trimmed": _encode(src, str(d / "trimmed.mp4"), "-ss", "1", "-c:v", "libx264", "-crf", "16"),
    }


def measure(ref, dist, **kw):
    argv, files = q.build(ref, dist, ff.probe(ref), ff.probe(dist), **kw)
    tmp = tempfile.mkdtemp()
    subprocess.run(argv, cwd=tmp, check=True, capture_output=True)
    return q.read(tmp, files, ff.probe(ref).video.fps, kw.get("skip_ref", 0.0))


@pytest.mark.ffmpeg
def test_scores_rank_the_copies(media, copies):
    src = media["h264"]
    same = measure(src, src).summary()
    assert same["vmaf"]["mean"] > 97 and same["ssim"]["mean"] == pytest.approx(1.0) and same["psnr"]["mean"] == "inf"
    good, bad = measure(src, copies["good"]).summary(), measure(src, copies["bad"]).summary()
    assert good["vmaf"]["mean"] > 90 > bad["vmaf"]["mean"]
    assert good["ssim"]["mean"] > bad["ssim"]["mean"] and good["psnr"]["mean"] > bad["psnr"]["mean"]
    assert q.verdict(good["vmaf"]["mean"]).startswith(("Excellent", "Visually"))
    small = measure(src, copies["small"]).summary()           # a smaller copy is scaled to compare
    assert 60 < small["vmaf"]["mean"] < good["vmaf"]["mean"] and small["vmaf"]["frames"] == 180


@pytest.mark.ffmpeg
def test_lines_up_a_trimmed_copy(media, copies):
    src = media["h264"]
    off = measure(src, copies["trimmed"]).summary()["vmaf"]["mean"]
    aligned = measure(src, copies["trimmed"], skip_ref=1.0)
    assert aligned.summary()["vmaf"]["mean"] > 90 > off
    assert aligned.vmaf.times[0] == pytest.approx(1.0)       # times stay in the original's timeline


@pytest.mark.ffmpeg
def test_faster_settings(media, copies):
    res = measure(media["h264"], copies["bad"], subsample=5, seconds=2.0, metrics=("vmaf",))
    s = res.summary()
    assert set(s) == {"vmaf"} and s["vmaf"]["frames"] == 12
    worst = res.worst(3, gap=0.5)
    assert len(worst) == 3 and all(abs(a[0] - b[0]) >= 0.5 for a, b in itertools.pairwise(worst))


def test_build_validates():
    with pytest.raises(ValueError):
        q.build("a", "b", ff.MediaInfo("a"), ff.MediaInfo("b"), metrics=())
    assert q.verdict(96) .startswith("Visually") and q.verdict(65).startswith("Poor")
    assert math.isinf(q.ssim_db(1.0)) and q.ssim_db(0.99) == pytest.approx(20.0)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_page(ui, media, copies, tmp_path):
    ui.reset()
    tab = ui.open("Compare & Quality")
    tab.ref_row.set("")
    tab.dist_row.set("")
    tab.load_file(media["h264"])                              # dropped files: original first...
    tab.load_file(copies["bad"])                              # ...then the copy
    assert tab.ref_row.path == media["h264"] and tab.dist_row.path == copies["bad"]
    assert "of the original's size" in tab.info_lbl.cget("text")
    tab._run()
    ui.wait_idle(240)
    infos = [d for d in ui.dialogs.log if d[0] == "showinfo"]
    assert not ui.dialogs.errors() and not infos, (ui.dialogs.errors(), infos)   # results show in the page
    assert tab.result is not None
    assert float(tab.score_lbls["vmaf"][0].cget("text")) < 90
    assert "VMAF" in tab.verdict_lbl.cget("text")
    assert len(tab.worst_frame.winfo_children()) > 1
    assert tab.graph.find_all()
    out = str(tmp_path / "scores.csv")
    ui.answer_save_as(out)
    tab._export()
    assert open(out, encoding="utf-8").readline().startswith("time,vmaf,ssim,psnr")
    assert json.load(open(os.path.splitext(out)[0] + ".json"))["summary"]["vmaf"]["frames"] == 180


@pytest.mark.ffmpeg
def test_cli(capsys, media, copies, tmp_path):
    from core import cli
    assert cli.main(["compare", media["h264"], copies["good"], "--csv", str(tmp_path / "s.csv"), "--json"]) == 0
    res = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert res["ok"] and res["vmaf"]["mean"] > 90 and res["verdict"] and 1 <= len(res["worst_moments"]) <= 5
    assert os.path.isfile(res["csv"])
