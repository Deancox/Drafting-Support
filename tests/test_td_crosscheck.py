"""Termination drawings against the cable block diagrams and the schematics: the cable label reader,
CABLE-001..006, PLC-002 (descriptions), DEV-004 (I/O direction), TERM-005 / 006 (JB sheet vs slot sheet),
TERM-007 (connector pins)."""

from __future__ import annotations

import pytest

from drawing_qa.model.enums import EntityType, Predicate, Severity
from drawing_qa.pipeline import run_pipeline
from drawing_qa.rules.cable import spec_difference
from drawing_qa.rules.plc import description_difference
from tests.test_cbd import THICK, box, cbd_sheet
from tests.test_rules import by_rule
from tests.test_termination import BRAKE, TO_SLOT_13, cfg as term_cfg, jb_sheet, schematic, slot_term_sheet  # noqa: F401
from tests.test_plc import cfg as plc_cfg  # noqa: F401  (fixture used by term_cfg)

MULTICORE = ("50C+E 1.5mm2 PVC", "C93812", "12 SPARE")


@pytest.fixture
def cfg(term_cfg):  # noqa: F811
    return term_cfg


def cbd(root, cables, *, jb2=False):
    """LTL-JB1 (and LTL-JB2 below it) on the left, LTL-PLC on the right; cables: [(y, from, spec, number)],
    `from` "JB1" or "JB2". Each label box sits on its cable: spec 7 pt above it, number on it."""
    items = [((330, 105, 410, 115), "=K07.11+LTL-JB1"), ((620, 105, 700, 115), "=K07.11+LTL-PLC")]
    lines = box(300, 100, 420, 300) + box(600, 100, 720, 600)
    if jb2:
        items.append(((330, 405, 410, 415), "=K07.11+LTL-JB2"))
        lines += box(300, 400, 420, 580)
    for y, start, spec, number in cables:
        items += [((450, y - 11, 520, y - 3), spec), ((470, y - 4, 500, y + 4), number)]
        lines += [(420, y, 460, y, THICK), (500, y, 600, y, THICK)]
    cbd_sheet(root, items, lines)


def td_cables(result):
    return [c for c in result.claims if c.subject_type is EntityType.CABLE and c.predicate is Predicate.HAS_SPEC]


# --- readers ------------------------------------------------------------------------------------


def test_termination_cable_label_stack(tmp_path, cfg):
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13, spec=MULTICORE)
    (c,) = td_cables(run_pipeline(tmp_path, cfg))
    v = c.value
    assert (c.subject_key, v["text"], v["cores"], v["spares"], v["side"]) == ("C93812", "50C+E 1.5mm2 PVC", 50, 12, "panel")
    assert {"LTL-JB1", "LTL-PLC"} <= set(v["places"])  # the old panel name LTL-AUT1 resolved


def test_cbd_label_goes_to_the_nearest_cable(tmp_path, cfg):
    """Cables 17 pt apart: the spec above the lower cable is within reach of the upper one too."""
    cbd(tmp_path, [(160, "JB1", "50C+E 1.5mm² PVC", "C93812"), (177, "JB1", "6C+E 1.5mm² PVC", "C93813")])
    result = run_pipeline(tmp_path, cfg)
    got = {c.value["cable_no"]: c.value["spec"] for c in result.claims if c.predicate is Predicate.ROUTED}
    assert got == {"C93812": "50C+E 1.5mm² PVC", "C93813": "6C+E 1.5mm² PVC"}
    assert {c.subject_key for c in result.claims if c.subject_type is EntityType.CABLE} == {"C93812", "C93813"}


def test_field_device_description(tmp_path, cfg):
    extra = [((330, 92, 362, 101), "S-TN38L4"), ((370, 95, 440, 104), "LONG TRAVEL WEST")]
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13, extra=extra)
    desc = [(c.subject_key, c.value["text"]) for c in run_pipeline(tmp_path, cfg).claims if c.predicate is Predicate.DESCRIBES]
    assert desc == [("S-TN38L4", "LONG TRAVEL WEST")]


