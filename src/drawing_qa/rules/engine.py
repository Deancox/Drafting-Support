"""Rule engine: runs every registered rule against a run, in isolation."""

from __future__ import annotations

import logging

from drawing_qa.model.enums import Severity, StageStatus
from drawing_qa.model.findings import Finding
from drawing_qa.model.run import RuleRun
from drawing_qa.rules.context import RuleContext
from drawing_qa.rules.findings import FindingBuilder
from drawing_qa.rules.registry import all_rules

log = logging.getLogger(__name__)

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.MAJOR: 1, Severity.MINOR: 2, Severity.INFO: 3}


class RuleEngine:
    def run(self, ctx: RuleContext) -> tuple[list[Finding], list[RuleRun]]:
        findings: list[Finding] = []
        runs: list[RuleRun] = []
        for cls in all_rules():
            meta = cls.meta
            setting = ctx.settings.rules.get(meta.rule_id)
            record = RuleRun(
                rule_id=meta.rule_id,
                version=meta.version,
                title=meta.title,
                category=meta.category.value,
                status=StageStatus.DONE,
            )
            runs.append(record)
            if setting is not None and not setting.enabled:
                record.status, record.note = StageStatus.SKIPPED, "Disabled in rules.toml"
                continue
            missing = meta.requires - ctx.available
            if missing:
                record.status = StageStatus.SKIPPED
                record.note = "Needs " + ", ".join(sorted(m.value for m in missing))
                continue
            severity = Severity(setting.severity) if setting and setting.severity else meta.default_severity
            make = FindingBuilder(meta, severity, ctx.drawing)
            try:
                produced = list(cls().evaluate(ctx, make))
            except Exception as exc:  # one failing rule must not stop the others
                log.exception("Rule %s failed", meta.rule_id)
                record.status, record.note = StageStatus.FAILED, f"{type(exc).__name__}: {exc}"
                continue
            record.findings = len(produced)
            findings.extend(produced)

        _number_repeats(findings)
        findings.sort(key=lambda f: (_SEVERITY_ORDER[f.severity], f.rule_id, f.subject_key, f.fingerprint))
        for n, f in enumerate(findings, start=1):
            f.id = f"F{n:05d}"
        return findings, runs


def _number_repeats(findings: list[Finding]) -> None:
    """Give each occurrence its own fingerprint. Two identical placeholders in one grid cell share a
    fingerprint; the second (in reading order) gets "-2", and so on, so each register row carries its own decision."""
    groups: dict[str, list[Finding]] = {}
    for f in findings:
        groups.setdefault(f.fingerprint, []).append(f)
    for same in groups.values():
        if len(same) < 2:
            continue

        def position(f: Finding) -> tuple[float, float]:
            src = next((e.source for e in f.evidence if e.source), None)
            return (src.bbox[1], src.bbox[0]) if src else (0.0, 0.0)

        for n, f in enumerate(sorted(same, key=position)[1:], start=2):
            f.fingerprint = f"{f.fingerprint}-{n}"
