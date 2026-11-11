"""Cable Validation (CABLE): the cables the termination drawings draw, against the cable block diagrams.

The termination parser reads every cable label stack on a JB termination sheet ("50C+E 1.5mm2 PVC /
C93812 / 12 SPARE") with the JBs, field enclosures and panels the sheet names. The CBD parser gives
each traced cable's ends, spec and number, and every cable number written on the diagram. A cable
number joins the two, so these rules compare:

  CABLE-001  a cable number on the terminations is on the cable block diagrams
  CABLE-002  its spec (cores, earth, conductor size, insulation) is the same on both
  CABLE-003  it joins the same JB / enclosure and panel on both
  CABLE-004  a CBD cable to an enclosure whose terminations are supplied is drawn on them
  CABLE-005  one cable number is not given to two different cables on the cable block diagrams

Placeholder numbers (CXXXXX) are PLACEHOLDER-002's business and are skipped here.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from difflib import SequenceMatcher

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import DocType, EntityType, Predicate, Severity
from drawing_qa.model.findings import Finding
from drawing_qa.rules.base import Category, Requirement, Rule, RuleMeta
from drawing_qa.rules.context import RuleContext
from drawing_qa.rules.findings import FindingBuilder
from drawing_qa.rules.registry import rule

_NEEDS = frozenset({Requirement.CLAIMS, Requirement.GRAPH, Requirement.TERMINATIONS})
_CORES = re.compile(r"(?P<n>\d+)\s*(?P<unit>C|T?PR)\b")
_SIZE = re.compile(r"(?P<size>\d+(?:\.\d+)?)\s*MM")
_SPEC_WORDS = re.compile(r"[A-Z][A-Z/]*")


def is_placeholder(number: str) -> bool:
    return "X" in number.upper()


def spec_parts(text: str | None) -> dict | None:
    """'12C+E 1.5mm² PVC' -> {cores: 12, unit: C, earth: True, size: 1.5, words: {PVC}}."""
    if not text:
        return None
    t = text.upper().replace("²", "2").replace("�", "2")
    cores, size = _CORES.search(t), _SIZE.search(t)
    rest = _SIZE.sub(" ", _CORES.sub(" ", t)).replace("+E", " ")
    words = {w for w in _SPEC_WORDS.findall(rest) if w not in {"MM", "MM2"}}
    return {
        "cores": int(cores["n"]) if cores else None,
        "unit": cores["unit"] if cores else None,
        "earth": "+E" in t.replace(" ", ""),
        "size": float(size["size"]) if size else None,
        "words": words,
    }


def spec_difference(a: str | None, b: str | None) -> tuple[str, Severity] | None:
    """What differs between two cable specs, and how much it matters. None when they agree (or one is missing)."""
    pa, pb = spec_parts(a), spec_parts(b)
    if pa is None or pb is None:
        return None
    major = []
    if (pa["cores"], pa["unit"]) != (pb["cores"], pb["unit"]) and pa["cores"] is not None and pb["cores"] is not None:
        major.append("core count")
    if pa["earth"] != pb["earth"] and pa["unit"] == pb["unit"] == "C":
        major.append("earth conductor")
    if pa["size"] is not None and pb["size"] is not None and pa["size"] != pb["size"]:
        major.append("conductor size")
    if major:
        return " and ".join(major), Severity.MAJOR
    if pa["words"] and pb["words"] and pa["words"] != pb["words"]:
        return "insulation / screen", Severity.MINOR
    return None


def td_cables(ctx: RuleContext) -> dict[str, list[Claim]]:
    """Cable number -> its label stacks on the termination sheets (real numbers only)."""
    out: dict[str, list[Claim]] = defaultdict(list)
    for c in ctx.claims_of(Predicate.HAS_SPEC, EntityType.CABLE):
        if c.parser is DocType.TERM and not is_placeholder(c.subject_key):
            out[c.subject_key].append(c)
    return out


def cbd_routes(ctx: RuleContext) -> dict[str, list[Claim]]:
    """Cable number -> the traced cables carrying it on the cable block diagrams."""
    out: dict[str, list[Claim]] = defaultdict(list)
    for c in ctx.claims_of(Predicate.ROUTED):
        number = (c.value.get("cable_no") or "").upper()
        if c.parser is DocType.CBD and number and not is_placeholder(number) and c.object_key:
            out[number].append(c)
    return out


def cbd_numbers(ctx: RuleContext) -> dict[str, list[Claim]]:
    out: dict[str, list[Claim]] = defaultdict(list)
    for c in ctx.claims_of(Predicate.OCCURS, EntityType.CABLE):
        if c.parser is DocType.CBD:
            out[c.subject_key].append(c)
    return out


def resolved(ctx: RuleContext, key: str) -> str:
    """A superseded designation's replacement (LTL-AUT1 -> LTL-PLC), as the linker resolves it."""
    new = ctx.settings.identifiers.superseded.get(key)
    return new or key


