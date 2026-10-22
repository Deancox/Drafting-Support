"""Resolved model (DATA_MODEL.md section 4): nodes, edges and signal traces built from claims."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class LinkStatus(StrEnum):
    CORROBORATED = "CORROBORATED"  # seen by two or more parsers
    SINGLE_SOURCE = "SINGLE_SOURCE"
    DERIVED = "DERIVED"  # implied by the linker (a JB from a terminal key, a panel from a rack)


class Node(BaseModel):
    id: str  # typed key, e.g. "DEVICE:S-TN36D4"
    type: str
    key: str
    status: LinkStatus = LinkStatus.SINGLE_SOURCE
    parsers: list[str] = Field(default_factory=list)  # PLC / CBD / TERM
    sheets: list[str] = Field(default_factory=list)  # where it appears, e.g. "401103 sh 036"
    written_as: list[str] = Field(default_factory=list)  # other spellings / superseded names
    claim_ids: list[str] = Field(default_factory=list)
    attrs: dict = Field(default_factory=dict)


class Edge(BaseModel):
    id: str
    type: str  # CHANNEL_MODULE, CHANNEL_DEVICE, CHANNEL_JUNCTION_BOX, CHANNEL_WIRE, MODULE_PANEL, TERMINAL_IN
    src: str
    dst: str
    status: LinkStatus = LinkStatus.SINGLE_SOURCE
    parsers: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    attrs: dict = Field(default_factory=dict)


class EngineeringModel(BaseModel):
    nodes: dict[str, Node] = Field(default_factory=dict)
    edges: list[Edge] = Field(default_factory=list)
    # Text that starts the package's channel descriptions ("07.11 SHIPLOADER"); see descriptions.py.
    description_prefix: str = ""


class TraceStatus(StrEnum):
    COMPLETE = "COMPLETE"  # every hop found
    PARTIAL = "PARTIAL"  # reaches a PLC channel, but one or more hops missing or not yet sourced
    ORPHAN = "ORPHAN"  # a field device that reaches no PLC channel
    SPARE = "SPARE"  # spare channel
    INTERNAL = "INTERNAL"  # channel with no field device and no JB (e.g. supply-healthy monitoring)


class HopStatus(StrEnum):
    FOUND = "FOUND"
    INFERRED = "INFERRED"  # taken from a neighbouring channel of the same device (dual-channel, shared device)
    MISSING = "MISSING"  # the supplied drawings should show it and do not
    NOT_SOURCED = "NOT_SOURCED"  # only a drawing type not yet parsed can give it (JB terminal: termination drawings)


HOP_ORDER = ["DEVICE", "WIRE", "JB_TERMINAL", "JB", "PANEL", "SLOT", "CHANNEL"]


class TraceHop(BaseModel):
    seq: int
    hop: str  # one of HOP_ORDER
    values: list[str] = Field(default_factory=list)
    status: HopStatus
    note: str = ""


class CoverageRow(BaseModel):
    """Trace coverage for one area, panel, slot or JB (traces/coverage.py)."""

    group: str  # Overall, Area, Panel, Slot, JB / location
    key: str
    traces: int = 0
    complete: int = 0  # every hop found
    complete_so_far: int = 0  # only hops from not-yet-parsed drawing types missing
    gap: int = 0  # a hop the supplied drawings should show is missing (COMP-001)
    orphan: int = 0
    conflicted: int = 0  # no gap, but two drawings disagree on a hop (not counted as traced)

    @property
    def traced_pct(self) -> float:
        """Share traced end to end as far as the parsed drawings go."""
        return (self.complete + self.complete_so_far) / self.traces if self.traces else 0.0


class TraceConflict(BaseModel):
    """Two sources disagree on one hop of a trace: the schematic says one thing, the CBD or the
    termination drawings another."""

    hop: str  # one of HOP_ORDER
    schematic: list[str] = Field(default_factory=list)
    other: list[str] = Field(default_factory=list)
    other_source: str = "CBD"  # CBD or TERM
    reported_by: str  # PATH-002 (JB vs CBD), PLC-001 (slot vs terminations), COMP-003 (anything without its own rule)


class SignalTrace(BaseModel):
    key: str  # channel key (LTL/S13/CH00), or the device for an orphan
    status: TraceStatus
    device: str | None = None
    channel: str | None = None
    description: str = ""
    hops: list[TraceHop] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)  # hops MISSING
    unsourced: list[str] = Field(default_factory=list)  # hops NOT_SOURCED
    on_cbd: bool | None = None  # device shown on a cable block diagram
    cbd_route: list[str] = Field(default_factory=list)  # device -> ... -> panel along CBD cables
    cbd_via: list[str] = Field(default_factory=list)  # the JBs / field locations on that route
    sheets: list[str] = Field(default_factory=list)
    channel_claim_id: str | None = None
    conflicts: list[TraceConflict] = Field(default_factory=list)

    @property
    def conflicted(self) -> bool:
        return bool(self.conflicts)
