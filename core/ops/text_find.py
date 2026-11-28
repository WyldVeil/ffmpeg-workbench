"""
core/ops/text_find.py - Find text that stays put on a video (handles,
watermarks, names, captions, logos) so the Text Blurrer can offer to blur it.

No model and no download.  Anything laid over a video keeps its edges in the
same place *and facing the same way* (light-to-dark or dark-to-light) in
every frame - even when it's half see-through and the picture behind it
changes.  The picture's own edges move and turn, so over a few seconds
they average out to almost nothing.  FFmpeg samples the video, measures the
signed brightness slope at every pixel, averages it over a rolling stretch
of samples, and returns how strong that average is for every small cell of
a grid.  Strong cells are grouped into word / line boxes, and boxes that
line up from one stretch to the next become one item with a start and end.

A video filmed without moving the camera keeps its own edges too, so there
shape decides instead: only short, wide boxes (lines of text) are kept.

Pure apart from ``sample`` (which runs FFmpeg); ``find`` takes the samples,
so the logic is tested with made-up grids.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from core import ffmpeg as ff
from core import proc as _proc

#: The analysis grid: cells across (each 4 x 4 pixels of a 640-wide copy).
GRID_W = 160
#: Samples to take over the whole video (fewer for very short ones).
SAMPLES = 120
#: A stretch is about this long (seconds), holds at least MIN_FRAMES samples, and
#: stretches overlap by half - anything on screen this long fills one completely.
STRETCH_S = 2.0
MIN_FRAMES = 4

#: A cell belongs to an overlay when its averaged edge strength (0-255) is at least this
STRONG = 40
#: ... in at least this share of a stretch's samples.
SHARE = 0.75
#: Shown straight away when there at least this long (seconds) or this share of the video
SURE_S = 5.0
SURE_SHARE = 0.4
#: ... or when shorter, but text-shaped with edges at least this strong
SURE_STRONG = 160
#: Anything shorter than this (seconds) isn't offered at all
MIN_S = 1.0

#: A stretch whose picture changes less than this (average 0-255 difference
#: between samples) has a still camera: its own edges stay put too.
STILL_SCENE = 4.0


@dataclass(frozen=True, slots=True)
class Grid:
    """One sample: the strength of the edges that stayed put over the last
    stretch, per cell, row by row (0-255), and how much the whole picture
    changed since the sample before (0-255)."""

    t: float
    steady: bytes
    motion: float = 255.0


@dataclass(slots=True)
class Found:
    """Something laid over the video: its box (fractions of the frame), when
    it's there (end None = to the end) and how sure the shape is of being text."""

    x: float
    y: float
    w: float
    h: float
    start: float
    end: float | None
    textlike: bool = True
    #: shown straight away (long-lasting, or short but clearly text); the
    #: rest are offered behind "show less certain finds"
    sure: bool = True

    def padded(self, pad: float = 0.25) -> Found:
        """The box grown by *pad* of its height on every side (blur covers the edges
        and outlines of letters), kept inside the frame."""
        p = self.h * pad
        x, y = max(0.0, self.x - p), max(0.0, self.y - p)
        w = min(1.0, self.x + self.w + p) - x
        h = min(1.0, self.y + self.h + p) - y
        return Found(x, y, w, h, self.start, self.end, self.textlike, self.sure)


def grid_size(width: int, height: int) -> tuple[int, int]:
    gh = max(8, round(GRID_W * height / max(1, width)))
    return GRID_W, gh


def rate_for(duration: float) -> float:
    """Samples a second: SAMPLES over the video, between 0.2 and 4 a second."""
    return min(4.0, max(0.2, SAMPLES / max(duration, 1.0)))


def window_frames(duration: float) -> int:
    """Samples in a stretch (the rolling average's length)."""
    return max(MIN_FRAMES, round(STRETCH_S * rate_for(duration)))


