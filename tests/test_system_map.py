"""system_map.html: the package as a map, panel -> PLC module -> JB / field location -> field device."""

from __future__ import annotations

import json
import re

from drawing_qa.pipeline import run_pipeline
from drawing_qa.report.system_map import build_map, render_map
from drawing_qa.report.writer import write_outputs
from tests.test_cbd import two_jb_cbd
from tests.test_plc import channel_row, slot_sheet
from tests.test_rules import add_sheet, cfg  # noqa: F401  (cfg is a fixture)
from tests.test_traces import _package


def _node(m, key):
    return next(n for n in m["nodes"] if n["key"] == key)


def _link(m, src, dst):
    return next(k for k in m["links"] if k["src"] == _node(m, src)["id"] and k["dst"] == _node(m, dst)["id"])


def test_map_runs_panel_to_module_to_jb_to_device(tmp_path, cfg):
    rows = [channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["LONG TRAVEL BRAKE"])]
    _package(tmp_path, rows, [((100, 200, 150, 212), "LTL-JB2")])  # the CBD shows the JB, not the device
    m = build_map(run_pipeline(tmp_path, cfg))
    assert [_node(m, k)["column"] for k in ("LTL-PLC", "LTL/S13", "LTL-JB2", "S-TN36D4")] == [0, 1, 2, 3]
    link = _link(m, "LTL-JB2", "S-TN36D4")
    assert link["channels"] == ["LTL/S13/CH00"] and link["state"] == "ok"
    assert _link(m, "LTL/S13", "LTL-JB2")["state"] == "ok" and _link(m, "LTL-PLC", "LTL/S13")
    device = _node(m, "S-TN36D4")
    assert device["missing_from_cbd"] is True
    assert (device["on_plc"], device["on_cbd"], device["via_field_jb"]) == (True, False, True)
    assert _node(m, "LTL-JB2")["on_cbd"] is True
    assert [f["rule"] for f in device["findings"]] == ["COMP-005"]
    assert any(loc.startswith("401103 sh 036") for loc in device["locations"])
    [ch] = device["channels"]
    assert ch["channel"] == "LTL/S13/CH00" and "BRAKE" in ch["description"] and ch["wires"] == ["TN36D6"]


def test_drawings_that_disagree_turn_the_line_red(tmp_path, cfg):
    two_jb_cbd(tmp_path)  # the CBD routes S-TN36D4 via LTL-JB8 and LTL-JB1; the schematic only via LTL-JB1
    items, frame = slot_sheet(rows=[channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB1", ["COW CATCHER"])])
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    m = build_map(run_pipeline(tmp_path, cfg))
    assert _link(m, "LTL-JB1", "S-TN36D4")["state"] == "conflict"
    assert _node(m, "S-TN36D4")["channels"][0]["cbd_via"] == ["LTL-JB8", "LTL-JB1"]


def test_device_only_on_the_cbd(tmp_path, cfg):
    """Drawn on a CBD, wired by no schematic channel: listed against the PLC devices for comparison."""
    rows = [channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["BRAKE"])]
    _package(tmp_path, rows, [((100, 200, 150, 212), "S-TN36D4"), ((100, 300, 150, 312), "S-TN37F4")])
    m = build_map(run_pipeline(tmp_path, cfg))
    extra = _node(m, "S-TN37F4")
    assert extra["orphan"] is True and extra["on_cbd"] is True and extra["on_plc"] is False
    assert _node(m, "S-TN36D4")["on_cbd"] is True


def test_spare_channels_are_left_off(tmp_path, cfg):
    rows = [channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["BRAKE"]),
            channel_row(182, "01", "IN-1", None, "E4", None, None, ["SPARE"])]
    _package(tmp_path, rows)
    m = build_map(run_pipeline(tmp_path, cfg))
    assert all("LTL/S13/CH01" not in k["channels"] for k in m["links"])


def test_page_is_self_contained(tmp_path, cfg):
    _package(tmp_path / "pkg", [channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["BRAKE </script>"])])
    result = run_pipeline(tmp_path / "pkg", cfg)
    html = render_map(result)
    assert not re.search(r"""<(script|link)[^>]+(src|href)=["']?https?:""", html)  # opens offline
    data = re.search(r'<script type="application/json" id="map-data">(.*?)</script>', html, re.S).group(1)
    assert json.loads(data)["nodes"]
    paths = write_outputs(result, tmp_path / "out")
    assert (tmp_path / "out" / "system_map.html") in paths
