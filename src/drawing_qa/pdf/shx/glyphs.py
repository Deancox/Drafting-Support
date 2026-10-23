"""Grouping a page's small vector paths into letters, and fingerprinting each letter.

A letter's paths are drawn together and touch each other; neighbouring letters do not touch.
They are not always drawn in an order where each touches the last (an H's two uprights come
before its crossbar), so paths are joined when they touch any of the few drawn just before.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from drawing_qa.model.source import BBox

GlyphKey = tuple


@dataclass(frozen=True)
class Glyph:
    box: BBox
    key: GlyphKey
    first_seq: int


def _points(path: dict) -> list:
    pts = []
    for item in path["items"]:
        op = item[0]
        if op == "l":
            pts += [item[1], item[2]]
        elif op == "c":
            pts += [item[1], item[2], item[3], item[4]]
        elif op == "re":
            pts += [item[1].tl, item[1].br]
        elif op == "qu":
            pts += [item[1].ul, item[1].ur, item[1].lr, item[1].ll]
    return pts


def _touch(a: pymupdf.Rect, b: pymupdf.Rect, t: float) -> bool:
    # Straight strokes have zero-width boxes, which Rect.intersects treats as empty.
    return a.x0 - t <= b.x1 and b.x0 - t <= a.x1 and a.y0 - t <= b.y1 and b.y0 - t <= a.y1


def glyph_groups(
    page: pymupdf.Page, *, window: int = 12, max_size: float = 40.0, touch: float = 0.3, quantum: float = 0.25
) -> list[Glyph]:
    """Letter candidates on the page, in drawing order. Line work larger than max_size is ignored."""
    # Letters are stroked with a width; filled shapes (a logo) and invisible text-note outlines are not.
    paths = sorted((d for d in page.get_drawings() if d.get("width")), key=lambda d: d["seqno"])
    rects = [pymupdf.Rect(d["rect"]) for d in paths]
    small = [r.width <= max_size and r.height <= max_size for r in rects]
    parent = list(range(len(paths)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(paths)):
        if not small[i]:
            continue
        for j in range(max(0, i - window), i):
            if small[j] and paths[j].get("width") == paths[i].get("width") and _touch(rects[i], rects[j], touch):
                parent[find(i)] = find(j)

    members: dict[int, list[int]] = {}
    for i in range(len(paths)):
        if small[i]:
            members.setdefault(find(i), []).append(i)

    glyphs = []
    for idx in sorted(members.values(), key=lambda ix: paths[ix[0]]["seqno"]):
        box = pymupdf.Rect(rects[idx[0]])
        for i in idx[1:]:
            box |= rects[i]
        pts = sorted((round((p.x - box.x0) / quantum), round((p.y - box.y0) / quantum)) for i in idx for p in _points(paths[i]))
        key = (round(box.width / quantum), round(box.height / quantum), len(idx), tuple(pts))
        glyphs.append(Glyph(box=(box.x0, box.y0, box.x1, box.y1), key=key, first_seq=paths[idx[0]]["seqno"]))
    return glyphs
