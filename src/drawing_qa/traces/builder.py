"""Signal trace builder (IMPLEMENTATION_PLAN.md section 6).

One trace per PLC channel, walking backward from the channel:
    CHANNEL -> SLOT (module) -> PANEL -> JB -> JB TERMINAL -> WIRE -> DEVICE
and one ORPHAN trace per field device (shown on a cable block diagram) that reaches no channel.

The JB terminal hop comes from the termination drawings: the JB terminals whose device block
is the trace's device. It is NOT_SOURCED (listed, never a gap) when the termination drawings are
left out of the review or none is supplied for the device's JB, and MISSING when that JB's
termination drawing is supplied but does not show the device.

A channel row with no device tag takes the device of the nearest channel on the same
module with the same description heading (the second contact of a dual-channel safety
device, or one device labelled once for several channels), marked INFERRED.
"""

from __future__ import annotations

import networkx as nx

from drawing_qa.graph import neighbours
from drawing_qa.model.engineering import (
    HOP_ORDER,
    EngineeringModel,
    HopStatus,
    Node,
    SignalTrace,
    TraceConflict,
    TraceHop,
    TraceStatus,
)
from drawing_qa.common.descriptions import heading
from drawing_qa.model.enums import DocType

def description_heading(description: str, prefix: str = "") -> str:
    """First line of a channel description after the project prefix, e.g. 'LONG TRAVEL SKEW EAST'."""
    return heading(description, prefix)


def _terminal_order(node_id: str) -> tuple:
    jb, _, number = node_id.partition(":")[2].rpartition(":")
    return jb, int(number) if number.isdigit() else 0, number


def _keys(model: EngineeringModel, ids: list[str]) -> list[str]:
    return [model.nodes[i].key for i in ids]


