"""Linker, graph, signal traces and COMP rules on synthetic schematic + CBD sheets."""

from __future__ import annotations

from drawing_qa.model.engineering import HopStatus, TraceStatus
from drawing_qa.pipeline import run_pipeline
from tests.conftest import cell_rect
from tests.test_plc import channel_row, slot_sheet
from tests.test_rules import add_sheet, by_rule

# The fixtures in test_plc.py give Settings with areas, 2-3 digit sheets.
from tests.test_plc import cfg  # noqa: F401  (pytest fixture)


def trace(result, key):
    return next(t for t in result.traces if t.key == key)


def hop(t, name):
    return next(h for h in t.hops if h.hop == name)


def _package(tmp_path, rows, cbd_items=()):
    items, frame = slot_sheet(rows=rows)
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    if cbd_items:
        add_sheet(tmp_path, "CBD", "014", list(cbd_items), drawing_no="401310")


def test_partial_trace_from_schematic(tmp_path, cfg):
    _package(tmp_path, [channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["LONG TRAVEL BRAKE"])])
    result = run_pipeline(tmp_path, cfg)
    t = trace(result, "LTL/S13/CH00")
    assert t.status is TraceStatus.PARTIAL
    assert t.gaps == [] and t.unsourced == ["JB_TERMINAL"]
    assert [(h.hop, h.values) for h in t.hops if h.values] == [
        ("DEVICE", ["S-TN36D4"]),
        ("WIRE", ["TN36D6"]),
        ("JB", ["LTL-JB2"]),
        ("PANEL", ["LTL-PLC"]),
        ("SLOT", ["LTL/S13"]),
        ("CHANNEL", ["LTL/S13/CH00"]),
    ]
    assert hop(t, "JB_TERMINAL").status is HopStatus.NOT_SOURCED
    assert by_rule(result, "COMP-001") == []


def test_spare_and_internal_channels(tmp_path, cfg):
    rows = [
        channel_row(113, "00", "IN-0", wire="D6", description=["SPARE"]),
        channel_row(182, "01", "IN-1", description=["24VDC SUPPLY HEALTHY"]),
    ]
    _package(tmp_path, rows)
    result = run_pipeline(tmp_path, cfg)
    assert trace(result, "LTL/S13/CH00").status is TraceStatus.SPARE
    assert trace(result, "LTL/S13/CH01").status is TraceStatus.INTERNAL


def test_second_contact_inherits_device(tmp_path, cfg):
    rows = [
        channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["SKEW EAST", "CONTACT 1"]),
        channel_row(182, "01", "IN-1", wire="F6", jb="LTL-JB2", description=["SKEW EAST", "CONTACT 2"]),
    ]
    _package(tmp_path, rows)
    t = trace(run_pipeline(tmp_path, cfg), "LTL/S13/CH01")
    assert hop(t, "DEVICE").status is HopStatus.INFERRED and t.device == "S-TN36D4"


def test_comp_001_missing_device_points_to_orphan(tmp_path, cfg):
    rows = [channel_row(113, "00", "IN-0", wire="D6", jb="LTL-JB2", description=["RAIL STRIKER"])]
    _package(tmp_path, rows, [(cell_rect("H3"), "S-TN36H3"), (cell_rect("C5"), "LTL-JB2")])
    result = run_pipeline(tmp_path, cfg)
    assert trace(result, "S-TN36H3").status is TraceStatus.ORPHAN
    findings = by_rule(result, "COMP-001")
    assert len(findings) == 1 and "S-TN36H3" in findings[0].message


