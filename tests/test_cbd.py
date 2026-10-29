"""CBD routing reader (enclosures, devices in them, cables) and the PATH rules, on drawn test sheets."""

from __future__ import annotations

from drawing_qa.model.enums import Predicate
from drawing_qa.pipeline import run_pipeline
from tests.conftest import build_pdf, sheet_frame
from tests.test_plc import cfg, channel_row, slot_sheet  # noqa: F401  (cfg is a fixture)
from tests.test_rules import add_sheet, by_rule

THIN, THICK = 0.24, 0.96


def box(x0, y0, x1, y1, width=THIN):
    return [(x0, y0, x1, y0, width), (x1, y0, x1, y1, width), (x1, y1, x0, y1, width), (x0, y1, x0, y0, width)]


def cbd_sheet(root, items, lines, sheet="015"):
    frame = sheet_frame("401310", sheet, "B", "PRELIMINARY", "LAND LEG REMOTE I/O PANEL", "CABLING BLOCK DIAGRAM")
    build_pdf(root / "CableBlocks" / f"401310-{sheet}.pdf", [[]], lines=[lines], shx=[frame + items])


def basic_cbd(root, *, device_to="JB", panel="LTL-PLC"):
    """Device S-TN36D4 (left) -> LTL-JB1 (middle box) -> LTL-PLC (panel box, right),
    or the device straight to the panel when device_to="PANEL"."""
    items = [
        ((60, 140, 110, 150), "S-TN36D4"),
        ((330, 105, 410, 115), "=K07.11+LTL-JB1"),
        ((620, 105, 700, 115), f"=K07.11+{panel}"),
        ((450, 150, 520, 158), "12C+E 1.5mm² PVC"),
        ((470, 162, 500, 170), "CXXXXX"),
    ]
    lines = box(300, 100, 420, 300) + box(600, 100, 720, 600)
    if device_to == "JB":
        lines += [(130, 160, 300, 160, THICK), (420, 160, 460, 160, THICK), (500, 160, 600, 160, THICK)]
    else:
        # below the JB box, so the cable does not pass through it
        items[0] = ((60, 380, 110, 390), "S-TN36D4")
        lines += [(130, 400, 600, 400, THICK)]
    cbd_sheet(root, items, lines)


def routes(result):
    return {(c.subject_key, c.object_key, c.value.get("spec"), c.value.get("cable_no")) for c in result.claims if c.predicate is Predicate.ROUTED}


def test_cables_between_device_jb_and_panel(tmp_path, cfg):
    basic_cbd(tmp_path)
    result = run_pipeline(tmp_path, cfg)
    assert routes(result) == {
        ("S-TN36D4", "LTL-JB1", None, None),
        ("LTL-JB1", "LTL-PLC", "12C+E 1.5mm² PVC", "CXXXXX"),  # label box gap rejoined, label attached
    }


def test_device_inside_enclosure(tmp_path, cfg):
    items = [((330, 105, 410, 115), "=K07.11+LTL-LCS1"), ((320, 150, 370, 160), "S-TN38D4")]
    cbd_sheet(tmp_path, items, box(300, 100, 420, 300))
    members = {(c.subject_key, c.object_key) for c in run_pipeline(tmp_path, cfg).claims if c.predicate is Predicate.IN_ENCLOSURE}
    assert members == {("S-TN38D4", "LTL-LCS1")}


def test_path_002_schematic_and_cbd_disagree_on_jb(tmp_path, cfg):
    """Like S-TN36D4: LTL-JB2 on schematic 036, LTL-JB1 on CBD 015."""
    basic_cbd(tmp_path)
    items, frame = slot_sheet(rows=[channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["BRAKE 9"])])
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    result = run_pipeline(tmp_path, cfg)
    trace = next(t for t in result.traces if t.key == "LTL/S13/CH00")
    assert trace.cbd_route == ["S-TN36D4", "LTL-JB1", "LTL-PLC"]
    findings = by_rule(result, "PATH-002")
    assert len(findings) == 1 and "via LTL-JB2" in findings[0].message and "via LTL-JB1" in findings[0].message


def two_jb_cbd(root):
    """Device S-TN36D4 -> LTL-JB8 (small box) -> LTL-JB1 -> LTL-PLC, like S-TN24H3 on CBD 015."""
    items = [
        ((60, 140, 110, 150), "S-TN36D4"),
        ((175, 105, 245, 115), "=K07.11+LTL-JB8"),
        ((330, 105, 410, 115), "=K07.11+LTL-JB1"),
        ((620, 105, 700, 115), "=K07.11+LTL-PLC"),
    ]
    lines = box(170, 100, 250, 300) + box(300, 100, 420, 300) + box(600, 100, 720, 600)
    lines += [(120, 160, 170, 160, THICK), (250, 160, 300, 160, THICK), (420, 160, 600, 160, THICK)]
    cbd_sheet(root, items, lines)


