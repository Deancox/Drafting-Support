"""Shared pieces of the termination template readers: terminal rows, bracket groups and cable specs.

A terminal row is drawn as two boxes side by side: the terminal number and the wire number
("5 | TN15D5"). Groups of rows are tied to a destination by a thin vertical bracket beside the
destination's label ("=K07.11+LTL-PLC / LAND LEG REMOTE I/O PANEL / DI SLOT 12").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from drawing_qa.common.identifiers import IdentifierGrammar, ParsedIdentifier
from drawing_qa.model.enums import EntityType
from drawing_qa.pdf.extract import PageContent, Segment, TextLine, Word

_NUMBER = re.compile(r"^\d{1,3}$")
# Wire-number shaped text, including placeholders (TNXXXX) that the grammar does not accept.
_WIRE_LIKE = re.compile(r"^[A-Z]{2}(?=[A-Z0-9]*[0-9X])[A-Z0-9]{3,6}$")  # TNXXXX, not COUPLER
# Terminals with no wire number: a cable screen, an earth, a spare.
_NON_WIRE = {"SCR", "SPARE", "E", "PE"}
SLOT_RE = re.compile(r"\b(?P<io>S?D[IO]|S?A[IO])\s+SLOT\s+0*(?P<slot>\d+)\b")
CABLE_SPEC_RE = re.compile(r"^(?P<cores>\d+)C(?:\+E)?\b")
SPARES_RE = re.compile(r"^(?P<n>\d+)\s+SPARES?$")
BRACKET_MAX_WIDTH = 0.5  # pt; brackets are drawn thin (0.36 pt on the client's sheets)


@dataclass(frozen=True)
class Row:
    """One terminal: its number box and the wire number box beside it."""

    number: Word
    wire: Word
    wire_id: ParsedIdentifier | None  # None for placeholders (TNXXXX) or unreadable text

    @property
    def terminal(self) -> str:
        return self.number.text

    @property
    def y(self) -> float:
        return (self.number.bbox[1] + self.number.bbox[3]) / 2

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.number.bbox[0], min(self.number.bbox[1], self.wire.bbox[1]),
                self.wire.bbox[2], max(self.number.bbox[3], self.wire.bbox[3]))


def mid_y(b) -> float:
    return (b[1] + b[3]) / 2


def mid_x(b) -> float:
    return (b[0] + b[2]) / 2


def terminal_rows(page: PageContent, grammar: IdentifierGrammar) -> list[Row]:
    """Every (number, wire) pair on the sheet: a 1-3 digit number with a wire number just right of it."""
    numbers = [w for w in page.words if _NUMBER.match(w.text)]
    wires = []
    for w in page.words:
        text = w.text.strip().upper()
        parsed = grammar.parse(text)
        if (parsed is not None and parsed.kind is EntityType.WIRE) or _WIRE_LIKE.match(text) or text in _NON_WIRE:
            wires.append((w, parsed if parsed is not None and parsed.kind is EntityType.WIRE else None))
    rows = []
    for n in numbers:
        best = None
        for w, parsed in wires:
            gap = w.bbox[0] - n.bbox[2]
            if -1 <= gap <= 12 and abs(mid_y(w.bbox) - mid_y(n.bbox)) <= 4:
                if best is None or gap < best[0]:
                    best = (gap, w, parsed)
        if best:
            rows.append(Row(number=n, wire=best[1], wire_id=best[2]))
    return rows


def brackets(page: PageContent, x_min: float, x_max: float, min_length: float = 8.0) -> list[Segment]:
    """Thin vertical lines between x_min and x_max: the brackets that group terminal rows."""
    return [
        s for s in page.segments
        if s.vertical and s.width <= BRACKET_MAX_WIDTH and s.length >= min_length and x_min <= s.x0 <= x_max
    ]


def span(seg: Segment) -> tuple[float, float]:
    return min(seg.y0, seg.y1), max(seg.y0, seg.y1)


def label_block(lines: list[TextLine], first: TextLine, max_gap: float = 14.0, max_lines: int = 5) -> list[TextLine]:
    """`first` and the lines stacked directly under it (same column), e.g. a destination label."""
    block = [first]
    for _ in range(max_lines - 1):
        last = block[-1]
        below = [
            ln for ln in lines
            if ln not in block and 0 <= ln.bbox[1] - last.bbox[1] <= max_gap
            and ln.bbox[0] < last.bbox[2] and ln.bbox[2] > last.bbox[0] and len(ln.text.strip()) > 1
        ]
        if not below:
            break
        block.append(min(below, key=lambda ln: ln.bbox[1]))
    return block


def block_bbox(block: list[TextLine]) -> tuple[float, float, float, float]:
    return (min(b.bbox[0] for b in block), min(b.bbox[1] for b in block),
            max(b.bbox[2] for b in block), max(b.bbox[3] for b in block))


def designation(grammar: IdentifierGrammar, line: TextLine) -> ParsedIdentifier | None:
    """The first JB / location / panel designation in a line ('=K07.11+LTL-JB1')."""
    for w in line.words:
        parsed = grammar.parse(w.text)
        if parsed is not None and parsed.kind in (EntityType.JUNCTION_BOX, EntityType.LOCATION, EntityType.PANEL):
            return parsed
    return None
