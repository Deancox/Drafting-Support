"""Finding record written to findings.json. Produced by the rule engine (M1+)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from drawing_qa.model.enums import Confidence, Decision, Severity
from drawing_qa.model.source import SourceRef


class FindingEvidence(BaseModel):
    role: str  # "A", "B" or "CONTEXT"
    claim_id: str | None = None
    source: SourceRef | None = None
    note: str | None = None


class FindingReview(BaseModel):
    """The checker's latest decision on this finding's fingerprint, from the QA register."""

    decision: Decision
    comment: str = ""
    reviewer: str = ""
    decided_on: str = ""  # as entered in the register, ISO date where possible
    recorded_in_run: str  # run that read it from the register (or applied the waiver)
    waiver: str | None = None  # set when the decision comes from a waiver in waivers.toml, not the register


class Finding(BaseModel):
    id: str
    fingerprint: str
    rule_id: str
    rule_version: str
    category: str
    severity: Severity
    confidence: Confidence
    subject_type: str
    subject_key: str
    trace_key: str | None = None
    hop: int | None = None
    message: str
    recommendation: str | None = None
    evidence: list[FindingEvidence] = Field(default_factory=list)
    status: Decision = Decision.OPEN  # the latest review decision carried forward, OPEN if none
    review: FindingReview | None = None
    first_seen: str | None = None  # date of the first run that raised this fingerprint
    decoded: bool = False  # rests on text decoded from letter strokes (--ocr): check the drawing
