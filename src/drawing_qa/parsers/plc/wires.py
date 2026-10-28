"""Abbreviated wire numbers on schematic sheets.

Schematics write wire numbers short: "D6" is wire TN36D6 on sheet 036 (FFI + this
sheet + grid), and "15D5" is TN15D5, a wire from sheet 015. The FFI comes from the
sheet's "WIRE NUMBER / TN / FIRST FIELD IDENTIFIER" legend.

A same-sheet abbreviation must sit near the grid cell it names (the convention says
the number is its location), which also stops other short tokens such as the module
terminal "T0" from being read as wires.

A number names the cell where its wire starts, but the label may sit anywhere along the wire
(401103-015: 'K2' labelled at C2, its wire starting at the power supply at K2). For a label
outside its cell, the wire it sits on is followed through corners and junctions, and the
cells of its ends are recorded (value["wire_ends"]).
"""

from __future__ import annotations

import math
import re

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import EntityType, Predicate
from drawing_qa.model.source import Drawing
from drawing_qa.parsers.base import DrawingParser, ParseContext
from drawing_qa.pdf.extract import PageContent, Segment

_SAME_SHEET = re.compile(r"^(?P<grid>[A-Z]\d)$")
_OTHER_SHEET = re.compile(r"^(?P<sheet>\d{2,3})(?P<grid>[A-Z]\d)$")
_FFI = re.compile(r"^[A-Z]{2}$")
_ROWS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_ON_WIRE = 10.0  # furthest a wire label sits from its wire, in points
_JOIN = 1.0  # wire pieces meeting within this distance are one wire
_MAX_PIECES = 60  # a wire followed further than this is a bus: its ends say nothing


def sheet_ffi(page: PageContent) -> str | None:
    """The FFI shown in the sheet's wire number legend, e.g. 'TN'."""
    label = next((ln for ln in page.lines if ln.text.strip().upper() == "FIRST FIELD"), None)
    if label is None:
        return None
    lx, ly = (label.bbox[0] + label.bbox[2]) / 2, (label.bbox[1] + label.bbox[3]) / 2
    candidates = [
        ln for ln in page.lines if _FFI.match(ln.text.strip()) and math.hypot((ln.bbox[0] + ln.bbox[2]) / 2 - lx, (ln.bbox[1] + ln.bbox[3]) / 2 - ly) < 100
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda ln: math.hypot((ln.bbox[0] + ln.bbox[2]) / 2 - lx, (ln.bbox[1] + ln.bbox[3]) / 2 - ly)).text.strip()


def _near(encoded: str, cell: str) -> bool:
    """Wire number text sits on the wire it names: next to its cell, or along a straight run
    from it (same column for vertical runs such as supply rails, same row for horizontal ones)."""
    if encoded[0] not in _ROWS or cell[0] not in _ROWS:
        return False
    dr = _ROWS.index(encoded[0]) - _ROWS.index(cell[0])
    dc = int(encoded[1:]) - int(cell[1:])
    return (-1 <= dr <= 2 and abs(dc) <= 1) or dc == 0 or dr == 0


def _beside_cell(encoded: str, cell: str) -> bool:
    dr = _ROWS.index(encoded[0]) - _ROWS.index(cell[0])
    return -1 <= dr <= 2 and abs(int(encoded[1:]) - int(cell[1:])) <= 1


def _boxed(bbox, segments) -> bool:
    """Text framed on all four sides, like the A1 / T1 terminal markings on a relay symbol
    (401103-018): a terminal name, not a wire number. A wire label touches at most its wire."""
    x0, y0, x1, y1 = bbox

    def side(at: float, lo: float, hi: float, vertical: bool, outward: int) -> bool:
        for s in segments:
            if s.width == 0:
                continue  # SHX letter strokes and invisible text outlines, not line work
            if vertical and s.vertical:
                pos, a, b = s.x0, min(s.y0, s.y1), max(s.y0, s.y1)
            elif not vertical and s.horizontal:
                pos, a, b = s.y0, min(s.x0, s.x1), max(s.x0, s.x1)
            else:
                continue
            if -1 <= (pos - at) * outward <= 4.5 and a <= lo + 1 and b >= hi - 1:
                return True
        return False

    return side(x0, y0, y1, True, -1) and side(x1, y0, y1, True, 1) and side(y0, x0, x1, False, -1) and side(y1, x0, x1, False, 1)


