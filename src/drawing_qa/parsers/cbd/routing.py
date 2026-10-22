"""Cable block diagram routing reader.

From the page geometry (see geometry.py):
  1. enclosures = dash-dot rectangles, named by the designation inside (or just above) them,
     e.g. "=K07.11+RCL-JB1" -> RCL-JB1; devices whose tags sit inside are IN_ENCLOSURE;
  2. cables = thick runs; each free end is attached to the enclosure whose outline it meets,
     or to the field device whose tag is next to the end (devices drawn outside any box);
  3. each cable gets the spec ("12C+E 1.5mm² PVC") and number ("CXXXXX") written on it.
Every cable becomes a ROUTED claim between its two ends.
"""

from __future__ import annotations

import math
import re

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import EntityType, ExtractionMethod, Predicate
from drawing_qa.model.source import BBox, Drawing
from drawing_qa.parsers.base import DrawingParser, ParseContext, ParseResult, TemplateReader
from drawing_qa.parsers.cbd.geometry import Box, Cable, find_boxes, find_cables, merge_runs, split_at_boxes
from drawing_qa.pdf.extract import PageContent

THIN_MAX = 0.3  # enclosure outline stroke width
THICK_MIN = 0.9  # cable stroke width
SOLID_MIN = 0.3  # solid outline (symbol boxes, some enclosures)
CABLE_LABEL_GAP = 40.0  # a cable is broken by its label box; rejoin across gaps up to this
_SPEC = re.compile(r"\b\d+\s*(C|PR|TPR)\b|MM|PVC|FIBRE|SCR", re.I)
_CABLE_NO = re.compile(r"^[CP][0-9X]{4,}$")
_ENCLOSURE_KINDS = (EntityType.JUNCTION_BOX, EntityType.LOCATION, EntityType.PANEL)


class _Label:
    """Stand-in for an endpoint that is a box named only by its text (e.g. "TIER 13F-18")."""

    subject_type = EntityType.TEXT

    def __init__(self, text: str) -> None:
        self.subject_key = text


def _centre(b: BBox) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


