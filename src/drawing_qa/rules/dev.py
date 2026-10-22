"""Device Validation (DEV)."""

from __future__ import annotations

from collections.abc import Iterable

from drawing_qa.model.enums import Severity
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
