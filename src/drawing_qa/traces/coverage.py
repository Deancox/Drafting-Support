"""Trace coverage (GAME_PLAN.md "Outputs"): how much of each area, panel, PLC slot and JB traces end to end.

Counted per field trace: every channel trace with a device or JB, plus every orphan. Spare and
internal channels are left out. "Complete so far" is a partial trace whose only missing hops are
ones a not-yet-parsed drawing type would give (JB terminals, from the termination drawings).
A conflicted trace (two drawings disagree on a hop) is not counted as traced.
"""

from __future__ import annotations

from collections import defaultdict

from drawing_qa.model.engineering import CoverageRow, EngineeringModel, SignalTrace, TraceStatus

GROUPS = ("Overall", "Area", "Panel", "Slot", "JB / location")
_NONE = "(none)"


def _hop(t: SignalTrace, name: str) -> list[str]:
    return next((h.values for h in t.hops if h.hop == name), [])


def _enclosures(model: EngineeringModel, device: str) -> list[str]:
    """JBs / locations the CBD draws an orphan device inside."""
    return sorted(model.nodes[e.dst].key for e in model.edges if e.type == "DEVICE_IN" and e.src == f"DEVICE:{device}")


def _area(location: str) -> str:
    return location.split("-", 1)[0] if "-" in location else _NONE


def compute_coverage(traces: list[SignalTrace], model: EngineeringModel) -> list[CoverageRow]:
    rows: dict[tuple[str, str], CoverageRow] = {}

    def add(group: str, key: str, t: SignalTrace) -> None:
        row = rows.setdefault((group, key), CoverageRow(group=group, key=key))
        row.traces += 1
        # Each trace counts once: orphan, gap, conflicted (two drawings disagree, so not traced), then traced.
        if t.status is TraceStatus.ORPHAN:
            row.orphan += 1
        elif t.gaps:
            row.gap += 1
        elif t.conflicted:
            row.conflicted += 1
        elif t.status is TraceStatus.COMPLETE:
            row.complete += 1
        else:
            row.complete_so_far += 1

    for t in traces:
        if t.status in (TraceStatus.SPARE, TraceStatus.INTERNAL):
            continue
        if t.status is TraceStatus.ORPHAN:
            locations = _enclosures(model, t.device) if t.device else []
            panels, slots = [], []
        else:
            locations = [v for v in _hop(t, "JB") if v != "direct to panel"]
            panels, slots = _hop(t, "PANEL"), _hop(t, "SLOT")
        add("Overall", "All field traces", t)
        add("Area", _area(locations[0]) if locations else (_area(panels[0]) if panels else _NONE), t)
        for p in panels or [_NONE]:
            add("Panel", p, t)
        for s in slots:
            add("Slot", s, t)
        for loc in locations or [_NONE]:
            add("JB / location", loc, t)

    def order(row: CoverageRow) -> tuple:
        if row.group == "Slot":
            rack, _, slot = row.key.partition("/S")
            return GROUPS.index(row.group), rack, int(slot) if slot.isdigit() else 0, ""
        return GROUPS.index(row.group), row.key == _NONE, 0, row.key

    return sorted(rows.values(), key=order)


def by_group(rows: list[CoverageRow]) -> dict[str, list[CoverageRow]]:
    out: dict[str, list[CoverageRow]] = defaultdict(list)
    for r in rows:
        out[r.group].append(r)
    return out