class CableRoutingReader(TemplateReader):
    name = "cbd-routing"

    def matches(self, page: PageContent, drawing: Drawing) -> bool:
        return True  # every cable block diagram

    def read(
        self, page: PageContent, drawing: Drawing, ctx: ParseContext, parser: DrawingParser, prior: list[Claim]
    ) -> ParseResult:
        result = ParseResult()
        common = {"parser": parser.doc_type, "parser_version": parser.version, "drawing": drawing, "method": ExtractionMethod.VECTOR}
        limit_y = page.height * 0.84  # above the title block
        # Letters are stroked at 0.36-0.72 pt; outlines (0.24) and cables (0.96) are not, so stroke width
        # alone separates them. Removing strokes inside text boxes would also cut outlines that pass a label.
        segs = [
            s
            for s in page.segments
            if (s.width <= THIN_MAX or s.width >= THICK_MIN)
            and max(s.y0, s.y1) < limit_y
            and s.length < page.width * 0.7  # not frame / title block lines
        ]
        thin = merge_runs([s for s in segs if s.width <= THIN_MAX], max_gap=6.0)
        thick = merge_runs([s for s in segs if s.width >= THICK_MIN], max_gap=CABLE_LABEL_GAP)
        boxes = find_boxes([r for r in thin if r.length >= 15])
        boxes = self._split_stacked(boxes, thin, prior, drawing)
        # Some enclosures are solid rectangles (LTL-JB3 and SH-JB3 on 401310-016). Solid boxes are also
        # device symbols, so only those with a designation at the top count as enclosures.
        solid = merge_runs(
            [s for s in page.segments if SOLID_MIN <= s.width < THICK_MIN and max(s.y0, s.y1) < limit_y], max_gap=3.0
        )
        designations = [
            c for c in prior
            if c.source.drawing_id == drawing.id and c.predicate is Predicate.OCCURS and c.subject_type in _ENCLOSURE_KINDS
        ]
        for box in find_boxes([r for r in solid if r.length >= 15], corner_tol=3.0):
            x0, y0, x1, y1 = box.bbox
            if any(box.contains(*_centre(c.source.bbox)) and _centre(c.source.bbox)[1] < y0 + 20 for c in designations):
                if not any(abs(b.bbox[0] - x0) < 3 and abs(b.bbox[1] - y0) < 3 for b in boxes):
                    boxes.append(box)

        thick = split_at_boxes(thick, boxes)
        cables = [c for c in find_cables([r for r in thick if r.length >= 10]) if sum(r.length for r in c.runs) >= 40]
        mine = [c for c in prior if c.source.drawing_id == drawing.id and c.predicate is Predicate.OCCURS]
        enclosures = self._name_boxes(boxes, mine, page)
        self._labels_by_box = {id(box): self._box_label(box, page) for box, enc in enclosures if enc is None}
        devices = [c for c in mine if c.subject_type is EntityType.DEVICE and c.value.get("format_valid")]

        # Devices listed inside an enclosure (smallest containing box), or tagged right beside one
        # (the tag sits next to the symbol in the box, as for LTL-JB3 / SH-JB3 on 401310-016).
        for d in devices:
            x, y = _centre(d.source.bbox)
            inside = [(box, claim) for box, claim in enclosures if box.contains(x, y) and claim is not None]
            if not inside:
                bx0, _, bx1, _ = d.source.bbox
                inside = [
                    (box, claim)
                    for box, claim in enclosures
                    if claim is not None
                    and box.bbox[1] <= y <= box.bbox[3]
                    and (-5 <= box.bbox[0] - bx1 <= 25 or -5 <= bx0 - box.bbox[2] <= 25)  # tag beside the box, may touch it
                ]
            if inside:
                box, enc = min(inside, key=lambda bc: bc[0].area)
                result.claims.append(
                    ctx.claim(
                        **common,
                        bbox=d.source.bbox,
                        raw_text=d.source.raw_text,
                        subject_type=EntityType.DEVICE,
                        subject_key=d.subject_key,
                        predicate=Predicate.IN_ENCLOSURE,
                        object_type=enc.subject_type,
                        object_key=enc.subject_key,
                    )
                )

        for cable in cables:
            claim = self._route(cable, enclosures, devices, page, ctx, common)
            if claim is not None:
                result.claims.append(claim)
        return result

    # --- enclosures -------------------------------------------------------------

    @staticmethod
    def _split_stacked(boxes: list[Box], thin, prior: list[Claim], drawing: Drawing) -> list[Box]:
        """Enclosures drawn touching each other (ES-LCS1/2/3 stacked on 401310-014) share their outline
        and come out as one box. Split a box at full-width inner lines when every part then holds its
        own designation; a JB's own inner dividers (symbol column, motor section) are left alone."""
        designations = [
            _centre(c.source.bbox)
            for c in prior
            if c.source.drawing_id == drawing.id and c.predicate is Predicate.OCCURS and c.subject_type in _ENCLOSURE_KINDS
        ]
        out = []
        for box in boxes:
            x0, y0, x1, y1 = box.bbox
            cuts = sorted(
                r.pos
                for r in thin
                if r.horizontal and y0 + 10 < r.pos < y1 - 10 and r.start <= x0 + 6 and r.end >= x1 - 6
            )
            if not cuts:
                out.append(box)
                continue
            edges = [y0, *cuts, y1]
            parts = [Box((x0, a, x1, b), box.runs) for a, b in zip(edges, edges[1:]) if b - a > 10]
            named_parts = [any(p.contains(*d) for d in designations) for p in parts]
            # Narrow stacks (a JB drawn under device symbol squares, LTL-JB3 on 016) split as soon as one
            # part is named; wide boxes only when every part is (a JB's own dividers stay inside it).
            if all(named_parts):
                out.extend(parts)
            elif any(named_parts) and x1 - x0 <= 60:
                # Device symbols stacked above a JB stay separate; the slices under a JB's header
                # (its own dividers, LTL-JB3 on 016) belong to that JB down to the next named header.
                merged: list[Box] = []
                for part, named in zip(parts, named_parts):
                    if named or not merged or not merged[-1].named:
                        part.named = named
                        merged.append(part)
                    else:
                        last = merged[-1]
                        merged[-1] = Box((x0, last.bbox[1], x1, part.bbox[3]), box.runs)
                        merged[-1].named = True
                out.extend(merged)
            else:
                out.append(box)
        return out

    def _name_boxes(self, boxes: list[Box], occurrences: list[Claim], page: PageContent) -> list[tuple[Box, Claim | None]]:
        """Each box's designation: an enclosure identifier inside it, top-most, or one just above it."""
        named = []
        candidates = [c for c in occurrences if c.subject_type in _ENCLOSURE_KINDS]
        for box in sorted(boxes, key=lambda b: b.area):
            x0, y0, x1, y1 = box.bbox
            inside = [c for c in candidates if box.contains(*_centre(c.source.bbox)) and _centre(c.source.bbox)[1] < y0 + 40]
            above = [c for c in candidates if x0 - 5 <= _centre(c.source.bbox)[0] <= x1 + 5 and 0 < y0 - _centre(c.source.bbox)[1] <= 20]
            anywhere = [c for c in candidates if box.contains(*_centre(c.source.bbox))]
            pick = inside or above or anywhere
            # A designation belongs to the smallest box around it.
            pick = [c for c in pick if not any(b.area < box.area and b.contains(*_centre(c.source.bbox)) for b, _ in named)]
            named.append((box, min(pick, key=lambda c: c.source.bbox[1]) if pick else None))
        return named

    # --- cables ------------------------------------------------------------------

    def _end_target(self, point, enclosures, devices) -> tuple[str, Claim | None]:
        x, y = point
        on = [(box, enc) for box, enc in enclosures if box.on_edge(x, y)]
        named = [(box, enc) for box, enc in on if enc is not None]
        if named:
            return "enclosure", min(named, key=lambda be: be[0].area)[1]
        # Unnamed small boxes are device symbols; otherwise the end is next to a field device's tag.
        named_boxes = [box for box, enc in enclosures if enc is not None]
        near = [
            d
            for d in devices
            if abs(_centre(d.source.bbox)[1] - y) <= 30 and abs(_centre(d.source.bbox)[0] - x) <= 140
            and not any(box.contains(*_centre(d.source.bbox)) for box in named_boxes)
        ]
        if near:
            return "device", min(near, key=lambda d: math.dist(_centre(d.source.bbox), point))
        if on:
            box = min((b for b, _ in on), key=lambda b: b.area)
            label = self._labels_by_box.get(id(box))
            return ("labelled box", label) if label else ("unnamed enclosure", None)
        return "open", None

    @staticmethod
    def _box_label(box: Box, page: PageContent) -> str | None:
        """A box with no designation (an MCC tier, a device group) is named by its first text line."""
        inside = [ln for ln in page.lines if box.contains(*_centre(ln.bbox)) and len(ln.text.strip()) > 2]
        if not inside:
            return None
        return min(inside, key=lambda ln: (ln.bbox[1], ln.bbox[0])).text.strip()

    def _route(self, cable: Cable, enclosures, devices, page: PageContent, ctx: ParseContext, common: dict) -> Claim | None:
        ends = sorted(cable.ends, key=lambda p: (p[0], p[1]))[:1] + sorted(cable.ends, key=lambda p: (p[0], p[1]))[-1:]
        if len(ends) < 2:
            return None
        (kind_a, a), (kind_b, b) = (self._end_target(p, enclosures, devices) for p in ends)
        labels = self._labels(cable, page)
        x0 = min(r.start if r.horizontal else r.pos for r in cable.runs)
        x1 = max(r.end if r.horizontal else r.pos for r in cable.runs)
        y0 = min(r.pos if r.horizontal else r.start for r in cable.runs)
        y1 = max(r.pos if r.horizontal else r.end for r in cable.runs)
        if a is None and b is None:
            return None
        a, b = (self._as_target(x) for x in (a, b))
        if a is not None and b is not None and (a.subject_type, a.subject_key) == (b.subject_type, b.subject_key):
            return None  # both ends on the same thing: an outline, not a cable
        src, dst = (a, b) if a is not None else (b, a)
        value = {
            "from_kind": kind_a if a is src else kind_b,
            "to_kind": kind_b if a is src else kind_a,
            "spec": labels["spec"],
            "cable_no": labels["cable_no"],
            "ends": [list(map(lambda v: round(v, 1), p)) for p in ends],
        }
        return ctx.claim(
            **common,
            bbox=(x0, y0 - 1, x1, y1 + 1),
            raw_text=f"{labels['spec'] or ''} {labels['cable_no'] or ''}".strip() or "cable",
            subject_type=src.subject_type,
            subject_key=src.subject_key,
            predicate=Predicate.ROUTED,
            object_type=dst.subject_type if dst is not None else None,
            object_key=dst.subject_key if dst is not None else None,
            value=value,
        )

    @staticmethod
    def _as_target(x):
        """A labelled box becomes a (type, key) pair like a claim's subject."""
        if isinstance(x, str):
            return _Label(x)
        return x

    def _labels(self, cable: Cable, page: PageContent) -> dict:
        spec = number = None
        for ln in page.lines:
            cx, cy = _centre(ln.bbox)
            text = ln.text.strip()
            for r in cable.runs:
                if r.horizontal and r.start - 5 <= cx <= r.end + 5 and abs(cy - r.pos) <= 14:
                    if _CABLE_NO.match(text.upper()) and number is None:
                        number = text.upper()
                    elif _SPEC.search(text) and spec is None:
                        spec = text
        return {"spec": spec, "cable_no": number}
