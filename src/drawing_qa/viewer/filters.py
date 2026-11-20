"""Which cases the list shows, and what a bulk decision would set.

A case whose fingerprint is not on the register's Findings tab (cases of an older run against a newer register)
counts as undecided in the list but can't be decided, so bulk plans leave it out and count it as skipped.
A stale Copilot verdict (made on an older version of the finding) counts as no usable verdict.
"""

from __future__ import annotations

from dataclasses import dataclass

from drawing_qa.viewer.cases import Case
from drawing_qa.viewer.register_writer import Entry

COMMENT_LIMIT = 250


@dataclass(frozen=True)
class CaseFilter:
    rule: str | None = None
    severity: str | None = None
    status: str = "UNDECIDED"  # UNDECIDED, ANY, DECIDED, or a decision
    copilot: str = "ANY"  # ANY, NONE, HIGH, DISAGREES, or a verdict
    text: str = ""
    only: frozenset[str] | None = None  # just these fingerprints (Show related)


@dataclass(frozen=True)
class BulkPlan:
    changes: dict[str, Entry]
    overwrites: int
    skipped: int


def _decision(case: Case, decisions: dict[str, Entry]) -> str:
    entry = decisions.get(case.fingerprint)
    return entry.decision if entry else "OPEN"


def _usable_verdict(case: Case):
    return case.verdict if case.verdict is not None and not case.verdict.stale else None


def _text_of(case: Case) -> str:
    sides = case.data.get("sides") or {}
    parts = [case.data.get("subject_key") or "", case.data.get("message") or ""]
    parts += [str(side.get("raw_text") or "") for side in sides.values() if isinstance(side, dict)]
    return " ".join(parts).lower()


def _matches(case: Case, decisions: dict[str, Entry], f: CaseFilter) -> bool:
    if f.only is not None and case.fingerprint not in f.only:
        return False
    if f.rule and case.rule_id != f.rule:
        return False
    if f.severity and case.severity != f.severity:
        return False
    mine = _decision(case, decisions)
    if f.status == "UNDECIDED" and mine != "OPEN":
        return False
    if f.status == "DECIDED" and mine == "OPEN":
        return False
    if f.status not in ("UNDECIDED", "ANY", "DECIDED") and mine != f.status:
        return False
    verdict = _usable_verdict(case)
    if f.copilot == "NONE" and verdict is not None:
        return False
    if f.copilot == "HIGH" and (verdict is None or verdict.confidence != "HIGH"):
        return False
    if f.copilot == "DISAGREES" and (verdict is None or mine == "OPEN" or verdict.verdict == mine):
        return False
    if f.copilot not in ("ANY", "NONE", "HIGH", "DISAGREES") and (verdict is None or verdict.verdict != f.copilot):
        return False
    return not f.text.strip() or f.text.strip().lower() in _text_of(case)


def filter_cases(cases: list[Case], decisions: dict[str, Entry], f: CaseFilter) -> list[Case]:
    return [c for c in cases if _matches(c, decisions, f)]


def _plan(cases: list[Case], decisions: dict[str, Entry], entry_for) -> BulkPlan:
    changes: dict[str, Entry] = {}
    overwrites = skipped = 0
    for case in cases:
        entry = entry_for(case) if case.fingerprint in decisions else None
        if entry is None:
            skipped += 1
            continue
        current = decisions[case.fingerprint].decision
        if current != "OPEN" and current != entry.decision:
            overwrites += 1
        changes[case.fingerprint] = entry
    return BulkPlan(changes, overwrites, skipped)


def plan_bulk(cases: list[Case], decisions: dict[str, Entry], decision: str, comment: str, reviewer: str,
              today: str) -> BulkPlan:
    """Every case shown set to one decision."""
    return _plan(cases, decisions, lambda case: Entry(decision, comment, reviewer, today))


def copilot_entry(case: Case, reviewer: str, today: str) -> Entry | None:
    """The decision that accepts this case's Copilot verdict, or None when it has no usable verdict."""
    verdict = _usable_verdict(case)
    if verdict is None:
        return None
    comment = f"Copilot: {verdict.reasoning}".strip()
    if len(comment) > COMMENT_LIMIT:
        comment = comment[: COMMENT_LIMIT - 3] + "..."
    return Entry(verdict.verdict, comment, reviewer, today)


def plan_accept_copilot(cases: list[Case], decisions: dict[str, Entry], reviewer: str, today: str) -> BulkPlan:
    """Every case shown set to its own Copilot verdict; stale, missing and undecidable ones are skipped."""
    return _plan(cases, decisions, lambda case: copilot_entry(case, reviewer, today))