# --- CABLE rules --------------------------------------------------------------------------------


def test_spec_difference():
    assert spec_difference("10C+E 1.5mm2 PVC", "10C+E 2.5mm² PVC") == ("conductor size", Severity.MAJOR)
    assert spec_difference("4C+E 1.5mm2 PVC", "2C+E 1.5mm² PVC") == ("core count", Severity.MAJOR)
    assert spec_difference("INST 6PR 0.5mm2 O/A SCR", "INST 6PR 0.5mm² IND SCR") == ("insulation / screen", Severity.MINOR)
    assert spec_difference("50C+E 1.5mm2 PVC", "50C+E 1.5mm² PVC") is None
    assert spec_difference("2C+E 4mm2", "2C+E 4mm² PVC") is None  # a word left off one is not a difference


def test_cable_002_spec_differs_from_the_cbd(tmp_path, cfg):
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13, spec=MULTICORE)
    cbd(tmp_path, [(160, "JB1", "50C+E 2.5mm² PVC", "C93812")])
    result = run_pipeline(tmp_path, cfg)
    (f,) = by_rule(result, "CABLE-002")
    assert f.severity is Severity.MAJOR and "conductor size" in f.message
    assert not by_rule(result, "CABLE-001") and not by_rule(result, "CABLE-003") and not by_rule(result, "CABLE-004")


def test_cable_001_names_the_cbd_cable_between_the_same_jb_and_panel(tmp_path, cfg):
    """C92807 on the terminations, C93807 on the CBD: one finding, not a CABLE-004 for C93807 as well."""
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13, spec=("12C+E 1.5mm2 PVC", "C92807", "1 SPARE"))
    cbd(tmp_path, [(160, "JB1", "12C+E 1.5mm² PVC", "C93807")])
    result = run_pipeline(tmp_path, cfg)
    (f,) = by_rule(result, "CABLE-001")
    assert "C92807" in f.message and "shows C93807 between LTL-JB1 and LTL-PLC" in f.message
    assert not by_rule(result, "CABLE-004")
    roles = {e.role: e.source.drawing_id.split("/")[0] for e in f.evidence}
    assert roles == {"A": "Terminations", "B": "CableBlocks"}  # both places: the C92807 label and the CBD's C93807


def test_cable_003_multicore_to_another_jb_on_the_cbd(tmp_path, cfg):
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13, spec=MULTICORE)
    cbd(tmp_path, [(450, "JB2", "50C+E 1.5mm² PVC", "C93812")], jb2=True)
    (f,) = by_rule(run_pipeline(tmp_path, cfg), "CABLE-003")
    assert "from LTL-JB1 to LTL-PLC on the termination drawing" in f.message and "LTL-JB2" in f.message


def test_cable_004_cbd_cable_missing_from_the_terminations(tmp_path, cfg):
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13, spec=MULTICORE)
    cbd(tmp_path, [(160, "JB1", "50C+E 1.5mm² PVC", "C93812"), (200, "JB1", "6C+E 1.5mm² PVC", "C93813")])
    (f,) = by_rule(run_pipeline(tmp_path, cfg), "CABLE-004")
    assert f.subject_key == "C93813" and "not drawn on the termination sheets of LTL-JB1" in f.message


def test_cable_005_one_number_two_cables(tmp_path, cfg):
    cbd(tmp_path, [(160, "JB1", "6C+E 1.5mm² PVC", "C93813"), (450, "JB2", "6C+E 1.5mm² PVC", "C93813")], jb2=True)
    (f,) = by_rule(run_pipeline(tmp_path, cfg), "CABLE-005")
    assert f.subject_key == "C93813" and "2 different cables" in f.message


# --- PLC-002 descriptions -----------------------------------------------------------------------


