"""FindingBuilder: the only way rules create findings.

Fingerprints identify the same problem across runs and revisions (DATA_MODEL.md
section 8). They use canonical keys, the drawing number, sheet and grid cell, never
run IDs, coordinates or revision letters.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import Confidence, Severity
from drawing_qa.model.findings import Finding, FindingEvidence
from drawing_qa.model.source import Drawing, SourceRef
from drawing_qa.rules.base import RuleMeta


class FindingBuilder:
    def __init__(self, meta: RuleMeta, severity: Severity, drawings: dict[str, Drawing]) -> None:
        self.meta = meta
        self.severity = severity
        self._drawings = drawings

    def where(self, source: SourceRef) -> str:
        """'401103 sh 203 grid D5' for messages."""
        d = self._drawings[source.drawing_id]
        return f"{d.label}" + (f" grid {source.grid_cell}" if source.grid_cell else "")

    def __call__(
        self,
        *,
        subject_type: str,
        subject_key: str,
        message: str,
        evidence: Sequence[Claim] = (),
        evidence_b: Sequence[Claim] = (),
        sources: Sequence[SourceRef] = (),
        recommendation: str | None = None,
        discriminator: str = "",
        severity: Severity | None = None,
        confidence: Confidence = Confidence.DETERMINISTIC,
    ) -> Finding:
        evidence_list = [FindingEvidence(role="A", claim_id=c.id, source=c.source) for c in evidence]
        evidence_list += [FindingEvidence(role="B", claim_id=c.id, source=c.source) for c in evidence_b]
        evidence_list += [FindingEvidence(role="CONTEXT", source=s) for s in sources]
        locations = sorted(
            {self._location_key(e.source) for e in evidence_list if e.source is not None}
        )
        basis = "|".join([self.meta.rule_id, subject_type, subject_key, discriminator, *locations])
        fingerprint = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
        return Finding(
            id="",  # numbered by the engine once all findings are sorted
            fingerprint=fingerprint,
            rule_id=self.meta.rule_id,
            rule_version=self.meta.version,
            category=self.meta.category.value,
            severity=severity or self.severity,
            confidence=confidence,
            subject_type=subject_type,
            subject_key=subject_key,
            message=message,
            recommendation=recommendation,
            evidence=evidence_list,
        )

    def _location_key(self, source: SourceRef) -> str:
        d = self._drawings[source.drawing_id]
        return f"{d.drawing_no}/{d.sheet}/{source.grid_cell or ''}/{source.raw_text}"
