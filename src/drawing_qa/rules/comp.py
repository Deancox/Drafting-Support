"""Completeness Validation (COMP): signal traces.

A trace's JB terminal hop is NOT_SOURCED until the termination parser exists; that is
never reported as a gap. Problems already reported by TAG-003 (a reference to a tag that
is not on its home sheet) are not repeated here.
"""

from __future__ import annotations

from collections.abc import Iterable

from drawing_qa.common.tags import near_tags
from drawing_qa.model.engineering import HopStatus, SignalTrace, TraceStatus
from drawing_qa.model.enums import DocType, Severity
from drawing_qa.model.findings import Finding
from drawing_qa.rules.base import Category, Requirement, Rule, RuleMeta
from drawing_qa.rules.context import RuleContext
from drawing_qa.rules.findings import FindingBuilder
from drawing_qa.rules.registry import rule
from drawing_qa.common.descriptions import without_prefix
from drawing_qa.traces.builder import description_heading

_NEEDS_TRACES = frozenset({Requirement.CLAIMS, Requirement.GRAPH, Requirement.TRACES})


def _claim(ctx: RuleContext, trace: SignalTrace):
    return next((c for c in ctx.claims if c.id == trace.channel_claim_id), None)


def _on_home_sheet(ctx: RuleContext, device: str) -> bool:
    node = ctx.model.nodes.get(f"DEVICE:{device}")
    if node is None or "ffi" not in node.attrs or "sheet" not in node.attrs:
        return False
    status, _, homes = ctx.home(node.attrs["ffi"], int(node.attrs["sheet"]))
    return status == "package" and bool(homes) and any(h.label in node.sheets for h in homes)


def _occurrences(ctx: RuleContext, device: str):
    """Where a device tag is written, on checked sheets."""
    from drawing_qa.model.enums import EntityType

    return ctx.occurrences_by_key.get((EntityType.DEVICE, device), [])


def _orphans(ctx: RuleContext) -> list[SignalTrace]:
    return [t for t in ctx.traces if t.status is TraceStatus.ORPHAN]


def _sheet_number(ctx: RuleContext, trace: SignalTrace) -> int | None:
    claim = _claim(ctx, trace)
    return ctx.sheet_of(claim).sheet_number if claim else None