def test_description_difference():
    plc = ["LONG TRAVEL LAND LEG ACCESS LCS LONG / TRAVEL PAUSED FOR BOARDING LAMP / (1 = ILLUMINATED)"]
    assert description_difference("SOUTH RAIL CLAMP / LOCAL MODE INDICATION LAMP", plc) == ["SOUTH", "RAIL", "CLAMP", "MODE"]
    plc = ["LONG TRAVEL LAND LEG LCS - / SKEW CORRECT LAND LEG PUSH BUTTON / (1 = SKEW CORRECT)"]
    assert description_difference("SKEW CORRECTION LAND LEG", plc) is None  # shortened, CORRECTION ~ CORRECT
    plc = ["LONG TRAVEL LAND LEG / SKEW LOAD CELL 2 / (4-20mA = 0-250 LB)"]
    assert description_difference("LONG TRAVEL (LAND LEG) / SKEW LOAD CELL 1", plc) == ["1"]


def test_plc_002_termination_description_differs(tmp_path, cfg):
    schematic(tmp_path)  # S-TN36D4 on LTL/S13/CH00, described "BRAKE 9"
    extra = [((330, 92, 362, 101), "S-TN36D4"), ((370, 95, 440, 104), "STORM PIN LOWERED")]
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13, extra=extra)
    (f,) = by_rule(run_pipeline(tmp_path, cfg), "PLC-002")
    assert "'STORM PIN LOWERED' on the termination drawing" in f.message and "BRAKE 9" in f.message


def test_plc_002_quiet_when_the_descriptions_agree(tmp_path, cfg):
    schematic(tmp_path)
    extra = [((330, 92, 362, 101), "S-TN36D4"), ((370, 95, 440, 104), "BRAKE 9")]
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13, extra=extra)
    assert by_rule(run_pipeline(tmp_path, cfg), "PLC-002") == []


# --- DEV-004 I/O direction ----------------------------------------------------------------------


def test_dev_004_switch_sent_to_an_output_slot(tmp_path, cfg):
    cfg.identifiers.io_direction = {"S": "IN", "H": "OUT"}
    jb_sheet(tmp_path, blocks=BRAKE, destinations=[("LTL-AUT1", "DO SLOT 13", "5", "6")])
    (f,) = by_rule(run_pipeline(tmp_path, cfg), "DEV-004")
    assert f.subject_key == "S-TN36D4" and "should be an input" in f.message and "DO SLOT 13" in f.message


def test_dev_004_quiet_without_config_or_when_directions_agree(tmp_path, cfg):
    jb_sheet(tmp_path, blocks=BRAKE, destinations=[("LTL-AUT1", "DO SLOT 13", "5", "6")])
    assert by_rule(run_pipeline(tmp_path, cfg), "DEV-004") == []  # io_direction empty: no check
    cfg.identifiers.io_direction = {"S": "IN"}
    other = tmp_path / "di"
    jb_sheet(other, blocks=BRAKE, destinations=TO_SLOT_13)
    assert by_rule(run_pipeline(other, cfg), "DEV-004") == []


# --- TERM-005 / 006: JB sheet vs slot sheet -----------------------------------------------------

REF_216 = [((72, 95, 109, 104), "(REF TERM. 216)")]  # under "DI SLOT 13" of TO_SLOT_13


def test_term_005_destination_refers_to_the_wrong_slot_sheet(tmp_path, cfg):
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13, extra=REF_216)
    slot_term_sheet(tmp_path, slot=13, rows=[("1", "TN36D6"), ("2", "TN15D5")], ref="0203")  # on sheet 211
    result = run_pipeline(tmp_path, cfg)
    dest = next(c for c in result.claims if c.predicate is Predicate.DESTINED_FOR and c.subject_type is EntityType.TERMINAL)
    assert dest.value["ref_sheet"] == "216"
    (f,) = by_rule(result, "TERM-005")
    assert "refer to sheet 216" in f.message and "on sheet 211" in f.message
    assert by_rule(result, "TERM-006") == []  # TN36D6 is on slot 13's strip