def test_path_002_schematic_leaves_out_a_jb_the_cbd_routes_through(tmp_path, cfg):
    """S-TN24H3: LTL-JB1 on schematic 024, LTL-JB8 then LTL-JB1 on CBD 015."""
    two_jb_cbd(tmp_path)
    items, frame = slot_sheet(rows=[channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB1", ["COW CATCHER"])])
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    [f] = by_rule(run_pipeline(tmp_path, cfg), "PATH-002")
    assert "via LTL-JB1" in f.message and "LTL-JB8, LTL-JB1" in f.message and "LTL-JB8 is not on the schematic" in f.message


def test_path_002_quiet_when_the_schematic_lists_both_jbs(tmp_path, cfg):
    """S-TN28D2: the schematic row shows LTL-JB1 and LTL-JB6, as the CBD does."""
    two_jb_cbd(tmp_path)
    row = channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB1", ["COW CATCHER"]) + [((520, 128, 552, 139), "LTL-JB8")]
    items, frame = slot_sheet(rows=[row])
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    result = run_pipeline(tmp_path, cfg)
    assert set(next(h for h in next(t for t in result.traces if t.key == "LTL/S13/CH00").hops if h.hop == "JB").values) == {"LTL-JB1", "LTL-JB8"}
    assert by_rule(result, "PATH-002") == []


def test_path_002_quiet_when_the_schematic_lists_more_stations(tmp_path, cfg):
    """S-TN22T4: an e-stop loop; the schematic row lists the other stations, the CBD only the device's own cable."""
    basic_cbd(tmp_path)
    row = channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB1", ["ESTOP"]) + [((520, 128, 552, 139), "LTL-LCS2")]
    items, frame = slot_sheet(rows=[row])
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    assert by_rule(run_pipeline(tmp_path, cfg), "PATH-002") == []


def test_direct_to_panel_is_not_a_missing_jb(tmp_path, cfg):
    """Like S-TN36R4: no JB in the schematic row, and the CBD shows a direct cable to the panel."""
    basic_cbd(tmp_path, device_to="PANEL")
    items, frame = slot_sheet(rows=[channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", None, ["DOOR CLOSED"])])
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    result = run_pipeline(tmp_path, cfg)
    trace = next(t for t in result.traces if t.key == "LTL/S13/CH00")
    assert next(h for h in trace.hops if h.hop == "JB").values == ["direct to panel"]
    assert by_rule(result, "COMP-001") == []


def test_path_001_device_on_cbd_without_route(tmp_path, cfg):
    items = [((60, 140, 110, 150), "S-TN36D4"), ((620, 105, 700, 115), "=K07.11+LTL-PLC")]
    cbd_sheet(tmp_path, items, box(600, 100, 720, 600))  # device drawn, no cable
    s_items, frame = slot_sheet(rows=[channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["BRAKE"])])
    add_sheet(tmp_path, "PLC", "036", s_items, **frame)
    assert [f.subject_key for f in by_rule(run_pipeline(tmp_path, cfg), "PATH-001")] == ["S-TN36D4"]


def test_path_001_quiet_when_a_cbd_is_decoded(tmp_path, cfg):
    from tests.test_shx_rules import recheck, rule_ids

    items = [((60, 140, 110, 150), "S-TN36D4"), ((620, 105, 700, 115), "=K07.11+LTL-PLC")]
    cbd_sheet(tmp_path, items, box(600, 100, 720, 600))
    s_items, frame = slot_sheet(rows=[channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["BRAKE"])])
    add_sheet(tmp_path, "PLC", "036", s_items, **frame)
    result = run_pipeline(tmp_path, cfg)
    assert rule_ids(result.findings, "PATH-001")
    assert not rule_ids(recheck(result, cfg, lambda d: d.doc_type.value == "CBD"), "PATH-001")


def test_stacked_separate_boxes_are_not_one_enclosure(tmp_path, cfg):
    """401310-020: a named box (SH-DB5) above a column of device symbol squares on the same right edge,
    3.5 pt apart, each tag to the left of its square. Bridging the gaps made one tall 'SH-DB5' around every
    device below it, so S-TL40H4's cable to BH-JB1 was never used."""
    items = [
        ((60, 46, 120, 56), "=K07.11+LTL-JB9"),
        ((60, 70, 95, 80), "S-TN36D4"),
        ((60, 108, 95, 118), "S-TN36F4"),
        ((330, 105, 410, 115), "=K07.11+LTL-JB1"),
    ]
    lines = box(57, 44, 127, 61) + box(99, 64.5, 127, 99.2) + box(99, 102.7, 127, 137.4) + box(300, 100, 420, 300)
    lines += [(127, 125, 300, 125, THICK)]  # S-TN36F4's box -> LTL-JB1
    cbd_sheet(tmp_path, items, lines)
    result = run_pipeline(tmp_path, cfg)
    members = {(c.subject_key, c.object_key) for c in result.claims if c.predicate is Predicate.IN_ENCLOSURE}
    assert not {m for m in members if m[1] == "LTL-JB9"}
    assert ("S-TN36F4", "LTL-JB1") in {(a, b) for a, b, _, _ in routes(result)}


def test_enclosure_with_an_inner_divider_stays_one(tmp_path, cfg):
    """A JB's own divider is one line across unbroken sides: the device below it is still in the JB."""
    items = [((60, 46, 120, 56), "=K07.11+LTL-JB9"), ((60, 108, 110, 118), "S-TN36F4")]
    lines = box(57, 44, 127, 140) + [(57, 90, 127, 90, THIN)]
    cbd_sheet(tmp_path, items, lines)
    members = {(c.subject_key, c.object_key) for c in run_pipeline(tmp_path, cfg).claims if c.predicate is Predicate.IN_ENCLOSURE}
    assert ("S-TN36F4", "LTL-JB9") in members

