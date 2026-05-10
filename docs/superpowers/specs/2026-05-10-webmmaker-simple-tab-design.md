# WebM Maker — Simple / Advanced sub-tabs

**Date:** 2026-05-10
**Scope:** `tabs/social/webm_maker.py` (single file UI restructure + new Simple-mode encoder)
**Type:** Feature — adds a "Simple" mode that auto-computes everything from a profile + duration

## Problem

The WebM Maker tab today exposes ~30 controls (target size, two-pass toggle, codec, audio codec, audio bitrate, resolution lock, width/height sliders, preset buttons, lock-aspect, FPS, pixel format, strip metadata, loop flag, tile-columns, row-mt, trim, quality preset, CRF override). For users who just want a size-capped webm and don't care about the knobs, this is overwhelming.

The reference tool [chameleon-ai/webm-for-4chan](https://github.com/chameleon-ai/webm-for-4chan) demonstrates the opposite philosophy: pick a profile (size cap + audio + duration cap) and let the engine auto-decide audio bitrate, mixdown, FPS cap, target resolution, and video bitrate from lookup tables keyed on duration. The user runs `python webm_for_4chan.py input.mp4` and gets a ready-to-upload file.

We want both philosophies, in one tab.

## Design

### Sub-tab structure

The existing flat layout becomes a two-tab `ttk.Notebook`:

```
┌─ WebM Maker ─────────────────────────────────────┐
│ Source File: [_____________] [Browse]            │  shared
│ Info: 1920×1080 · 30 fps · 5m 01s · 12 MB        │
│                                                   │
│ ┌──[Simple]──[Advanced]──────────────────────┐   │
│ │  (only the active tab's controls render)    │   │
│ └─────────────────────────────────────────────┘   │
│                                                   │
│ Output: [_____________] [Save as]                 │  shared
│         [▶ ENCODE (Simple)]   [✗ Cancel]          │  label flips per tab
│         Status: Ready.                            │
│ ┌── console ──────────────────────────────────┐   │
│ └─────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────┘
```

**Shared widgets** (live above and below the notebook): source-file picker + ffprobe info label, output path entry + save-as button, render button, cancel button, status label, progress bar, console.

**Per sub-tab** (independent state): all encoding controls. Simple has 3 controls; Advanced retains the existing two-column layout verbatim.

**Default sub-tab:** Simple.

**Encode dispatch:** one shared `tk.Button`. On click, read `self.notebook.tab(self.notebook.select(), "text")` to determine the active tab and route to either `_encode_worker_simple()` (new) or `_encode_worker()` (existing, renamed for clarity to `_encode_worker_advanced()`). The button's text updates on `<<NotebookTabChanged>>` to read `▶ ENCODE (Simple)` or `▶ ENCODE (Advanced)` so the user knows which mode will run.

### Simple sub-tab UI

```
┌─ Simple ─────────────────────────────────────────┐
│  Profile (size · audio · max duration)            │
│   ● 6 MB · audio · up to 6m 40s                   │
│   ○ 4 MB · audio · up to 5m 00s                   │
│   ○ 4 MB · silent · up to 2m 00s                  │
│                                                   │
│  ☐ Prioritize audio quality (music mode)          │
│       Boosts audio bitrate; video quality drops.  │
│                                                   │
│  Trim (optional)                                  │
│   Start [0       ] s    End [0 = full] s          │
│                                                   │
│  Estimated:  ~1248 kbps video · 128 kbps audio    │
│              · ~720p · 30 fps                     │
└───────────────────────────────────────────────────┘
```

Three controls. Profile (`tk.StringVar`, three radios), music-mode (`tk.BooleanVar`), trim (start + end as `tk.StringVar`s, identical semantics to Advanced's trim).

**Profile radio → internal key mapping** (the `value=` of each `tk.Radiobutton`):

| Visible label | StringVar value | Cap | Audio |
|---|---|---|---|
| `6 MB · audio · up to 6m 40s` | `"6mb_audio"` | 6 MiB | yes |
| `4 MB · audio · up to 5m 00s` | `"4mb_audio"` | 4 MiB | yes |
| `4 MB · silent · up to 2m 00s` | `"4mb_silent"` | 4 MiB | no |

Default selection: `"6mb_audio"` (matches the reference tool's default).

The **Estimated** line is a live preview label. It re-renders whenever any of the inputs (profile / music-mode / trim) or the source metadata changes, by calling the same `_simple_compute_settings()` helper the encoder will use. This serves as both UX feedback and self-documentation: the user sees what the auto-engine will decide before clicking encode.

**Live-update wiring**: a single `_update_simple_estimate()` method recomputes and renders the label. Triggers:
- Profile radios → wired via `command=self._update_simple_estimate` on each `tk.Radiobutton`.
- Music-mode checkbox → same (`command=`).
- Trim entries → `<KeyRelease>` event (lightweight pure compute; no jank concern since the helper is fast and the IntVar/StringVar split that fixed Advanced isn't needed here — Simple has no slider widgets).
- Source load → `_load_metadata` already calls `self.after(0, self._update_budget_label)` for Advanced; we add `self.after(0, self._update_simple_estimate)` next to it.

**Estimated-label format** (cosmetic):
- Landscape source: `~{video_kbps} kbps video · {audio_kbps} kbps audio · ~{target_max_dim}p · {fps_cap:g} fps`
- Portrait source: same but display `{max_dim}×{shorter_dim}` instead of `{p}p` since "720p" implies landscape (e.g. `~720×1280`).
- Silent profile: omit the `· {audio} kbps audio` segment entirely.

### Auto-compute engine

A new pure (no Tkinter) module-level function:

```python
@dataclass(frozen=True)
class SimpleEncodeSettings:
    audio_bitrate_kbps: int      # 0 == silent profile (drives -an)
    force_mono: bool             # adds -ac 1 when True; only meaningful if audio_bitrate_kbps > 0
    dynaudnorm: bool             # adds -af dynaudnorm; tied to force_mono
    fps_cap: float
    target_max_dim: int          # cap for the larger axis (W if landscape, H if portrait)
    video_bitrate_kbps: int

def _simple_compute_settings(
    profile: str,                # "6mb_audio" | "4mb_audio" | "4mb_silent"
    duration_s: float,
    source_w: int,
    source_fps: float,
    music_mode: bool,
) -> SimpleEncodeSettings:
    ...
```

Lookup tables (verbatim ports from `webm_for_4chan.py`):

```python
_PROFILE_SIZE_BYTES = {"6mb_audio": 6 * 1024 * 1024,
                       "4mb_audio": 4 * 1024 * 1024,
                       "4mb_silent": 4 * 1024 * 1024}
_PROFILE_DURATION_CAP_S = {"6mb_audio": 399.92,
                           "4mb_audio": 300.0,
                           "4mb_silent": 120.0}
_PROFILE_HAS_AUDIO = {"6mb_audio": True,
                      "4mb_audio": True,
                      "4mb_silent": False}

# duration → audio bitrate (kbps)
_AUDIO_MAP_6MB     = {15:128, 30:120, 45:112, 95:96, 120:80, 240:64, 300:56, 360:48, 400:32}
_AUDIO_MAP_4MB     = {10:96,  20:64,  40:56,  60:48, 120:32}
_AUDIO_MAP_MUSIC   = {15:320, 45:256, 100:192, 220:160, 285:128, 300:120, 330:112, 400:96}

_FPS_MAP            = {150.0: 60.0, 200.0: 30.0, 400.0: 24.0}
_BITRATE_COMP_MAP   = {240.0: 0, 300.0: 2, 360.0: 3, 380.0: 4, 400.0: 6}
_RESOLUTION_TABLE   = (480, 576, 640, 736, 854, 960, 1024, 1280, 1440, 1600, 1920, 2048)

_VIDEO_BITRATE_FLOOR = 50      # kbps; below this, quality is unwatchable
_VIDEO_BITRATE_CEIL  = 2800    # kbps cap (matches reference)
```

Computation order:

1. **Audio bitrate.** If profile is silent → 0. Else: pick the right map (`_AUDIO_MAP_6MB`, `_AUDIO_MAP_4MB`, or `_AUDIO_MAP_MUSIC` if music_mode). Walk the map's keys in ascending order; the first key where `duration_s ≤ key` wins; otherwise the largest key's value. Lookup helper: `_lookup_threshold_map(duration_s, table)`.

2. **Mixdown.** Match the reference's exact thresholds:
   - `audio_kbps == 0` → `force_mono = False`, `dynaudnorm = False` (the silent profile uses `-an` instead)
   - `audio_kbps ≤ 64` → `force_mono = True`, `dynaudnorm = True`
   - `audio_kbps > 64`  → `force_mono = False`, `dynaudnorm = False`

   *Deliberate simplification vs reference*: the reference distinguishes a third "downmix-to-stereo" tier when `64 < audio_kbps ≤ 96` for sources with >2 channels (5.1 etc.). We don't add `-ac 2` in that range — opus handles whatever the source has. In practice 4chan-style sources are stereo, so this is a no-op for >99% of inputs and saves us a multichannel-detection step.

3. **FPS cap.** Look up `_FPS_MAP` by duration; clamp to source: `fps_cap = min(map_value, source_fps)`. If `source_fps == 0` (probe failed), fall back to map_value.

4. **Video bitrate.**
   ```
   total_bits      = profile_size_bytes × 8
   audio_bits      = audio_kbps × 1000 × duration_s
   bitrate_comp    = _lookup_threshold_map(duration_s, _BITRATE_COMP_MAP)
   video_kbps_raw  = (total_bits − audio_bits) / duration_s / 1000 − bitrate_comp
   video_kbps      = max(_VIDEO_BITRATE_FLOOR, min(_VIDEO_BITRATE_CEIL, int(video_kbps_raw)))
   ```

5. **Target resolution.** Verbatim port of the reference's `calculate_target_resolution` with `resize_mode='logarithmic'`. Operates on the *largest dimension* (max of width, height) so it works equally for landscape and portrait sources:

   ```python
   # 5a. Pre-scale source so the shorter side is 1080.
   #     This normalises the curve, which was tuned at 1080p; without this
   #     step a 4K source over-estimates and a 360p source under-estimates.
   short_side  = min(source_w, source_h)
   norm_w      = source_w * 1080.0 / short_side
   norm_h      = source_h * 1080.0 / short_side
   total_px    = norm_w * norm_h

   # 5b. Logarithmic scale factor (constants from the reference tool).
   _LOG_A = 0.2311
   _LOG_B = 35.47
   scale_factor   = _LOG_A * math.log(video_kbps / _LOG_B)
   scaled_px      = total_px * scale_factor
   # Either dimension reconstructed from scaled total pixels (the math is
   # symmetric — we take the larger of the two as the "max dimension").
   max_dim_target = max(scaled_px / norm_h, scaled_px / norm_w)

   # 5c. Snap DOWN to _RESOLUTION_TABLE (largest entry ≤ max_dim_target).
   nearest = _RESOLUTION_TABLE[0]
   for r in _RESOLUTION_TABLE:
       if max_dim_target >= r:
           nearest = r
       else:
           break

   # 5d. If source's max dimension is already smaller, don't upscale.
   source_max_dim = max(source_w, source_h)
   if source_max_dim <= nearest:
       target_max_dim = source_max_dim
   else:
       target_max_dim = nearest

   # 5e. Convert max-dim target back to a width-or-height:
   #     The encoder's filter is scale=W:-2:flags=lanczos, so we pass
   #     target_max_dim as W if landscape, else as H (use scale=-2:H).
   ```

   *Edge cases:*
   - `video_kbps ≤ _LOG_B` (very low bitrate) → `math.log(...)` returns ≤ 0 → `scale_factor ≤ 0` → `max_dim_target ≤ 0`. Guard: if `scale_factor ≤ 0`, fall back to `_RESOLUTION_TABLE[0]` (480). This matches the reference tool's table-fallback behaviour without us needing to port the fallback map.
   - Source already smaller than table[0] (480) → no resize, encode at source resolution.

   *Note for the encoder*: the resulting `target_max_dim` is the cap for the **larger** axis. The actual `scale=` filter uses one axis explicit and the other as `-2` (FFmpeg auto-computes height divisible by 2 preserving aspect). For landscape sources: `scale={target_max_dim}:-2`. For portrait: `scale=-2:{target_max_dim}`. The dataclass field gets renamed `target_max_dim: int` to reflect this.

6. Return the dataclass.

### Encoder invocation

`_encode_worker_simple(self, src, out, settings: SimpleEncodeSettings)` — sibling to the existing `_encode_worker_advanced(self, src, out, size_mb)`.

- Codec: VP9, hardcoded.
- Two-pass: always.
- `-cpu-used 1`, `-deadline good` (using `_effective_vp9_deadline("good", two_pass=True)` for consistency with the recent fix).
- `-tile-columns 2`, `-row-mt 1` (matches reference).
- `-pix_fmt`: `_resolve_pixfmt("source", self._src_pixfmt, "VP9")` so HDR / 10-bit sources are preserved with our existing fallback.
- Video filter chain: `scale={target_max_dim}:-2:flags=lanczos,fps={fps_cap}` for landscape sources, `scale=-2:{target_max_dim}:flags=lanczos,fps={fps_cap}` for portrait. The `-2` makes the other axis auto-compute preserving aspect, divisible by 2. Skip `scale=` entirely when `target_max_dim == max(src_w, src_h)` (no resize needed).
- Audio: when `audio_bitrate_kbps == 0`, use `-an`. Else `-c:a libopus -b:a {audio_bitrate_kbps}k`; if `force_mono` then add `-ac 1`; if `dynaudnorm` then add `-af dynaudnorm`.
- Two-pass scaffolding identical to existing flow (`tempfile.mktemp()` pass log, `_run_cmd` for streaming, `_finish` for cleanup).

### Pre-encode validation

Runs in `_start_render` before queueing the worker, when Simple is the active tab.

| Condition | Action |
|---|---|
| `not src or not exists(src)` | `messagebox.showwarning("Warning", "Load a source file first.")`, return |
| `_src_dur == 0` | Same as above (probe never completed) |
| `trim_start ≥ src_dur` | `messagebox.showerror("Error", "Trim start ({Xs}) is past the end of the clip ({Ys}).")`, return |
| `effective_dur < 2.0` | `messagebox.showerror("Error", "Clip is too short to encode (< 2s).")`, return |
| `effective_dur > profile_cap` | `messagebox.showerror("Error", "Clip is {Xm Ys} after trim, but the {profile_label} profile caps at {Cm Ds}. Trim further or pick a smaller profile.")`, return |
| `_simple_compute_settings(...).video_bitrate_kbps == _VIDEO_BITRATE_FLOOR` | Warn in console (`⚠ Video bitrate floored at 50 kbps; quality will be very poor.`) but proceed |

`effective_dur = (trim_end if trim_end > trim_start else src_dur) − trim_start`, identical to the existing Advanced path.

### Code organization

Existing `_build_ui` (~440 lines) gets refactored into:

- `_build_ui(self)` — header, source row, notebook scaffold, output row, render bar, console
- `_build_simple_tab(self, parent)` — Simple sub-tab body (new)
- `_build_advanced_tab(self, parent)` — Advanced sub-tab body (existing two-column layout moved verbatim into this method)

The existing `_encode_worker` is renamed `_encode_worker_advanced` for symmetry. `_start_render` is updated to dispatch on the active sub-tab.

The new pure helpers (`_simple_compute_settings`, `_lookup_threshold_map`, the lookup-table constants, `SimpleEncodeSettings` dataclass) live next to `_resolve_pixfmt` and `_effective_vp9_deadline` in the existing "Pure helpers" module-level section.

## Files touched

| File | Status | Purpose |
|---|---|---|
| `tabs/social/webm_maker.py` | Modify | Sub-tab refactor, Simple UI, Simple encoder, auto-compute helpers, pre-encode validation |
| `tests/test_webm_maker_logic.py` | Modify | ~6 new unit tests for `_simple_compute_settings` and `_lookup_threshold_map` |
| `locale/en_US.json` | Modify | ~6 new i18n keys for Simple-tab labels (3 profile entries, music-mode label, music-mode hint, "Estimated:" prefix) |

No new files. Code grows by roughly +250 lines net in `webm_maker.py`.

## Out of scope (deliberate)

These webm-for-4chan features stay Advanced-only or are skipped entirely:

- yt-dlp download (`--download`)
- HDR special handling (`--hdr`)
- Subtitle burn-in (`--auto_subs`, `--sub_index`, `--sub_lang`, `--sub_file`)
- Captions with custom font (`--caption`)
- Auto-crop (`--auto_crop`)
- Silence trim (`--trim_silence`)
- Segment concat / cut (`--concat`, `--cut`)
- First-second-every-minute (`--first_second_every_minute`)
- Static image mode (`--static_image`)
- Black-frame skip (`--blackframe`)
- Manual resolution cap (`-r`)
- Manual FPS override (`--fps`)
- Codec choice (libx264, mp4 mode, vp9_vaapi)
- Carbon-copy MKV (`--cc`)
- B-frames, GOP control
- Custom audio/video filter strings
- Audio replace mode (`--audio_replace`)
- 2-pass audio normalization beyond the auto-applied dynaudnorm

If any of these become important they can ship as follow-up work without touching the Simple/Advanced split.

## Acceptance criteria

1. **Sub-tab visible & default.** Opening WebM Maker shows a notebook with `[Simple]` selected and `[Advanced]` available.
2. **Simple controls.** Simple shows 3 profile radios, 1 music-mode checkbox, 2 trim entries, 1 estimated-output line. Nothing else.
3. **Live estimate.** Loading a 5-minute 1080p source, picking "6 MB · audio · up to 6m 40s", music-mode off → estimated line shows roughly `~1248 kbps video · 56 kbps audio · ~720p · 24 fps` (numbers approximate; exact values come from the lookup tables).
4. **Validation.** Picking "4 MB · silent · up to 2m 00s" with a 5-minute source (no trim) and clicking encode → modal error pop-up before any FFmpeg invocation.
5. **Encode dispatch.** Encoding from Simple uses VP9 + two-pass + computed bitrate/resolution/fps; encoding from Advanced uses the existing path. The two encoders never share state mid-encode.
6. **Sub-tab independence.** Setting Advanced's resolution to 4K and switching to Simple → Simple's UI is unaffected. Conversely.
7. **Source/output sharing.** Loading a file in Simple and switching to Advanced → the source path and ffprobe info are still populated; output path is shared too.
8. **All 10 existing pure-helper tests still pass.** The new auto-compute tests bring the file to ~16 passing tests.
