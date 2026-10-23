"""Termination Validation (TERM): JB terminal strips and slot termination sheets (M2).

All of these need the termination drawings (Requirement.TERMINATIONS) and are skipped with a
reason when they are left out of the review (--exclude term).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import DocType, EntityType, ExtractionMethod, Predicate, Severity
from drawing_qa.model.findings import Finding
from drawing_qa.rules.base import Category, Requirement, Rule, RuleMeta
from drawing_qa.rules.context import RuleContext
from drawing_qa.rules.findings import FindingBuilder
from drawing_qa.rules.registry import rule

NEEDS_TERMINATIONS = frozenset({Requirement.CLAIMS, Requirement.GRAPH, Requirement.TERMINATIONS})
_PLACEHOLDER_WIRE = re.compile(r"X{3,}")


def strip_rows(ctx: RuleContext) -> list[Claim]:
    """One claim per JB terminal row drawn on a termination sheet."""
    return [
        c for c in ctx.claims
        if c.parser is DocType.TERM and c.method is ExtractionMethod.TEMPLATE
        and c.predicate is Predicate.OCCURS and c.subject_type is EntityType.TERMINAL
    ]


def term_claims(ctx: RuleContext, predicate: Predicate) -> list[Claim]:
    return [c for c in ctx.claims if c.parser is DocType.TERM and c.method is ExtractionMethod.TEMPLATE and c.predicate is predicate]


def jb_rows(ctx: RuleContext) -> list[Claim]:
    """Rows of the JB's own strip: not the small strips of equipment boxed inside it (motor isolators)."""
    return [c for c in strip_rows(ctx) if c.value.get("device_block", True)]


def rows_by_jb(ctx: RuleContext) -> dict[str, list[Claim]]:
    out: dict[str, list[Claim]] = defaultdict(list)
    for c in jb_rows(ctx):
        out[c.value["jb"]].append(c)
    return out


def terminal_number(c: Claim) -> int | None:
    t = str(c.value.get("terminal", ""))
    return int(t) if t.isdigit() else None


def ranges(numbers: list[int]) -> str:
    """[41, 42, 43, 47] -> '41-43, 47'."""
    out, start, prev = [], None, None
    for n in sorted(numbers):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if prev != start else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if prev != start else str(start))
    return ", ".join(out)


def is_placeholder_wire(text: str) -> bool:
    return bool(_PLACEHOLDER_WIRE.search(text.upper()))


