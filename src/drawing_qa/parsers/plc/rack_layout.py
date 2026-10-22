"""Rack layout reader (e.g. 401103-013 "LTL PLC LANDWARD LEG POINT I/O RACK LAYOUT").

    SLOT No.   0  1  2  3 ...                       <- slot number row
               |1734-IB8S SAFETY INPUT MODULE| ...  <- module label per slot (vertical text)
    SHEET No.  015 020 021 ...                      <- schematic sheet per slot

Each module label and sheet number is assigned to the slot whose number sits
directly above it (within a few points). Labels between slots, such as power
extension modules, belong to no slot and are ignored.
"""

from __future__ import annotations

import re

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import EntityType, Predicate
from drawing_qa.model.source import Drawing
from drawing_qa.parsers.base import DrawingParser, ParseContext, ParseResult, TemplateReader
from drawing_qa.pdf.extract import PageContent, TextLine

_SLOT_LABEL = re.compile(r"^SLOT\s+NO\.?$", re.I)
_SHEET_LABEL = re.compile(r"^SHEET\s+NO\.?$", re.I)
_PART = re.compile(r"^(?P<part>\d{4}-[A-Z0-9]+)\b\s*(?P<desc>.*)$", re.I)
_NUMBER = re.compile(r"^\d{1,3}$")
_ALIGN = 15.0  # max horizontal distance from a slot number, in points


def _cx(ln: TextLine) -> float:
    return (ln.bbox[0] + ln.bbox[2]) / 2


def _cy(ln: TextLine) -> float:
    return (ln.bbox[1] + ln.bbox[3]) / 2


class RackLayoutReader(TemplateReader):
    name = "plc-rack-layout"

    def matches(self, page: PageContent, drawing: Drawing) -> bool:
        has_label = any(_SLOT_LABEL.match(ln.text.strip()) for ln in page.lines)
        return has_label and any(_PART.match(ln.text.strip()) for ln in page.lines)

    def read(
        self, page: PageContent, drawing: Drawing, ctx: ParseContext, parser: DrawingParser, prior: list[Claim]
    ) -> ParseResult:
        result = ParseResult()
        common = {"parser": parser.doc_type, "parser_version": parser.version, "drawing": drawing}
        rack = (drawing.title or "").split(" ")[0].upper()
        slot_label = next(ln for ln in page.lines if _SLOT_LABEL.match(ln.text.strip()))
        slots = [
            (int(ln.text.strip()), _cx(ln))
            for ln in page.lines
            if _NUMBER.match(ln.text.strip()) and abs(_cy(ln) - _cy(slot_label)) <= 10 and ln.bbox[0] > slot_label.bbox[2]
        ]
        if not rack or not slots:
            return result

        def slot_at(x: float) -> int | None:
            number, sx = min(slots, key=lambda s: abs(s[1] - x))
            return number if abs(sx - x) <= _ALIGN else None

        for ln in page.lines:
            m = _PART.match(ln.text.strip())
            if not m or (slot := slot_at(_cx(ln))) is None:
                continue
            part = m["part"].upper()
            spec = ctx.settings.plc.modules.get(part)
            result.claims.append(
                ctx.claim(
                    **common,
                    method=ln.words[0].method,
                    bbox=ln.bbox,
                    raw_text=ln.text,
                    subject_type=EntityType.PLC_MODULE,
                    subject_key=f"{rack}/S{slot}",
                    predicate=Predicate.INSTALLED_IN,
                    value={
                        "source": "rack_layout",
                        "rack": rack,
                        "slot": slot,
                        "part": part,
                        "module_type": spec.type if spec else None,
                        "channels": spec.channels if spec else None,
                        "safety": spec.safety if spec else None,
                        "label": m["desc"].strip(),
                    },
                )
            )

        sheet_label = next((ln for ln in page.lines if _SHEET_LABEL.match(ln.text.strip())), None)
        if sheet_label is not None:
            for ln in page.lines:
                text = ln.text.strip()
                if not re.match(r"^\d{3}$", text) or not (sheet_label.bbox[1] - 5 <= ln.bbox[1] <= sheet_label.bbox[3] + 25):
                    continue
                if (slot := slot_at(_cx(ln))) is None:
                    continue
                result.claims.append(
                    ctx.claim(
                        **common,
                        method=ln.words[0].method,
                        bbox=ln.bbox,
                        raw_text=ln.text,
                        subject_type=EntityType.PLC_MODULE,
                        subject_key=f"{rack}/S{slot}",
                        predicate=Predicate.SLOT_SHEET,
                        value={"slot": slot, "sheet": int(text)},
                    )
                )
        return result
