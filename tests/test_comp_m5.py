"""M5: conflicted traces (COMP-003), branching (COMP-004), multi-device rows and trace coverage."""

from __future__ import annotations

from drawing_qa.model.enums import Severity
from drawing_qa.pipeline import run_pipeline
from drawing_qa.traces.coverage import by_group
from tests.conftest import cell_rect
from tests.test_cbd import basic_cbd
from tests.test_plc import cfg, channel_row, slot_sheet  # noqa: F401  (cfg is a fixture)
from tests.test_rules import add_sheet, by_rule


def trace(result, key):
    return next(t for t in result.traces if t.key == key)


def safety_sheet(tmp_path, sheet, slot, rows):
    items, frame = slot_sheet(sheet=sheet, slot=slot, part="1734-IB8S", rows=rows,
                              title=f"LTL PLC LANDWARD LEG SAFETY POINT I/O SLOT {slot} DIGITAL INPUT")
    add_sheet(tmp_path, "PLC", sheet, items, **frame)


def test_second_device_in_a_row_is_not_an_orphan(tmp_path, cfg):
    """Like K-TN32C4 and K-TN32C8 on LTL/S11/CH00: both are in the channel row."""
    row = channel_row(113, "00", "IN-0", "K-TN36D4", "B4", "D6", "LTL-JB2", ["CLAMP HOLDING BRAKE RELAY"])
    row.append((cell_rect("B8"), "K-TN36D8"))
    items, frame = slot_sheet(rows=[row])
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    add_sheet(tmp_path, "CBD", "014", [(cell_rect("H3"), "K-TN36D8")], drawing_no="401310")
    result = run_pipeline(tmp_path, cfg)
    assert next(h for h in trace(result, "LTL/S13/CH00").hops if h.hop == "DEVICE").values == ["K-TN36D4", "K-TN36D8"]
    assert "K-TN36D8" not in {t.key for t in result.traces if t.status.value == "ORPHAN"}
    assert by_rule(result, "COMP-002") == []
    assert by_rule(result, "COMP-004") == []  # K- relays are panel devices, not two field devices


def test_comp_004_two_field_devices_in_one_row(tmp_path, cfg):
    """Like U-TN56E1 and U-TN56E3 on LTL/S26/CH00."""
    row = channel_row(113, "00", "IN-0", "U-TN36D1", "B1", "D6", "LTL-JB4", ["SKEW LOAD CELL 1"])
    row.append((cell_rect("B5"), "U-TN36D5"))
    items, frame = slot_sheet(rows=[row])
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    findings = by_rule(run_pipeline(tmp_path, cfg), "COMP-004")
    assert len(findings) == 1
    assert findings[0].severity is Severity.MINOR and "U-TN36D1, U-TN36D5" in findings[0].message


def test_comp_004_device_on_safety_channels_with_different_descriptions(tmp_path, cfg):
    """Like S-TN23T4: south rail on sheet 023, and again as cow catcher contact 2 on sheet 024 beside S-TN24D2."""
    safety_sheet(tmp_path, "036", 13, [channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["SOUTH RAIL"])])
    safety_sheet(tmp_path, "037", 14, [
        channel_row(113, "00", "IN-0", "S-TN37D4", "B4", "D6", "LTL-JB2", ["COW CATCHER", "CONTACT 1"]),
        channel_row(182, "01", "IN-1", "S-TN36D4", "E4", "F6", "LTL-JB2", ["COW CATCHER", "CONTACT 2"]),
    ])
    result = run_pipeline(tmp_path, cfg)
    assert trace(result, "LTL/S14/CH01").device == "S-TN36D4"
    findings = [f for f in by_rule(result, "COMP-004") if f.subject_key == "S-TN36D4"]
    assert len(findings) == 1 and findings[0].severity is Severity.MAJOR
    assert "LTL/S14/CH01: its neighbour LTL/S14/CH00 with the same description has S-TN37D4" in findings[0].recommendation


def test_dual_channel_safety_device_is_not_branching(tmp_path, cfg):
    safety_sheet(tmp_path, "036", 13, [
        channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["SKEW EAST", "CONTACT 1"]),
        channel_row(182, "01", "IN-1", "S-TN36D4", "E4", "F6", "LTL-JB2", ["SKEW EAST", "CONTACT 2"]),
    ])
    assert by_rule(run_pipeline(tmp_path, cfg), "COMP-004") == []


def test_jb_disagreement_marks_the_trace_conflicted(tmp_path, cfg):
    basic_cbd(tmp_path)
    items, frame = slot_sheet(rows=[channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["BRAKE 9"])])
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    result = run_pipeline(tmp_path, cfg)
    t = trace(result, "LTL/S13/CH00")
    assert t.conflicted and [(c.hop, c.schematic, c.other, c.reported_by) for c in t.conflicts] == [
        ("JB", ["LTL-JB2"], ["LTL-JB1"], "PATH-002")
    ]
    assert by_rule(result, "COMP-003") == []  # PATH-002 reports it
    overall = by_group(result.coverage)["Overall"][0]
    assert (overall.traces, overall.conflicted, overall.complete_so_far) == (1, 1, 0)


def test_comp_003_cbd_cables_device_to_another_panel(tmp_path, cfg):
    basic_cbd(tmp_path, panel="RCL-PLC")
    items, frame = slot_sheet(rows=[channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB1", ["BRAKE 9"])])
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    result = run_pipeline(tmp_path, cfg)
    t = trace(result, "LTL/S13/CH00")
    assert [(c.hop, c.schematic, c.other) for c in t.conflicts] == [("PANEL", ["LTL-PLC"], ["RCL-PLC"])]
    findings = by_rule(result, "COMP-003")
    assert len(findings) == 1 and "panel LTL-PLC" in findings[0].message and "RCL-PLC" in findings[0].message


def test_coverage_groups(tmp_path, cfg):
    rows = [
        channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["BRAKE"]),
        channel_row(182, "01", "IN-1", "S-TN36F4", "E4", "F6", None, ["DOOR"]),  # no JB: a gap
        channel_row(251, "02", "IN-2", wire="H6", description=["SPARE"]),
    ]
    items, frame = slot_sheet(rows=rows)
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    result = run_pipeline(tmp_path, cfg)
    groups = by_group(result.coverage)
    overall = groups["Overall"][0]
    assert (overall.traces, overall.complete_so_far, overall.gap) == (2, 1, 1)  # the spare is left out
    assert overall.traced_pct == 0.5
    assert {r.key: r.traces for r in groups["JB / location"]} == {"LTL-JB2": 1, "(none)": 1}
    assert [(r.key, r.traces) for r in groups["Slot"]] == [("LTL/S13", 2)]
    assert {r.key for r in groups["Area"]} == {"LTL"}
