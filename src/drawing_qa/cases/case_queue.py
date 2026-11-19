"""CaseQueue.xlsx: one row per case, in a real Excel table named CaseQueue, for the Copilot workflow.

The workflow lists the table's rows, then fetches each case by Package, RunID and Fingerprint. Every value is
taken from the cases the packer has built and from the run's findings.json. Before the file is saved, its rows
are checked against the run folder, so a queue that does not match its cases is never written.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.filters import AutoFilter
from openpyxl.worksheet.table import Table, TableColumn, TableStyleInfo

CASE_QUEUE_NAME = "CaseQueue.xlsx"
SHEET_NAME = "Cases"
TABLE_NAME = "CaseQueue"
HEADERS = (
    "Package", "RunID", "Ref", "Fingerprint", "Rule", "Severity", "Subject", "CaseFile", "Status", "Attempts", "LastError",
)
WIDTHS = (12, 12, 10, 18, 14, 11, 24, 34, 11, 10, 60)
PENDING = "Pending"
# The characters a worksheet cannot hold: the control characters, and U+FFFE and U+FFFF, which a saved
# file cannot be read back with.
ILLEGAL_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


# A CaseFile is a file directly in the run's cases folder: cases/<name>.json, with no folder below it.
CASE_FILE_PATTERN = re.compile(r"cases/[^/\\]+\.json")
# The columns that must equal the same value in their case file, as (column, the row's key, the case file's key).
FILE_COLUMNS = (
    ("Package", "package", "package"),
    ("RunID", "run_id", "run_id"),
    ("Ref", "ref", "ref"),
    ("Fingerprint", "fingerprint", "fingerprint"),
    ("Rule", "rule", "rule_id"),
    ("Severity", "severity", "severity"),
    ("Subject", "subject", "subject_key"),
)


def queue_row(case: dict, case_file: str) -> dict:
    """The queue row for one built case. case_file is the case's JSON path relative to the run folder, with "/"
    between folders. A new row is Pending, with no attempts and no error yet."""
    return {
        "package": case["package"],
        "run_id": case["run_id"],
        "ref": case["ref"],
        "fingerprint": case["fingerprint"],
        "rule": case["rule_id"],
        "severity": case["severity"],
        "subject": case["subject_key"],
        "case_file": case_file,
        "status": PENDING,
        "attempts": 0,
        "last_error": None,
    }


def write_case_queue(
    path: Path, run_dir: Path, package: str, run_id: str, findings: list[dict], rows: list[dict],
) -> None:
    """Write the run's CaseQueue.xlsx to path, once its rows have passed the checks in _check.

    findings are the run's findings in findings.json order. rows are one per finding, from queue_row, in the same
    order; each row's case_file is relative to run_dir. Raises ValueError, "Case queue check failed: ...", naming
    the Ref, fingerprint or file at fault, when a check fails. Raises OSError when the file cannot be written.
    """
    _check(run_dir, package, run_id, findings, rows)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME
    for column, name in enumerate(HEADERS, start=1):
        cell = sheet.cell(1, column)
        _put(cell, name)
        cell.font = Font(bold=True)
    for line, row in enumerate(rows, start=2):
        for column, (name, value) in enumerate(zip(HEADERS, _values(row)), start=1):
            cell = sheet.cell(line, column)
            if name == "Attempts":
                cell.value = value  # a real number, not text
            else:
                _put(cell, value)
            cell.alignment = Alignment(vertical="top", wrap_text=name == "LastError")
    for column, width in enumerate(WIDTHS, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = "A2"
    # An empty table keeps one blank row, which is how Excel itself stores an empty table.
    ref = f"A1:{get_column_letter(len(HEADERS))}{max(len(rows), 1) + 1}"
    table = Table(displayName=TABLE_NAME, name=TABLE_NAME, ref=ref)
    table.tableColumns = [TableColumn(id=n, name=name) for n, name in enumerate(HEADERS, start=1)]
    table.autoFilter = AutoFilter(ref=ref)
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    sheet.add_table(table)
    workbook.save(path)


def _check(run_dir: Path, package: str, run_id: str, findings: list[dict], rows: list[dict]) -> None:
    """Raise ValueError unless the rows are exactly the run's cases, as the case files on disk say they are.

    Each row is checked against its own finding, then against its own case file, and the first problem found is
    the one named. After the rows, the case files the rows name must be exactly the *.json files in cases/.
    """
    if len(rows) != len(findings):
        _fail(f"{_count(len(rows), 'row')} for {_count(len(findings), 'finding')}")
    queued: set[str] = set()
    for number, (finding, row) in enumerate(zip(findings, rows), start=1):
        ref = _text(row["ref"])
        fingerprint = _text(row["fingerprint"])
        if ref != _text(finding["id"]):
            _fail(f"row {number} is Ref {ref}, but finding {number} is Ref {_text(finding['id'])}")
        if not fingerprint.strip():
            _fail(f"Ref {ref} has no fingerprint")
        if fingerprint != _text(finding["fingerprint"]):
            _fail(f"Ref {ref}: fingerprint {fingerprint} is not the finding's fingerprint {_text(finding['fingerprint'])}")
        if _text(row["package"]) != package or _text(row["run_id"]) != run_id:
            _fail(
                f"Ref {ref}: Package and RunID are {_text(row['package'])} and {_text(row['run_id'])}, "
                f"not {package} and {run_id}"
            )
        if _text(row["status"]) != PENDING:
            _fail(f"Ref {ref}: Status is {_text(row['status'])}, not {PENDING}")
        if type(row["attempts"]) is not int or row["attempts"] != 0:
            _fail(f"Ref {ref}: Attempts is {row['attempts']!r}, not 0")
        case_file = _text(row["case_file"])
        if CASE_FILE_PATTERN.fullmatch(case_file) is None:
            _fail(f"{ref} ({fingerprint}): CaseFile {case_file} is not cases/<name>.json directly in the run's cases folder")
        if case_file in queued:
            _fail(f"Ref {ref}: case file {case_file} is already the case file of an earlier row")
        queued.add(case_file)
        case_path = run_dir / case_file
        if not case_path.is_file():
            _fail(f"Ref {ref}, fingerprint {fingerprint}: case file {case_file} is not there")
        try:
            case = json.loads(case_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _fail(f"Ref {ref}: case file {case_file} cannot be read: {exc}")
        if not isinstance(case, dict):
            _fail(f"{ref} ({fingerprint}): {case_file} does not hold a JSON object")
        for column, row_key, file_key in FILE_COLUMNS:
            queue_value = _text(row[row_key])
            file_value = _text(case.get(file_key))
            if queue_value != file_value:
                _fail(
                    f"{ref} ({fingerprint}): {column} is {_shown(queue_value)} in the queue "
                    f"but {_shown(file_value)} in {case_file}"
                )
    on_disk = {f"cases/{path.name}" for path in (run_dir / "cases").glob("*.json") if path.is_file()}
    if on_disk != queued:
        problems = [f"{name} is not in the queue" for name in sorted(on_disk - queued)]
        problems += [f"{name} is in the queue but not in cases/" for name in sorted(queued - on_disk)]
        _fail("; ".join(problems))


def _fail(problem: str) -> None:
    raise ValueError(f"Case queue check failed: {problem}")


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _shown(text: str) -> str:
    """A value as an error message shows it: "(blank)" when there is none."""
    return text or "(blank)"


def _values(row: dict) -> tuple:
    """The row's values in HEADERS order."""
    return (
        row["package"], row["run_id"], row["ref"], row["fingerprint"], row["rule"], row["severity"],
        row["subject"], row["case_file"], row["status"], row["attempts"], row["last_error"],
    )


def _put(cell, value: object) -> None:
    """Store a value as text, so a value like 401104 or =K07 stays text. An empty value leaves the cell blank."""
    text = _text(value)
    if text:
        cell.value = text
        cell.data_type = "s"


def _text(value: object) -> str:
    """The text a cell holds for a value, without the characters a worksheet cannot hold; "" for None."""
    return "" if value is None else ILLEGAL_TEXT.sub("", str(value))