def ends(ctx: RuleContext, route: Claim) -> frozenset[str]:
    return frozenset({resolved(ctx, route.subject_key), resolved(ctx, route.object_key or "")})


def on_cbd(ctx: RuleContext) -> set[str]:
    """Keys of everything a cable block diagram shows (enclosures, panels, devices)."""
    return {n.key for n in (ctx.model.nodes.values() if ctx.model else []) if "CBD" in n.parsers}


def has_cbd(ctx: RuleContext) -> bool:
    return any(d.doc_type is DocType.CBD for d in ctx.active_drawings)


def strip_enclosures(ctx: RuleContext) -> dict[str, set[str]]:
    """JB / enclosure key -> the termination sheets that draw its terminal strip."""
    out: dict[str, set[str]] = defaultdict(set)
    for c in ctx.claims_of(Predicate.OCCURS, EntityType.TERMINAL):
        if c.parser is DocType.TERM and c.value.get("jb"):
            out[c.value["jb"]].add(ctx.sheet_of(c).label)
    return out


def panel_multicores(ctx: RuleContext) -> dict[str, tuple[str, str, Claim]]:
    """Cable number -> (JB, panel, claim) for each JB's multicore to its panel (the strip's own cable)."""
    out = {}
    for c in ctx.claims_of(Predicate.HAS_SPEC, EntityType.JUNCTION_BOX):
        number = (c.value.get("cable_no") or "").upper()
        if c.parser is DocType.TERM and number and not is_placeholder(number) and c.value.get("to"):
            out.setdefault(number, (resolved(ctx, c.subject_key), resolved(ctx, c.value["to"]), c))
    return out


def unshown_cbd_cables(ctx: RuleContext) -> dict[str, list[Claim]]:
    """CBD cables (real numbers) to an enclosure whose terminal strip is on a supplied termination sheet,
    that no termination sheet draws."""
    drawn = set(td_cables(ctx))
    strips = strip_enclosures(ctx)
    out = {}
    for number, routes in cbd_routes(ctx).items():
        if number in drawn:
            continue
        if any(e in strips for r in routes for e in ends(ctx, r)):
            out[number] = routes
    return out


def closest(number: str, candidates: Iterable[str]) -> str | None:
    best = max(((SequenceMatcher(None, number, c).ratio(), c) for c in candidates), default=(0.0, None))
    return best[1] if best[0] >= 0.8 else None