@rule
class TerminalAllocation(Rule):
    meta = RuleMeta(
        rule_id="TERM-001",
        title="Terminal Allocation",
        category=Category.TERM,
        default_severity=Severity.MINOR,
        description=(
            "A JB terminal with no wire number, or not under any device block. Placeholder wire numbers (TNXXXX) "
            "are reported by PLACEHOLDER-002 instead."
        ),
        requires=NEEDS_TERMINATIONS,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for c in sorted(strip_rows(ctx), key=lambda c: (c.value["jb"], terminal_number(c) or 0)):
            wire = str(c.value.get("wire_text", ""))
            missing = []
            if not wire:
                missing.append("wire number")
            if not c.value.get("block"):
                missing.append("device")
            if not missing:
                continue
            if c.source.drawing_id in ctx.decoded_ids:
                continue  # decoded sheet (--ocr): the wire number or device may not have decoded
            what = " or ".join(missing)
            yield make(
                subject_type="TERMINAL",
                subject_key=c.subject_key,
                message=f"{c.subject_key} at {make.where(c.source)} has no {what}",
                evidence=[c],
                discriminator=",".join(missing),
                recommendation=f"Show the {what} for {c.subject_key}, or mark the terminal SPARE",
            )


@rule
class DuplicateTerminal(Rule):
    meta = RuleMeta(
        rule_id="TERM-002",
        title="Duplicate Terminal",
        category=Category.TERM,
        default_severity=Severity.MAJOR,
        description=(
            "One JB terminal number drawn twice with different wires or devices (on one sheet or across sheets). "
            "Equipment boxed inside the JB with its own small strip (a motor isolator) is numbered on its own."
        ),
        requires=NEEDS_TERMINATIONS,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        by_key: dict[str, list[Claim]] = defaultdict(list)
        for c in jb_rows(ctx):
            by_key[c.subject_key].append(c)
        for key, cs in sorted(by_key.items()):
            variants = {(c.value.get("wire_text"), c.value.get("block")) for c in cs}
            if len(cs) < 2 or len(variants) < 2:
                continue
            listed = "; ".join(f"{c.value.get('wire_text') or 'no wire'} / {c.value.get('block') or 'no device'} at {make.where(c.source)}"
                               for c in cs)
            yield make(
                subject_type="TERMINAL",
                subject_key=key,
                message=f"{key} is drawn {len(cs)} times: {listed}",
                evidence=cs,
                recommendation="Number the terminals uniquely within the JB",
            )


@rule
class TerminalReference(Rule):
    meta = RuleMeta(
        rule_id="TERM-003",
        title="Terminal Reference",
        category=Category.TERM,
        default_severity=Severity.MINOR,
        description=(
            "A PLC slot termination sheet sends wiring to a JB 'REF. TERM. SHT nnnn', but that sheet does not show the "
            "JB's terminal strip; or a <JB>:<terminal> reference names a terminal its JB's strip does not have."
        ),
        requires=NEEDS_TERMINATIONS,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        strips = rows_by_jb(ctx)
        sheets_of_jb: dict[str, set[str]] = {jb: {ctx.sheet_of(c).sheet for c in cs} for jb, cs in strips.items()}
        seen = set()
        for c in term_claims(ctx, Predicate.DESTINED_FOR):
            if c.subject_type is not EntityType.PLC_MODULE or c.object_type is not EntityType.JUNCTION_BOX:
                continue
            ref = c.value.get("ref_sheet")
            if not ref or (c.subject_key, c.object_key, ref) in seen:
                continue
            seen.add((c.subject_key, c.object_key, ref))
            ref_sheet = f"{int(ref):03d}"
            supplied = any(d.sheet == ref_sheet for d in ctx.active_drawings if d.doc_type is DocType.TERM)
            if DocType.TERM in ctx.decoded_types:
                continue  # a decoded termination sheet (--ocr): the JB's terminals may not have decoded
            if not supplied or ref_sheet in sheets_of_jb.get(c.object_key, set()):
                continue  # a sheet not supplied is DRW-001's business
            shown = ", ".join(sorted(sheets_of_jb.get(c.object_key, set()))) or "no supplied sheet"
            yield make(
                subject_type="JUNCTION_BOX",
                subject_key=c.object_key,
                message=(f"Slot {c.value.get('slot')} wiring at {make.where(c.source)} goes to {c.object_key} 'REF. TERM. SHT {ref}', "
                         f"but sheet {ref_sheet} does not show {c.object_key}'s terminals ({c.object_key} is on {shown})"),
                evidence=[c],
                discriminator=f"ref|{c.subject_key}|{ref}",
                recommendation=f"Correct the sheet reference for {c.object_key}",
            )
        for c in ctx.claims_of(Predicate.OCCURS, EntityType.TERMINAL):
            if c.method is ExtractionMethod.TEMPLATE:
                continue
            jb = c.value.get("jb")
            if jb in strips and any(r.source.drawing_id in ctx.decoded_ids for r in strips[jb]):
                continue  # the strip is on a decoded sheet (--ocr): the terminal may not have decoded
            if jb in strips and c.subject_key not in {r.subject_key for r in strips[jb]}:
                yield make(
                    subject_type="TERMINAL",
                    subject_key=c.subject_key,
                    message=f"{c.subject_key} is referenced at {make.where(c.source)}, but {jb}'s terminal strip has no terminal {c.value.get('terminal')}",
                    evidence=[c],
                    discriminator="missing-terminal",
                    recommendation=f"Check the terminal number against {jb}'s termination drawing",
                )


@rule
class TerminalSequence(Rule):
    meta = RuleMeta(
        rule_id="TERM-004",
        title="Terminal Sequence",
        category=Category.TERM,
        default_severity=Severity.MINOR,
        description="Gaps in a JB's terminal numbering, across all its termination sheets.",
        requires=NEEDS_TERMINATIONS,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for jb, cs in sorted(rows_by_jb(ctx).items()):
            numbers = {n for c in cs if (n := terminal_number(c)) is not None}
            if not numbers:
                continue
            missing = sorted(set(range(1, max(numbers) + 1)) - numbers)
            if not missing or any(c.source.drawing_id in ctx.decoded_ids for c in cs):
                continue  # none missing, or the strip is on a decoded sheet (--ocr): a terminal may not have decoded
            sheets = ", ".join(sorted({ctx.sheet_of(c).label for c in cs}))
            yield make(
                subject_type="JUNCTION_BOX",
                subject_key=jb,
                message=f"{jb} terminals {ranges(missing)} are not drawn (terminals 1-{max(numbers)} on {sheets})",
                evidence=sorted(cs, key=lambda c: terminal_number(c) or 0)[:1],
                discriminator=ranges(missing),
                recommendation=f"Show terminals {ranges(missing)} (as SPARE if unused), or renumber the strip",
            )
