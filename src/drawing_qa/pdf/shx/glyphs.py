"""Grouping a page's small vector paths into letters, and fingerprinting each letter.

A letter's paths are drawn together and touch each other; neighbouring letters do not touch.
They are not always drawn in an order where each touches the last (an H's two uprights come
before its crossbar), so paths are joined when they touch any of the few drawn just before.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

from drawing_qa.model.source import BBox

GlyphKey = tuple


@dataclass(frozen=True)
class Glyph:
    box: BBox
    key: GlyphKey
    first_seq: int
    on_line: bool = False  # touches a long stroke (a wire, a frame): a symbol or a letter overdrawn, not text
    # Its points from the box's corner, unrounded, in the order drawn: the shape at any scale (SHX scales its
    # strokes), and the order tells apart letters with the same corners (N and Z).
    points: tuple[tuple[float, float], ...] = field(default=(), compare=False)


def _segments(path: dict) -> list[tuple]:
    """Straight pieces of a path as (x0, y0, x1, y1); curves as the box of their points."""
    out = []
    for item in path["items"]:
        if item[0] == "l":
            out.append((item[1].x, item[1].y, item[2].x, item[2].y))
        else:
            pts = _points({"items": [item]})
            if pts:
                xs, ys = [q.x for q in pts], [q.y for q in pts]
                out += [(min(xs), min(ys), max(xs), min(ys)), (max(xs), min(ys), max(xs), max(ys)),
                        (max(xs), max(ys), min(xs), max(ys)), (min(xs), max(ys), min(xs), min(ys))]
    return out


def _hits(seg: tuple, box: tuple) -> bool:
    """Whether a straight segment passes through a box (Liang-Barsky clipping)."""
    x0, y0, x1, y1 = seg
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - box[0]), (dx, box[2] - x0), (-dy, y0 - box[1]), (dy, box[3] - y0)):
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return False
    return True


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

    cell = 20.0
    long_segments: dict[tuple[int, int], list[tuple]] = {}
    for i, d in enumerate(paths):
        if small[i]:
            continue
        for seg in _segments(d):
            for cx in range(int(min(seg[0], seg[2]) // cell), int(max(seg[0], seg[2]) // cell) + 1):
                for cy in range(int(min(seg[1], seg[3]) // cell), int(max(seg[1], seg[3]) // cell) + 1):
                    long_segments.setdefault((cx, cy), []).append(seg)

    def on_line(box: pymupdf.Rect) -> bool:
        grown = (box.x0 - touch, box.y0 - touch, box.x1 + touch, box.y1 + touch)
        near = {seg for cx in range(int(grown[0] // cell), int(grown[2] // cell) + 1)
                for cy in range(int(grown[1] // cell), int(grown[3] // cell) + 1) for seg in long_segments.get((cx, cy), ())}
        return any(_hits(seg, grown) for seg in near)

    glyphs = []
    for idx in sorted(members.values(), key=lambda ix: paths[ix[0]]["seqno"]):
        # By coordinates: PyMuPDF's union skips the zero-area boxes of straight strokes.
        box = pymupdf.Rect(min(rects[i].x0 for i in idx), min(rects[i].y0 for i in idx),
                           max(rects[i].x1 for i in idx), max(rects[i].y1 for i in idx))
        raw = [(p.x - box.x0, p.y - box.y0) for i in idx for p in _points(paths[i])]
        pts = sorted((round(x / quantum), round(y / quantum)) for x, y in raw)
        key = (round(box.width / quantum), round(box.height / quantum), len(idx), tuple(pts))
        glyphs.append(Glyph(box=(box.x0, box.y0, box.x1, box.y1), key=key, first_seq=paths[idx[0]]["seqno"], on_line=on_line(box),
                            points=tuple(raw)))
    return glyphs
