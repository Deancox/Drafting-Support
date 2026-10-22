"""Waivers (DATA_MODEL.md section 4.7): known, accepted exceptions from config/waivers.toml.

A waiver marks every finding of its rule whose subject matches its pattern as WAIVED, with the
waiver's reason as the comment. The checker's own decision in the QA register (anything but OPEN)
takes precedence. Expired waivers are listed but not applied.

Rows a waiver produced are written to the register with the comment "Waiver <id>: <reason>"; the
reader does not take those back as the checker's own decisions, so removing a waiver reopens them.
"""

from __future__ import annotations

import re
from datetime import date
from fnmatch import fnmatchcase

from drawing_qa.config import Waiver
from drawing_qa.model.enums import Decision
from drawing_qa.model.findings import Finding, FindingReview
from drawing_qa.model.run import WaiverUse

_GENERATED = re.compile(r"^Waiver \S+: ")


def waiver_comment(w: Waiver) -> str:
    return f"Waiver {w.id}: {w.reason}"


def is_waiver_generated(decision: Decision, comment: str) -> bool:
    """A register row that only repeats what a waiver wrote (WAIVED + 'Waiver <id>: ...')."""
    return decision is Decision.WAIVED and bool(_GENERATED.match(comment))


def matches(w: Waiver, f: Finding) -> bool:
    return fnmatchcase(f.rule_id.upper(), w.rule.upper()) and fnmatchcase(f.subject_key.upper(), w.subject.upper())


def waive(findings: list[Finding], waivers: list[Waiver], run_id: str, today: date) -> tuple[dict[str, FindingReview], list[WaiverUse]]:
    """Fingerprint -> the review an active waiver gives it (first matching waiver wins), and a use record per waiver."""
    reviews: dict[str, FindingReview] = {}
    uses: list[WaiverUse] = []
    for w in waivers:
        expired = w.expires is not None and w.expires < today
        use = WaiverUse(id=w.id, rule=w.rule, subject=w.subject, reason=w.reason, approved_by=w.approved_by,
                        expires=w.expires, status="EXPIRED" if expired else "ACTIVE")
        uses.append(use)
        if expired:
            continue
        for f in findings:
            if f.fingerprint in reviews or not matches(w, f):
                continue
            reviews[f.fingerprint] = FindingReview(
                decision=Decision.WAIVED, comment=waiver_comment(w), reviewer=w.approved_by,
                recorded_in_run=run_id, waiver=w.id,
            )
            use.matched += 1
    return reviews, uses