def sample_cmd(src: str, width: int, height: int, duration: float) -> list[str]:
    """Raw grey grids to stdout, one per sample: GRID_W x gh bytes of steady
    edges, then GRID_W bytes of picture change."""
    gw, gh = grid_size(width, height)
    k = window_frames(duration)
    # signed slopes (128 = flat), averaged over the last k samples; how far that
    # average is from flat is the strength of the edges that stayed put
    steady = f"tmix=frames={k},lut=y='min(255,abs(val-128)*4)'"
    graph = (
        f"fps={rate_for(duration):.4f},split=2[v][w];"
        f"[v]scale={gw * 4}:{gh * 4}:flags=area,format=gray,split=2[a][b];"
        f"[a]convolution=0m='0 0 0 -1 0 1 0 0 0':0rdiv=0.5:0bias=128,{steady}[x];"
        f"[b]convolution=0m='0 -1 0 0 0 0 0 1 0':0rdiv=0.5:0bias=128,{steady}[y];"
        f"[x][y]blend=all_mode=lighten,scale={gw}:{gh}:flags=area[s];"
        # one more row: how much each column of the picture changed since the last sample
        f"[w]scale={gw}:{gh}:flags=area,format=gray,tblend=all_mode=difference,"
        f"scale={gw}:1:flags=area[m];"
        f"[s][m]vstack=shortest=1"
    )
    head = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error"]
    if duration > 600:  # long videos: only the keyframes need decoding
        head += ["-skip_frame", "nokey"]
    return [
        *head,
        "-i",
        src,
        "-an",
        "-sn",
        "-dn",
        "-filter_complex",
        graph,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-",
    ]


def sample(
    src: str,
    width: int,
    height: int,
    duration: float,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[float], None] = lambda _f: None,
) -> list[Grid]:
    """Run FFmpeg over *src* and collect the grids."""
    gw, gh = grid_size(width, height)
    size = gw * (gh + 1)  # the grid and the motion row
    cells = gw * gh
    rate = rate_for(duration)
    expected = max(1, int(duration * rate))
    proc = _proc.spawn(
        sample_cmd(src, width, height, duration), stdout=_proc.PIPE, stderr=_proc.DEVNULL
    )
    out = proc.stdout
    assert out is not None  # started with stdout=PIPE
    grids: list[Grid] = []
    buf = b""
    try:
        while True:
            if cancelled():
                ff.kill_tree(proc)
                raise ff.CancelledError("cancelled")
            chunk = out.read(size * 8)
            if not chunk:
                break
            buf += chunk
            while len(buf) >= size:
                row = buf[cells:size]
                grids.append(Grid(len(grids) / rate, buf[:cells], sum(row) / len(row)))
                buf = buf[size:]
            progress(min(1.0, len(grids) / expected))
    finally:
        proc.wait()
    return grids


# ── finding ─────────────────────────────────────────────────────────────────


