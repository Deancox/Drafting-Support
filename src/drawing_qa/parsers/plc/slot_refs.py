"""Notes that name PLC slots, for PLC-010: "24VDC SUPPLY TO DIGITAL OUTPUT MODULE SLOT 22 & 23" with
"(SHT 50 & 51)" on the next line (401103-015), or "PLC SLOT 12,12,14 - TERMINATION DIAGRAM" (401103-003).

One SLOT_REF claim per note that names more than one slot or has a sheet reference: the slots it names,
the module type it names (DIGITAL OUTPUT -> DO), and the sheets it references. The reference is in the note
itself or on the line after it: below it for level text, to its right for text turned to read up the sheet.
A slot sheet's own heading (a single slot, no reference) and its RACK line make no claim.
"""

from __future__ import annotations

import re

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import EntityType, ExtractionMethod, Predicate
from drawing_qa.model.source import Drawing
from drawing_qa.parsers.base import DrawingParser, ParseContext
from drawing_qa.pdf.extract import PageContent, TextLine

_LIST = r"(\d+(?:\s*(?:,|&|\bAND\b|\bTO\b)\s*\d+)*)"
_SLOTS = re.compile(r"\bSLOTS?\s*(?:N\S{0,4}\s*)?" + _LIST, re.I)
_SHEETS = re.compile(r"\b(?:SHTS?|SHEETS?|SH)\.?\s*(?:N\S{0,4}\s*)?" + _LIST, re.I)
_TYPES = (("DO", r"DIGITAL\s+OUTPUT"), ("DI", r"DIGITAL\s+INPUT"), ("AO", r"ANALOG(?:UE)?\s+OUTPUT"), ("AI", r"ANALOG(?:UE)?\s+INPUT"))
_NEXT_LINE = 1.5  # the reference is at most this many text heights after the note
_LONGEST_RANGE = 64


def numbers(text: str) -> list[int] | None:
    """'22 & 23' -> [22, 23]; '12 TO 23' -> [12 .. 23]; '12,12,14' -> [12, 12, 14]. None for a backwards or huge range."""
    parts = re.split(r"\s*(,|&|\bAND\b|\bTO\b)\s*", text.strip(), flags=re.I)
    out = [int(parts[0])]
    for sep, n in zip(parts[1::2], parts[2::2]):
        n = int(n)
        if sep.upper() == "TO":
            if not out[-1] < n <= out[-1] + _LONGEST_RANGE:
                return None
            out += range(out[-1] + 1, n + 1)
        else:
            out.append(n)
    return out


def _vertical(ln: TextLine) -> bool:
    x0, y0, x1, y1 = ln.bbox
    return y1 - y0 > x1 - x0


def _reference_after(note: TextLine, lines: list[TextLine]) -> TextLine | None:
    """The sheet reference on the line after the note: overlapping it along the text, within _NEXT_LINE heights across."""
    x0, y0, x1, y1 = note.bbox
    vertical = _vertical(note)
    height = (x1 - x0) if vertical else (y1 - y0)
    best = None
    for ln in lines:
        if ln is note or not _SHEETS.search(ln.text):
            continue
        a0, b0, a1, b1 = ln.bbox
        if vertical:
            along, start, gap = y0 <= (b0 + b1) / 2 <= y1, a0 - x0, a0 - x1
        else:
            along, start, gap = x0 <= (a0 + a1) / 2 <= x1, b0 - y0, b0 - y1
        if along and start > 0 and gap <= _NEXT_LINE * height and (best is None or gap < best[0]):
            best = (gap, ln)
    return best[1] if best else None


def read_slot_refs(page: PageContent, drawing: Drawing, ctx: ParseContext, parser: DrawingParser) -> list[Claim]:
    lines = page.lines
    out = []
    for ln in lines:
        m = _SLOTS.search(ln.text)
        if not m or re.search(r"\bRACK\b", ln.text, re.I):
            continue
        slots = numbers(m.group(1))
        if not slots:
            continue
        sheet_text, ref = None, ln
        if s := _SHEETS.search(ln.text[m.end():]):
            sheet_text = s.group(1)
        elif (after := _reference_after(ln, lines)) is not None:
            sheet_text, ref = _SHEETS.search(after.text).group(1), after
        sheets = numbers(sheet_text) if sheet_text else None
        if len(slots) < 2 and not sheets:
            continue
        kind = next((k for k, pattern in _TYPES if re.search(pattern, ln.text, re.I)), None)
        out.append(ctx.claim(
            parser=parser.doc_type, parser_version=parser.version, drawing=drawing,
            method=ln.words[0].method if ln.words else ExtractionMethod.TEMPLATE,
            bbox=ln.bbox, raw_text=ln.text if ref is ln else f"{ln.text} {ref.text}",
            subject_type=EntityType.TEXT, subject_key=f"SLOT {m.group(1).strip()}", predicate=Predicate.SLOT_REF,
            value={"note": ln.text.strip(), "slots_text": m.group(1).strip(), "slots": slots, "module_type": kind,
                   "sheets_text": sheet_text.strip() if sheets else None, "sheets": sheets or []},
        ))
    return out
