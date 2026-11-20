"""Reads and writes the checker's decisions in the QA register's yellow columns (Findings tab only).

Only the Decision, Checker comment, Reviewed by and Date cells of the rows being decided are changed; every
other cell, tab, drop-down list and formula is kept. The next run reads these columns back
(drawing_qa.review.register_reader), so nothing else needs to know about the viewer. The register is saved to
a temporary file beside it and then swapped in, so a failed save never leaves it half-written.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook

from drawing_qa.report.register import (
    COL_COMMENT,
    COL_DATE,
    COL_DECISION,
    COL_FINGERPRINT,
    COL_REVIEWER,
    FINDINGS_SHEET,
    HEADER_ROW,
)
from drawing_qa.review.register_reader import _text

COLUMNS = (COL_FINGERPRINT, COL_DECISION, COL_COMMENT, COL_REVIEWER, COL_DATE)


@dataclass(frozen=True)
class Entry:
    """A row's four yellow cells, as text. A blank Decision is OPEN."""

    decision: str
    comment: str
    reviewer: str
    date: str


class NoDecisionColumns(Exception):
    """The register was made with review off, so it has no Decision or Fingerprint columns."""


class RegisterLocked(Exception):
    """The register is open in Excel (or otherwise can't be replaced), so it can't be saved now."""


def is_locked(register: Path) -> bool:
    """True while Excel has the register open: Excel keeps a ~$<name> file beside it."""
    return (register.parent / f"~${register.name}").exists()


def _columns(header: list[str]) -> dict[str, int] | None:
    if any(name not in header for name in COLUMNS):
        return None
    return {name: header.index(name) for name in COLUMNS}


def has_decision_columns(register: Path) -> bool:
    wb = load_workbook(register, read_only=True)
    try:
        if FINDINGS_SHEET not in wb.sheetnames:
            return False
        header = next(wb[FINDINGS_SHEET].iter_rows(min_row=HEADER_ROW, max_row=HEADER_ROW, values_only=True), ())
        return _columns([_text(h) for h in header]) is not None
    finally:
        wb.close()


def read_decisions(register: Path) -> dict[str, Entry]:
    """Every Findings row's decision, by fingerprint. Raises NoDecisionColumns for a review-off register."""
    wb = load_workbook(register, read_only=True, data_only=True)
    try:
        if FINDINGS_SHEET not in wb.sheetnames:
            raise NoDecisionColumns(f"{register.name} has no {FINDINGS_SHEET} tab")
        rows = wb[FINDINGS_SHEET].iter_rows(min_row=HEADER_ROW, values_only=True)
        col = _columns([_text(h) for h in next(rows, ())])
        if col is None:
            raise NoDecisionColumns(f"{register.name} has no decision columns (it was made with review off)")
        decisions: dict[str, Entry] = {}
        for row in rows:
            def get(name: str) -> str:
                i = col[name]
                return _text(row[i]) if i < len(row) else ""

            fingerprint = get(COL_FINGERPRINT)
            if fingerprint:
                decision = get(COL_DECISION).upper().replace(" ", "_").replace("-", "_") or "OPEN"
                decisions[fingerprint] = Entry(decision, get(COL_COMMENT), get(COL_REVIEWER), get(COL_DATE))
        return decisions
    finally:
        wb.close()


def write_decisions(register: Path, changes: dict[str, Entry]) -> None:
    """Write each fingerprint's four yellow cells. Nothing is written if any fingerprint is not on the tab
    (KeyError) or the register is locked (RegisterLocked)."""
    if is_locked(register):
        raise RegisterLocked(f"{register.name} is open in Excel")
    wb = load_workbook(register)
    ws = wb[FINDINGS_SHEET]
    header = [_text(c.value) for c in ws[HEADER_ROW]]
    col = _columns(header)
    if col is None:
        raise NoDecisionColumns(f"{register.name} has no decision columns (it was made with review off)")
    rows = {}
    for r in range(HEADER_ROW + 1, ws.max_row + 1):
        fp = _text(ws.cell(row=r, column=col[COL_FINGERPRINT] + 1).value)
        if fp:
            rows.setdefault(fp, r)
    missing = [fp for fp in changes if fp not in rows]
    if missing:
        raise KeyError(f"not in {register.name}: {', '.join(missing)}")
    for fp, e in changes.items():
        r = rows[fp]
        for name, value in ((COL_DECISION, e.decision), (COL_COMMENT, e.comment), (COL_REVIEWER, e.reviewer),
                            (COL_DATE, e.date)):
            ws.cell(row=r, column=col[name] + 1).value = value
    temp = register.with_name(f"{register.stem}.saving.xlsx")
    try:
        wb.save(temp)
        os.replace(temp, register)
    except PermissionError as exc:
        raise RegisterLocked(f"{register.name} can't be saved: {exc}") from exc
    finally:
        if temp.exists():
            temp.unlink()