class TraceBuilder:
    def __init__(
        self, model: EngineeringModel, graph: nx.MultiDiGraph, parsed_types: set[DocType], external_ffis: set[str]
    ) -> None:
        self.model = model
        self.g = graph
        self.parsed_types = parsed_types
        self.external_ffis = external_ffis
        # Routing layer: cables (ROUTE) and devices drawn inside an enclosure (DEVICE_IN), undirected.
        self.routes = nx.Graph()
        for u, v, d in graph.edges(data=True):
            if d["type"] in ("ROUTE", "DEVICE_IN"):
                self.routes.add_edge(u, v)

    def build(self) -> list[SignalTrace]:
        def order(n: Node) -> tuple:
            rack, _, slot = n.key.split("/CH")[0].partition("/S")
            return rack, int(slot) if slot.isdigit() else 0, n.attrs.get("channel", 0)

        channels = sorted((n for n in self.model.nodes.values() if n.type == "PLC_CHANNEL"), key=order)
        rows = {n.id: self._row(n) for n in channels}
        by_module: dict[str, list[str]] = {}
        for n in channels:
            by_module.setdefault(rows[n.id]["module"] or "", []).append(n.id)

        traces = [self._channel_trace(n, rows, by_module) for n in channels]
        # Every device in a channel row is reached, not just the first: a relay and its contact, or a
        # sensor and its isolator, share one row (K-TN32C4 and K-TN32C8 on LTL/S11/CH00).
        traced_devices = {v for t in traces for h in t.hops if h.hop == "DEVICE" for v in h.values}
        traces += self._orphans(traced_devices)
        return traces

    # --- per channel ------------------------------------------------------------------

    def _row(self, n: Node) -> dict:
        module = neighbours(self.g, n.id, "CHANNEL_MODULE")
        panel = neighbours(self.g, module[0], "MODULE_PANEL") if module else []
        return {
            "module": module[0] if module else None,
            "panel": panel,
            "devices": neighbours(self.g, n.id, "CHANNEL_DEVICE"),
            # The field-side path node: a JB, or a field location such as a local control station (LTL-LCS1).
            "jbs": neighbours(self.g, n.id, "CHANNEL_JUNCTION_BOX") + neighbours(self.g, n.id, "CHANNEL_LOCATION"),
            "wires": neighbours(self.g, n.id, "CHANNEL_WIRE"),
        }

    def _inherit(self, n: Node, rows: dict, by_module: dict) -> tuple[list[str], str]:
        prefix = self.model.description_prefix
        heading = description_heading(n.attrs.get("description", ""), prefix)
        if not heading:
            return [], ""
        mine = n.attrs.get("channel", 0)
        candidates = []
        for other_id in by_module.get(rows[n.id]["module"] or "", []):
            other = self.model.nodes[other_id]
            if other_id == n.id or not rows[other_id]["devices"]:
                continue
            if description_heading(other.attrs.get("description", ""), prefix) == heading:
                candidates.append((abs(other.attrs.get("channel", 0) - mine), other))
        if not candidates:
            return [], ""
        _, source = min(candidates, key=lambda c: c[0])
        return rows[source.id]["devices"], f"from {source.key} (same description heading)"

    def _channel_trace(self, n: Node, rows: dict, by_module: dict) -> SignalTrace:
        row = rows[n.id]
        devices, device_status, device_note = row["devices"], HopStatus.FOUND, ""
        if not devices:
            devices, device_note = self._inherit(n, rows, by_module)
            device_status = HopStatus.INFERRED if devices else HopStatus.MISSING
        spare = bool(n.attrs.get("spare"))
        description = n.attrs.get("description", "")
        device_keys = _keys(self.model, devices)
        base = {
            "key": n.key,
            "channel": n.key,
            "description": description,
            "device": device_keys[0] if device_keys else None,
            "sheets": n.sheets,
            "channel_claim_id": n.attrs.get("claim_id"),
        }
        if spare and not devices:
            return SignalTrace(status=TraceStatus.SPARE, **base)
        if not devices and not row["jbs"]:
            return SignalTrace(status=TraceStatus.INTERNAL, **base)

        # Only a device shown on a CBD under its own tag counts. One shown under a wrong tag (H-TN50D8 for
        # H-TN50D7) is not evidence of a direct cable: H-TN50D7 really has no JB (checker, AK-014).
        on_cbd = any("CBD" in self.model.nodes[d].parsers for d in devices) if devices else None
        route = self._cbd_route(devices, row["panel"]) if on_cbd else []
        via = [self.model.nodes[i].key for i in route[1:-1] if self.model.nodes[i].type != "DEVICE"]
        jb_values, jb_status, jb_note = _keys(self.model, row["jbs"]), HopStatus.FOUND, ""
        if not row["jbs"]:
            if route and not via:
                # Cabled straight to the panel (S-TN36R4): no JB is correct (AK-N05).
                jb_values, jb_note = ["direct to panel"], "the CBD shows a direct cable to the panel"
            elif route:
                jb_status, jb_note = HopStatus.MISSING, f"the CBD routes it via {', '.join(via)}"
            elif on_cbd:
                jb_status, jb_note = HopStatus.NOT_SOURCED, "device is on a CBD but no cable route to the panel was read"
            else:
                jb_status = HopStatus.MISSING
        hops = [
            TraceHop(seq=1, hop="DEVICE", values=device_keys, status=device_status, note=device_note),
            TraceHop(seq=2, hop="WIRE", values=_keys(self.model, row["wires"]), status=HopStatus.FOUND if row["wires"] else HopStatus.MISSING),
            self._terminal_hop(devices, row),
            TraceHop(seq=4, hop="JB", values=jb_values, status=jb_status, note=jb_note),
            TraceHop(seq=5, hop="PANEL", values=_keys(self.model, row["panel"]), status=HopStatus.FOUND if row["panel"] else HopStatus.MISSING),
            TraceHop(seq=6, hop="SLOT", values=[self.model.nodes[row["module"]].key] if row["module"] else [], status=HopStatus.FOUND if row["module"] else HopStatus.MISSING),
            TraceHop(seq=7, hop="CHANNEL", values=[n.key], status=HopStatus.FOUND),
        ]
        conflicts = []
        schematic_jbs = _keys(self.model, row["jbs"])
        if schematic_jbs and via and not set(schematic_jbs) & set(via):
            conflicts.append(TraceConflict(hop="JB", schematic=schematic_jbs, other=via, reported_by="PATH-002"))
        if on_cbd and not route and row["panel"]:
            other = self._reachable_panels(devices) - set(row["panel"])
            if other:
                conflicts.append(TraceConflict(hop="PANEL", schematic=_keys(self.model, row["panel"]),
                                               other=sorted(_keys(self.model, list(other))), reported_by="COMP-003"))
        conflicts += self._termination_conflicts(hops[2], schematic_jbs, row)
        gaps = [h.hop for h in hops if h.status is HopStatus.MISSING]
        unsourced = [h.hop for h in hops if h.status is HopStatus.NOT_SOURCED]
        status =TraceStatus.COMPLETE if not gaps and not unsourced else TraceStatus.PARTIAL
        return SignalTrace(
            status=status,
            hops=hops,
            gaps=gaps,
            unsourced=unsourced,
            on_cbd=on_cbd,
            cbd_route=[self.model.nodes[i].key for i in route],
            cbd_via=via,
            conflicts=conflicts,
            **base,
        )

    # --- terminations ----------------------------------------------------------------

    def _terminals_for(self, devices: list[str]) -> list[str]:
        """JB terminal node ids whose device block is one of these devices."""
        return sorted({t for d in devices for t in neighbours(self.g, d, "TERMINAL_DEVICE")}, key=_terminal_order)

    def _terminal_hop(self, devices: list[str], row: dict) -> TraceHop:
        if DocType.TERM not in self.parsed_types:
            return TraceHop(seq=3, hop="JB_TERMINAL", status=HopStatus.NOT_SOURCED, note="termination drawings left out of the review")
        terminals = self._terminals_for(devices)
        if row["module"] and terminals:
            # A device with several channels lands on several terminal pairs; drop the ones sent to another slot.
            # A terminal with no destination read (just outside its bracket, 401103-207 LTL-JB3:4) stays.
            dest = {t: neighbours(self.g, t, "DESTINED_FOR") for t in terminals}
            if any(row["module"] in d for d in dest.values()):
                terminals = [t for t in terminals if not dest[t] or row["module"] in dest[t]]
        if terminals:
            return TraceHop(seq=3, hop="JB_TERMINAL", values=_keys(self.model, terminals), status=HopStatus.FOUND)
        drawn = [j for j in row["jbs"] if neighbours(self.g, j, "TERMINAL_IN")]
        if drawn:
            names = ", ".join(_keys(self.model, drawn))
            return TraceHop(seq=3, hop="JB_TERMINAL", status=HopStatus.MISSING, note=f"the termination drawing for {names} does not show the device")
        return TraceHop(seq=3, hop="JB_TERMINAL", status=HopStatus.NOT_SOURCED, note="no termination drawing supplied for its JB")

    def _termination_conflicts(self, hop: TraceHop, schematic_jbs: list[str], row: dict) -> list[TraceConflict]:
        if hop.status is not HopStatus.FOUND:
            return []
        terminals = [f"TERMINAL:{v}" for v in hop.values]
        out = []
        term_jbs = sorted({self.model.nodes[j].key for t in terminals for j in neighbours(self.g, t, "TERMINAL_IN")})
        if schematic_jbs and term_jbs and not set(schematic_jbs) & set(term_jbs):
            out.append(TraceConflict(hop="JB", schematic=schematic_jbs, other=term_jbs, other_source="TERM", reported_by="COMP-003"))
        term_slots = sorted({self.model.nodes[m].key for t in terminals for m in neighbours(self.g, t, "DESTINED_FOR")})
        if row["module"] and term_slots and self.model.nodes[row["module"]].key not in term_slots:
            out.append(TraceConflict(hop="SLOT", schematic=[self.model.nodes[row["module"]].key], other=term_slots,
                                     other_source="TERM", reported_by="PLC-001"))
        return out

    def _reachable_panels(self, devices: list[str]) -> set[str]:
        """Panels the CBD cables connect these devices to (by any route)."""
        found: set[str] = set()
        for d in devices:
            if d in self.routes:
                found |= {n for n in nx.node_connected_component(self.routes, d) if self.model.nodes[n].type == "PANEL"}
        return found

    def _cbd_route(self, devices: list[str], panels: list[str]) -> list[str]:
        """Shortest route along CBD cables from the device to its panel (node ids), or []."""
        best: list[str] = []
        for d in devices:
            for p in panels:
                if d in self.routes and p in self.routes and nx.has_path(self.routes, d, p):
                    path = nx.shortest_path(self.routes, d, p)
                    if not best or len(path) < len(best):
                        best = path
        return best

    # --- orphans ----------------------------------------------------------------------

    def _orphans(self, traced: set[str]) -> list[SignalTrace]:
        """Field devices (on a cable block diagram) that no channel trace reaches."""
        out = []
        for node in sorted(self.model.nodes.values(), key=lambda n: n.key):
            if node.type != "DEVICE" or "CBD" not in node.parsers or node.key in traced:
                continue
            if not node.attrs.get("format_valid", True) or node.attrs.get("ffi") in self.external_ffis:
                continue  # malformed tags are TAG-001; separate-package devices are DRW-009
            hops = [TraceHop(seq=1, hop="DEVICE", values=[node.key], status=HopStatus.FOUND)] + [
                TraceHop(seq=i + 1, hop=h, status=HopStatus.MISSING) for i, h in enumerate(HOP_ORDER) if h != "DEVICE"
            ]
            out.append(
                SignalTrace(
                    key=node.key,
                    status=TraceStatus.ORPHAN,
                    device=node.key,
                    hops=sorted(hops, key=lambda h: h.seq),
                    gaps=[h for h in HOP_ORDER if h != "DEVICE"],
                    on_cbd=True,
                    sheets=node.sheets,
                )
            )
        return out


def build_traces(
    model: EngineeringModel, graph: nx.MultiDiGraph, parsed_types: set[DocType], external_ffis: set[str] = frozenset()
) -> list[SignalTrace]:
    return TraceBuilder(model, graph, parsed_types, external_ffis).build()
