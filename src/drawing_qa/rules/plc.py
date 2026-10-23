"""PLC Validation (PLC): rack layout, slot sheets and channel rows from the schematics.

PLC-001 and PLC-009 compare with the termination drawings (M2); PLC-002 (description match)
waits for AI-assisted matching (M6).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import DocType, Predicate, Severity
from drawing_qa.model.findings import Finding
from drawing_qa.rules.base import Category, Requirement, Rule, RuleMeta
from drawing_qa.rules.context import RuleContext
from drawing_qa.rules.findings import FindingBuilder
from drawing_qa.rules.registry import rule


def modules(ctx: RuleContext, source: str) -> dict[str, list[Claim]]:
    """Module claims from 'slot_sheet' or 'rack_layout', by module key (LTL/S13)."""
    out: dict[str, list[Claim]] = defaultdict(list)
    for c in ctx.claims_of(Predicate.INSTALLED_IN):
        if c.value.get("source") == source:
            out[c.subject_key].append(c)
    return out


def channels(ctx: RuleContext) -> list[Claim]:
    return ctx.claims_of(Predicate.HAS_CHANNEL)


@rule
class SafetyConsistency(Rule):
    meta = RuleMeta(
        rule_id="PLC-004",
        title="Safety Module Consistency",
        category=Category.PLC,
        default_severity=Severity.MAJOR,
        description="A safety module (part number ending S, e.g. 1734-IB8S) on a sheet not titled SAFETY, or the reverse.",
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for key, claims in sorted(modules(ctx, "slot_sheet").items()):
            c = claims[0]
            safety, title = c.value.get("safety"), (c.value.get("title") or "").upper()
            if safety is None or not title:
                continue
            if bool(safety) != ("SAFETY" in title):
                kind = "safety" if safety else "standard"
                yield make(
                    subject_type="PLC_MODULE",
                    subject_key=key,
                    message=f"{key} is a {kind} module ({c.value['part']}) but {ctx.sheet_of(c).label} is titled '{c.value['title']}'",
                    evidence=[c],
                    recommendation="Check the module part number and the sheet title",
                )


@rule
class DuplicateChannelAllocation(Rule):
    meta = RuleMeta(
        rule_id="PLC-003",
        title="Duplicate Channel Allocation",
        category=Category.PLC,
        default_severity=Severity.MAJOR,
        description=(
            "A channel drawn on two sheets, or one device wired to channels on standard (non-safety) modules more than "
            "once. Dual-channel safety devices on safety modules are expected and not reported."
        ),
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        by_key: dict[str, list[Claim]] = defaultdict(list)
        for c in channels(ctx):
            by_key[c.subject_key].append(c)
        for key, cs in sorted(by_key.items()):
            sheets = sorted({ctx.sheet_of(c).label for c in cs})
            if len(sheets) > 1:
                yield make(
                    subject_type="PLC_CHANNEL",
                    subject_key=key,
                    message=f"Channel {key} is drawn on {', '.join(sheets)}",
                    evidence=cs,
                    recommendation="Keep the channel on one sheet",
                )

        safety = {k: bool(cs[0].value.get("safety")) for k, cs in modules(ctx, "slot_sheet").items()}
        by_device: dict[str, list[Claim]] = defaultdict(list)
        for c in channels(ctx):
            for device in c.value.get("devices", []):
                by_device[device].append(c)
        for device, cs in sorted(by_device.items()):
            keys = sorted({c.subject_key for c in cs})
            if len(keys) < 2 or all(safety.get(c.object_key) for c in cs):
                continue
            yield make(
                subject_type="DEVICE",
                subject_key=device,
                message=f"{device} is wired to {len(keys)} channels: {', '.join(keys)}",
                evidence=cs,
                recommendation="Check the channel allocation; a standard input device should use one channel",
            )


@rule
class ModuleCapacity(Rule):
    meta = RuleMeta(
        rule_id="PLC-005",
        title="Module Capacity",
        category=Category.PLC,
        default_severity=Severity.MAJOR,
        description="POINTS range or channel rows exceed the module's channel count from the part number.",
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        found: dict[str, set[int]] = defaultdict(set)
        for c in channels(ctx):
            found[c.object_key].add(c.value["channel"])
        for key, claims in sorted(modules(ctx, "slot_sheet").items()):
            c = claims[0]
            capacity = c.value.get("channels")
            if not capacity:
                continue
            last = c.value.get("points_last")
            if last is not None and last >= capacity:
                yield make(
                    subject_type="PLC_MODULE",
                    subject_key=key,
                    message=f"{key} POINTS '{c.value['points']}' runs to {last:02d} but {c.value['part']} has {capacity} channels",
                    evidence=[c],
                    recommendation="Correct the POINTS range or the module",
                )
            over = sorted(n for n in found.get(key, ()) if n >= capacity)
            if over:
                yield make(
                    subject_type="PLC_MODULE",
                    subject_key=key,
                    message=f"{key} has channel rows {', '.join(f'{n:02d}' for n in over)} beyond the {capacity} channels of {c.value['part']}",
                    evidence=[c],
                    discriminator="rows",
                )


@rule
class ModuleTypeMatch(Rule):
    meta = RuleMeta(
        rule_id="PLC-006",
        title="Module Type Match",
        category=Category.PLC,
        default_severity=Severity.MAJOR,
        description="The rack layout and the slot sheet disagree on the module, or the slot sheet's POINTS type disagrees with its part number.",
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        layout = modules(ctx, "rack_layout")
        for key, claims in sorted(modules(ctx, "slot_sheet").items()):
            c = claims[0]
            part, points_type, module_type = c.value.get("part"), c.value.get("points_type"), c.value.get("module_type")
            if points_type and module_type and points_type != module_type:
                yield make(
                    subject_type="PLC_MODULE",
                    subject_key=key,
                    message=f"{key} on {ctx.sheet_of(c).label}: POINTS says {points_type} but {part} is a {module_type} module",
                    evidence=[c],
                    discriminator="points",
                )
            for lc in layout.get(key, []):
                if part and lc.value.get("part") and lc.value["part"] != part:
                    yield make(
                        subject_type="PLC_MODULE",
                        subject_key=key,
                        message=f"{key}: rack layout {ctx.sheet_of(lc).label} shows {lc.value['part']}, slot sheet {ctx.sheet_of(c).label} shows {part}",
                        evidence=[lc],
                        evidence_b=[c],
                        discriminator="layout",
                        recommendation="Make the rack layout and the slot sheet agree",
                    )
        listed = {(c.subject_key, int(c.value["sheet"])) for c in ctx.claims_of(Predicate.SLOT_SHEET)}
        decoded_plc = {d.sheet_number for d in ctx.drawings if d.id in ctx.decoded_ids and d.doc_type is DocType.PLC}
        for key, lcs in sorted(layout.items()):
            if any(k == key and n in decoded_plc for k, n in listed):
                continue  # its slot sheet was decoded (--ocr): the slot header may not have decoded
            if key not in modules(ctx, "slot_sheet") and ctx.sheet_numbers:
                yield make(
                    subject_type="PLC_MODULE",
                    subject_key=key,
                    message=f"{key} ({lcs[0].value['part']}) is on the rack layout but no slot sheet in the package describes it",
                    evidence=lcs[:1],
                    severity=Severity.MINOR,
                    discriminator="no-sheet",
                    recommendation="Add the slot's schematic sheet to the package",
                )


@rule
class SlotSheetConsistency(Rule):
    meta = RuleMeta(
        rule_id="PLC-007",
        title="Slot / Sheet Consistency",
        category=Category.PLC,
        default_severity=Severity.MAJOR,
        description=(
            "The slot number on a slot sheet (title, RACK line, POINTS) must agree, and the rack layout's sheet "
            "reference for each slot must be that sheet, once."
        ),
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        slot_sheets = modules(ctx, "slot_sheet")
        for key, claims in sorted(slot_sheets.items()):
            c = claims[0]
            v = c.value
            stated = {"title": v.get("title_slot"), "RACK line": v.get("rack_line_slot"), "POINTS": v.get("points_slot")}
            values = {s for s in stated.values() if s is not None}
            if len(values) > 1:
                detail = ", ".join(f"{k} {s}" for k, s in stated.items() if s is not None)
                yield make(
                    subject_type="PLC_MODULE",
                    subject_key=key,
                    message=f"{ctx.sheet_of(c).label} gives different slot numbers: {detail}",
                    evidence=[c],
                    discriminator="header",
                )

        refs: dict[str, list[Claim]] = defaultdict(list)
        for c in ctx.claims_of(Predicate.SLOT_SHEET):
            refs[c.subject_key].append(c)
        for key, rs in sorted(refs.items()):
            listed = sorted({r.value["sheet"] for r in rs})
            actual = sorted({ctx.sheet_of(c).sheet_number for c in slot_sheets.get(key, [])})
            if len(listed) > 1:
                yield make(
                    subject_type="PLC_MODULE",
                    subject_key=key,
                    message=f"Rack layout lists {len(listed)} sheets for {key}: {', '.join(f'{s:03d}' for s in listed)}"
                    + (f" (the slot is on sheet {', '.join(f'{s:03d}' for s in actual)})" if actual else ""),
                    evidence=rs,
                    discriminator="multiple",
                    recommendation="Remove the extra sheet reference from the rack layout",
                )
            elif actual and listed[0] not in actual:
                yield make(
                    subject_type="PLC_MODULE",
                    subject_key=key,
                    message=f"Rack layout gives sheet {listed[0]:03d} for {key}, but the slot is drawn on sheet {actual[0]:03d}",
                    evidence=rs,
                    evidence_b=slot_sheets[key],
                    discriminator="wrong",
                    recommendation=f"Change the rack layout reference to {actual[0]:03d}",
                )


@rule
class PointsNotation(Rule):
    meta = RuleMeta(
        rule_id="PLC-008",
        title="POINTS Notation",
        category=Category.PLC,
        default_severity=Severity.MINOR,
        description="The slot sheet POINTS line does not follow <rack>.S<slot>.<type>.<first>..<last> (e.g. LTL.S13.DI.00..07).",
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for key, claims in sorted(modules(ctx, "slot_sheet").items()):
            c = claims[0]
            v = c.value
            if v.get("points") is None or v.get("points_valid"):
                continue
            expected = None
            if v.get("points_type") is not None:
                expected = f"{v['rack']}.S{v['points_slot']}.{v['points_type']}.{v['points_first']:02d}..{v['points_last']:02d}"
            yield make(
                subject_type="PLC_MODULE",
                subject_key=key,
                message=f"{ctx.sheet_of(c).label} POINTS '{v['points']}' does not follow the notation" + (f"; expected {expected}" if expected else ""),
                evidence=[c],
                recommendation=f"Write as {expected}" if expected else "Use <rack>.S<slot>.<type>.<first>..<last>",
            )


# --- termination cross-checks (M2) ------------------------------------------------------------

_NEEDS_TERM_GRAPH = frozenset({Requirement.CLAIMS, Requirement.GRAPH, Requirement.TERMINATIONS})


@rule
class ChannelMatch(Rule):
    meta = RuleMeta(
        rule_id="PLC-001",
        title="Channel Match",
        category=Category.PLC,
        default_severity=Severity.MAJOR,
        description=(
            "The schematics and the termination drawings put a device, or a wire, on different PLC slots: the device's "
            "JB terminals go to another slot than its schematic channel, or a slot termination sheet lands a wire on a "
            "module whose schematic channels do not have it."
        ),
        requires=frozenset(_NEEDS_TERM_GRAPH | {Requirement.TRACES}),
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        seen = set()
        for t in ctx.traces:
            for c in t.conflicts:
                if c.reported_by != self.meta.rule_id or (t.device, t.channel) in seen:
                    continue
                seen.add((t.device, t.channel))
                claim = ctx.claim_by_id.get(t.channel_claim_id or "")
                terminals = next((h.values for h in t.hops if h.hop == "JB_TERMINAL"), [])
                yield make(
                    subject_type="DEVICE",
                    subject_key=t.device or t.key,
                    message=(f"{t.device} is on {t.channel} on the schematics, but its JB terminals {', '.join(terminals)} "
                             f"go to {', '.join(c.other)} on the termination drawings"),
                    evidence=[claim] if claim else [],
                    discriminator=f"device|{t.channel}|{','.join(c.other)}",
                    recommendation="Make the schematic channel and the termination drawing's destination slot agree",
                )

        # Slot termination sheets: each wire on a module terminal should be on that module's schematic channels.
        schematic_modules: dict[str, set[str]] = defaultdict(set)
        for e in ctx.edges_of("CHANNEL_WIRE"):
            for m in ctx.linked(e.src, "CHANNEL_MODULE"):
                schematic_modules[e.dst].add(m)
        for e in sorted(ctx.edges_of("WIRE_LANDS_ON"), key=lambda e: (e.dst, e.src)):
            if not e.dst.startswith("PLC_MODULE:"):
                continue
            mods = schematic_modules.get(e.src, set())
            if len(mods) != 1 or e.dst in mods:
                continue  # supply wires on many channels, or not on the schematics at all
            wire, module, other = ctx.model.nodes[e.src].key, ctx.model.nodes[e.dst].key, ctx.model.nodes[next(iter(mods))].key
            yield make(
                subject_type="WIRE",
                subject_key=wire,
                message=(f"{wire} lands on {module} terminal {e.attrs.get('terminal')} on the slot termination sheet, "
                         f"but the schematics have it on {other}"),
                evidence=ctx.evidence(e.claim_ids),
                discriminator=f"slot-sheet|{module}|{other}",
                recommendation=f"Make the slot termination sheet and the schematic agree on {wire}'s module",
            )


@rule
class PanelSlotConsistency(Rule):
    meta = RuleMeta(
        rule_id="PLC-009",
        title="Panel / Slot Consistency",
        category=Category.PLC,
        default_severity=Severity.MAJOR,
        description=(
            "A JB terminal's destination on the termination drawing ('LTL-PLC ... DI SLOT 12') names a slot that "
            "panel does not have, or a module of a different I/O type."
        ),
        requires=_NEEDS_TERM_GRAPH,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        groups: dict[tuple, list] = defaultdict(list)
        for e in ctx.edges_of("DESTINED_FOR"):
            if not e.src.startswith("TERMINAL:") or not e.dst.startswith("PLC_MODULE:"):
                continue
            panel = next(iter(ctx.linked(e.src, "TERMINAL_PANEL")), None)
            jb = next(iter(ctx.linked(e.src, "TERMINAL_IN")), None)
            groups[(jb, panel, e.dst, e.attrs.get("io"))].append(e)
        for (jb, panel, module_id, io), edges in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
            module = ctx.model.nodes[module_id]
            jb_key = ctx.model.nodes[jb].key if jb else "?"
            panel_key = ctx.model.nodes[panel].key if panel else "?"
            in_panels = [ctx.model.nodes[p].key for p in ctx.linked(module_id, "MODULE_PANEL")]
            problem = None
            if not in_panels and not module.attrs.get("part"):
                problem = f"there is no {module.key} on the schematics"
            elif panel and in_panels and panel_key not in in_panels:
                problem = f"{module.key} is in {', '.join(in_panels)}"
            elif io and module.attrs.get("module_type") and module.attrs["module_type"] not in io.upper():
                problem = f"{module.key} is a {module.attrs['module_type']} module ({module.attrs.get('part', '?')})"
            if problem is None:
                continue
            terminals = sorted(ctx.model.nodes[e.src].key for e in edges)
            yield make(
                subject_type="PLC_MODULE",
                subject_key=module.key,
                message=f"{jb_key} terminals {', '.join(terminals)} go to {panel_key} {io or ''} slot {module.attrs.get('slot', module.key)}, but {problem}",
                evidence=ctx.evidence(edges[0].claim_ids),
                discriminator=f"{jb_key}|{panel_key}|{io}",
                recommendation="Correct the destination panel / slot on the termination drawing",
            )
