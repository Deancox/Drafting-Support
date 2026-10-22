"""Placeholder Validation (PLACEHOLDER): incomplete drafting markers left on the drawings.

Severity follows the drawing status: MAJOR on issued drawings (FOR CONSTRUCTION,
AS BUILT), MINOR on PRELIMINARY ones, where open items are expected but still listed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import Predicate, Severity
from drawing_qa.model.findings import Finding
from drawing_qa.rules.base import Category, Rule, RuleMeta
from drawing_qa.rules.common import issued_severity
from drawing_qa.rules.context import RuleContext
from drawing_qa.rules.findings import FindingBuilder
from drawing_qa.rules.registry import rule

_PURE_X = re.compile(r"^X{3,}$")
_IDENTIFIER_TOKENS = {"TNXXXX", "PXXXXX", "CXXXXX"}

# What each placeholder stands for, for the recommendation.
_MEANING = {
    "CXXXXX": "cable number",
    "PXXXXX": "PLC / power reference",
    "TNXXXX": "wire or device number",
}


class PlaceholderRule(Rule):
    """Base: one finding per placeholder claim that this rule matches. Not registered itself."""

    def matches(self, claim: Claim) -> bool:
        raise NotImplementedError

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for c in ctx.claims_of(Predicate.PLACEHOLDER):
            if not self.matches(c):
                continue
            drawing = ctx.sheet_of(c)
            yield make(
                subject_type="TEXT",
                subject_key=c.subject_key,
                message=f"Placeholder '{c.subject_key}' at {make.where(c.source)} ({drawing.status or 'status unknown'})",
                evidence=[c],
                recommendation=self.recommendation(c),
                severity=self.severity_for(make, drawing),
            )

    def severity_for(self, make: FindingBuilder, drawing) -> Severity:
        # A severity set in rules.toml wins over the status-based default.
        if make.severity is not self.meta.default_severity:
            return make.severity
        return issued_severity(drawing)

    def recommendation(self, claim: Claim) -> str:
        meaning = _MEANING.get(claim.subject_key)
        return f"Replace with the allocated {meaning}" if meaning else "Complete the drafting"


@rule
class XxxxDetection(PlaceholderRule):
    meta = RuleMeta(
        rule_id="PLACEHOLDER-001",
        title="XXXX Detection",
        category=Category.PLACEHOLDER,
        default_severity=Severity.MINOR,
        description="XXX, XXXX or any run of X standing alone.",
    )

    def matches(self, claim: Claim) -> bool:
        return claim.value.get("pattern") in ("TOKEN", "X_RUN", "COMPONENT") and bool(_PURE_X.match(claim.subject_key))


@rule
class IdentifierPlaceholder(PlaceholderRule):
    meta = RuleMeta(
        rule_id="PLACEHOLDER-002",
        title="Identifier Placeholder",
        category=Category.PLACEHOLDER,
        default_severity=Severity.MINOR,
        description="TNXXXX, PXXXXX, CXXXXX and identifiers whose number is X (S-TNXXXX, SPL-JBX, SPL-JB1:XX).",
    )

    def matches(self, claim: Claim) -> bool:
        p, key = claim.value.get("pattern"), claim.subject_key
        if p == "TOKEN":
            return key in _IDENTIFIER_TOKENS
        return p == "IDENTIFIER_X" or (p == "X_RUN" and not _PURE_X.match(key))


@rule
class RefTermXxx(PlaceholderRule):
    meta = RuleMeta(
        rule_id="PLACEHOLDER-003",
        title="REF TERM XXX",
        category=Category.PLACEHOLDER,
        default_severity=Severity.MINOR,
        description="Termination reference left as 'REF TERM XXX'.",
    )

    def matches(self, claim: Claim) -> bool:
        return claim.value.get("pattern") == "PHRASE"

    def recommendation(self, claim: Claim) -> str:
        return "Replace with the actual termination reference"


@rule
class Hold(PlaceholderRule):
    meta = RuleMeta(
        rule_id="PLACEHOLDER-004",
        title="HOLD",
        category=Category.PLACEHOLDER,
        default_severity=Severity.MINOR,
        description="HOLD marker on the drawing.",
    )

    def matches(self, claim: Claim) -> bool:
        return claim.subject_key == "HOLD"

    def recommendation(self, claim: Claim) -> str:
        return "Resolve the hold and remove the marker"


@rule
class Aut1(PlaceholderRule):
    meta = RuleMeta(
        rule_id="PLACEHOLDER-005",
        title="AUT1",
        category=Category.PLACEHOLDER,
        default_severity=Severity.MINOR,
        description="AUT1 marker. Inside a superseded designation (LTL-AUT1) it is reported by DRW-006 instead.",
    )

    def matches(self, claim: Claim) -> bool:
        return claim.subject_key == "AUT1" and "replacement" not in claim.value
