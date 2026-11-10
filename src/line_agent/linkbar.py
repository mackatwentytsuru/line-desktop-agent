"""Find the gray link bar under a photo / file row in a rendered row image.

LINE draws "保存 | 名前を付けて保存 | 転送 | Keepメモに転送" under a not yet saved
photo or file, and "フォルダを開く | 転送 | Keepメモに転送" after saving. These are
not UI Automation elements, so the bar is found from pixels (read-only).

find_bar(img) -> list of (x0, x1, y_mid) label boxes in image coordinates.
save_point(img) -> (x, y) of the first label only when the bar has exactly 4
labels and the 2nd is the widest (the 名前を付けて保存 shape); otherwise None.
"""
from __future__ import annotations

from typing import Optional

INK_MAX = 235  # min(r,g,b) below this is ink (text, ClearType fringe, picture)
BLACK_MAX = 110  # max(r,g,b) below this is black text (not a gray link)
CORE_SHARE = 0.15  # share of neutral mid-gray pixels in the band (ClearType: about 23%)
WORD_GAP = 6  # columns of white that split two labels
SEP_MAX_W = 3  # the thin "|" between labels
BAND_MAX_H = 30


def _ink(p) -> bool:
    return min(p[:3]) < INK_MAX


def _core(p) -> bool:
    r, g, b = p[:3]
    return max(r, g, b) - min(r, g, b) <= 12 and 120 <= r <= 200


def _band(img) -> Optional[tuple]:
    """The lowest band of ink in the row (y0, y1), if it looks like gray links."""
    w, h = img.size
    px = img.load()
    ink_rows = [any(_ink(px[x, y]) for x in range(w)) for y in range(h)]
    y1 = next((y for y in range(h - 1, -1, -1) if ink_rows[y]), None)
    if y1 is None:
        return None
    y0 = y1
    while y0 - 1 >= 0 and (ink_rows[y0 - 1] or (y0 - 2 >= 0 and ink_rows[y0 - 2])):
        y0 -= 1
    if y1 - y0 + 1 > BAND_MAX_H:
        return None
    pix = [px[x, y] for y in range(y0, y1 + 1) for x in range(w)]
    ink = [p for p in pix if _ink(p)]
    if not ink or sum(map(_core, ink)) < CORE_SHARE * len(ink):
        return None
    if sum(1 for p in ink if max(p[:3]) < BLACK_MAX) > 0.02 * len(ink):
        return None
    return (y0, y1)


def find_bar(img) -> list:
    band = _band(img)
    if band is None:
        return []
    y0, y1 = band
    w, _ = img.size
    px = img.load()
    cols = [any(_ink(px[x, y]) for y in range(y0, y1 + 1)) for x in range(w)]
    runs, start, gap, last = [], None, 0, 0
    for x, ink in enumerate(cols + [False] * (WORD_GAP + 1)):
        if ink:
            if start is None:
                start = x
            gap = 0
            last = x
        elif start is not None:
            gap += 1
            if gap >= WORD_GAP:
                runs.append((start, last))
                start = None
    labels = [(a, b) for a, b in runs if b - a + 1 > SEP_MAX_W]
    ym = (y0 + y1) // 2
    return [(a, b, ym) for a, b in labels]


def save_point(img) -> Optional[tuple]:
    bar = find_bar(img)
    if len(bar) != 4:
        return None
    widths = [b - a for a, b, _ in bar]
    if widths[1] != max(widths) or widths[0] >= widths[1] / 2:
        return None
    a, b, ym = bar[0]
    return ((a + b) // 2, ym)