def test_comp_001_missing_jb(tmp_path, cfg):
    _package(tmp_path, [channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", None, ["DOOR CLOSED"])])
    findings = by_rule(run_pipeline(tmp_path, cfg), "COMP-001")
    assert len(findings) == 1 and "JB / field location" in findings[0].message


def test_no_jb_is_not_a_gap_when_the_device_is_on_a_cbd(tmp_path, cfg):
    """Like S-TN36R4: cabled straight to the panel, shown on the CBD without a JB."""
    _package(tmp_path, [channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", None, ["DOOR CLOSED"])], [(cell_rect("Y9"), "S-TN36D4")])
    result = run_pipeline(tmp_path, cfg)
    assert by_rule(result, "COMP-001") == []
    assert hop(trace(result, "LTL/S13/CH00"), "JB").status is HopStatus.NOT_SOURCED


def test_lcs_counts_as_field_location(tmp_path, cfg):
    rows = [channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-LCS1", ["PUSH BUTTON"])]
    _package(tmp_path, rows)
    result = run_pipeline(tmp_path, cfg)
    assert hop(trace(result, "LTL/S13/CH00"), "JB").values == ["LTL-LCS1"]
    assert by_rule(result, "COMP-001") == []


def test_comp_002_orphan_drawn_on_home_sheet(tmp_path, cfg):
    """A device on the CBD and on its home sheet that no channel reaches (like K-TN32C8)."""
    items, frame = slot_sheet(rows=[channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["BRAKE"])])
    items.append((cell_rect("W8"), "S-TN36W8"))  # drawn on sheet 036 but outside any channel row
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    add_sheet(tmp_path, "CBD", "014", [(cell_rect("H3"), "S-TN36W8"), (cell_rect("C3"), "S-TN36D4")], drawing_no="401310")
    result = run_pipeline(tmp_path, cfg)
    assert [f.subject_key for f in by_rule(result, "COMP-002")] == ["S-TN36W8"]


def test_comp_002_skips_tags_reported_by_tag_003(tmp_path, cfg):
    _package(
        tmp_path,
        [channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["BRAKE"])],
        [(cell_rect("H3"), "S-TN36D8"), (cell_rect("C3"), "S-TN36D4")],
    )
    result = run_pipeline(tmp_path, cfg)
    assert by_rule(result, "COMP-002") == []
    assert [f.subject_key for f in by_rule(result, "TAG-003")] == ["S-TN36D8"]


def test_comp_005_field_device_not_on_cbd(tmp_path, cfg):
    rows = [
        channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["BRAKE"]),
        channel_row(182, "01", "IN-1", "K-TN36F4", "E4", "F6", "LTL-JB2", ["RELAY"]),
    ]
    _package(tmp_path, rows, [(cell_rect("C5"), "LTL-JB2")])
    findings = by_rule(run_pipeline(tmp_path, cfg), "COMP-005")
    assert [f.subject_key for f in findings] == ["S-TN36D4"]  # K- relays are panel devices


def test_superseded_designation_links_to_replacement(tmp_path, cfg):
    cfg.identifiers.superseded = {"LTL-AUT1": "LTL-PLC"}
    add_sheet(tmp_path, "TERM", "201", [(cell_rect("A1", 100), "=K07.11+LTL-AUT1")])
    result = run_pipeline(tmp_path, cfg)
    node = result.model.nodes["PANEL:LTL-PLC"]
    assert node.written_as == ["LTL-AUT1"]
    assert "LOCATION:LTL-AUT1" not in result.model.nodes
    assert by_rule(result, "DRW-006")  # still reported


def test_comp_002_quiet_when_home_sheet_is_decoded(tmp_path, cfg):
    from tests.test_shx_rules import recheck, rule_ids

    items, frame = slot_sheet(rows=[channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["BRAKE"])])
    items.append((cell_rect("W8"), "S-TN36W8"))
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    add_sheet(tmp_path, "CBD", "014", [(cell_rect("H3"), "S-TN36W8"), (cell_rect("C3"), "S-TN36D4")], drawing_no="401310")
    result = run_pipeline(tmp_path, cfg)
    assert rule_ids(result.findings, "COMP-002")
    assert not rule_ids(recheck(result, cfg, lambda d: d.sheet == "036"), "COMP-002")


def test_comp_005_quiet_when_a_cbd_is_decoded(tmp_path, cfg):
    from tests.test_shx_rules import recheck, rule_ids

    rows = [channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["BRAKE"])]
    _package(tmp_path, rows, [(cell_rect("C5"), "LTL-JB2")])
    result = run_pipeline(tmp_path, cfg)
    assert rule_ids(result.findings, "COMP-005")
    assert not rule_ids(recheck(result, cfg, lambda d: d.doc_type.value == "CBD"), "COMP-005")
