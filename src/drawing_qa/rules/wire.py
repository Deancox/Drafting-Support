"""Wire Validation (WIRE). Wire numbers are <FFI><Sheet><Grid>, e.g. TN36D6; schematics write
them short ("D6" on sheet 036) and the PLC parser expands them."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import DocType, EntityType, Predicate, Severity
from drawing_qa.model.findings import Finding
from drawing_qa.rules.base import Category, Requirement, Rule, RuleMeta
from drawing_qa.rules.common import col_offset, row_offset
from drawing_qa.rules.context import RuleContext
from drawing_qa.rules.findings import FindingBuilder
from drawing_qa.rules.registry import rule
from drawing_qa.rules.tag import home_sheets


def wire_claims(ctx: RuleContext) -> dict[str, list[Claim]]:
    out: dict[str, list[Claim]] = defaultdict(list)
    for c in ctx.claims_of(Predicate.OCCURS, EntityType.WIRE):
        if c.value.get("format_valid") is not False:  # malformed numbers are WIRE-001's
            out[c.subject_key].append(c)
    return out


@rule
class WireNumberFormat(Rule):
    meta = RuleMeta(
        rule_id="WIRE-001",
        title="Wire Number Format",
        category=Category.WIRE,
        default_severity=Severity.MAJOR,
        description=(
            "Text that starts like a wire number of this package (a known FFI and a digit) but does not fit "
            "<FFI><Sheet><Grid>, e.g. TN36D (grid digit missing) or TN3D6 (one-digit sheet)."
        ),
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for c in ctx.claims_of(Predicate.OCCURS, EntityType.WIRE):
            if c.value.get("format_valid") is not False:
                continue
            yield make(
                subject_type="WIRE",
                subject_key=c.subject_key,
                message=f"Wire number '{c.source.raw_text}' at {make.where(c.source)} does not fit <FFI><Sheet><Grid> (e.g. TN36D6)",
                evidence=[c],
                recommendation="Correct the wire number",
            )


@rule
class DuplicateWireNumber(Rule):
    meta = RuleMeta(
        rule_id="WIRE-003",
        title="Duplicate Wire Number",
        category=Category.WIRE,
        default_severity=Severity.MAJOR,
        description=(
            "One wire number used for two different signals: in the channel rows of two PLC channels on the "
            "schematics, or on terminals of two different PLC modules on the slot termination sheets. Commons and "
            "supplies shared by one module's terminals, and wires looped from device to device in a JB, are not reported."
        ),
        requires=frozenset({Requirement.CLAIMS, Requirement.GRAPH}),
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        channels: dict[str, set[str]] = defaultdict(set)
        for e in ctx.edges_of("CHANNEL_WIRE"):
            channels[e.dst].add(e.src)
        for wire_id, chs in sorted(channels.items()):
            if len(chs) < 2:
                continue
            wire = ctx.model.nodes[wire_id].key
            keys = sorted(ctx.model.nodes[c].key for c in chs)
            claims = [c for c in ctx.claims_of(Predicate.WIRED_TO) if c.object_key == wire and c.subject_key in keys]
            yield make(
                subject_type="WIRE",
                subject_key=wire,
                message=f"Wire {wire} is in the rows of {len(keys)} PLC channels: {', '.join(keys)}",
                evidence=claims,
                discriminator="channels|" + ",".join(keys),
                recommendation=f"Give each channel's wire its own number (only one of {', '.join(keys)} should be {wire})",
            )
        modules: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for e in ctx.edges_of("WIRE_LANDS_ON"):
            if e.dst.startswith("PLC_MODULE:"):
                modules[e.src][e.dst].append(e)
        for wire_id, by_module in sorted(modules.items()):
            if len(by_module) < 2:
                continue
            wire = ctx.model.nodes[wire_id].key
            where = "; ".join(f"{ctx.model.nodes[m].key} terminal {', '.join(sorted(str(e.attrs.get('terminal')) for e in es))}"
                              for m, es in sorted(by_module.items()))
            yield make(
                subject_type="WIRE",
                subject_key=wire,
                message=f"Wire {wire} lands on {len(by_module)} PLC modules on the slot termination sheets: {where}",
                evidence=[c for es in by_module.values() for c in ctx.evidence(es[0].claim_ids)],
                discriminator="modules|" + ",".join(sorted(by_module)),
                recommendation="Check the wire numbers on the slot termination sheets",
            )


@rule
class WireHomeSheet(Rule):
    meta = RuleMeta(
        rule_id="WIRE-002",
        title="Wire Home Sheet",
        category=Category.WIRE,
        default_severity=Severity.MAJOR,
        description=(
            "A wire number is used, its home sheet (the sheet it encodes) is in the package, but that sheet does not "
            "show it. Same check as TAG-003, for wires."
        ),
        requires=frozenset({Requirement.CLAIMS, Requirement.GRID}),
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        wires = wire_claims(ctx)
        for wire, claims in sorted(wires.items()):
            homes = home_sheets(ctx, claims[0].value["ffi"], int(claims[0].value["sheet"]))
            if not homes:
                continue
            home_ids = {d.id for d in homes}
            if any(c.source.drawing_id in home_ids for c in claims):
                continue
            if home_ids & ctx.decoded_ids:
                continue  # home sheet decoded from its strokes (--ocr): the wire may be a word that did not decode
            near = _near_wires(wire, claims[0], wires, home_ids)
            refs = sorted({ctx.sheet_of(c).label for c in claims})
            yield make(
                subject_type="WIRE",
                subject_key=wire,
                message=f"Wire {wire} is used on {', '.join(refs)} but is not on its home sheet {homes[0].label}"
                + (f"; sheet {homes[0].sheet} has {', '.join(near)}" if near else ""),
                evidence=claims,
                recommendation=f"Check the wire number; did you mean {near[0]}?" if near else "Correct the wire number or add it to its home sheet",
            )


@rule
class WireGrid(Rule):
    meta = RuleMeta(
        rule_id="WIRE-005",
        title="Wire Grid",
        category=Category.WIRE,
        default_severity=Severity.MINOR,
        description=(
            "On its home sheet, a wire number is not drawn near the grid cell it encodes. A wire is numbered where it "
            "starts, so 'C2' drawn at C6 on a wire that never reaches C2 is numbered from the wrong end (401104-176). "
            "The label may sit anywhere along its wire: one whose wire has an end at the cell it names is accepted "
            "('K2' labelled at C2 on a wire from the power supply at K2, 401103-015). A wire carried to another sheet "
            "('16D2') is checked at its origin. Same tolerance as TAG-004."
        ),
        requires=frozenset({Requirement.CLAIMS, Requirement.GRID}),
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        from drawing_qa.rules.tag import GridValidation

        close = GridValidation()._close
        for wire, claims in sorted(wire_claims(ctx).items()):
            grid = claims[0].value["grid"]
            homes = {d.id for d in home_sheets(ctx, claims[0].value["ffi"], int(claims[0].value["sheet"]))}
            on_home = [c for c in claims if c.source.drawing_id in homes and c.source.grid_cell]
            if not on_home or any(close(grid, c.source.grid_cell) or any(close(grid, e) for e in c.value.get("wire_ends", ()))
                                  for c in on_home):
                continue
            c = on_home[0]
            label = c.value.get("abbreviated") or wire
            ends = c.value.get("wire_ends")
            runs = f"; its wire runs between {', '.join(ends)}" if ends else ""
            yield make(
                subject_type="WIRE",
                subject_key=wire,
                message=f"Wire {wire} ('{label}') encodes grid {grid} but is drawn at {make.where(c.source)}{runs}",
                evidence=on_home,
                recommendation=f"Number the wire for where it starts ({c.source.grid_cell}?) or check its label",
            )


def _near_wires(wire: str, claim: Claim, wires: dict[str, list[Claim]], home_ids: set[str]) -> list[str]:
    v = claim.value
    found = []
    for other, cs in wires.items():
        if other == wire or not any(c.source.drawing_id in home_ids for c in cs):
            continue
        ov = cs[0].value
        if (ov["ffi"], ov["sheet"]) != (v["ffi"], v["sheet"]):
            continue
        dr, dc = row_offset(v["grid"], ov["grid"]), col_offset(v["grid"], ov["grid"])
        if dr is not None and dc is not None and abs(dr) <= 1 and abs(dc) <= 1:
            found.append((abs(dr) + abs(dc), other))
    return [w for _, w in sorted(found)]


# --- termination cross-checks (M2) ------------------------------------------------------------

@rule
class WireContinuity(Rule):
    meta = RuleMeta(
        rule_id="WIRE-004",
        title="Wire Continuity",
        category=Category.WIRE,
        default_severity=Severity.MAJOR,
        description=(
            "A wire number the schematic channel row shows for a device that its JB terminals on the termination "
            "drawing do not have."
        ),
        requires=frozenset({Requirement.CLAIMS, Requirement.GRAPH, Requirement.TRACES, Requirement.TERMINATIONS}),
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        from drawing_qa.rules.term import is_placeholder_wire

        if DocType.TERM in ctx.decoded_types:
            return  # a decoded termination sheet (--ocr): its wire numbers may not have decoded
        # Schematic channel row vs JB terminals.
        seen = set()
        for t in ctx.traces:
            hop = next((h for h in t.hops if h.hop == "JB_TERMINAL"), None)
            wires_hop = next((h for h in t.hops if h.hop == "WIRE"), None)
            if not t.device or hop is None or hop.status.value != "FOUND" or not wires_hop or not wires_hop.values:
                continue
            landed = {ctx.model.nodes[w].key for v in hop.values for w in ctx.linked(f"TERMINAL:{v}", "WIRE_LANDS_ON")}
            missing = [w for w in wires_hop.values if w not in landed and not is_placeholder_wire(w)]
            if not missing or (t.device, tuple(missing)) in seen:
                continue
            seen.add((t.device, tuple(missing)))
            claim = ctx.claim_by_id.get(t.channel_claim_id or "")
            yield make(
                subject_type="DEVICE",
                subject_key=t.device,
                message=(f"{t.device} ({t.channel}): the schematic shows {', '.join(missing)}, its JB terminals "
                         f"{', '.join(hop.values)} have {', '.join(sorted(landed)) or 'no wires'}"),
                evidence=[claim] if claim else [],
                discriminator="schematic|" + ",".join(missing),
                recommendation="Make the schematic wire numbers and the termination drawing agree",
            )
