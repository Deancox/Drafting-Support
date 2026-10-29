"""system_map.html: the package as a map, the way a signal runs: panel -> PLC module -> JB / field location
-> field device. One line per connection, labelled with its channels and coloured by its traces: green when
traced as far as the drawings go, amber with a gap, red where two drawings disagree. A device the schematic
wires out through a field JB but no cable block diagram shows is dashed. Clicking an item highlights its
traces and lists its sheets, channels and findings.

One self-contained page (no internet needed); built from the run's traces, so it shows only what was read.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from drawing_qa.common.descriptions import heading, without_prefix
from drawing_qa.model.engineering import SignalTrace, TraceStatus
from drawing_qa.model.enums import Predicate
from drawing_qa.model.run import RunResult

MAP_NAME = "system_map.html"
COLUMNS = ["Panel", "PLC module", "JB / field location", "Field device"]
_SHOWN = (TraceStatus.COMPLETE, TraceStatus.PARTIAL, TraceStatus.ORPHAN)
# Findings that say two drawings disagree on a trace (the line turns red); COMP-001 is a gap (amber).
_DISAGREE = ("PATH-", "COMP-003", "PLC-001")
_STATES = ["ok", "gap", "conflict"]  # worst last


def _hop(t: SignalTrace, name: str) -> list[str]:
    return next((h.values for h in t.hops if h.hop == name), [])


def build_map(result: RunResult) -> dict:
    prefix = result.model.description_prefix if result.model else ""
    drawings = {d.id: d for d in result.drawings}
    locations: dict[str, list[str]] = defaultdict(list)
    for c in result.claims:
        if c.predicate is Predicate.OCCURS and c.source.drawing_id in drawings:
            d = drawings[c.source.drawing_id]
            loc = f"{d.drawing_no} sh {d.sheet}" + (f" {c.source.grid_cell}" if c.source.grid_cell else "")
            if loc not in locations[c.subject_key]:
                locations[c.subject_key].append(loc)
    findings: dict[str, list[dict]] = defaultdict(list)
    for f in result.findings:
        findings[f.subject_key].append({"id": f.id, "rule": f.rule_id, "severity": f.severity.value.title(),
                                        "message": f.message, "recommendation": f.recommendation or ""})
    model_nodes = {(n.type, n.key): n for n in result.model.nodes.values()} if result.model else {}

    nodes: dict[tuple[int, str], dict] = {}
    links: dict[tuple[str, str], dict] = {}

    def node(column: int, key: str) -> dict:
        if (column, key) not in nodes:
            nodes[(column, key)] = {"id": f"n{len(nodes)}", "key": key, "column": column, "sub": "",
                                    "locations": locations.get(key, []), "findings": [], "channels": [],
                                    "traces": [], "missing_from_cbd": False, "orphan": False}
        return nodes[(column, key)]

    def add_findings(n: dict, keys: list[str]) -> None:
        for k in keys:
            for f in findings.get(k, []):
                if f not in n["findings"]:
                    n["findings"].append(f)

    def link(a: dict, b: dict, i: int, channel: str, state: str) -> None:
        k = links.setdefault((a["id"], b["id"]), {"src": a["id"], "dst": b["id"], "channels": [], "traces": [], "state": "ok"})
        if channel and channel not in k["channels"]:
            k["channels"].append(channel)
        k["traces"].append(i)
        k["state"] = max(k["state"], state, key=_STATES.index)

    for i, t in enumerate(result.traces):
        if t.status not in _SHOWN:
            continue
        devices = _hop(t, "DEVICE") or ([t.device] if t.device else [])
        if t.status is TraceStatus.ORPHAN:
            for dev in devices:
                n = node(3, dev)
                n["orphan"] = True
                n["traces"].append(i)
                add_findings(n, [dev])
            continue
        wires, jbs = _hop(t, "WIRE"), _hop(t, "JB")
        said = [f["rule"] for k in [*devices, t.channel or ""] for f in findings.get(k, [])]
        state = ("conflict" if t.conflicts or any(r.startswith(_DISAGREE) for r in said)
                 else "gap" if t.gaps or "COMP-001" in said else "ok")
        detail = {"channel": t.channel, "description": without_prefix(t.description, prefix), "wires": wires,
                  "jbs": jbs, "cbd_via": t.cbd_via, "on_cbd": t.on_cbd, "state": state}
        path = [node(0, p) for p in _hop(t, "PANEL")[:1]] + [node(1, s) for s in _hop(t, "SLOT")[:1]]
        stations = [node(2, j) for j in jbs]
        ends = [node(3, d) for d in devices]
        for n in path + stations + ends:
            n["traces"].append(i)
            if n["column"] and detail not in n["channels"]:
                n["channels"].append(detail)
        for n in ends:
            n["missing_from_cbd"] = n["missing_from_cbd"] or (t.on_cbd is False and bool(jbs))
            if not n["sub"]:
                n["sub"] = heading(t.description, prefix).title()
            add_findings(n, [n["key"], t.channel or "", *wires])
        if len(path) == 2:
            link(path[0], path[1], i, "", state)
            add_findings(path[1], [t.channel or ""])
        upstream = path[-1:] if path else []
        for a in upstream:
            for b in stations or ends:
                link(a, b, i, t.channel or "", state)
        for a in stations:
            for b in ends:
                link(a, b, i, t.channel or "", state)

    for (column, key), n in nodes.items():
        add_findings(n, [key])
        if column == 1 and (m := model_nodes.get(("PLC_MODULE", key))):
            n["sub"] = " · ".join(str(v) for v in (m.attrs.get("module_type"), m.attrs.get("part")) if v)
        elif column == 2:
            n["sub"] = "Field location" if ("LOCATION", key) in model_nodes else "Junction box"
        if not n["locations"] and (m := next((model_nodes[k] for k in model_nodes if k[1] == key), None)):
            n["locations"] = m.sheets
    _order(nodes, links)
    return {
        "run": {"id": result.info.run_id, "input": result.info.input_root},
        "columns": COLUMNS,
        "nodes": sorted(nodes.values(), key=lambda n: (n["column"], n["order"])),
        "links": list(links.values()),
    }


def _slot(key: str) -> tuple[str, int]:
    rack, _, slot = key.partition("/S")
    return (rack, int(slot)) if slot.isdigit() else (key, 0)


def _order(nodes: dict, links: dict) -> None:
    """Top-to-bottom order in each column: panels and modules by name and slot, then each JB and device
    under the things that feed it, so lines cross as little as possible."""
    by_id = {n["id"]: n for n in nodes.values()}
    feeds: dict[str, list[str]] = defaultdict(list)
    for src, dst in links:
        feeds[dst].append(src)
    for column in range(4):
        col = [n for n in nodes.values() if n["column"] == column]
        if column == 0:
            col.sort(key=lambda n: n["key"])
        elif column == 1:
            col.sort(key=lambda n: _slot(n["key"]))
        else:
            def place(n: dict) -> tuple:
                above = [by_id[s]["order"] for s in feeds.get(n["id"], []) if "order" in by_id[s]]
                return (n["orphan"], sum(above) / len(above) if above else 1e9, n["key"])
            col.sort(key=place)
        for i, n in enumerate(col):
            n["order"] = i


def render_map(result: RunResult) -> str:
    data = json.dumps(build_map(result), ensure_ascii=False).replace("</", "<\\/")
    return _PAGE.replace("/*DATA*/", data)


def write_map(result: RunResult, out_dir: Path) -> Path:
    path = out_dir / MAP_NAME
    path.write_text(render_map(result), encoding="utf-8")
    return path


_PAGE = (Path(__file__).with_name("system_map.html")).read_text(encoding="utf-8")