def test_term_006_jb_wire_not_on_the_slot_strip(tmp_path, cfg):
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13)
    slot_term_sheet(tmp_path, slot=13, rows=[("1", "TN36D7"), ("2", "TN15D5")], ref="0203")
    findings = by_rule(run_pipeline(tmp_path, cfg), "TERM-006")
    assert [f.subject_key for f in findings] == ["TN36D6"] and "has no TN36D6" in findings[0].message


# --- false alarms found reviewing the 401103 Land Leg register (2026-11-11) ---------------------


def test_grid_body_bottom_is_half_a_row_below_the_last_row():
    from drawing_qa.model.source import GridFrame

    grid = GridFrame(rows=[("A", 50.0), ("B", 80.0), ("C", 110.0)], cols=[("0", 40.0), ("1", 140.0)])
    assert grid.body_bottom == 125.0  # Y6 / Y7 in the last row are drawing, not title block
    assert GridFrame(rows=[("A", 50.0)], cols=[]).body_bottom is None


def test_comp_001_no_jb_expected_for_a_panel_relay(tmp_path, cfg):
    from tests.test_plc import channel_row, slot_sheet
    from tests.test_rules import add_sheet

    rows = [channel_row(113, "00", "IN-0", "K-TN36D4", "B4", "D6", None, ["MOTOR THERMISTOR RELAY"]),
            channel_row(182, "01", "IN-1", "S-TN36F4", "E4", "F6", None, ["BRAKE SWITCH"])]
    items, frame = slot_sheet(rows=rows)
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    devices = [f.message.split("(", 1)[1].split(",")[0] for f in by_rule(run_pipeline(tmp_path, cfg), "COMP-001")]
    assert devices == ["S-TN36F4"]  # the field switch needs a JB in its row; the panel relay does not


def test_cable_004_quiet_for_the_cbd_number_cable_001_names(tmp_path, cfg):
    """C939890 on the terminations is CABLE-001 ('the cable block diagrams have C93890'), not also a CABLE-004."""
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13, spec=MULTICORE,
             extra=[((420, 120, 450, 129), "C939890")])
    cbd(tmp_path, [(160, "JB1", "50C+E 1.5mm² PVC", "C93812"), (200, "JB1", "4C+E 1.5mm² PVC", "C93890")])
    result = run_pipeline(tmp_path, cfg)
    (f,) = by_rule(result, "CABLE-001")
    assert f.subject_key == "C939890"
    assert [e.source.drawing_id.split("/")[0] for e in f.evidence if e.role == "B"] == ["CableBlocks"]
    assert by_rule(result, "CABLE-004") == []


# --- missed issues found reviewing the 401103 Land Leg register (2026-11-11) --------------------


def test_cable_006_spare_count_differs_between_jb_and_slot_sheets(tmp_path, cfg):
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13, spec=MULTICORE)  # 12 SPARE on 203
    slot_term_sheet(tmp_path, slot=13, rows=[("1", "TN36D6"), ("2", "TN15D5")], cable=("C93812", "19 SPARE"))
    result = run_pipeline(tmp_path, cfg)
    slot = next(c for c in td_cables(result) if c.value["side"] == "slot")
    assert (slot.subject_key, slot.value["spares"], slot.value["places"]) == ("C93812", 19, ["LTL-JB1"])
    (f,) = by_rule(result, "CABLE-006")
    assert f.subject_key == "C93812" and "12 SPARE on 401103 sh 203" in f.message and "19 SPARE on 401103 sh 211" in f.message
    assert by_rule(result, "CABLE-001") == []  # the slot sheet's labels are only for the spare and spec checks


def test_cable_006_quiet_when_the_counts_agree(tmp_path, cfg):
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13, spec=MULTICORE)
    slot_term_sheet(tmp_path, slot=13, rows=[("1", "TN36D6"), ("2", "TN15D5")], cable=("C93812", "12 SPARES"))
    assert by_rule(run_pipeline(tmp_path, cfg), "CABLE-006") == []


