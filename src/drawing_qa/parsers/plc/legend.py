"""The LEGEND box on schematic sheets: the JBs, field locations and panel the sheet's channels are
routed through, each with its full designation ("LTL-JB2  =K07.11+LTL-JB2").

One ROUTE_LEGEND claim per entry (subject: the location, value: the short name written beside it),
plus one with no location when the box has a LEGEND heading but no entries, so PATH-004 can
compare the legend with the JBs in the sheet's channel rows.
"""

from __future__ import annotations

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import EntityType, ExtractionMethod, Predicate
from drawing_qa.model.source import Drawing
from drawing_qa.parsers.base import DrawingParser, ParseContext
from drawing_qa.pdf.extract import PageContent

_WIDTH = 160.0  # pt right of the LEGEND heading
_GAP = 30.0  # the box ends at the first vertical gap wider than this (the revision table below it)
_KINDS = (EntityType.JUNCTION_BOX, EntityType.LOCATION, EntityType.PANEL)


def read_legend(page: PageContent, drawing: Drawing, ctx: ParseContext, parser: DrawingParser) -> list[Claim]:
    heading = next((ln for ln in page.lines if ln.text.strip().upper() == "LEGEND"), None)
    if heading is None:
        return []

    def claim(bbox, raw, subject_type, key, value):
        return ctx.claim(parser=parser.doc_type, parser_version=parser.version, drawing=drawing, bbox=bbox, raw_text=raw,
                         subject_type=subject_type, subject_key=key, predicate=Predicate.ROUTE_LEGEND,
                         method=ExtractionMethod.TEMPLATE, value=value)

    column = sorted((ln for ln in page.lines if -5 <= ln.bbox[0] - heading.bbox[0] < _WIDTH and ln.bbox[1] > heading.bbox[1]),
                    key=lambda ln: ln.bbox[1])
    box, bottom = [], heading.bbox[3]
    for ln in column:  # entries run on down the column, however many there are (sheet 022 has eight)
        if ln.bbox[1] - bottom > _GAP:
            break
        box.append(ln)
        bottom = max(bottom, ln.bbox[3])
    out = []
    for ln in box:
        text = ln.text.strip()
        if not text.startswith(("=", "+")):
            continue
        parsed = ctx.grammar.parse(text)
        short = next((s.text.strip() for s in box if s is not ln and s.bbox[2] <= ln.bbox[0] + 4
                      and abs((s.bbox[1] + s.bbox[3]) / 2 - (ln.bbox[1] + ln.bbox[3]) / 2) < 8), "")
        if parsed is not None and parsed.kind in _KINDS:
            out.append(claim(ln.bbox, text, parsed.kind, parsed.key, {"short": short, "known": True}))
        else:  # a designation the grammar does not accept, kept as written for PATH-004 to report
            out.append(claim(ln.bbox, text, EntityType.TEXT, text, {"short": short, "known": False}))
    if not out:
        out.append(claim(heading.bbox, heading.text, EntityType.DRAWING, drawing.label, {"empty": True}))
    return out
