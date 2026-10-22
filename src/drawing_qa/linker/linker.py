"""Linker: claims from all parsers -> one engineering model (nodes + edges).

- One node per (type, canonical key); every node keeps which parsers saw it, on which
  sheets, and the claims behind it.
- A superseded designation (LTL-AUT1) is linked to its replacement (LTL-PLC) so traces are
  not broken by a stale name; the old spelling is kept in `written_as` and DRW-006 still reports it.
- Edges come from the claims that relate two things (WIRED_TO, HAS_CHANNEL, the termination
  claims LANDS_ON / REFERS_TO_DEVICE / DESTINED_* / CONNECTS) or are derived (a terminal's JB, a
  module's panel).
- Slot termination sheets name a module by slot only ("S12"); it is resolved to the rack's module
  once all modules are known, when only one rack has that slot.
"""

from __future__ import annotations

from drawing_qa.common.descriptions import detect_prefix
from drawing_qa.common.identifiers import IdentifierGrammar
from drawing_qa.config import Settings
from drawing_qa.model.claims import Claim
from drawing_qa.model.engineering import Edge, EngineeringModel, LinkStatus, Node
from drawing_qa.model.enums import EntityType, Predicate
from drawing_qa.model.source import Drawing

_NODE_TYPES = {
    EntityType.DEVICE,
    EntityType.WIRE,
    EntityType.JUNCTION_BOX,
    EntityType.TERMINAL,
    EntityType.PANEL,
    EntityType.LOCATION,
}


# Termination claim predicate -> edge type.
_TERMINATION = {
    Predicate.LANDS_ON: "WIRE_LANDS_ON",  # wire -> JB terminal, or wire -> PLC module (slot sheet)
    Predicate.REFERS_TO_DEVICE: "TERMINAL_DEVICE",
    Predicate.DESTINED_PANEL: "TERMINAL_PANEL",
    Predicate.DESTINED_FOR: "DESTINED_FOR",  # JB terminal -> PLC module, or PLC module -> JB / device
    Predicate.CONNECTS: "WIRE_AT_DEVICE",  # wire number at the field device's own terminal
}


def node_id(entity_type: str, key: str) -> str:
    return f"{entity_type}:{key}"