@rule
class IncompleteTrace(Rule):
    meta = RuleMeta(
        rule_id="COMP-001",
        title="Incomplete Signal Trace",
        category=Category.COMP,
        default_severity=Severity.MINOR,
        description=(
            "A channel's trace is missing a hop the supplied drawings should show: the device tag (MAJOR), "
            "the field wire, or the JB / field location in the channel row."
        ),
        requires=_NEEDS_TRACES,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        orphans = _orphans(ctx)
        for t in ctx.traces:
            if t.status is not TraceStatus.PARTIAL or not t.gaps:
                continue
            claim = _claim(ctx, t)
            if claim and claim.source.drawing_id in ctx.decoded_ids:
                continue  # decoded from its strokes (--ocr): a gap may be a word that did not decode
            where = make.where(claim.source) if claim else t.channel
            heading = without_prefix(t.description, ctx.model.description_prefix)[:70]
            if "DEVICE" in t.gaps:
                sheet = _sheet_number(ctx, t)
                candidates = sorted(o.device for o in orphans if o.device and sheet is not None and _tag_sheet(o.device) == sheet)
                hint = f"; the cable block diagrams show {', '.join(candidates)} for sheet {sheet:03d}, which is not tagged there" if candidates else ""
                yield make(
                    subject_type="PLC_CHANNEL",
                    subject_key=t.channel,
                    message=f"{t.channel} ({heading}) at {where} has no device tag in its row{hint}",
                    evidence=[claim] if claim else [],
                    severity=Severity.MAJOR,
                    discriminator="DEVICE",
                    recommendation=f"Tag the device{' (' + candidates[0] + '?)' if candidates else ''}",
                )
            other = [g for g in t.gaps if g not in ("DEVICE", "JB_TERMINAL")]  # a missing JB terminal is DEV-003
            if other:
                what = " and ".join({"JB": "JB / field location", "WIRE": "field wire number"}.get(g, g) for g in other)
                yield make(
                    subject_type="PLC_CHANNEL",
                    subject_key=t.channel,
                    message=f"{t.channel} ({t.device or 'no device'}, {heading}) at {where}: no {what} shown in the channel row",
                    evidence=[claim] if claim else [],
                    discriminator=",".join(other),
                    recommendation=f"Show the {what} for {t.device or 'this channel'}",
                )


def _tag_sheet(tag: str) -> int | None:
    digits = ""
    rest = tag.split("-", 1)[-1][2:]  # after prefix- and the 2-letter FFI
    for ch in rest:
        if not ch.isdigit():
            break
        digits += ch
    return int(digits) if digits else None


@rule
class OrphanDevice(Rule):
    meta = RuleMeta(
        rule_id="COMP-002",
        title="Orphan Field Device",
        category=Category.COMP,
        default_severity=Severity.MAJOR,
        description=(
            "A device on a cable block diagram, drawn on its home sheet, that no PLC channel trace reaches. "
            "Devices not on their home sheet are reported by TAG-003 instead."
        ),
        requires=_NEEDS_TRACES,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for t in _orphans(ctx):
            if not t.device or not _on_home_sheet(ctx, t.device):
                continue
            if any(c.source.drawing_id in ctx.decoded_ids for c in _occurrences(ctx, t.device)):
                continue  # on a decoded sheet (--ocr): its channel row may not have decoded
            yield make(
                subject_type="DEVICE",
                subject_key=t.device,
                message=f"{t.device} is on {', '.join(t.sheets)} but is not wired to any PLC channel on the schematics",
                evidence=_occurrences(ctx, t.device),
                discriminator="orphan",
                recommendation="Show the device's PLC channel on its slot sheet, or confirm it is not a PLC signal",
            )


@rule
class NotOnCableBlockDiagram(Rule):
    meta = RuleMeta(
        rule_id="COMP-005",
        title="Field Device Not on CBD",
        category=Category.COMP,
        default_severity=Severity.MINOR,
        description=(
            "A field device in a channel trace whose JB / field location is on the supplied cable block diagrams, "
            "but the device itself is not. Skipped when the CBD shows a near-identical tag (TAG-003 reports that). "
            "Panel devices (K- relays, Q- breakers) are reported too, noting that either the CBD or the schematic's "
            "JB terminals are wrong (K-TN18F1..F8: PLC cubicle relays drawn between LTL-JB1 terminals on 401103-042)."
        ),
        requires=_NEEDS_TRACES,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        if DocType.CBD in ctx.decoded_types:
            return  # a decoded CBD (--ocr): the device's tag may not have decoded
        orphan_tags = [o.device for o in _orphans(ctx) if o.device]
        internal = {p.upper() for p in ctx.settings.identifiers.panel_device_prefixes}
        seen: set[str] = set()
        for t in ctx.traces:
            if t.status not in (TraceStatus.PARTIAL, TraceStatus.COMPLETE) or not t.device or t.on_cbd or t.device in seen:
                continue
            jbs = next((h.values for h in t.hops if h.hop == "JB"), [])
            on_cbd_jbs = [j for j in jbs if _node_on_cbd(ctx, j)]
            if not on_cbd_jbs or any(near_tags(t.device, o) for o in orphan_tags):
                continue
            seen.add(t.device)
            via = ", ".join(on_cbd_jbs)
            message = f"{t.device} ({t.channel} via {via}) is not on the cable block diagrams, which do show {via}"
            recommendation = f"Add {t.device} to the cable block diagram for {on_cbd_jbs[0]}"
            if t.device.split("-", 1)[0] in internal:  # relays, breakers: normally inside the panel
                message += "; it is a panel device, yet the schematic wires it out through a field JB"
                recommendation += f", or if it is in the panel, correct the {via} terminals the schematic draws it between"
            yield make(
                subject_type="DEVICE",
                subject_key=t.device,
                message=message,
                evidence=[c for c in [_claim(ctx, t)] if c] + _occurrences(ctx, t.device)[:1],
                discriminator="not-on-cbd",
                recommendation=recommendation,
            )


@rule
class ConflictedTrace(Rule):
    meta = RuleMeta(
        rule_id="COMP-003",
        title="Conflicted Trace",
        category=Category.COMP,
        default_severity=Severity.MAJOR,
        description=(
            "Two drawings disagree on a hop of a channel's trace and no more specific rule reports it: the cable "
            "block diagram cables the device to a different panel from the one its PLC slot is in, or the termination "
            "drawings land it in a different JB from the schematic. (A JB the schematic and the CBD disagree on is "
            "PATH-002; a PLC slot the terminations disagree on is PLC-001.)"
        ),
        requires=_NEEDS_TRACES,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        seen: set[tuple[str, str]] = set()
        for t in ctx.traces:
            for c in t.conflicts:
                if c.reported_by != self.meta.rule_id or (t.device or t.key, c.hop) in seen:
                    continue
                if c.other_source == "TERM" and c.hop == "JB" and set(c.other) <= set(t.cbd_via):
                    continue  # the CBD says the same: PATH-002 reports it, noting the terminations agree
                seen.add((t.device or t.key, c.hop))
                claim = _claim(ctx, t)
                what = c.hop.replace("_", " ").lower() if c.hop != "JB" else "JB"
                where = "the cable block diagrams cable it to" if c.other_source == "CBD" else "the termination drawings land it in"
                yield make(
                    subject_type="DEVICE" if t.device else "PLC_CHANNEL",
                    subject_key=t.device or t.key,
                    message=(f"{t.device} ({t.channel}): the schematics give {what} {', '.join(c.schematic)}, "
                             f"{where} {', '.join(c.other)}"),
                    evidence=[claim] if claim else [],
                    evidence_b=_occurrences(ctx, t.device)[:3] if t.device else [],
                    discriminator=f"{c.hop}|{','.join(c.schematic)}|{','.join(c.other)}",
                    recommendation=(f"Make the schematic and the {'cable block diagram' if c.other_source == 'CBD' else 'termination drawing'} "
                                    f"agree on the {what}"),
                )


def _field_devices(ctx: RuleContext, devices: list[str]) -> list[str]:
    """Devices that are field equipment: not panel devices (relays, breakers) and not from a separate package."""
    internal = {p.upper() for p in ctx.settings.identifiers.panel_device_prefixes}
    external = {f.upper() for f, d in ctx.settings.identifiers.ffi_drawings.items() if d.upper() == "EXTERNAL"}
    out = []
    for d in devices:
        prefix, _, rest = d.partition("-")
        if prefix.upper() in internal or rest[:2].upper() in external:
            continue
        out.append(d)
    return out


def _found_devices(t: SignalTrace) -> list[str]:
    """Devices drawn in the channel row itself (not inferred from a neighbouring channel)."""
    return next((h.values for h in t.hops if h.hop == "DEVICE" and h.status is HopStatus.FOUND), [])


@rule
class Branching(Rule):
    meta = RuleMeta(
        rule_id="COMP-004",
        title="Branching",
        category=Category.COMP,
        default_severity=Severity.MAJOR,
        description=(
            "One device on safety-module channels with different descriptions (a dual-channel device is expected on "
            "two channels with the same description; standard modules are PLC-003), or two field devices in one "
            "channel row (MINOR: confirm one is an interposing device such as an isolator)."
        ),
        requires=_NEEDS_TRACES,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        channel_traces = [t for t in ctx.traces if t.channel and t.status in (TraceStatus.PARTIAL, TraceStatus.COMPLETE)]
        by_device: dict[str, list[SignalTrace]] = {}
        for t in channel_traces:
            for d in _found_devices(t):
                by_device.setdefault(d, []).append(t)
        for device, ts in sorted(by_device.items()):
            headings = {description_heading(t.description, ctx.model.description_prefix) for t in ts}
            if len(ts) < 2 or len(headings) < 2 or not all(_safety_module(ctx, t) for t in ts):
                continue  # one channel, a dual-channel pair, or a standard module (PLC-003)
            home = _tag_sheet(device)
            odd = [t for t in ts if home is not None and _sheet_number(ctx, t) != home] or ts[1:]
            listed = "; ".join(f"{t.channel} ({description_heading(t.description, ctx.model.description_prefix).title()}, {', '.join(t.sheets)})" for t in ts)
            hints = []
            for t in odd:
                twin = _same_heading_neighbour(channel_traces, t, ctx.model.description_prefix)
                hints.append(f"{t.channel}: its neighbour {twin.channel} with the same description has {twin.device}"
                             if twin else f"{t.channel}: tag the device drawn there")
            yield make(
                subject_type="DEVICE",
                subject_key=device,
                message=f"{device} is on channels with different descriptions: {listed}",
                evidence=[c for c in (_claim(ctx, t) for t in ts) if c],
                discriminator="device-on-channels|" + ",".join(sorted(t.channel for t in ts)),
                recommendation="Check " + "; ".join(hints) + " (a copied tag?)",
            )

        for t in channel_traces:
            field = _field_devices(ctx, _found_devices(t))
            if len(field) < 2:
                continue
            claim = _claim(ctx, t)
            yield make(
                subject_type="PLC_CHANNEL",
                subject_key=t.channel,
                message=(f"{t.channel} ({description_heading(t.description, ctx.model.description_prefix).title()}, {', '.join(t.sheets)}) has "
                         f"{len(field)} field devices in its row: {', '.join(field)}"),
                evidence=[claim] if claim else [],
                severity=Severity.MINOR,
                discriminator="devices-in-row|" + ",".join(field),
                recommendation="Confirm one is an interposing device (isolator, barrier, amplifier), or give each device its own channel",
            )


def _safety_module(ctx: RuleContext, t: SignalTrace) -> bool:
    slot = next((h.values[0] for h in t.hops if h.hop == "SLOT" and h.values), None)
    node = ctx.model.nodes.get(f"PLC_MODULE:{slot}") if slot else None
    return bool(node and node.attrs.get("safety"))


def _same_heading_neighbour(traces: list[SignalTrace], t: SignalTrace, prefix: str) -> SignalTrace | None:
    """The nearest other channel on the same module with the same description heading and a different device."""
    module, _, number = t.channel.rpartition("/CH")
    heading = description_heading(t.description, prefix)
    candidates = [
        o for o in traces
        if o is not t and o.channel.rpartition("/CH")[0] == module and o.device and o.device != t.device
        and description_heading(o.description, prefix) == heading
    ]
    return min(candidates, key=lambda o: abs(int(o.channel.rpartition("/CH")[2]) - int(number)), default=None)


def _node_on_cbd(ctx: RuleContext, key: str) -> bool:
    for kind in ("JUNCTION_BOX", "LOCATION"):
        node = ctx.model.nodes.get(f"{kind}:{key}")
        if node is not None and "CBD" in node.parsers:
            return True
    return False
