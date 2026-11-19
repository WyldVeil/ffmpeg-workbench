"""
core/metaclean.py  -  Find and remove personal metadata, losslessly

``inspect(info)`` lists what a file says about where, when, with what and by
whom it was made - container tags (GPS location, phone make/model, dates,
titles, comments, names, software), per-track tags, chapter titles, cover
art, attached files and telemetry tracks (GoPro GPS logs, DJI flight data,
Apple timed metadata, camera motion).

``clean_command(...)`` copies the picture and sound into a fresh container
without any of it - no re-encoding, so quality and size don't change -
while keeping what playback needs: rotation, HDR information, and (if
wanted) the language of each audio/subtitle track.  An optional deep clean
also removes notes hidden inside the video stream (H.264/HEVC "SEI", where
encoders such as x264 write their settings); it is skipped for HDR video,
whose brightness information lives there too.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from core import ffmpeg as ff

LOCATION_KEYS = ("location", "location-eng", "com.apple.quicktime.location.iso6709", "xyz", "gps",
                 "com.apple.quicktime.location.name", "com.apple.quicktime.location.body",
                 "com.apple.quicktime.location.note", "com.apple.quicktime.location.role",
                 "com.android.location")
DEVICE_KEYS = ("make", "model", "com.apple.quicktime.make", "com.apple.quicktime.model",
               "com.android.manufacturer", "com.android.model", "com.apple.quicktime.camera.identifier",
               "com.apple.quicktime.camera.lens_model", "com.apple.quicktime.camera.focal_length.35mm_equivalent",
               "com.apple.quicktime.full-frame-rate-playback-intent", "com.android.capture.fps")
DATE_KEYS = ("creation_time", "date", "com.apple.quicktime.creationdate", "date_recorded", "date_released",
             "year", "modification_date")
SOFTWARE_KEYS = ("encoder", "software", "com.apple.quicktime.software", "com.android.version", "encoded_by",
                 "writing_application", "muxing_application", "handler_name", "vendor_id")
STRUCTURAL_KEYS = ("major_brand", "minor_version", "compatible_brands", "language", "duration",
                   "number_of_frames", "number_of_bytes", "_statistics_tags", "_statistics_writing_app",
                   "_statistics_writing_date_utc", "bps", "rotate")
GENERIC_HANDLERS = ("videohandler", "soundhandler", "subtitlehandler", "datahandler", "", "core media video",
                    "core media audio")
TELEMETRY_TAGS = {"gpmd": "GoPro GPS/sensor log", "djmd": "DJI flight log", "dbgi": "DJI debug data",
                  "mebx": "Apple timed metadata (location, camera)", "camm": "Camera motion data",
                  "tmcd": "Timecode track", "rtmd": "Sony real-time metadata", "fdsc": "GoPro data",
                  "tx3g": "", "text": ""}

CATEGORY_TITLES = {"location": "Location", "device": "Camera / phone", "dates": "Dates", "text": "Titles, names, "
                   "comments", "software": "Software", "chapters": "Chapter titles", "cover": "Cover picture",
                   "telemetry": "Telemetry tracks", "attachments": "Attached files", "other": "Other tags"}


FRIENDLY = {
    "location": "GPS location", "location-eng": "GPS location", "com.apple.quicktime.location.iso6709": "GPS location",
    "com.android.location": "GPS location", "xyz": "GPS location",
    "com.apple.quicktime.make": "Maker", "com.apple.quicktime.model": "Model", "make": "Maker", "model": "Model",
    "com.android.manufacturer": "Maker", "com.android.model": "Model",
    "com.apple.quicktime.software": "Phone software version", "com.android.version": "Android version",
    "creation_time": "Recorded on", "date": "Date", "com.apple.quicktime.creationdate": "Recorded on",
    "encoder": "Made with", "encoded_by": "Encoded by", "handler_name": "Track name",
    "title": "Title", "artist": "Artist", "author": "Author", "comment": "Comment", "description": "Description",
    "copyright": "Copyright", "album_artist": "Album artist", "composer": "Composer", "keywords": "Keywords",
}


@dataclass
class Finding:
    category: str
    label: str
    value: str
    where: str = "file"                     # "file", "track N", "chapters"

    @property
    def name(self) -> str:
        return FRIENDLY.get(self.label.lower(), self.label)

    def line(self) -> str:
        v = self.value if len(self.value) <= 70 else self.value[:67] + "…"
        return f"{CATEGORY_TITLES.get(self.category, self.category)}: {self.name} = {v}  ({self.where})"


def _category(key: str) -> str:
    k = key.lower()
    if k in LOCATION_KEYS or "location" in k or "gps" in k:
        return "location"
    if k in DEVICE_KEYS or k.endswith((".make", ".model")):
        return "device"
    if k in DATE_KEYS or "date" in k or "time" in k:
        return "dates"
    if k in SOFTWARE_KEYS or "encoder" in k or "software" in k:
        return "software"
    return "text"


def inspect(info: ff.MediaInfo) -> list:
    """Everything personal or identifying in *info*."""
    out = []
    for key, value in (info.tags or {}).items():
        if key.lower() in STRUCTURAL_KEYS:
            continue
        out.append(Finding(_category(key), key, str(value)))
    for s in info.streams:
        where = f"track {s.index} ({s.type})"
        tag = (s.raw.get("codec_tag_string") or "").lower()
        if s.type == "data" or (s.type == "subtitle" and tag in ("gpmd", "camm")):
            name = TELEMETRY_TAGS.get(tag) or f"Data track ({tag or s.codec or 'unknown'})"
            out.append(Finding("telemetry", name, tag or s.codec or "data", where))
            continue
        if s.type == "attachment":
            out.append(Finding("attachments", s.tags.get("filename", "attached file"),
                               s.tags.get("mimetype", s.codec or ""), where))
            continue
        if s.attached_pic:
            out.append(Finding("cover", "Cover picture", f"{s.codec} {s.width}×{s.height}", where))
            continue
        for key, value in (s.tags or {}).items():
            k = key.lower()
            if k in STRUCTURAL_KEYS or k.startswith("_statistics") or (k == "handler_name"
                                                                     and str(value).lower() in GENERIC_HANDLERS):
                continue
            if k == "vendor_id" and str(value).strip("[]0 ") == "":
                continue
            out.append(Finding(_category(key), key, str(value), where))
    titled = [c for c in (info.chapters or []) if (c.get("tags") or {}).get("title")]
    if titled:
        names = ", ".join(str(c["tags"]["title"]) for c in titled[:4]) + ("…" if len(titled) > 4 else "")
        out.append(Finding("chapters", f"{len(titled)} chapter title(s)", names, "chapters"))
    return out


def _is_hdr(s) -> bool:
    return (s.color_transfer or "") in ("smpte2084", "arib-std-b67")


def clean_command(info: ff.MediaInfo, src: str, out: str, *, keep_language: bool = True,
                  keep_subtitles: bool = True, keep_chapters: bool = False, keep_cover: bool = False,
                  deep: bool = False) -> tuple:
    """(argv, notes) for a lossless copy without the metadata."""
    notes = []
    argv = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-i", src]
    kept = []                                   # streams in output order
    for s in info.streams:
        if s.type == "video" and not s.attached_pic:
            kept.append(s)
        elif s.type == "audio":
            kept.append(s)
        elif s.type == "subtitle" and keep_subtitles and (s.raw.get("codec_tag_string") or "").lower() not in (
                "gpmd", "camm"):
            kept.append(s)
        elif s.attached_pic and keep_cover:
            kept.append(s)
    ext = os.path.splitext(out)[1].lower()
    ass = any(s.type == "subtitle" and s.codec in ("ass", "ssa") for s in kept)
    fonts = [s for s in info.streams if s.type == "attachment" and ass and ext == ".mkv"
             and "font" in (s.tags.get("mimetype", "") + s.codec).lower()]
    for s in kept + fonts:
        argv += ["-map", f"0:{s.index}"]
    argv += ["-c", "copy", "-map_metadata", "-1", "-map_chapters", "0" if keep_chapters else "-1",
             "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact"]
    if keep_chapters:
        notes.append("Chapters kept (their titles too).")
    for n, s in enumerate(kept):
        lang = (s.tags or {}).get("language", "")
        if keep_language and lang and lang != "und" and s.type in ("audio", "subtitle"):
            argv += [f"-metadata:s:{n}", f"language={lang}"]
        if s.attached_pic:
            argv += [f"-disposition:{n}", "attached_pic"]
    if deep:
        for n, s in enumerate(x for x in kept if x.type == "video" and not x.attached_pic):
            if s.codec == "h264":
                argv += [f"-bsf:v:{n}", "filter_units=remove_types=6"]
            elif s.codec == "hevc" and not _is_hdr(s):
                argv += [f"-bsf:v:{n}", "filter_units=remove_types=39|40"]
            elif s.codec == "hevc":
                notes.append("HDR video: its stream notes were kept (they carry the brightness information).")
    for s in info.streams:
        if s.type == "data" or (s.type == "subtitle" and (s.raw.get("codec_tag_string") or "").lower() in
                                ("gpmd", "camm")):
            notes.append(f"Removed {TELEMETRY_TAGS.get((s.raw.get('codec_tag_string') or '').lower()) or 'a data track'}.")
    if ext in (".mp4", ".mov", ".m4v", ".m4a"):
        argv += ["-movflags", "+faststart"]
    return argv + [out], notes


def leftovers(after: list, *, keep_chapters: bool = False, keep_cover: bool = False,
              keep_subtitles: bool = True) -> list:
    """Findings still in the cleaned file that weren't asked to be kept."""
    out = []
    for f in after:
        if (f.category == "chapters" and keep_chapters) or (f.category == "cover" and keep_cover):
            continue
        if f.category == "attachments" and keep_subtitles and "font" in (f.value + f.label).lower():
            continue                                    # fonts that styled subtitles need
        out.append(f)
    return out


ENCODER_SIGNATURES = {b"x264 - core": "x264 encoder settings", b"x265 (build": "x265 encoder settings",
                      b"HandBrake": "HandBrake version",
                      b"Apple iPhone": "Apple device name", b"GoPro": "GoPro name"}


def stream_notes(path: str, limit: int = 4 << 20) -> list:
    """Encoder / device notes written inside the media data itself (not tags)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(limit)
    except OSError:
        return []
    out = []
    for sig, label in ENCODER_SIGNATURES.items():
        i = head.find(sig)
        if i >= 0:
            text = head[i:i + 60].split(b"\x00")[0].decode("ascii", "replace")
            out.append(Finding("software", label, "".join(c if c.isprintable() else " " for c in text).strip(),
                               "inside the video/audio data"))
    return out


def free_output(src: str, folder: Optional[str] = None) -> str:
    base, ext = os.path.splitext(os.path.basename(src))
    path = os.path.join(folder or os.path.dirname(src), f"{base} (clean){ext}")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder or os.path.dirname(src), f"{base} (clean {n}){ext}")
        n += 1
    return path
