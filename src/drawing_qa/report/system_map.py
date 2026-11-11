"""system_map.html: the package as a map, the way a signal runs: panel -> PLC module -> JB / field location
-> field device. One line per connection, labelled with its channels and coloured by its traces: green when
traced as far as the drawings go, amber with a gap, red where two drawings disagree. A device the schematic
wires out through a field JB but no cable block diagram shows is dashed. From the termination drawings: each
channel's JB terminals, a JB the terminations land a device in when the schematic names another, and the
multicore between each JB and its panel (number and spec, as the termination drawing and the CBD give it).
Clicking an item highlights its traces and lists its sheets, channels, cables and findings.

One self-contained page (no internet needed); built from the run's traces, so it shows only what was read.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from drawing_qa.common.descriptions import heading, without_prefix
from drawing_qa.model.engineering import SignalTrace, TraceStatus
from drawing_qa.model.enums import EntityType, Predicate
from drawing_qa.model.run import RunResult

MAP_NAME = "system_map.html"
COLUMNS = ["Panel", "PLC module", "JB / field location", "Field device"]
_SHOWN = (TraceStatus.COMPLETE, TraceStatus.PARTIAL, TraceStatus.ORPHAN)
# Findings that say two drawings disagree on a trace (the line turns red); COMP-001 is a gap (amber).
_DISAGREE = ("PATH-", "COMP-003", "PLC-001")
# The termination drawing disagrees about one device's wires, pins or slot: only its own line (JB -> device) turns red.
_TD_DISAGREE = ("PLC-009", "WIRE-004", "TERM-005", "TERM-006", "TERM-007")
_CABLE_DISAGREE = ("CABLE-003",)  # the multicore joins another JB on the CBD: its module -> JB line turns red
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
    strips: dict[str, list[str]] = defaultdict(list)  # JB -> termination sheets drawing its terminal strip
    for c in result.claims:
        if c.predicate is Predicate.OCCURS and c.subject_type is EntityType.TERMINAL and c.value.get("jb"):
            d = drawings.get(c.source.drawing_id)
            label = f"{d.drawing_no} sh {d.sheet}" if d else c.source.drawing_id
            if label not in strips[c.value["jb"]]:
                strips[c.value["jb"]].append(label)
    cables = _cables(result)

    nodes: dict[tuple[int, str], dict] = {}
    links: dict[tuple[str, str], dict] = {}

    def node(column: int, key: str) -> dict:
        if (column, key) not in nodes:
            nodes[(column, key)] = {"id": f"n{len(nodes)}", "key": key, "column": column, "sub": "",
                                    "locations": locations.get(key, []), "findings": [], "channels": [],
                                    "traces": [], "missing_from_cbd": False, "orphan": False, "cables": [],
                                    "missing_from_term": False}
        return nodes[(column, key)]

    def add_findings(n: dict, keys: list[str]) -> None:
        for k in keys:
            for f in findings.get(k, []):
                if f not in n["findings"]:
                    n["findings"].append(f)

    def link(a: dict, b: dict, i: int, channel: str, state: str, cable_list=()) -> None:
        k = links.setdefault((a["id"], b["id"]), {"src": a["id"], "dst": b["id"], "channels": [], "traces": [], "state": "ok",
                                                  "cables": []})
        for cab in cable_list:
            if cab not in k["cables"]:
                k["cables"].append(cab)
            if any(f["rule"].startswith(_CABLE_DISAGREE) and f["severity"] == "Major" for f in findings.get(cab["number"], [])):
                state = max(state, "conflict", key=_STATES.index)
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
        terminals = _hop(t, "JB_TERMINAL")
        td_jbs = sorted({v.split(":", 1)[0] for v in terminals})
        said = [f["rule"] for k in [*devices, t.channel or "", *wires] for f in findings.get(k, [])]
        state = ("conflict" if t.conflicts or any(r.startswith(_DISAGREE) for r in said)
                 else "gap" if t.gaps or "COMP-001" in said else "ok")
        last = max(state, "conflict" if any(r.startswith(_TD_DISAGREE) for r in said) else "ok", key=_STATES.index)
        detail = {"channel": t.channel, "description": without_prefix(t.description, prefix), "wires": wires,
                  "jbs": jbs, "cbd_via": t.cbd_via, "on_cbd": t.on_cbd, "state": last,
                  "terminals": terminals, "td_jbs": td_jbs}
        panels = _hop(t, "PANEL")[:1]
        path = [node(0, p) for p in panels] + [node(1, s) for s in _hop(t, "SLOT")[:1]]
        # A JB the terminations land the device in that the schematic does not name (COMP-003) is shown too.
        stations = [node(2, j) for j in [*jbs, *(j for j in td_jbs if j not in jbs)]]
        ends = [node(3, d) for d in devices]
        for n in path + stations + ends:
            n["traces"].append(i)
            if n["column"] and detail not in n["channels"]:
                n["channels"].append(detail)
        for n in ends:
            n["missing_from_cbd"] = n["missing_from_cbd"] or (t.on_cbd is False and bool(jbs))
            # Its JB's strip is on a termination sheet, but no terminal there serves it.
            n["missing_from_term"] = n["missing_from_term"] or (not terminals and any(j in strips for j in jbs))
            if not n["sub"]:
                n["sub"] = heading(t.description, prefix).title()
            add_findings(n, [n["key"], t.channel or "", *wires])
        if len(path) == 2:
            link(path[0], path[1], i, "", state)
            add_findings(path[1], [t.channel or ""])
        upstream = path[-1:] if path else []
        for a in upstream:
            for b in stations or ends:
                to_panel = [c for p in panels for c in cables.get((b["key"], p), [])]
                link(a, b, i, t.channel or "", state if stations else last, to_panel)
                for c in to_panel:
                    if c not in b["cables"]:
                        b["cables"].append(c)
                add_findings(b, [c["number"] for c in to_panel])
        for a in stations:
            for b in ends:
                link(a, b, i, t.channel or "", last)

    types = {0: ("PANEL",), 1: ("PLC_MODULE",), 2: ("JUNCTION_BOX", "LOCATION"), 3: ("DEVICE",)}
    for (column, key), n in nodes.items():
        add_findings(n, [key])
        parsers = {p for t in types[column] if (m := model_nodes.get((t, key))) for p in m.parsers}
        n["on_plc"], n["on_cbd"] = "PLC" in parsers, "CBD" in parsers
        n["on_term"] = key in strips if column == 2 else "TERM" in parsers
        n["strip_sheets"] = strips.get(key, [])
        n["via_field_jb"] = any(c["jbs"] for c in n["channels"])  # so it should be on a CBD
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


def _cables(result: RunResult) -> dict[tuple[str, str], list[dict]]:
    """(JB, panel) -> the multicores between them: number, spec and which drawings give it."""
    out: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)

    def add(jb: str, panel: str, number: str | None, spec: str | None, on: str) -> None:
        if not number:
            return
        c = out[(jb, panel)].setdefault(number.upper(), {"number": number.upper(), "spec": {}, "on": []})
        if on not in c["on"]:
            c["on"].append(on)
        if spec:
            c["spec"].setdefault(on, spec.replace("�", "²"))  # "mm²" read from some CBDs as a replacement character

    if result.model:
        nodes = result.model.nodes
        alias = {w: n.key for n in nodes.values() for w in n.written_as}  # LTL-AUT1 -> LTL-PLC
        for n in nodes.values():
            for m in n.attrs.get("multicores", []):
                if m.get("to"):
                    add(n.key, alias.get(m["to"], m["to"]), m.get("cable_no"), m.get("text"), "termination")
        for e in result.model.edges:
            if e.type == "ROUTE" and e.src in nodes and e.dst in nodes:
                a, b = nodes[e.src], nodes[e.dst]
                pair = (b.key, a.key) if a.type == "PANEL" else (a.key, b.key) if b.type == "PANEL" else None
                if pair:
                    add(*pair, e.attrs.get("cable_no"), e.attrs.get("spec"), "CBD")
    return {k: [v[n] for n in sorted(v)] for k, v in out.items()}


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
