"""Run summary shared by review.json and report.md."""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, Field

from drawing_qa.model.enums import DocType, Predicate
from drawing_qa.model.run import RunResult


class DocTypeSummary(BaseModel):
    files: int = 0
    sheets: int = 0
    sheets_by_kind: dict[str, int] = Field(default_factory=dict)
    claims: int = 0
    identifiers_by_kind: dict[str, int] = Field(default_factory=dict)
    unique_identifiers_by_kind: dict[str, int] = Field(default_factory=dict)
    placeholders: int = 0


class RunSummary(BaseModel):
    by_doc_type: dict[str, DocTypeSummary]
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
        issues_by_code=dict(sorted(Counter(i.code.value for i in result.issues).items())),
        findings_by_category=dict(sorted(Counter(f.category for f in result.findings).items())),
        findings_by_severity=dict(sorted(Counter(f.severity.value for f in result.findings).items())),
    )