def _pins(tmp_path, cfg, schematic_pin, td_pin):
    from tests.test_plc import channel_row, slot_sheet
    from tests.test_rules import add_sheet

    row = channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB1", ["BRAKE"]) + [((680, 116, 705, 125), f"PIN {schematic_pin}")]
    items, frame = slot_sheet(rows=[row])
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13,
             extra=[((420, 120, 450, 129), "TN36D6"), ((460, 121, 485, 130), f"PIN {td_pin}")])
    return run_pipeline(tmp_path, cfg)


def test_term_007_connector_pin_differs(tmp_path, cfg):
    result = _pins(tmp_path, cfg, "D", "A")
    pins = {(c.parser.value, c.subject_key, c.value["pin"]) for c in result.claims if c.predicate is Predicate.AT_PIN}
    assert pins == {("PLC", "TN36D6", "D"), ("TERM", "TN36D6", "A")}
    (f,) = by_rule(result, "TERM-007")
    assert "TN36D6 on PIN D" in f.message and "TN36D6 on PIN A" in f.message


def test_term_007_quiet_when_the_pins_agree(tmp_path, cfg):
    assert by_rule(_pins(tmp_path, cfg, "A", "A"), "TERM-007") == []


# --- second pass over the 401103 Land Leg register (2026-11-11) ---------------------------------


def test_wire_004_keeps_a_device_terminal_just_outside_its_destination_bracket(tmp_path, cfg):
    """401103-207: the DI SLOT 14 bracket stops one row short; LTL-JB3:4 (TN37F6) is still the device's."""
    jb_sheet(tmp_path, blocks=BRAKE, destinations=[("LTL-AUT1", "DI SLOT 13", "5", "5")])
    schematic(tmp_path)
    result = run_pipeline(tmp_path, cfg)
    t = next(t for t in result.traces if t.key == "LTL/S13/CH00")
    assert next(h for h in t.hops if h.hop == "JB_TERMINAL").values == ["LTL-JB1:5", "LTL-JB1:6"]
    assert by_rule(result, "WIRE-004") == []


def test_core_marks_are_not_block_headers():
    from drawing_qa.parsers.termination.jb_strip import _CORE_MARK

    assert all(_CORE_MARK.match(t) for t in ("1WH", "1BK", "12BU", "BR", "GNYE"))
    assert not any(_CORE_MARK.match(t) for t in ("R-TH158V4", "SCR", "S-TN36D4", "WHEEL"))


# --- system map: the termination drawings ------------------------------------------------------


def test_system_map_shows_terminals_cables_and_the_termination_jb(tmp_path, cfg):
    from drawing_qa.report.system_map import build_map

    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13, spec=("50C+E 1.5mm2 PVC", "C92812", "12 SPARE"))
    schematic(tmp_path, jb="LTL-JB2")  # the schematic sends S-TN36D4 through LTL-JB2; the terminations land it in LTL-JB1
    cbd(tmp_path, [(160, "JB1", "50C+E 1.5mm² PVC", "C93812")])
    m = build_map(run_pipeline(tmp_path, cfg))
    node = {n["key"]: n for n in m["nodes"]}
    assert node["LTL-JB1"]["column"] == 2 and node["LTL-JB1"]["on_term"] is True  # shown though only the terminations name it
    assert node["LTL-JB1"]["strip_sheets"] == ["401103 sh 203"]
    cables = {c["number"]: c["on"] for c in node["LTL-JB1"]["cables"]}
    assert cables == {"C92812": ["termination"], "C93812": ["CBD"]}
    [ch] = node["S-TN36D4"]["channels"]
    assert ch["terminals"] == ["LTL-JB1:5", "LTL-JB1:6"] and ch["td_jbs"] == ["LTL-JB1"] and ch["jbs"] == ["LTL-JB2"]
    assert node["S-TN36D4"]["on_term"] is True
    ids = {n["id"]: n["key"] for n in m["nodes"]}
    states = {(ids[l["src"]], ids[l["dst"]]): l["state"] for l in m["links"]}
    assert states[("LTL-JB1", "S-TN36D4")] == "conflict"  # COMP-003: the drawings disagree on its JB