def _stretches(grids: Sequence[Grid], rate: float) -> list[tuple[int, int]]:
    """Index ranges [a, b) of samples, each about STRETCH_S long, overlapping by half."""
    n = len(grids)
    per = max(MIN_FRAMES, round(STRETCH_S * rate))
    if n <= per:
        return [(0, n)] if n else []
    step = max(1, per // 2)
    out = [(a, a + per) for a in range(0, n - per + 1, step)]
    if out[-1][1] < n:  # the end, too
        out.append((n - per, n))
    return out


def _presence(grids: Sequence[Grid], box: tuple[int, int, int, int], gw: int) -> list[float]:
    """Per sample, the share of *box*'s cells whose edges stayed put."""
    x, y, w, h = box
    cells = [r * gw + c for r in range(y, y + h) for c in range(x, x + w)]
    return [sum(1 for i in cells if g.steady[i] >= STRONG) / len(cells) for g in grids]


def _strength(
    grids: Sequence[Grid], box: tuple[int, int, int, int], gw: int, a: int, b: int
) -> int:
    """How strong the box's edges are: the top quarter of its cells' best value in a..b."""
    x, y, w, h = box
    best = sorted(
        max(g.steady[r * gw + c] for g in grids[a : b + 1])
        for r in range(y, y + h)
        for c in range(x, x + w)
    )
    top = best[-max(1, len(best) // 4) :]
    return round(sum(top) / len(top))


def _one_band(
    grids: Sequence[Grid], box: tuple[int, int, int, int], gw: int, a: int, b: int
) -> bool:
    """A thin box is a line of text when its strong rows touch (letters fill
    their height); two separate strong rows are stacked edges - a border, the
    side of a picture-in-a-frame.  Boxes taller than 6 cells always pass."""
    x, y, w, h = box
    if h > 6:
        return True
    rows = [
        sum(max(g.steady[r * gw + c] for g in grids[a : b + 1]) for c in range(x, x + w)) / w
        for r in range(y, y + h)
    ]
    top = max(rows)
    strong = [i for i, v in enumerate(rows) if v >= top / 2]
    return len(strong) >= 2 and strong[-1] - strong[0] + 1 == len(strong)


def _span(presence: list[float], first: int, last: int) -> tuple[int, int]:
    """The samples [a, b] the thing is really there: grown out from the samples
    *first*..*last* (where it was found) while it stays at least half as present."""
    core = sorted(presence[first : last + 1])
    level = core[len(core) // 2] * 0.5 if core else 0.0
    a, b = first, last
    while a > 0 and presence[a - 1] >= level:
        a -= 1
    while b < len(presence) - 1 and presence[b + 1] >= level:
        b += 1
    while a < b and presence[a] < level:
        a += 1
    while b > a and presence[b] < level:
        b -= 1
    return a, b


def _levels(grids: Sequence[Grid], a: int, b: int) -> list[int]:
    """Per cell, the steady-edge strength it keeps in at least SHARE of samples a..b."""
    k = max(0, (b - a) - int(SHARE * (b - a) + 0.999))  # the sample at that share, from the bottom
    columns = zip(*(g.steady for g in grids[a:b]), strict=True)
    return [sorted(col)[k] for col in columns]


#: Thresholds to look for overlays at: a group too big or too loose to be
#: text at one level is looked inside again at the next (text touching
#: steady scenery joins it at a low level, and stands alone at a higher one)
LEVELS = (STRONG, 70, 110, 160)


def _groups(
    levels: list[int], gw: int, gh: int, still_scene: bool
) -> list[tuple[tuple[int, int, int, int], bool]]:
    """Boxes worth offering (box, looks like text) - see LEVELS."""
    out: list[tuple[tuple[int, int, int, int], bool]] = []
    todo: list[tuple[int, tuple[int, int, int, int] | None]] = [(0, None)]
    while todo:
        depth, inside = todo.pop()
        floor = LEVELS[depth]
        if inside is None:
            mask = [v >= floor for v in levels]
        else:
            x0, y0, w0, h0 = inside
            mask = [
                levels[i] >= floor and x0 <= i % gw < x0 + w0 and y0 <= i // gw < y0 + h0
                for i in range(len(levels))
            ]
        for x, y, w, h, cells in _boxes(mask, gw, gh):
            textlike = _keep((x, y, w, h, cells), gw, gh, still_scene)
            if textlike is not None:
                out.append(((x, y, w, h), textlike))
            elif cells >= 12 and depth + 1 < len(LEVELS):
                todo.append((depth + 1, (x, y, w, h)))
    return out


def _boxes(mask: list[bool], gw: int, gh: int) -> list[tuple[int, int, int, int, int]]:
    """Groups of marked cells, letters joined into words and lines: (x, y, w, h, cells).

    A group whose rows suddenly change width is split there (a handle sitting
    on the edge of a title band, a caption touching a border)."""
    # join letters: grow each cell 2 across and 1 down/up, group the grown shape
    grown = [False] * len(mask)
    for i, m in enumerate(mask):
        if m:
            r, c = divmod(i, gw)
            for rr in range(max(0, r - 1), min(gh, r + 2)):
                for cc in range(max(0, c - 2), min(gw, c + 3)):
                    grown[rr * gw + cc] = True
    seen = [False] * len(mask)
    out: list[tuple[int, int, int, int, int]] = []
    for start in range(len(grown)):
        if not grown[start] or seen[start]:
            continue
        stack: list[int] = [start]
        members: list[int] = []
        seen[start] = True
        while stack:
            i = stack.pop()
            members.append(i)
            r, c = divmod(i, gw)
            for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if 0 <= rr < gh and 0 <= cc < gw:
                    j = rr * gw + cc
                    if grown[j] and not seen[j]:
                        seen[j] = True
                        stack.append(j)
        cells = [i for i in members if mask[i]]  # the box hugs the real cells
        for part in _split_rows(cells, gw):
            rows = [i // gw for i in part]
            cols = [i % gw for i in part]
            x0, y0 = min(cols), min(rows)
            out.append((x0, y0, max(cols) - x0 + 1, max(rows) - y0 + 1, len(part)))
    return out


def _split_rows(cells: list[int], gw: int) -> list[list[int]]:
    """*cells* cut into bands where the width jumps from one row to the next
    (at least double, or half, for two rows running): a handle on the edge of a
    title band, a caption touching a border.  Gradual changes (the slope of a
    big letter A) don't cut."""
    by_row: dict[int, list[int]] = {}
    for i in cells:
        by_row.setdefault(i // gw, []).append(i)
    rows = sorted(by_row)

    def width(r: int) -> int:
        cols = [i % gw for i in by_row[r]]
        return max(cols) - min(cols) + 1

    widths = [width(r) for r in rows]
    bands: list[list[int]] = [[rows[0]]] if rows else []
    for k in range(1, len(rows)):
        before, now = widths[k - 1], widths[k]
        nxt = widths[k + 1] if k + 1 < len(rows) else now
        jump = (now >= 2 * before and nxt >= 2 * before) or (
            before >= 2 * now and before >= 2 * nxt
        )
        if jump and rows[k] - rows[k - 1] <= 2:
            bands.append([rows[k]])
        else:
            bands[-1].append(rows[k])
    return [[i for r in band for i in by_row[r]] for band in bands]


def _keep(box: tuple[int, int, int, int, int], gw: int, gh: int, still_scene: bool) -> bool | None:
    """Whether a group is worth offering: None = drop, else whether it looks like text."""
    _x, _y, w, h, cells = box
    if cells < 6 or h < 3 or w < 3:  # too small for letters
        return None
    if w > 12 * h or h > 12 * w:  # a line: a border, the edge of a picture-in-a-frame
        return None
    if w * h > 0.35 * gw * gh:  # most of the frame: the scene itself
        return None
    if cells / (w * h) < 0.2:  # a sparse scatter, not a word or logo
        return None
    textlike = w >= 1.6 * h and h <= 0.2 * gh and w >= 5
    if still_scene and not (textlike and w >= 3 * h):  # a still camera: only clear lines of text
        return None
    if not textlike and (w < 5 or h < 5 or w > 3 * h or h > 3 * w):  # logos are compact
        return None
    return textlike


def _alike(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    """Two boxes are the same thing, shifted a little, when covering both takes
    at most 1.3 times the bigger one (so a box never creeps over a neighbour
    it was briefly joined to)."""
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    union = (max(a[0] + a[2], b[0] + b[2]) - x0) * (max(a[1] + a[3], b[1] + b[3]) - y0)
    return union <= 1.3 * max(a[2] * a[3], b[2] * b[3])


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


@dataclass(slots=True)
class _Track:
    box: tuple[int, int, int, int]
    first: int  # stretch index
    last: int
    textlike: bool


def _follow(
    grids: Sequence[Grid], stretches: list[tuple[int, int]], gw: int, gh: int
) -> list[_Track]:
    """The boxes of every stretch, joined into tracks where the same thing carries on."""
    tracks: list[_Track] = []
    for k, (a, b) in enumerate(stretches):
        levels = _levels(grids, a, b)
        moves = sorted(g.motion for g in grids[a:b])
        steady_share = sum(1 for v in levels if v >= STRONG) / len(levels)
        still_scene = moves[len(moves) // 2] < STILL_SCENE or steady_share > 0.12
        for box, textlike in _groups(levels, gw, gh, still_scene):
            match = next(
                (
                    tr
                    for tr in tracks
                    if tr.last == k - 1 and _iou(tr.box, box) >= 0.35 and _alike(tr.box, box)
                ),
                None,
            )
            if match is None:
                tracks.append(_Track(box, k, k, textlike))
                continue
            # the same thing still there: grow to cover both (text can shift a cell)
            x, y, w, h = box
            mx, my, mw, mh = match.box
            x0, y0 = min(mx, x), min(my, y)
            match.box = (x0, y0, max(mx + mw, x + w) - x0, max(my + mh, y + h) - y0)
            match.last = k
            match.textlike = match.textlike or textlike
    return tracks


def _found(
    tr: _Track,
    grids: Sequence[Grid],
    stretches: list[tuple[int, int]],
    gw: int,
    gh: int,
    duration: float,
) -> Found | None:
    """What a track becomes: its box, times and how sure it is - or None if not worth offering."""
    step = 1.0 / rate_for(duration)
    x, y, w, h = tr.box
    textlike = w >= 1.6 * h and h <= 0.2 * gh and w >= 5  # by the box it ended up with
    a, b = _span(_presence(grids, tr.box, gw), stretches[tr.first][0], stretches[tr.last][1] - 1)
    # each sample averages the stretch before it: half a stretch earlier is when
    # it really happened; plus a sample's margin each side, since blurring a
    # moment too long is better than showing the text for a moment
    lag = (window_frames(duration) - 1) / 2 * step
    start = 0.0 if a <= 1 else max(0.0, grids[a].t - lag - step)
    end = None if b >= len(grids) - 2 else min(duration, grids[b].t - lag + 2 * step)
    length = (duration if end is None else end) - start
    if length < MIN_S or (not textlike and length < SURE_SHARE * duration):  # logos stay long
        return None
    long_lasting = length >= min(SURE_S, SURE_SHARE * duration) or (end is None and start == 0)
    strong = textlike and w >= 3 * h and _strength(grids, tr.box, gw, a, b) >= SURE_STRONG
    sure = (long_lasting or strong) and _one_band(grids, tr.box, gw, a, b)
    return Found(x / gw, y / gh, w / gw, h / gh, start, end, textlike, sure)


def find(grids: Sequence[Grid], width: int, height: int, duration: float) -> list[Found]:
    """Everything laid over the video that stays put for a stretch or more."""
    if not grids:
        return []
    gw, gh = grid_size(width, height)
    stretches = _stretches(grids, rate_for(duration))
    found: list[Found] = []
    for tr in _follow(grids, stretches, gw, gh):
        if tr.last == tr.first and len(stretches) > 2:  # seen in one stretch only: a passing merge
            continue
        f = _found(tr, grids, stretches, gw, gh, duration)
        if f is not None:
            found.append(f)
    found = _merge_overlapping(found)
    found = [f for f in found if not any(g is not f and _covers(g, f) for g in found)]
    found.sort(key=lambda f: (f.start, f.y, f.x))
    return found


def _times(f: Found) -> tuple[float, float]:
    return f.start, f.end if f.end is not None else float("inf")


def _merge_overlapping(found: list[Found]) -> list[Found]:
    """Two boxes that mostly overlap at mostly the same times are one thing
    found twice: keep one box covering both."""
    items = list(found)
    merged = True
    while merged:
        merged = False
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
                iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
                smaller = min(a.w * a.h, b.w * b.h)
                (sa, ea), (sb, eb) = _times(a), _times(b)
                together = min(ea, eb) - max(sa, sb)
                shorter = min(ea - sa, eb - sb)
                bigger = max(a.w * a.h, b.w * b.h)
                if ix * iy < 0.5 * smaller or together < 0.8 * shorter or bigger > 2 * smaller:
                    continue
                x, y = min(a.x, b.x), min(a.y, b.y)
                end = None if a.end is None or b.end is None else max(a.end, b.end)
                items[i] = Found(
                    x,
                    y,
                    max(a.x + a.w, b.x + b.w) - x,
                    max(a.y + a.h, b.y + b.h) - y,
                    min(a.start, b.start),
                    end,
                    a.textlike or b.textlike,
                    a.sure or b.sure,
                )
                del items[j]
                merged = True
                break
            if merged:
                break
    return items


def _covers(big: Found, small: Found) -> bool:
    """*small* is (almost) inside *big*, at (almost) the same times: one thing found twice."""
    ix = max(0.0, min(big.x + big.w, small.x + small.w) - max(big.x, small.x))
    iy = max(0.0, min(big.y + big.h, small.y + small.h) - max(big.y, small.y))
    if ix * iy < 0.85 * small.w * small.h or big.w * big.h < small.w * small.h:
        return False
    if big.w * big.h > 2 * small.w * small.h:  # a small thing inside a big one: offer both
        return False
    if big.w * big.h == small.w * small.h and id(big) > id(small):  # identical twins: keep one
        return False
    b_end = big.end if big.end is not None else float("inf")
    s_end = small.end if small.end is not None else float("inf")
    if s_end == float("inf"):
        return b_end == float("inf") and big.start <= small.start + 1.0
    overlap = max(0.0, min(b_end, s_end) - max(big.start, small.start))
    return overlap >= 0.8 * (s_end - small.start)
