"""Helpers shared by rules."""

from __future__ import annotations

from drawing_qa.model.enums import Severity
from drawing_qa.model.source import Drawing

# Grid rows in order (I and O are not used).
ROWS = "ABCDEFGHJKLMNPQRSTUVWXYZ"

_ISSUED = ("CONSTRUCTION", "AS BUILT", "ISSUED", "APPROVED")


def is_issued(drawing: Drawing) -> bool:
    """True for drawings issued for use (FOR CONSTRUCTION, AS BUILT ...), where open items matter more."""
    return any(word in (drawing.status or "").upper() for word in _ISSUED)


def issued_severity(drawing: Drawing, issued: Severity = Severity.MAJOR, otherwise: Severity = Severity.MINOR) -> Severity:
    return issued if is_issued(drawing) else otherwise


def row_offset(from_cell: str, to_cell: str) -> int | None:
    """Rows from from_cell down to to_cell (positive = to_cell is lower on the sheet)."""
    a, b = from_cell[:1], to_cell[:1]
    if a not in ROWS or b not in ROWS:
        return None
    return ROWS.index(b) - ROWS.index(a)


def col_offset(from_cell: str, to_cell: str) -> int | None:
    a, b = from_cell[1:], to_cell[1:]
    if not (a.isdigit() and b.isdigit()):
        return None
    return int(b) - int(a)