@rule
class CableOnCbd(Rule):
    meta = RuleMeta(
        rule_id="CABLE-001",
        title="Cable On CBD",
        category=Category.CABLE,
        default_severity=Severity.MAJOR,
        description=(
            "A cable number drawn on a termination sheet is not on any supplied cable block diagram, although the "
            "JB / enclosure it serves is. Where the CBD shows a cable between the same JB and panel under another "
            "number, or a near-identical number, the finding names it (C92807 on the terminations, C93807 on the CBD)."
        ),
        requires=_NEEDS,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        if not has_cbd(ctx):
            return
        shown = on_cbd(ctx)
        numbers = cbd_numbers(ctx)
        routes = cbd_routes(ctx)
        multicores = panel_multicores(ctx)
        unshown = unshown_cbd_cables(ctx)
        for number, cs in sorted(td_cables(ctx).items()):
            if number in numbers or number in routes:
                continue
            places = {p for c in cs for p in c.value.get("places", [])}
            if not places & shown:
                continue  # the sheet's enclosures are on no supplied CBD: its CBD is not in the package
            hint = ""
            if number in multicores:
                jb, panel, _ = multicores[number]
                other = sorted(n for n, rs in unshown.items() if any(ends(ctx, r) == {jb, panel} for r in rs))
                if other:
                    hint = f"; the cable block diagram shows {', '.join(other)} between {jb} and {panel}"
            if not hint and (near := closest(number, numbers)):
                hint = f"; the cable block diagrams have {near}"
            sheets = ", ".join(sorted({ctx.sheet_of(c).label for c in cs}))
            spec = next((c.value.get("text") for c in cs if c.value.get("text")), None)
            yield make(
                subject_type="CABLE",
                subject_key=number,
                message=f"Cable {number}{f' ({spec})' if spec else ''} on {sheets} is not on the cable block diagrams{hint}",
                evidence=cs[:1],
                recommendation="Correct the cable number on the termination drawing or the cable block diagram",
            )


@rule
class CableSpecMatch(Rule):
    meta = RuleMeta(
        rule_id="CABLE-002",
        title="Cable Spec Match",
        category=Category.CABLE,
        default_severity=Severity.MAJOR,
        description=(
            "One cable number with different specs: on a termination sheet and the cable block diagram, or on two "
            "termination sheets. Core count, earth conductor or conductor size differing is MAJOR (10C+E 1.5mm2 on the "
            "terminations, 10C+E 2.5mm2 on the CBD); only the insulation / screen words differing is MINOR."
        ),
        requires=_NEEDS,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        routes = cbd_routes(ctx)
        for number, cs in sorted(td_cables(ctx).items()):
            specs = [c for c in cs if c.value.get("text")]
            if not specs:
                continue
            td = specs[0]
            for other in specs[1:]:  # the same cable on two termination sheets (a strip continued)
                diff = spec_difference(td.value["text"], other.value["text"])
                if diff:
                    yield make(
                        subject_type="CABLE",
                        subject_key=number,
                        message=(f"Cable {number} is {td.value['text']} on {make.where(td.source)} but {other.value['text']} "
                                 f"on {make.where(other.source)} ({diff[0]} differ)"),
                        evidence=[td],
                        evidence_b=[other],
                        severity=diff[1],
                        discriminator=f"term|{other.value['text']}",
                        recommendation=f"Give cable {number} one spec on all termination sheets",
                    )
                    break
            for route in routes.get(number, [])[:1]:
                diff = spec_difference(td.value["text"], route.value.get("spec"))
                if diff:
                    yield make(
                        subject_type="CABLE",
                        subject_key=number,
                        message=(f"Cable {number} is {td.value['text']} on the termination drawing ({make.where(td.source)}) but "
                                 f"{route.value.get('spec')} on the cable block diagram ({make.where(route.source)}): {diff[0]} differ"),
                        evidence=[td],
                        evidence_b=[route],
                        severity=diff[1],
                        discriminator="cbd",
                        recommendation=f"Correct cable {number}'s spec on the termination drawing or the cable block diagram",
                    )


@rule
class CableEndpointMatch(Rule):
    meta = RuleMeta(
        rule_id="CABLE-003",
        title="Cable Endpoint Match",
        category=Category.CABLE,
        default_severity=Severity.MAJOR,
        description=(
            "A cable number joins different things on the termination drawing and the cable block diagram: a JB's "
            "multicore to its panel goes between another JB and panel on the CBD (C93899 LTL-JB3 to LTL-PLC on the "
            "terminations, SH-JB3 to LTL-PLC on the CBD), or a field cable's CBD ends are none of the JBs, enclosures, "
            "panels or devices its termination sheet shows."
        ),
        requires=_NEEDS,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        routes = cbd_routes(ctx)
        multicores = panel_multicores(ctx)
        devices_on: dict[str, set[str]] = defaultdict(set)
        for c in ctx.claims_of(Predicate.OCCURS, EntityType.DEVICE):
            devices_on[c.source.drawing_id].add(c.subject_key)
        for number, cs in sorted(td_cables(ctx).items()):
            rs = routes.get(number, [])
            if not rs:
                continue
            cbd_ends = sorted({e for r in rs for e in ends(ctx, r)})
            if number in multicores:
                jb, panel, claim = multicores[number]
                if not any(ends(ctx, r) == {jb, panel} for r in rs):
                    yield make(
                        subject_type="CABLE",
                        subject_key=number,
                        message=(f"Cable {number} goes from {jb} to {panel} on the termination drawing ({make.where(claim.source)}), "
                                 f"but between {' and '.join(cbd_ends)} on the cable block diagram ({make.where(rs[0].source)})"),
                        evidence=[claim],
                        evidence_b=rs[:1],
                        discriminator="multicore",
                        recommendation=f"Correct the JB / panel of cable {number} on one of the drawings",
                    )
                continue
            known = {p for c in cs for p in c.value.get("places", [])} | {d for c in cs for d in devices_on[c.source.drawing_id]}
            if not set(cbd_ends) & known:
                yield make(
                    subject_type="CABLE",
                    subject_key=number,
                    message=(f"Cable {number} runs between {' and '.join(cbd_ends)} on the cable block diagram ({make.where(rs[0].source)}), "
                             f"but its termination sheet {make.where(cs[0].source)} shows neither"),
                    evidence=cs[:1],
                    evidence_b=rs[:1],
                    discriminator="field",
                    recommendation=f"Check cable {number}'s number on the termination drawing and the cable block diagram",
                )


@rule
class CableNotTerminated(Rule):
    meta = RuleMeta(
        rule_id="CABLE-004",
        title="Cable Not On Terminations",
        category=Category.CABLE,
        default_severity=Severity.MINOR,
        description=(
            "A cable on the cable block diagram (a real number, not CXXXXX) goes to a JB / enclosure whose terminal "
            "strip is on a supplied termination sheet, but no termination sheet draws that cable number. A number "
            "CABLE-001 already names as the CBD's version of a mistyped termination cable is reported there instead."
        ),
        requires=_NEEDS,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        strips = strip_enclosures(ctx)
        multicores = panel_multicores(ctx)
        drawn = set(td_cables(ctx))
        named = set()  # CBD numbers CABLE-001 puts against a mistyped termination number
        unshown = unshown_cbd_cables(ctx)
        for number, (jb, panel, _) in multicores.items():
            if number not in cbd_numbers(ctx) and number not in cbd_routes(ctx):
                named |= {n for n, rs in unshown.items() if any(ends(ctx, r) == {jb, panel} for r in rs)}
        for number, rs in sorted(unshown.items()):
            if number in named or number in drawn:
                continue
            r = rs[0]
            enclosures = sorted(e for e in ends(ctx, r) if e in strips)
            sheets = ", ".join(sorted({s for e in enclosures for s in strips[e]}))
            yield make(
                subject_type="CABLE",
                subject_key=number,
                message=(f"Cable {number} ({r.value.get('spec') or 'no spec'}) between {' and '.join(sorted(ends(ctx, r)))} on "
                         f"{make.where(r.source)} is not drawn on the termination sheets of {', '.join(enclosures)} ({sheets})"),
                evidence=[r],
                recommendation=f"Show cable {number} on the termination drawing, or remove it from the cable block diagram",
            )


@rule
class DuplicateCableNumber(Rule):
    meta = RuleMeta(
        rule_id="CABLE-005",
        title="Duplicate Cable Number",
        category=Category.CABLE,
        default_severity=Severity.MAJOR,
        description="One cable number given to two cables with different ends on the cable block diagrams.",
        requires=frozenset({Requirement.CLAIMS}),
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for number, rs in sorted(cbd_routes(ctx).items()):
            pairs = {ends(ctx, r): r for r in rs}
            if len(pairs) < 2:
                continue
            listed = "; ".join(f"{' - '.join(sorted(p))} ({make.where(r.source)})" for p, r in sorted(pairs.items(), key=lambda kv: sorted(kv[0])))
            first, *others = list(pairs.values())
            yield make(
                subject_type="CABLE",
                subject_key=number,
                message=f"Cable number {number} is given to {len(pairs)} different cables: {listed}",
                evidence=[first],
                evidence_b=others,
                recommendation="Give each cable its own number",
            )