class Linker:
    def __init__(self, drawings: list[Drawing], settings: Settings) -> None:
        self.settings = settings
        self.grammar = IdentifierGrammar(settings.identifiers)
        self.labels = {d.id: d.label for d in drawings}
        self.model = EngineeringModel()
        self._edge_index: dict[tuple[str, str, str], Edge] = {}
        self._pending: list[Claim] = []  # termination claims naming a module by slot only

    def link(self, claims: list[Claim], skip_drawings: set[str] = frozenset()) -> EngineeringModel:
        for c in claims:
            if c.source.drawing_id in skip_drawings:
                continue
            if c.predicate is Predicate.OCCURS and c.subject_type in _NODE_TYPES:
                self._occurrence(c)
            elif c.predicate is Predicate.INSTALLED_IN:
                self._module(c)
            elif c.predicate is Predicate.HAS_CHANNEL:
                self._channel(c)
            elif c.predicate is Predicate.ROUTED and c.object_key:
                src = self._node(c.subject_type.value, c.subject_key, c)
                dst = self._node(c.object_type.value, c.object_key, c)
                edge = self._edge("ROUTE", src.id, dst.id, c, unique=True)
                edge.attrs = {k: c.value.get(k) for k in ("spec", "cable_no", "from_kind", "to_kind")}
            elif c.predicate is Predicate.IN_ENCLOSURE and c.object_key:
                dev = self._node(EntityType.DEVICE.value, c.subject_key, c)
                enc = self._node(c.object_type.value, c.object_key, c)
                self._edge("DEVICE_IN", dev.id, enc.id, c)
            elif c.predicate is Predicate.WIRED_TO and c.object_type is not None:
                target = self._node(c.object_type.value, c.object_key or "", c)
                self._edge(f"CHANNEL_{c.object_type.value}", node_id("PLC_CHANNEL", c.subject_key), target.id, c)
            elif c.predicate in _TERMINATION and c.object_key:
                self._termination(c)
            elif c.predicate is Predicate.HAS_SPEC and c.subject_type is EntityType.JUNCTION_BOX:
                jb = self._node(c.subject_type.value, c.subject_key, c)
                jb.attrs.setdefault("multicores", []).append({**c.value, "claim_id": c.id})
        self._resolve_slots()
        self._finish()
        return self.model

    # --- nodes ---------------------------------------------------------------------

    def _node(self, entity_type: str, key: str, claim: Claim | None = None, derived: bool = False) -> Node:
        nid = node_id(entity_type, key)
        node = self.model.nodes.get(nid)
        if node is None:
            node = Node(id=nid, type=entity_type, key=key, status=LinkStatus.DERIVED if derived else LinkStatus.SINGLE_SOURCE)
            self.model.nodes[nid] = node
        if claim is not None:
            if claim.parser.value not in node.parsers:
                node.parsers.append(claim.parser.value)
            label = self.labels.get(claim.source.drawing_id)
            if label and label not in node.sheets:
                node.sheets.append(label)
            node.claim_ids.append(claim.id)
            if node.status is LinkStatus.DERIVED:
                node.status = LinkStatus.SINGLE_SOURCE
        return node

    def _occurrence(self, c: Claim) -> None:
        entity_type, key = c.subject_type, c.subject_key
        if replacement := c.value.get("superseded_by"):
            parsed = self.grammar.parse(replacement)
            if parsed is not None:
                entity_type, key = parsed.kind, parsed.key
        node = self._node(entity_type.value, key, c)
        for part in ("ffi", "sheet", "grid", "format_valid"):
            if part in c.value:
                node.attrs.setdefault(part, c.value[part])
        if key != c.subject_key and c.subject_key not in node.written_as:
            node.written_as.append(c.subject_key)
        if entity_type is EntityType.TERMINAL and c.value.get("jb"):
            jb = self._node(c.value.get("jb_kind", EntityType.JUNCTION_BOX.value), c.value["jb"], derived=True)
            self._edge("TERMINAL_IN", node.id, jb.id, c, derived=True)
            for k in ("wire_text", "block"):
                if k in c.value:
                    node.attrs.setdefault(k, c.value[k])

    def _module(self, c: Claim) -> None:
        node = self._node("PLC_MODULE", c.subject_key, c)
        v = c.value
        source = v.get("source")
        node.attrs.setdefault("rack", v.get("rack"))
        node.attrs.setdefault("slot", v.get("slot"))
        node.attrs[f"part_{source}"] = v.get("part")
        if source == "slot_sheet" or "part" not in node.attrs:
            node.attrs.update(
                {k: v.get(k) for k in ("part", "module_type", "channels", "safety") if v.get(k) is not None}
            )
        rack = v.get("rack")
        if rack:
            panel_key = self.settings.plc.rack_panels.get(rack, f"{rack}-PLC")
            panel = self._node(EntityType.PANEL.value, panel_key, derived=True)
            self._edge("MODULE_PANEL", node.id, panel.id, c, derived=True)

    def _channel(self, c: Claim) -> None:
        node = self._node("PLC_CHANNEL", c.subject_key, c)
        v = c.value
        node.attrs.update(
            {
                "channel": v["channel"],
                "description": v.get("description", ""),
                "spare": v.get("spare", False),
                "module_terminals": v.get("module_terminals", []),
                "claim_id": c.id,
            }
        )
        module = self._node("PLC_MODULE", c.object_key or "", derived=True)
        self._edge("CHANNEL_MODULE", node.id, module.id, c)

    def _replacement(self, entity_type: str, key: str) -> tuple[str, str]:
        """A superseded designation's replacement (LTL-AUT1 -> PANEL LTL-PLC), else itself."""
        parsed = self.grammar.parse(key)
        if parsed is not None and parsed.parts.get("superseded_by"):
            new = self.grammar.parse(str(parsed.parts["superseded_by"]))
            if new is not None:
                return new.kind.value, new.key
        return entity_type, key

    def _termination(self, c: Claim) -> None:
        """Termination claims -> edges. Module keys "S<slot>" wait for _resolve_slots."""
        if c.object_type is EntityType.PLC_MODULE and c.object_key.startswith("S") and "/" not in c.object_key:
            self._pending.append(c)
            return
        if c.subject_type is EntityType.PLC_MODULE and c.subject_key.startswith("S") and "/" not in c.subject_key:
            self._pending.append(c)
            return
        src = self._node(c.subject_type.value, c.subject_key, c)
        dst_type, dst_key = self._replacement(c.object_type.value, c.object_key)
        dst = self._node(dst_type, dst_key, c)
        edge = self._edge(_TERMINATION[c.predicate], src.id, dst.id, c)
        edge.attrs.update({k: v for k, v in c.value.items() if k in ("terminal", "slot", "io", "core", "wire", "ref_sheet", "panel_written")})

    def _resolve_slots(self) -> None:
        by_slot: dict[int, list[Node]] = {}
        for n in self.model.nodes.values():
            if n.type == "PLC_MODULE" and n.attrs.get("slot") is not None:
                by_slot.setdefault(int(n.attrs["slot"]), []).append(n)
        for c in self._pending:
            slot = int(c.value.get("slot") or (c.object_key if c.object_type is EntityType.PLC_MODULE else c.subject_key)[1:])
            modules = by_slot.get(slot, [])
            key = modules[0].key if len(modules) == 1 else f"?/S{slot}"
            if c.subject_type is EntityType.PLC_MODULE:
                src = self._node("PLC_MODULE", key, c)
                dst_type, dst_key = self._replacement(c.object_type.value, c.object_key)
                dst = self._node(dst_type, dst_key, c)
            else:
                src = self._node(c.subject_type.value, c.subject_key, c)
                dst = self._node("PLC_MODULE", key, c)
            edge = self._edge(_TERMINATION[c.predicate], src.id, dst.id, c, unique=True)
            edge.attrs.update({k: v for k, v in c.value.items() if k in ("terminal", "slot", "core", "wire", "ref_sheet")})

    # --- edges ---------------------------------------------------------------------

    def _edge(self, edge_type: str, src: str, dst: str, claim: Claim, derived: bool = False, unique: bool = False) -> Edge:
        # unique: one edge per claim (each drawn cable is its own edge, even between the same two boxes)
        key = (edge_type, src, dst, claim.id) if unique else (edge_type, src, dst)
        edge = self._edge_index.get(key)
        if edge is None:
            edge = Edge(
                id=f"E{len(self.model.edges) + 1:05d}",
                type=edge_type,
                src=src,
                dst=dst,
                status=LinkStatus.DERIVED if derived else LinkStatus.SINGLE_SOURCE,
            )
            self._edge_index[key] = edge
            self.model.edges.append(edge)
        if claim.parser.value not in edge.parsers:
            edge.parsers.append(claim.parser.value)
        edge.claim_ids.append(claim.id)
        return edge

    def _finish(self) -> None:
        configured = self.settings.plc.description_prefix
        self.model.description_prefix = (
            configured.strip().upper() if configured is not None
            else detect_prefix(n.attrs.get("description", "") for n in self.model.nodes.values() if n.type == "PLC_CHANNEL")
        )
        for item in list(self.model.nodes.values()) + self.model.edges:
            item.parsers.sort()
            if len(item.parsers) >= 2:
                item.status = LinkStatus.CORROBORATED
        for node in self.model.nodes.values():
            node.sheets.sort()


def link(claims: list[Claim], drawings: list[Drawing], settings: Settings, skip_drawings: set[str] = frozenset()) -> EngineeringModel:
    return Linker(drawings, settings).link(claims, skip_drawings)
