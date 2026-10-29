"""Run summary shared by review.json and report.md."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from drawing_qa.model.enums import DocType, IssueCode, Predicate
from drawing_qa.model.run import RunResult


class DocTypeSummary(BaseModel):
    files: int = 0
    sheets: int = 0
    sheets_by_kind: dict[str, int] = Field(default_factory=dict)
    claims: int = 0
    identifiers_by_kind: dict[str, int] = Field(default_factory=dict)
    unique_identifiers_by_kind: dict[str, int] = Field(default_factory=dict)
    placeholders: int = 0


READ, DECODED, PARTIAL, NOT_READ = "Read", "Decoded", "Partially decoded", "Not read"
READABILITY = [READ, DECODED, PARTIAL, NOT_READ]
# Issues that leave a sheet unread, most telling first: its detail in the readability list.
_NOT_READ = [IssueCode.PARSER_ERROR, IssueCode.SHX_NOT_DECODED, IssueCode.NO_SHX_TEXT, IssueCode.NEEDS_OCR, IssueCode.EMPTY_PAGE]


class SheetReadability(BaseModel):
    """How much of one sheet was read, so the checker knows where to look harder."""

    sheet: str  # 401103 sh 036, or the file name when the file could not be opened
    doc_type: str
    status: str  # one of READABILITY
    detail: str = ""


class RunSummary(BaseModel):
    by_doc_type: dict[str, DocTypeSummary]
    sheets: list[SheetReadability] = Field(default_factory=list)
    issues_by_code: dict[str, int]
    findings_by_category: dict[str, int]
    findings_by_severity: dict[str, int]


def summarise(result: RunResult) -> RunSummary:
    by_type: dict[str, DocTypeSummary] = {t.value: DocTypeSummary() for t in (DocType.PLC, DocType.CBD, DocType.TERM)}
    for f in result.files:
        by_type.setdefault(f.doc_type.value, DocTypeSummary()).files += 1

    drawing_type = {d.id: d.doc_type.value for d in result.drawings}
    for d in result.drawings:
        s = by_type.setdefault(d.doc_type.value, DocTypeSummary())
        s.sheets += 1
        s.sheets_by_kind[d.page_kind.value] = s.sheets_by_kind.get(d.page_kind.value, 0) + 1

    occurrences: dict[str, Counter[str]] = {}
    unique: dict[str, dict[str, set[str]]] = {}
    for c in result.claims:
        s = by_type[drawing_type[c.source.drawing_id]]
        s.claims += 1
        dt = drawing_type[c.source.drawing_id]
        if c.predicate is Predicate.PLACEHOLDER:
            s.placeholders += 1
        elif c.predicate is Predicate.OCCURS:
            occurrences.setdefault(dt, Counter())[c.subject_type.value] += 1
            unique.setdefault(dt, {}).setdefault(c.subject_type.value, set()).add(c.subject_key)
    for dt, counts in occurrences.items():
        by_type[dt].identifiers_by_kind = dict(sorted(counts.items()))
        by_type[dt].unique_identifiers_by_kind = {k: len(v) for k, v in sorted(unique[dt].items())}

    return RunSummary(
        by_doc_type=by_type,
        sheets=sheet_readability(result),
        issues_by_code=dict(sorted(Counter(i.code.value for i in result.issues).items())),
        findings_by_category=dict(sorted(Counter(f.category for f in result.findings).items())),
        findings_by_severity=dict(sorted(Counter(f.severity.value for f in result.findings).items())),
    )


def sheet_readability(result: RunResult) -> list[SheetReadability]:
    """Every sheet: read from its SHX text notes, decoded from its letter strokes (--ocr) with every word or only
    some, or not read at all (plotted without SHX notes and not decoded, scanned, empty, or the parser failed)."""
    issues: dict[str, dict[IssueCode, str]] = {}
    for i in result.issues:
        if i.drawing_id:
            issues.setdefault(i.drawing_id, {}).setdefault(i.code, i.message)
    out = []
    for d in result.drawings:
        own = issues.get(d.id, {})
        if d.decoded is not None and IssueCode.PARSER_ERROR not in own:
            k = d.decoded
            status = DECODED if k.words_dropped == 0 else PARTIAL
            detail = (f"decoded from line work (--ocr): {k.letters}/{k.shapes} letters, {k.words_kept} words used, "
                      f"{k.words_dropped} dropped")
        elif d.text_unreadable or any(c in own for c in _NOT_READ):
            status = NOT_READ
            detail = "; ".join(own[c] for c in _NOT_READ if c in own)
        else:
            status = READ
            detail = own.get(IssueCode.NO_GRID, "")
        out.append(SheetReadability(sheet=d.label, doc_type=d.doc_type.value, status=status, detail=detail))
    for f in result.files:
        if not f.readable:
            message = next((i.message for i in result.issues if i.code is IssueCode.UNREADABLE_FILE and i.file_path == f.path), "")
            out.append(SheetReadability(sheet=Path(f.path).name, doc_type=f.doc_type.value, status=NOT_READ,
                                        detail=f"file could not be opened: {message}"))
    return sorted(out, key=lambda s: (s.doc_type, s.sheet))
