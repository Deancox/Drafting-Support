"""Path Validation (PATH): the cable route on the cable block diagrams against the schematics."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from drawing_qa.model.engineering import TraceStatus
from drawing_qa.model.enums import DocType, Severity
from drawing_qa.model.findings import Finding
from drawing_qa.rules.base import Category, Requirement, Rule, RuleMeta
from drawing_qa.rules.context import RuleContext
from drawing_qa.rules.findings import FindingBuilder
from drawing_qa.rules.registry import rule

_NEEDS_TRACES = frozenset({Requirement.CLAIMS, Requirement.GRAPH, Requirement.TRACES})


def _field_traces(ctx: RuleContext):
    return [t for t in ctx.traces if t.status in (TraceStatus.PARTIAL, TraceStatus.COMPLETE) and t.device]


def _channel_claim(ctx: RuleContext, trace):
    return next((c for c in ctx.claims if c.id == trace.channel_claim_id), None)


@rule
class RouteExists(Rule):
    meta = RuleMeta(
        rule_id="PATH-001",
        title="Route Exists",
        category=Category.PATH,
        default_severity=Severity.MINOR,
        description="A field device shown on a cable block diagram with no cable route from it to its PLC panel.",
        requires=_NEEDS_TRACES,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        if DocType.CBD in ctx.decoded_types:
            return  # a decoded CBD (--ocr): a cable or its number may not have decoded
        seen = set()
        for t in _field_traces(ctx):
            if not t.on_cbd or t.cbd_route or t.device in seen:
                continue
            seen.add(t.device)
            panel = next((h.values[0] for h in t.hops if h.hop == "PANEL" and h.values), "its panel")
            claim = _channel_claim(ctx, t)
            yield make(
                subject_type="DEVICE",
                subject_key=t.device,
                message=f"{t.device} ({t.channel}) is on the cable block diagrams but no cable route from it to {panel} was found",
                evidence=[claim] if claim else [],
                recommendation=f"Check the cabling from {t.device} to {panel} on the CBD",
            )


@rule
class JbConsistency(Rule):
    meta = RuleMeta(
        rule_id="PATH-002",
        title="JB Consistency",
        category=Category.PATH,
        default_severity=Severity.MAJOR,
        description=(
            "The schematic channel row and the cable block diagram route the same device through different "
            "JBs / field locations, including a route that leaves out one JB the other passes through (S-TN24H3: "
            "LTL-JB1 on the schematic, LTL-JB8 then LTL-JB1 on the cable block diagram). A schematic row that lists "
            "more locations than the cable block diagram route is not reported: an e-stop series loop runs through "
            "other stations."
        ),
        requires=_NEEDS_TRACES,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        seen = set()
        for t in _field_traces(ctx):
            schematic = next((h.values for h in t.hops if h.hop == "JB" and h.status.value == "FOUND"), [])
            schematic = [v for v in schematic if v != "direct to panel"]
            extra = [j for j in t.cbd_via if j not in schematic]  # on the CBD route, not in the schematic row
            # A row that lists more locations than the CBD route is an e-stop series loop through other stations
            # (S-TN22T4: LTL-JB2, LTL-LCS1, LTL-LCS2, LTL-LCS5 on the schematic, LTL-LCS5 then LTL-JB2 on the CBD).
            overlap = set(schematic) & set(t.cbd_via)
            if not schematic or not t.cbd_via or (overlap and not extra) or t.device in seen:
                continue
            seen.add(t.device)
            claim = _channel_claim(ctx, t)
            term = next((c.other for c in t.conflicts if c.hop == "JB" and c.other_source == "TERM"), None)
            agree = " (the termination drawings agree with the cable block diagram)" if term and set(term) <= set(t.cbd_via) else ""
            gap = f": {', '.join(extra)} {'is' if len(extra) == 1 else 'are'} not on the schematic" if overlap else ""
            yield make(
                subject_type="DEVICE",
                subject_key=t.device,
                message=(
                    f"{t.device} ({t.channel}): the schematic shows it via {', '.join(schematic)}, "
                    f"the cable block diagram routes it via {', '.join(t.cbd_via)}{gap}{agree}"
                ),
                evidence=[claim] if claim else [],
                discriminator=",".join(sorted(schematic)) + "|" + ",".join(sorted(t.cbd_via)),
                recommendation="Make the schematic and the cable block diagram agree on the JB",
            )


# --- termination cross-checks (M2) ------------------------------------------------------------

_NEEDS_TERM = frozenset({Requirement.CLAIMS, Requirement.GRAPH, Requirement.TERMINATIONS})


@rule
class RouteEndpointMatch(Rule):
    meta = RuleMeta(
        rule_id="PATH-003",
        title="Route Endpoint Match",
        category=Category.PATH,
        default_severity=Severity.MINOR,
        description=(
            "The termination drawings send a JB's terminals to a panel, both are on the cable block diagrams, but "
            "the cable block diagrams show no cable route between them."
        ),
        requires=_NEEDS_TERM,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        import networkx as nx

        routes = nx.Graph()
        for e in ctx.edges_of("ROUTE") + ctx.edges_of("DEVICE_IN"):
            routes.add_edge(e.src, e.dst)
        pairs: dict[tuple[str, str], list] = defaultdict(list)
        for e in ctx.edges_of("TERMINAL_PANEL"):
            for jb in ctx.linked(e.src, "TERMINAL_IN"):
                pairs[(jb, e.dst)].append(e)
        for (jb, panel), edges in sorted(pairs.items()):
            on_cbd = all("CBD" in ctx.model.nodes[n].parsers for n in (jb, panel))
            if not on_cbd or (jb in routes and panel in routes and nx.has_path(routes, jb, panel)):
                continue
            jb_key, panel_key = ctx.model.nodes[jb].key, ctx.model.nodes[panel].key
            yield make(
                subject_type="JUNCTION_BOX",
                subject_key=jb_key,
                message=(f"The termination drawings send {jb_key} terminals to {panel_key}, but the cable block diagrams "
                         f"show no cable route from {jb_key} to {panel_key}"),
                evidence=ctx.evidence(edges[0].claim_ids),
                discriminator=panel_key,
                recommendation=f"Show the cable from {jb_key} to {panel_key} on the cable block diagram, or correct the termination drawing",
            )


@rule
class CoreCount(Rule):
    meta = RuleMeta(
        rule_id="PATH-005",
        title="Core Count",
        category=Category.PATH,
        default_severity=Severity.MAJOR,
        description=(
            "A JB's multicore to the panel has fewer cores than terminals using it (MAJOR), or its stated spare "
            "count does not equal cores minus cores used (MINOR)."
        ),
        requires=_NEEDS_TERM,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for node in sorted(ctx.model.nodes.values(), key=lambda n: n.key):
            specs = node.attrs.get("multicores", [])
            sheets = {ctx.claim_by_id[s["claim_id"]].source.drawing_id for s in specs if s.get("claim_id") in ctx.claim_by_id}
            seen = set()
            for spec in specs:
                claim = ctx.claim_by_id.get(spec.get("claim_id", ""))
                if claim is None or (spec.get("text"), spec.get("to")) in seen:
                    continue
                seen.add((spec.get("text"), spec.get("to")))
                cores, used, spares = spec.get("cores"), len(spec.get("terminals", [])), spec.get("spares")
                if not cores:
                    continue
                if used > cores:
                    yield make(
                        subject_type="JUNCTION_BOX",
                        subject_key=node.key,
                        message=f"{node.key}'s multicore to {spec.get('to')} ({spec.get('text')}) has {cores} cores but {used} terminals use it",
                        evidence=[claim],
                        discriminator=f"over|{spec.get('to')}",
                        recommendation="Use a larger cable, or check the terminal allocation",
                    )
                elif spares is not None and spares != cores - used and len(sheets) == 1:
                    # Only when the JB is on one sheet: a strip continued on another sheet shares the cable.
                    yield make(
                        subject_type="JUNCTION_BOX",
                        subject_key=node.key,
                        message=(f"{node.key}'s multicore to {spec.get('to')} ({spec.get('text')}): {used} of {cores} cores used, "
                                 f"so {cores - used} spare, but the drawing says {spares} spare"),
                        evidence=[claim],
                        severity=Severity.MINOR,
                        discriminator=f"spares|{spec.get('to')}",
                        recommendation=f"Correct the spare count to {cores - used}",
                    )


@rule
class RouteLegendMatch(Rule):
    meta = RuleMeta(
        rule_id="PATH-004",
        title="Route Legend Match",
        category=Category.PATH,
        default_severity=Severity.MINOR,
        description=(
            "A schematic sheet's LEGEND box does not match its channel rows: a JB / field location the rows route "
            "through is not in the legend (or the legend is empty), or a legend entry names a location found nowhere "
            "else in the package (MAJOR, with a near-match from the rows)."
        ),
        requires=frozenset({Requirement.CLAIMS, Requirement.GRAPH}),
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        from drawing_qa.model.enums import EntityType, Predicate

        legends: dict[str, list] = defaultdict(list)
        for c in ctx.claims_of(Predicate.ROUTE_LEGEND):
            legends[c.source.drawing_id].append(c)
        rows: dict[str, set[str]] = defaultdict(set)
        for c in ctx.claims_of(Predicate.WIRED_TO):
            if c.object_type in (EntityType.JUNCTION_BOX, EntityType.LOCATION) and c.object_key:
                rows[c.source.drawing_id].add(c.object_key)
        for drawing_id, entries in sorted(legends.items(), key=lambda kv: ctx.drawing[kv[0]].label):
            if drawing_id in ctx.decoded_ids:
                continue  # decoded sheet (--ocr): its legend or channel rows may not have fully decoded
            label = ctx.drawing[drawing_id].label
            listed = {c.subject_key for c in entries if c.subject_type is not EntityType.DRAWING}
            missing = sorted(rows.get(drawing_id, set()) - listed)
            suggested: set[str] = set()
            for c in entries:
                if c.subject_type is EntityType.DRAWING or c.subject_type is EntityType.PANEL:
                    continue
                node = ctx.model.nodes.get(f"{c.subject_type.value}:{c.subject_key}")
                elsewhere = node is not None and any(s != label for s in node.sheets)
                if elsewhere or c.subject_key in rows.get(drawing_id, set()):
                    continue
                # The rows' location with the same trailing number is the likely intended entry (LTL-SH8 -> SH-LCS8).
                near = [m for m in missing if m.rstrip("0123456789") != m and m[len(m.rstrip("0123456789")):] == c.subject_key[len(c.subject_key.rstrip("0123456789")):]]
                suggested.update(near[:1])
                yield make(
                    subject_type="DRAWING",
                    subject_key=label,
                    message=(f"The legend on {label} lists {c.source.raw_text}, which is on no other drawing and in none of the sheet's channel rows"
                             + (f"; the rows use {', '.join(near)}" if near else "")),
                    evidence=[c],
                    severity=Severity.MAJOR,
                    discriminator=f"unknown|{c.subject_key}",
                    recommendation=f"Correct the legend entry{' to =K07.11+' + near[0] if near else ''}",
                )
            missing = [m for m in missing if m not in suggested]
            if missing:
                empty = not listed
                yield make(
                    subject_type="DRAWING",
                    subject_key=label,
                    message=(f"The legend on {label} is empty, but the channel rows route through {', '.join(missing)}" if empty
                             else f"The channel rows on {label} route through {', '.join(missing)}, which the legend does not list"),
                    evidence=entries[:1],
                    discriminator="missing|" + ",".join(missing),
                    recommendation=f"Add {', '.join(missing)} to the legend with the full designation",
                )
