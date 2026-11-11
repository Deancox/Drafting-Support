"""Rules in the catalogue (IMPLEMENTATION_PLAN.md section 7.3) that are not built yet.

Built rules describe themselves through RuleMeta; this list fills in the rest so the QA
register's rule key always shows the whole catalogue and what each planned rule waits for.
Remove an entry when its rule is built.
"""

from __future__ import annotations

from dataclasses import dataclass

_TERM = "termination drawings (M2)"


@dataclass(frozen=True)
class PlannedRule:
    rule_id: str
    title: str
    check: str
    waits_for: str = ""


PLANNED: tuple[PlannedRule, ...] = (
    PlannedRule("DEV-001", "Device Presence", "Appears in every document type the document matrix expects", "equipment list (M8)"),
    PlannedRule("DEV-002", "Device Attribute Consistency", "Same type, description and signal across sources", "equipment list (M8)"),
)
