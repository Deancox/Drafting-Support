"""Device Validation (DEV)."""

from __future__ import annotations

from collections.abc import Iterable

from drawing_qa.model.enums import DocType, Predicate, Severity
from drawing_qa.model.findings import Finding
from drawing_qa.rules.base import Category, Requirement, Rule, RuleMeta
from drawing_qa.rules.context import RuleContext
from drawing_qa.rules.findings import FindingBuilder
from drawing_qa.rules.registry import rule


@rule
class DeviceTerminated(Rule):
    meta = RuleMeta(
        rule_id="DEV-003",
        title="Device Terminated",
        category=Category.DEV,
        default_severity=Severity.MINOR,
        description=(
            "A field device on a PLC channel whose JB's termination drawing is supplied (the JB in its schematic "
            "channel row, or the enclosure the cable block diagram draws it in), but no terminal on it serves the device."
        ),
        requires=frozenset({Requirement.CLAIMS, Requirement.GRAPH, Requirement.TRACES, Requirement.TERMINATIONS}),
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        if DocType.TERM in ctx.decoded_types:
            return  # a decoded termination sheet (--ocr): the terminals may not have decoded
        internal = {p.upper() for p in ctx.settings.identifiers.panel_device_prefixes}
        seen = set()
        for t in ctx.traces:
            if not t.channel or not t.device or t.device in seen or t.device.split("-", 1)[0] in internal:
                continue  # channel traces only: an orphan's tag on the CBD is often a wrong tag (TAG-003)
            device_id = f"DEVICE:{t.device}"
            if ctx.linked(device_id, "TERMINAL_DEVICE"):
                continue
            jbs = [h for h in t.hops if h.hop == "JB"]
            candidates = list(jbs[0].values) if jbs else []
            candidates += [ctx.model.nodes[n].key for n in ctx.linked(device_id, "DEVICE_IN")]
            drawn = sorted({k for k in candidates if any(ctx.linked(f"{kind}:{k}", "TERMINAL_IN") for kind in ("JUNCTION_BOX", "LOCATION"))})
            if not drawn:
                continue
            seen.add(t.device)
            sheets = sorted({ctx.drawing[c.source.drawing_id].label for k in drawn for kind in ("JUNCTION_BOX", "LOCATION")
                             for term in ctx.linked(f"{kind}:{k}", "TERMINAL_IN") for c in ctx.evidence(ctx.model.nodes[term].claim_ids)})
            claim = ctx.claim_by_id.get(t.channel_claim_id or "")
            yield make(
                subject_type="DEVICE",
                subject_key=t.device,
                message=(f"{t.device} ({t.channel or 'on the CBD'}) goes through {', '.join(drawn)}, but no terminal on "
                         f"{', '.join(drawn)} ({', '.join(sheets)}) serves it"),
                evidence=[claim] if claim else [],
                discriminator=",".join(drawn),
                recommendation=f"Show {t.device}'s terminals on the termination drawing for {', '.join(drawn)}",
            )


def io_direction(io: str | None) -> str | None:
    """DI / SDI / AI -> IN; DO / SDO / AO -> OUT."""
    io = (io or "").upper()
    return "IN" if io.endswith("I") else "OUT" if io.endswith("O") else None


@rule
class IoDirection(Rule):
    meta = RuleMeta(
        rule_id="DEV-004",
        title="I/O Direction",
        category=Category.DEV,
        default_severity=Severity.MINOR,
        description=(
            "A device whose prefix gives its signal a direction ([identifiers] io_direction: switches S are inputs, "
            "lamps H outputs) is wired to a module of the other direction: on the schematics (its channel's module "
            "type) or on the termination drawing (the destination 'DO SLOT 25' of its JB terminals). A field limit switch "
            "on a DO, or an indication lamp on a DI, is reported for the checker to confirm. A termination drawing slot that "
            "PLC-009 (module of another type) or PLC-001 (device on another slot) already reports is left to them. No output "
            "while io_direction is empty."
        ),
        requires=frozenset({Requirement.CLAIMS, Requirement.GRAPH}),
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        expected_by_prefix = {k.upper(): v.upper() for k, v in ctx.settings.identifiers.io_direction.items()}
        if not expected_by_prefix:
            return
        found: dict[str, list[tuple[str, str, list]]] = {}  # device -> [(where, io, claims)]
        for e in ctx.edges_of("CHANNEL_DEVICE"):
            device = ctx.model.nodes[e.dst].key
            for m in ctx.linked(e.src, "CHANNEL_MODULE"):
                module = ctx.model.nodes[m]
                io = module.attrs.get("module_type")
                if io:
                    io = f"S{io}" if module.attrs.get("safety") and not io.startswith("S") else io
                    found.setdefault(device, []).append((f"{module.key} on the schematics", io, ctx.evidence(e.claim_ids)))
        terminal_devices: dict[str, set[str]] = {}
        for c in ctx.claims_of(Predicate.REFERS_TO_DEVICE):
            if c.parser is DocType.TERM and c.object_key:
                terminal_devices.setdefault(c.subject_key, set()).add(c.object_key)
        # One finding per mistake: a device PLC-001 puts on another slot than its schematic channel, or a
        # 'DO SLOT n' whose module is a DI (PLC-009), is reported there, not again here.
        plc001 = {t.device for t in (ctx.traces or []) if any(cf.reported_by == "PLC-001" for cf in t.conflicts)}
        module_io: dict[str, str] = {}
        for e in ctx.edges_of("DESTINED_FOR"):
            if e.src.startswith("TERMINAL:") and (m := ctx.model.nodes.get(e.dst)) is not None and m.attrs.get("module_type"):
                module_io[ctx.model.nodes[e.src].key] = m.attrs["module_type"]
        for c in ctx.claims_of(Predicate.DESTINED_PANEL):
            if c.parser is not DocType.TERM or not c.value.get("io"):
                continue
            actual = module_io.get(c.subject_key)
            if actual and io_direction(actual) != io_direction(c.value["io"]):
                continue  # PLC-009
            for device in terminal_devices.get(c.subject_key, ()):
                if device in plc001:
                    continue
                found.setdefault(device, []).append((f"'{c.value['io']} SLOT {c.value.get('slot')}' on the termination drawing",
                                                     c.value["io"], [c]))
        for device, uses in sorted(found.items()):
            expected = expected_by_prefix.get(device.split("-", 1)[0].upper())
            if expected is None:
                continue
            wrong = [(where, io, cs) for where, io, cs in uses if io_direction(io) not in (None, expected)]
            if not wrong:
                continue
            seen, places = set(), []
            for where, io, _ in wrong:
                if where not in seen:
                    seen.add(where)
                    places.append(where)
            kind = "an input" if expected == "IN" else "an output"
            yield make(
                subject_type="DEVICE",
                subject_key=device,
                message=(f"{device} should be {kind} by its prefix, but is wired to {wrong[0][1]} ({'; '.join(places)}): "
                         "check the device type, its tag, or the slot"),
                evidence=wrong[0][2][:1],
                discriminator=wrong[0][1],
                recommendation=f"Confirm {device}'s I/O type; correct the tag prefix or the module it is wired to",
            )
