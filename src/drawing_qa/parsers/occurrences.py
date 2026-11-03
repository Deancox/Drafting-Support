"""Generic occurrence reader, run on every sheet of every document type.

Emits an OCCURS claim for each package identifier found anywhere on the sheet,
and a PLACEHOLDER claim for each incomplete-drafting marker. A device label away from the grid
its tag encodes may sit on top of a tall symbol in a dashed outline (a siren on 401103 sh 050);
the outline's cell is recorded with it, as the device's drawn position.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from drawing_qa.common.identifiers import clean_token
from drawing_qa.model.enums import EntityType, Predicate
from drawing_qa.model.source import Drawing
from drawing_qa.model.source import BBox
from drawing_qa.parsers.base import ParseContext, ParseResult
from drawing_qa.parsers.cbd.geometry import Run, merge_runs
from drawing_qa.pdf.extract import PageContent

if TYPE_CHECKING:
    from drawing_qa.parsers.base import DrawingParser

# Sheet cross-references: "(SHT 35)", "(CONT. SHT 204)", "SHEET 12". Not "SHEET 11 OF 20"
# (sheet numbering), and not on lines that are worked examples in a legend.
_CROSS_REF = re.compile(r"\b(?:SHT|SHEET)\.?\s*(\d{1,4})\b(?!\s+OF\b)", re.I)
_NOT_A_REFERENCE = re.compile(r"\bEXAMPLE\b", re.I)
# Relay contact table entry under a coil: "<sheet>/<grid>", e.g. "43/D4".
_CONTACT_POINTER = re.compile(r"^(?P<sheet>\d{1,3})/(?P<grid>[A-Z]\d)$")
_COIL_SEARCH_HEIGHT = 60.0  # points above a pointer to look for its coil's tag (table header)
_WORD = re.compile(r"^[A-Z]+$")
_DASH_GAP = 3.0  # largest gap between the dashes of an outline, in points
_OUTLINE_REACH = 200.0  # how far below its label a device outline may reach, in points
# A device tag written alone on its line, as labels are (S-TL140M7, or a placeholder S-TLXXXX).
_TAG_LABEL = re.compile(r"^(?P<prefix>[A-Z]{1,4})-[A-Z]{1,3}(?:\d|XX)[A-Z0-9]*$")  # not DE-ENERGISED


class OccurrenceReader:
    name = "occurrences"

    def read(self, page: PageContent, drawing: Drawing, ctx: ParseContext, parser: DrawingParser) -> ParseResult:
        result = ParseResult()
        common = {"parser": parser.doc_type, "parser_version": parser.version, "drawing": drawing}
        min_word = ctx.settings.spelling.min_length
        for line in page.lines:
            refs =[] if _NOT_A_REFERENCE.search(line.text) else _CROSS_REF.finditer(line.text)
            for m in refs:
                result.claims.append(
                    ctx.claim(
                        **common,
                        method=line.words[0].method,
                        bbox=line.bbox,
                        raw_text=line.text,
                        subject_type=EntityType.DRAWING,
                        subject_key=f"{drawing.drawing_no}/{int(m.group(1))}",
                        predicate=Predicate.CROSS_REF,
                        value={"drawing_no": drawing.drawing_no, "sheet": int(m.group(1))},
                    )
                )
            phrase_hits = ctx.placeholders.check_line(line.text)
            # Words already reported as part of a phrase are not reported again on their own.
            phrase_words = {w for hit in phrase_hits for w in hit.text.split()}
            for hit in phrase_hits:
                result.claims.append(
                    ctx.claim(
                        **common,
                        method=line.words[0].method,
                        bbox=line.bbox,
                        raw_text=line.text,
                        subject_type=EntityType.TEXT,
                        subject_key=hit.text,
                        predicate=Predicate.PLACEHOLDER,
                        value={"pattern": hit.pattern, "line": line.text},
                    )
                )
            for word in line.words:
                ident = ctx.grammar.parse(word.text)
                hit = ctx.placeholders.check_token(word.text, line.text)
                if hit is not None and hit.text not in phrase_words:
                    value = {"pattern": hit.pattern, "line": line.text}
                    if ident is not None and "superseded_by" in ident.parts:
                        # e.g. AUT1 in "=K07.11+LTL-AUT1": the fix is the current designation.
                        value["replacement"] = ident.parts["superseded_by"]
                    result.claims.append(
                        ctx.claim(
                            **common,
                            method=word.method,
                            bbox=word.bbox,
                            raw_text=word.text,
                            subject_type=EntityType.TEXT,
                            subject_key=hit.text,
                            predicate=Predicate.PLACEHOLDER,
                            value=value,
                        )
                    )
                # A word can be both: "=K07.11+LTL-AUT1" is a location and contains AUT1.
                if ident is None:
                    text = clean_token(word.text)
                    if hit is None and len(text) >= min_word and _WORD.match(text):
                        result.claims.append(
                            ctx.claim(
                                **common,
                                method=word.method,
                                bbox=word.bbox,
                                raw_text=word.text,
                                subject_type=EntityType.TEXT,
                                subject_key=text,
                                predicate=Predicate.WORD,
                            )
                        )
                    continue
                result.claims.append(
                    ctx.claim(
                        **common,
                        method=word.method,
                        bbox=word.bbox,
                        raw_text=word.text,
                        subject_type=ident.kind,
                        subject_key=ident.key,
                        predicate=Predicate.OCCURS,
                        value={**ident.parts, "is_variant": ident.is_variant},
                    )
                )
        result.claims.extend(self._contact_pointers(page, drawing, ctx, common, result.claims))
        self._symbol_outlines(page, drawing, result.claims)
        _captions(page, result.claims)
        return result

    def _symbol_outlines(self, page: PageContent, drawing: Drawing, claims) -> None:
        """For a device label not in the cell its tag encodes: the dashed outline directly under it, if any,
        recorded as value["symbol_cell"] (its centre's cell) and value["symbol_bbox"]. The page's line work is
        read only when such a label exists."""
        if drawing.grid is None:
            return
        away = [c for c in claims if c.subject_type is EntityType.DEVICE and c.predicate is Predicate.OCCURS
                and c.value.get("format_valid") and c.value.get("grid") and c.source.grid_cell != c.value["grid"]]
        if not away:
            return
        segments = page.segments
        for c in away:
            box = _outline_under(c.source.bbox, segments)
            if box is not None:
                c.value["symbol_bbox"] = box
                c.value["symbol_cell"] = drawing.grid.cell_at((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

    def _contact_pointers(self, page, drawing, ctx, common, claims):
        """Relay contact tables: each "43/D4" under a coil tag points to that coil's auxiliary
        contact on sheet 43 at grid D4, which is labelled <prefix>-<FFI><coil sheet>D4."""
        coils = [c for c in claims if c.subject_type is EntityType.DEVICE and c.value.get("format_valid")]
        for line in page.lines:
            m = _CONTACT_POINTER.match(line.text.strip())
            if not m:
                continue
            x0, y0, x1, _ = line.bbox
            above = [
                c
                for c in coils
                if 0 < y0 - c.source.bbox[3] <= _COIL_SEARCH_HEIGHT
                and c.source.bbox[0] < x1 + 15
                and c.source.bbox[2] > x0 - 15
            ]
            coil = min(above, key=lambda c: y0 - c.source.bbox[3]) if above else None
            aux_tag = None
            if coil is not None:
                v = coil.value
                aux_tag = f"{v['prefix']}-{v['ffi']}{v['sheet']}{m['grid']}"
            yield ctx.claim(
                **common,
                method=line.words[0].method,
                bbox=line.bbox,
                raw_text=line.text,
                subject_type=EntityType.DEVICE,
                subject_key=coil.subject_key if coil else "",
                predicate=Predicate.CONTACT_REF,
                value={
                    "target_sheet": int(m["sheet"]),
                    "target_grid": m["grid"],
                    "coil": coil.subject_key if coil else None,
                    "aux_tag": aux_tag,
                },
            )


def _outline_under(label: BBox, segments) -> BBox | None:
    """A dashed rectangle whose top edge runs under the label (from its top to one label height below it,
    across its centre), with dashed sides going down from both ends of that edge."""
    x0, y0, x1, y1 = label
    cx, height = (x0 + x1) / 2, y1 - y0
    near = [s for s in segments if s.width > 0 and s.length < _OUTLINE_REACH  # width 0: a text note's outline
            and min(s.y0, s.y1) >= y0 - 2 and max(s.y0, s.y1) <= y1 + _OUTLINE_REACH
            and max(s.x0, s.x1) >= x0 - _OUTLINE_REACH and min(s.x0, s.x1) <= x1 + _OUTLINE_REACH]
    runs = merge_runs(near, _DASH_GAP)
    tops = sorted((r for r in runs if r.horizontal and r.pieces >= 3 and r.start <= cx <= r.end
                   and y0 - 2 <= r.pos <= y1 + height), key=lambda r: r.pos)
    sides = [r for r in runs if not r.horizontal and r.pieces >= 2]
    for top in tops:
        left = _side(sides, top.start, top.pos)
        right = _side(sides, top.end, top.pos)
        if left and right:
            return (top.start, top.pos, top.end, max(left.end, right.end))
    return None


def _side(sides: list[Run], x: float, top: float, tol: float = 2.0) -> Run | None:
    return next((r for r in sides if abs(r.pos - x) <= tol and abs(r.start - top) <= tol), None)


def _captions(page: PageContent, claims) -> None:
    """A device label's caption: the lines of the same text height stacked directly under it, as drawn
    (S-TL140M7 / BOOM CONVEYOR / EAST BRAKE / HYDRAULIC PRESSURE / RELEASE SOLENOID), up to a gap, a line of
    another size, or another tag. Recorded as value["caption"], lines joined by " / "; also on placeholder
    tags (S-TLXXXX)."""
    labels = [c for c in claims if (c.predicate is Predicate.OCCURS and c.subject_type is EntityType.DEVICE)
              or (c.predicate is Predicate.PLACEHOLDER and _TAG_LABEL.match(c.source.raw_text or ""))]
    if not labels:
        return
    lines = sorted(page.lines, key=lambda ln: ln.bbox[1])
    for c in labels:
        own = next((ln for ln in lines if len(ln.words) == 1 and tuple(ln.words[0].bbox) == tuple(c.source.bbox)), None)
        if own is None or not _TAG_LABEL.match(own.text.strip()):
            continue
        # Note boxes (SHX) are taller than the line pitch and overlap; decoded words are boxed tight. The next
        # line starts below the last one's top, within 1.8 heights of it and 0.8 heights of its bottom, and lines
        # up with the label: left, right or centred. Marks of a symbol drawn beside it ('P', 'mA') are passed over.
        height = own.bbox[3] - own.bbox[1]
        lx0, lx1 = own.bbox[0], own.bbox[2]

        def aligned(b) -> bool:
            return abs(b[0] - lx0) <= height or abs(b[2] - lx1) <= height or abs((b[0] + b[2] - lx0 - lx1) / 2) <= height

        block, last = [], own.bbox
        while True:
            nxt = min((ln for ln in lines
                       if ln is not own and ln not in block and len(ln.text.strip()) > 2
                       and 0 < ln.bbox[1] - last[1] <= 1.8 * height and ln.bbox[1] - last[3] <= 0.8 * height
                       and aligned(ln.bbox) and abs((ln.bbox[3] - ln.bbox[1]) - height) <= 0.4 * height),
                      key=lambda ln: ln.bbox[1], default=None)
            if nxt is None or _TAG_LABEL.match(nxt.text.strip()):
                break
            block.append(nxt)
            last = nxt.bbox
        if block:
            c.value["caption"] = " / ".join(ln.text.strip() for ln in block)
