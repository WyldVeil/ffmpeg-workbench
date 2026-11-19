"""
tests/test_webm_maker_logic.py — unit tests for pure helpers in webm_maker.py
(no GUI required — Tkinter is never imported).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_snap_even_already_even():
    from tabs.social.webm_maker import _snap_even
    assert _snap_even(1080) == 1080
    assert _snap_even(2) == 2
    assert _snap_even(1920) == 1920


def test_snap_even_rounds_odd_up():
    from tabs.social.webm_maker import _snap_even
    assert _snap_even(1) == 2
    assert _snap_even(1079) == 1080
    assert _snap_even(7681) == 7682


def test_resolve_pixfmt_explicit_choice_passes_through():
    from tabs.social.webm_maker import _resolve_pixfmt
    pf, warn = _resolve_pixfmt("yuv420p", "yuv420p10le", "VP9")
    assert pf == "yuv420p"
    assert warn is None


def test_resolve_pixfmt_source_in_supported_set_vp9():
    from tabs.social.webm_maker import _resolve_pixfmt
    pf, warn = _resolve_pixfmt("source", "yuv420p10le", "VP9")
    assert pf == "yuv420p10le"
    assert warn is None


def test_resolve_pixfmt_source_unsupported_falls_back_with_warning():
    from tabs.social.webm_maker import _resolve_pixfmt
    pf, warn = _resolve_pixfmt("source", "rgb24", "VP9")
    assert pf == "yuv420p"
    assert warn is not None
    assert "rgb24" in warn
    assert "yuv420p" in warn


def test_resolve_pixfmt_source_unsupported_in_vp8_falls_back():
    from tabs.social.webm_maker import _resolve_pixfmt
    # yuv444p is supported in VP9 but not VP8
    pf, warn = _resolve_pixfmt("source", "yuv444p", "VP8")
    assert pf == "yuv420p"
    assert warn is not None
    assert "yuv444p" in warn


def test_resolve_pixfmt_source_unknown_falls_back_silently():
    """If source pix_fmt is empty (probe never succeeded), use yuv420p with no warning."""
    from tabs.social.webm_maker import _resolve_pixfmt
    pf, warn = _resolve_pixfmt("source", "", "VP9")
    assert pf == "yuv420p"
    assert warn is None


def test_effective_vp9_deadline_realtime_two_pass_overridden():
    """libvpx-vp9 'realtime' skips first-pass analysis — must downgrade to 'good'."""
    from tabs.social.webm_maker import _effective_vp9_deadline
    assert _effective_vp9_deadline("realtime", True) == "good"


def test_effective_vp9_deadline_realtime_single_pass_kept():
    """Single-pass realtime is fine — no override."""
    from tabs.social.webm_maker import _effective_vp9_deadline
    assert _effective_vp9_deadline("realtime", False) == "realtime"


def test_effective_vp9_deadline_good_and_best_unchanged():
    """Non-realtime deadlines are never overridden."""
    from tabs.social.webm_maker import _effective_vp9_deadline
    assert _effective_vp9_deadline("good", True) == "good"
    assert _effective_vp9_deadline("good", False) == "good"
    assert _effective_vp9_deadline("best", True) == "best"
    assert _effective_vp9_deadline("best", False) == "best"


# ─── Simple mode (spec 2026-05-10) ───────────────────────────────────────────

def test_lookup_threshold_map_boundaries():
    from tabs.social.webm_maker import _lookup_threshold_map, _AUDIO_MAP_6MB
    assert _lookup_threshold_map(10, _AUDIO_MAP_6MB) == 128
    assert _lookup_threshold_map(15, _AUDIO_MAP_6MB) == 128      # key is inclusive
    assert _lookup_threshold_map(15.01, _AUDIO_MAP_6MB) == 120
    assert _lookup_threshold_map(10_000, _AUDIO_MAP_6MB) == 32    # past the end: last value


def test_simple_silent_profile_has_no_audio():
    from tabs.social.webm_maker import _simple_compute_settings
    st = _simple_compute_settings("4mb_silent", 60, 1920, 1080, 30, False)
    assert st.audio_bitrate_kbps == 0 and not st.force_mono and not st.dynaudnorm


def test_simple_low_audio_bitrate_forces_mono_and_dynaudnorm():
    from tabs.social.webm_maker import _simple_compute_settings
    st = _simple_compute_settings("6mb_audio", 250, 1920, 1080, 30, False)
    assert st.audio_bitrate_kbps == 56 and st.force_mono and st.dynaudnorm
    st = _simple_compute_settings("6mb_audio", 20, 1920, 1080, 30, False)
    assert st.audio_bitrate_kbps == 120 and not st.force_mono


def test_simple_music_mode_boosts_audio():
    from tabs.social.webm_maker import _simple_compute_settings
    plain = _simple_compute_settings("6mb_audio", 60, 1920, 1080, 30, False)
    music = _simple_compute_settings("6mb_audio", 60, 1920, 1080, 30, True)
    assert music.audio_bitrate_kbps > plain.audio_bitrate_kbps
    assert music.video_bitrate_kbps < plain.video_bitrate_kbps


def test_simple_fps_cap_never_exceeds_source():
    from tabs.social.webm_maker import _simple_compute_settings
    assert _simple_compute_settings("6mb_audio", 30, 1920, 1080, 25, False).fps_cap == 25
    assert _simple_compute_settings("6mb_audio", 300, 1920, 1080, 60, False).fps_cap == 24.0
    assert _simple_compute_settings("6mb_audio", 30, 1920, 1080, 0, False).fps_cap == 60.0


def test_simple_budget_fits_the_cap_and_never_upscales():
    from tabs.social.webm_maker import (_simple_compute_settings, _PROFILE_SIZE_BYTES,
                                        _VIDEO_BITRATE_FLOOR, _RESOLUTION_TABLE)
    for profile, size in _PROFILE_SIZE_BYTES.items():
        for dur in (5, 30, 90, 119):
            st = _simple_compute_settings(profile, dur, 1920, 1080, 30, False)
            total = (st.video_bitrate_kbps + st.audio_bitrate_kbps) * 1000 * dur / 8
            assert total <= size or st.video_bitrate_kbps == _VIDEO_BITRATE_FLOOR
            assert st.target_max_dim in _RESOLUTION_TABLE or st.target_max_dim == 1920
    small = _simple_compute_settings("6mb_audio", 10, 640, 360, 30, False)
    assert small.target_max_dim <= 640                     # never upscale
    portrait = _simple_compute_settings("6mb_audio", 60, 1080, 1920, 30, False)
    assert portrait.target_max_dim <= 1920


def test_size_retry_bitrate_shrinks():
    from tabs.social.webm_maker import _size_retry_bitrate
    assert _size_retry_bitrate(1000, 4_000_000, 5_000_000) < 800
