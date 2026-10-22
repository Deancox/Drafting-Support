"""review.json and findings.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from drawing_qa.model.enums import Predicate
from drawing_qa.model.run import RunResult
from drawing_qa.report.summary import RunSummary

SCHEMA_VERSION = "0.4"


def _write(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_review(result: RunResult, summary: RunSummary, out_dir: Path) -> Path:
    return _write(
        out_dir / "review.json",
        {
            "schema_version": SCHEMA_VERSION,
            "run": result.info.model_dump(mode="json"),
            "stages": [s.model_dump(mode="json") for s in result.stages],
            "ffi_map": result.ffi_map,
            "rule_runs": [r.model_dump(mode="json") for r in result.rule_runs],
            "summary": summary.model_dump(mode="json"),
            "files": [f.model_dump(mode="json") for f in result.files],
            "drawings": [d.model_dump(mode="json") for d in result.drawings],
            "issues": [i.model_dump(mode="json") for i in result.issues],
            # Plain words (spelling check input) are left out to keep the file readable; spelling
            # findings carry their own evidence in findings.json.
            "claims": [c.model_dump(mode="json") for c in result.claims if c.predicate is not Predicate.WORD],
            "model": {
                "nodes": [n.model_dump(mode="json") for n in result.model.nodes.values()],
                "edges": [e.model_dump(mode="json") for e in result.model.edges],
            },
            "traces": [t.model_dump(mode="json") for t in result.traces],
            "coverage": [{**r.model_dump(mode="json"), "traced_pct": round(r.traced_pct, 3)} for r in result.coverage],
        },
    )


def write_findings(result: RunResult, summary: RunSummary, out_dir: Path) -> Path:
    return _write(
        out_dir / "findings.json",
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": result.info.run_id,
            "summary": {
                "total": len(result.findings),
                "by_category": summary.findings_by_category,
                "by_severity": summary.findings_by_severity,
                "by_decision": result.review.by_decision if result.review else {},
            },
            "review": result.review.model_dump(mode="json") if result.review else None,
            "findings": [f.model_dump(mode="json") for f in result.findings],
        },
    )
