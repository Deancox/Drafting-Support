"""Loads one run's review cases, each rule's "what this checks" line, and Copilot's verdict files.

A case is a cases/<fingerprint>.json file written by drawing_qa.cases. A Copilot verdict, when the Copilot flow
has judged the case, is <run folder>/verdicts/<fingerprint>.json:

    {"fingerprint": ..., "message_hash": ..., "verdict": "CONFIRMED" | "FALSE_POSITIVE" | "DEFERRED",
     "confidence": "HIGH" | "MEDIUM" | "LOW", "reasoning": "...", "reviewed_at": "2026-11-20T09:14:00Z"}

A verdict whose message_hash is not the case's was made on an older version of the finding (stale). A verdict
file that can't be used gives a problem line instead, and the case can still be reviewed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

VERDICTS = ("CONFIRMED", "FALSE_POSITIVE", "DEFERRED")
CONFIDENCES = ("HIGH", "MEDIUM", "LOW")


@dataclass(frozen=True)
class Verdict:
    verdict: str
    confidence: str
    reasoning: str
    reviewed_at: str
    stale: bool


@dataclass(frozen=True)
class Case:
    data: dict
    cases_dir: Path
    rule_description: str
    verdict: Verdict | None
    verdict_problem: str | None

    @property
    def fingerprint(self) -> str:
        return self.data["fingerprint"]

    @property
    def ref(self) -> str:
        return self.data.get("ref", "")

    @property
    def rule_id(self) -> str:
        return self.data.get("rule_id", "")

    @property
    def severity(self) -> str:
        return self.data.get("severity", "")


@cache
def rule_descriptions() -> dict[str, str]:
    """Each built rule's one-line description, by rule id."""
    from drawing_qa.rules.registry import all_rules

    return {cls.meta.rule_id: cls.meta.description for cls in all_rules()}


def read_verdict(run_dir: Path, case: dict) -> tuple[Verdict | None, str | None]:
    """The case's Copilot verdict, or (None, None) when there is none, or (None, problem) when it can't be used."""
    path = Path(run_dir) / "verdicts" / f"{case['fingerprint']}.json"
    if not path.is_file():
        return None, None
    name = f"verdicts/{path.name}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"Copilot verdict file {name} can't be read: {exc}"
    if not isinstance(data, dict):
        return None, f"Copilot verdict file {name} is not a verdict"
    if data.get("fingerprint") != case["fingerprint"]:
        return None, f"Copilot verdict file {name} is for another case ({data.get('fingerprint')!r})"
    if data.get("verdict") not in VERDICTS:
        return None, f"Copilot verdict file {name} has an unknown verdict {data.get('verdict')!r}"
    if data.get("confidence") not in CONFIDENCES:
        return None, f"Copilot verdict file {name} has an unknown confidence {data.get('confidence')!r}"
    reasoning, reviewed_at = data.get("reasoning", ""), data.get("reviewed_at", "")
    if not isinstance(reasoning, str) or not isinstance(reviewed_at, str):
        return None, f"Copilot verdict file {name}: reasoning and reviewed_at must be text"
    return Verdict(
        verdict=data["verdict"],
        confidence=data["confidence"],
        reasoning=reasoning,
        reviewed_at=reviewed_at,
        stale=data.get("message_hash") != case.get("message_hash"),
    ), None


def load_cases_with_problems(run_dir: Path) -> tuple[list[Case], list[str]]:
    """Every case in the run folder, sorted by Ref, and a line for each case file that can't be read."""
    cases_dir = Path(run_dir) / "cases"
    descriptions = rule_descriptions()
    cases: list[Case] = []
    problems: list[str] = []
    for path in sorted(cases_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("fingerprint"), str):
                raise ValueError("not a case file")
        except (OSError, ValueError) as exc:
            problems.append(f"cases/{path.name} skipped: {exc}")
            continue
        verdict, problem = read_verdict(run_dir, data)
        cases.append(Case(data, cases_dir, descriptions.get(data.get("rule_id", ""), ""), verdict, problem))
    cases.sort(key=lambda c: (c.ref, c.fingerprint))
    return cases, problems


def load_cases(run_dir: Path) -> list[Case]:
    """Every case in the run folder, sorted by Ref."""
    return load_cases_with_problems(run_dir)[0]
