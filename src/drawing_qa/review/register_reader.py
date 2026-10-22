"""Read the checker's decisions back from a QA register (only the yellow columns, found by header name)."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

from drawing_qa.model.enums import Decision
from drawing_qa.report.register import (
    COL_COMMENT,
    COL_DATE,
    COL_DECISION,
    COL_FINGERPRINT,
    COL_REVIEWER,
    FINDINGS_SHEET,
    GONE_SHEET,
    HEADER_ROW,
)
from drawing_qa.review.store import RegisterEntry

log = logging.getLogger(__name__)


class RegisterError(Exception):
    """The file is not a QA register this version can read."""


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _decision(value: str) -> Decision | None:
    text = value.strip().upper().replace(" ", "_").replace("-", "_")
    if not text:
        return Decision.OPEN
    try:
        return Decision(text)
    except ValueError:
        return None


_NEED = (COL_FINGERPRINT, COL_DECISION, COL_COMMENT, COL_REVIEWER, COL_DATE)


def _read_sheet(ws, path: Path, entries: list[RegisterEntry], problems: list[str]) -> None:
    rows = ws.iter_rows(min_row=HEADER_ROW, values_only=True)
    header = [_text(h) for h in next(rows, ())]
    missing = [n for n in _NEED if n not in header]
    if missing:
        if ws.title == FINDINGS_SHEET:
            raise RegisterError(f"{path.name}: missing column(s) {', '.join(missing)}")
        problems.append(f"'{ws.title}' tab: missing column(s) {', '.join(missing)}; its decisions were not read")
        return
    col = {n: header.index(n) for n in _NEED}
    for n, row in enumerate(rows, start=HEADER_ROW + 1):
        def get(name: str) -> str:
            i = col[name]
            return _text(row[i]) if i < len(row) else ""

        fingerprint = get(COL_FINGERPRINT)
        if not fingerprint:
            continue
        decision = _decision(get(COL_DECISION))
        if decision is None:
            problems.append(f"'{ws.title}' row {n}: unknown decision '{get(COL_DECISION)}' (kept the previous decision)")
            continue
        entries.append(
            RegisterEntry(
                fingerprint=fingerprint,
                decision=decision,
                comment=get(COL_COMMENT),
                reviewer=get(COL_REVIEWER),
                decided_on=get(COL_DATE),
            )
        )


def read_register(path: Path) -> tuple[list[RegisterEntry], list[str]]:
    """Return the decision rows and a list of problems (rows skipped and why)."""
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # corrupt, not a workbook
        raise RegisterError(f"{path.name}: {type(exc).__name__}: {exc}") from exc
    try:
        if FINDINGS_SHEET not in wb.sheetnames:
            raise RegisterError(f"{path.name}: no '{FINDINGS_SHEET}' tab")
        entries: list[RegisterEntry] = []
        problems: list[str] = []
        for sheet in (FINDINGS_SHEET, GONE_SHEET):
            if sheet in wb.sheetnames:
                _read_sheet(wb[sheet], path, entries, problems)
        return entries, problems
    finally:
        wb.close()
