"""Run-level records: everything one pipeline run produced."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from drawing_qa.model.claims import Claim
from drawing_qa.model.engineering import CoverageRow, EngineeringModel, SignalTrace
from drawing_qa.model.enums import Decision, StageStatus
from drawing_qa.model.findings import Finding, FindingReview
from drawing_qa.model.source import Drawing, InputFile, Issue


class RunInfo(BaseModel):
    run_id: str
    tool_version: str
    started_at: datetime
    finished_at: datetime | None = None
    input_root: str
    config_hash: str


class StageResult(BaseModel):
    name: str
    status: StageStatus
    note: str = ""


class RuleRun(BaseModel):
    """Audit of one rule in one run: what ran, which version, and what happened."""

    rule_id: str
    version: str
    title: str
    category: str
    status: StageStatus  # DONE, SKIPPED (prerequisite missing / disabled) or FAILED
    findings: int = 0
    note: str = ""


class GoneFinding(BaseModel):
    """A finding this run does not raise: the previous run raised it, or the checker gave it a decision
    other than FIXED. It stays listed until marked FIXED (or the decision is cleared)."""

    fingerprint: str
    rule_id: str
    subject_key: str
    severity: str
    message: str
    last_raised: str  # date of the last run that raised it
    decision: Decision = Decision.OPEN  # the checker's last decision on it
    review: FindingReview | None = None


class WaiverUse(BaseModel):
    """One waiver from waivers.toml and what it did this run."""

    id: str
    rule: str
    subject: str
    reason: str
    approved_by: str = ""
    expires: date | None = None
    status: str  # ACTIVE, EXPIRED
    matched: int = 0  # findings it matched this run (0 for an expired waiver)
    overridden: int = 0  # findings it matched where the checker's own decision was kept


class ReviewSummary(BaseModel):
    """Review carry-forward for this run (M5): what the QA register and review store contributed."""

    store_path: str
    register_read: str | None = None  # QA register the checker's decisions were read from
    decisions_recorded: int = 0  # register rows that changed a decision this run
    previous_run_id: str | None = None
    new_findings: int = 0  # fingerprints the previous run did not raise (all of them on a first run)
    by_decision: dict[str, int] = Field(default_factory=dict)  # this run's findings by carried decision
    no_longer_raised: list[GoneFinding] = Field(default_factory=list)
    waivers: list[WaiverUse] = Field(default_factory=list)


class RunResult(BaseModel):
    """In-memory result of a run; the report writers turn it into output files."""

    info: RunInfo
    files: list[InputFile] = Field(default_factory=list)
    drawings: list[Drawing] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    stages: list[StageResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    rule_runs: list[RuleRun] = Field(default_factory=list)
    model: EngineeringModel = Field(default_factory=EngineeringModel)
    traces: list[SignalTrace] = Field(default_factory=list)
    coverage: list[CoverageRow] = Field(default_factory=list)
    review: ReviewSummary | None = None
    left_out: list[str] = Field(default_factory=list)  # input types left out of the review (--exclude / config)