def expand_wires(page: PageContent, drawing: Drawing, ctx: ParseContext, parser: DrawingParser) -> list[Claim]:
    ffi = sheet_ffi(page)
    if ffi is None or drawing.grid is None or drawing.sheet_number is None:
        return []
    this_sheet = f"{drawing.sheet_number:02d}"
    claims = []
    for line in page.lines:
        if len(line.words) != 1:
            continue
        text = line.text.strip().upper()
        x0, y0, x1, y1 = line.bbox
        if (x0 + x1) / 2 > page.width * 0.45 and (y0 + y1) / 2 > page.height * 0.8:
            continue  # title block
        if m := _SAME_SHEET.match(text):
            cell = drawing.grid.cell_at((x0 + x1) / 2, (y0 + y1) / 2)
            if not _near(m["grid"], cell):
                continue
            if not _beside_cell(m["grid"], cell) and _boxed(line.bbox, page.segments):
                continue
            sheet, grid = this_sheet, m["grid"]
        elif m := _OTHER_SHEET.match(text):
            sheet, grid = m["sheet"], m["grid"]
        else:
            continue
        value = {"ffi": ffi, "sheet": sheet, "grid": grid, "abbreviated": text, "is_variant": False}
        if sheet == this_sheet and not _beside_cell(grid, drawing.grid.cell_at((x0 + x1) / 2, (y0 + y1) / 2)):
            ends = _wire_ends(line.bbox, page.segments)
            if ends:
                value["wire_ends"] = sorted({drawing.grid.cell_at(x, y) for x, y in ends})
        claims.append(
            ctx.claim(
                parser=parser.doc_type,
                parser_version=parser.version,
                drawing=drawing,
                method=line.words[0].method,
                bbox=line.bbox,
                raw_text=line.text,
                subject_type=EntityType.WIRE,
                subject_key=f"{ffi}{sheet}{grid}",
                predicate=Predicate.OCCURS,
                value=value,
            )
        )
    return claims


def _straight(s: Segment) -> bool:
    return s.width > 0 and (s.horizontal or s.vertical) and s.length > 0.5  # width 0: text strokes, note outlines


def _on(p: tuple[float, float], s: Segment) -> bool:
    """The point lies on the segment (an end of it, or a junction along it)."""
    x, y = p
    if s.horizontal:
        return abs(y - s.y0) <= _JOIN and min(s.x0, s.x1) - _JOIN <= x <= max(s.x0, s.x1) + _JOIN
    return abs(x - s.x0) <= _JOIN and min(s.y0, s.y1) - _JOIN <= y <= max(s.y0, s.y1) + _JOIN


def _wire_ends(label, segments: list[Segment]) -> list[tuple[float, float]]:
    """Ends of the wire the label sits on: the nearest straight line within _ON_WIRE, followed through every
    line of its stroke width that meets it end to end or at a junction. A device's own line work (a contact,
    a terminal circle) is drawn otherwise and ends the wire. Empty when there is no wire, or it is a bus."""
    x0, y0, x1, y1 = label
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

    def distance(s: Segment) -> float:
        if s.horizontal:
            return max(min(s.x0, s.x1) - cx, cx - max(s.x0, s.x1), 0) + max(abs(s.y0 - cy) - (y1 - y0) / 2, 0)
        return max(min(s.y0, s.y1) - cy, cy - max(s.y0, s.y1), 0) + max(abs(s.x0 - cx) - (x1 - x0) / 2, 0)

    lines = [s for s in segments if _straight(s)]
    start = min((s for s in lines if s.length > 8), key=distance, default=None)
    if start is None or distance(start) > _ON_WIRE:
        return []
    same = [s for s in lines if abs(s.width - start.width) < 0.01]
    wire, todo = {id(start): start}, [start]
    while todo:
        s = todo.pop()
        points = ((s.x0, s.y0), (s.x1, s.y1))
        for t in same:
            if id(t) not in wire and (any(_on(p, t) for p in points) or any(_on(q, s) for q in ((t.x0, t.y0), (t.x1, t.y1)))):
                wire[id(t)] = t
                todo.append(t)
                if len(wire) > _MAX_PIECES:
                    return []
    ends = [p for s in wire.values() for p in ((s.x0, s.y0), (s.x1, s.y1))]
    # A free end meets no other piece of the wire; one where pieces meet is a corner or a junction.
    return [p for p in ends if sum(_on(p, t) for t in wire.values()) == 1]
