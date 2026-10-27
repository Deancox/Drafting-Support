"""Line geometry for cable block diagrams.

AutoCAD plots SHX text as line strokes, so a CBD page has ~17,000 segments, half of them
letters. What is left after removing text strokes:
  - enclosure outlines (JBs, LCSs, panels): thin (~0.24 pt) dash-dot lines forming rectangles;
  - cables: thick (~0.96 pt) straight runs, broken only where the cable label box sits on them.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from drawing_qa.model.source import BBox
from drawing_qa.pdf.extract import Segment

_BUCKET = 25.0


def strip_text_strokes(segments: list[Segment], text_boxes: list[BBox], pad: float = 2.0) -> list[Segment]:
    """Drop segments that lie inside a text box (SHX letter strokes)."""
    index: dict[tuple[int, int], list[BBox]] = defaultdict(list)
    for x0, y0, x1, y1 in text_boxes:
        box = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
        for bx in range(int(box[0] // _BUCKET), int(box[2] // _BUCKET) + 1):
            for by in range(int(box[1] // _BUCKET), int(box[3] // _BUCKET) + 1):
                index[(bx, by)].append(box)

    def inside(x: float, y: float, box: BBox) -> bool:
        return box[0] <= x <= box[2] and box[1] <= y <= box[3]

    kept = []
    for s in segments:
        boxes = index.get((int(s.x0 // _BUCKET), int(s.y0 // _BUCKET)), [])
        if any(inside(s.x0, s.y0, b) and inside(s.x1, s.y1, b) for b in boxes):
            continue
        kept.append(s)
    return kept


@dataclass
class Run:
    """A straight run merged from collinear pieces (dashes, or a cable broken by its label box)."""

    horizontal: bool
    pos: float  # y for horizontal runs, x for vertical
    start: float  # x0 / y0
    end: float  # x1 / y1
    pieces: int = 1
    spans: list[tuple[float, float]] = field(default_factory=list)  # the drawn pieces it was merged from

    @property
    def length(self) -> float:
        return self.end - self.start

    @property
    def ends(self) -> tuple[tuple[float, float], tuple[float, float]]:
        if self.horizontal:
            return (self.start, self.pos), (self.end, self.pos)
        return (self.pos, self.start), (self.pos, self.end)


def merge_runs(segments: list[Segment], max_gap: float) -> list[Run]:
    """Merge axis-aligned collinear segments whose gaps are at most max_gap."""
    groups: dict[tuple[bool, float], list[tuple[float, float]]] = defaultdict(list)
    for s in segments:
        if s.horizontal:
            groups[(True, round(s.y0 * 2) / 2)].append((min(s.x0, s.x1), max(s.x0, s.x1)))
        elif s.vertical:
            groups[(False, round(s.x0 * 2) / 2)].append((min(s.y0, s.y1), max(s.y0, s.y1)))
    runs = []
    for (horizontal, pos), spans in groups.items():
        spans.sort()
        cur = Run(horizontal, pos, *spans[0], spans=[spans[0]])
        for a, b in spans[1:]:
            if a - cur.end <= max_gap:
                cur.end = max(cur.end, b)
                cur.pieces += 1
                cur.spans.append((a, b))
            else:
                runs.append(cur)
                cur = Run(horizontal, pos, a, b, spans=[(a, b)])
        runs.append(cur)
    return runs


def split_at_corners(runs: list[Run], segments: list[Segment], tol: float = 1.0) -> list[Run]:
    """Undo joins across a gap where perpendicular lines end at both edges of the gap: that is the bottom
    corner of one box and the top corner of the next, stacked a few points apart, not a dash-dot gap
    (nothing ends at a dash-dot gap). Each such run is cut there."""
    buckets: dict[tuple[bool, int, int], list[tuple[float, float]]] = defaultdict(list)
    for s in segments:
        if s.horizontal or s.vertical:
            for x, y in ((s.x0, s.y0), (s.x1, s.y1)):
                buckets[(s.horizontal, round(x), round(y))].append((x, y))

    def cornered(run: Run, at: float) -> bool:
        x, y = (at, run.pos) if run.horizontal else (run.pos, at)
        return any(abs(px - x) <= tol and abs(py - y) <= tol
                   for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                   for px, py in buckets.get((not run.horizontal, round(x) + dx, round(y) + dy), ()))

    out: list[Run] = []
    for run in runs:
        spans = sorted(run.spans)
        piece = [spans[0]]
        for a, b in zip(spans, spans[1:]):
            if b[0] - a[1] > 0.5 and cornered(run, a[1]) and cornered(run, b[0]):
                out.append(Run(run.horizontal, run.pos, piece[0][0], max(e for _, e in piece), len(piece), piece))
                piece = []
            piece.append(b)
        out.append(Run(run.horizontal, run.pos, piece[0][0], max(e for _, e in piece), len(piece), piece))
    return out


@dataclass
class Box:
    bbox: BBox
    runs: list[Run] = field(default_factory=list)
    named: bool = False  # set while splitting stacked boxes

    @property
    def area(self) -> float:
        return (self.bbox[2] - self.bbox[0]) * (self.bbox[3] - self.bbox[1])

    def contains(self, x: float, y: float, pad: float = 0.0) -> bool:
        x0, y0, x1, y1 = self.bbox
        return x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad

    def on_edge(self, x: float, y: float, tol: float = 6.0) -> bool:
        x0, y0, x1, y1 = self.bbox
        if not self.contains(x, y, tol):
            return False
        return min(abs(x - x0), abs(x - x1), abs(y - y0), abs(y - y1)) <= tol


def find_boxes(runs: list[Run], corner_tol: float = 6.0, min_size: float = 25.0) -> list[Box]:
    """Rectangles made of runs whose ends meet at corners (dash-dot enclosure outlines)."""
    parent = list(range(len(runs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    ends = [r.ends for r in runs]
    # Each end meets the nearest perpendicular end within corner_tol: with boxes stacked a few points
    # apart, a side's end is near the corners of both boxes, and only the nearer one is its own.
    for i in range(len(runs)):
        for a in ends[i]:
            best, best_d = None, None
            for j in range(len(runs)):
                if runs[i].horizontal == runs[j].horizontal:
                    continue
                for b in ends[j]:
                    if abs(a[0] - b[0]) <= corner_tol and abs(a[1] - b[1]) <= corner_tol:
                        d = abs(a[0] - b[0]) + abs(a[1] - b[1])
                        if best_d is None or d < best_d:
                            best, best_d = j, d
            if best is not None:
                parent[find(i)] = find(best)
    groups: dict[int, list[Run]] = defaultdict(list)
    for i, r in enumerate(runs):
        groups[find(i)].append(r)
    boxes = []
    for members in groups.values():
        if len(members) < 3:
            continue
        xs = [p[0] for r in members for p in r.ends]
        ys = [p[1] for r in members for p in r.ends]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        if bbox[2] - bbox[0] >= min_size and bbox[3] - bbox[1] >= min_size:
            boxes.append(Box(bbox, members))
    boxes += _wall_pairs(runs, boxes)
    return boxes


def _wall_pairs(runs: list[Run], boxes: list[Box], min_len: float = 100.0, tol: float = 8.0) -> list[Box]:
    """An enclosure drawn with only its two side walls as outline (top and bottom are continuation
    lines to the next sheet, e.g. the LTL-PLC panel on 401310-015): two long parallel vertical runs
    with the same extent."""
    used = {id(r) for b in boxes for r in b.runs}
    walls = sorted((r for r in runs if not r.horizontal and r.length >= min_len and id(r) not in used), key=lambda r: r.pos)
    out = []
    taken: set[int] = set()
    for i, left in enumerate(walls):
        if id(left) in taken:
            continue
        for right in walls[i + 1 :]:
            if id(right) in taken or not (20 <= right.pos - left.pos <= 400):
                continue
            if abs(right.start - left.start) <= tol and abs(right.end - left.end) <= tol:
                taken |= {id(left), id(right)}
                out.append(Box((left.pos, min(left.start, right.start), right.pos, max(left.end, right.end)), [left, right]))
                break
    return out


def split_at_boxes(runs: list[Run], boxes: list[Box]) -> list[Run]:
    """Cut runs where they cross an enclosure. A narrow JB (28 pt on 401310-016) is narrower than the
    gap allowed for a cable's label box, so two cables either side of it would otherwise join up."""
    out = []
    for run in runs:
        pieces = [run]
        for box in boxes:
            x0, y0, x1, y1 = box.bbox
            nxt = []
            for r in pieces:
                # Cut only where the drawn line stops at the box (a gap across it). A line drawn straight
                # through the box (S-TN44D4 across SH-LCS8 on 401310-014) passes it by.
                mid = (x0 + x1) / 2
                drawn_through = any(a <= mid <= b for a, b in r.spans)
                if r.horizontal and y0 <= r.pos <= y1 and r.start < x0 - 2 and r.end > x1 + 2 and not drawn_through:
                    left = [s for s in r.spans if s[1] <= x0 + 2]
                    right = [s for s in r.spans if s[0] >= x1 - 2]
                    nxt += [Run(True, r.pos, r.start, x0, spans=left), Run(True, r.pos, x1, r.end, spans=right)]
                else:
                    nxt.append(r)
            pieces = nxt
        out += pieces
    return out


@dataclass
class Cable:
    runs: list[Run]
    ends: list[tuple[float, float]]  # free ends of the polyline


def find_cables(runs: list[Run], join_tol: float = 2.5) -> list[Cable]:
    """Join thick runs that meet end to end (L-shaped cables) and return each polyline's free ends."""
    parent = list(range(len(runs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def touches(p: tuple[float, float], q: tuple[float, float]) -> bool:
        return abs(p[0] - q[0]) <= join_tol and abs(p[1] - q[1]) <= join_tol

    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            if any(touches(a, b) for a in runs[i].ends for b in runs[j].ends):
                parent[find(i)] = find(j)
    groups: dict[int, list[Run]] = defaultdict(list)
    for i, r in enumerate(runs):
        groups[find(i)].append(r)
    cables = []
    for members in groups.values():
        points = [p for r in members for p in r.ends]
        free = [p for p in points if sum(touches(p, q) for q in points) == 1]
        cables.append(Cable(members, free or points[:2]))
    return cables
