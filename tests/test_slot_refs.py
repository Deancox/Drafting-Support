"""PLC-010: notes that name PLC slots ("24VDC SUPPLY TO DIGITAL OUTPUT MODULE SLOT 22 & 23 (SHT 50 & 51)")
checked against the slot sheets they reference, and slot lists that name a slot twice."""

from __future__ import annotations

from drawing_qa.pipeline import run_pipeline
from tests.test_plc import slot_sheet
from tests.test_rules import add_sheet, by_rule, cfg  # noqa: F401  (cfg is a fixture)


def add_slot(root, sheet, slot, kind="DI"):
    part = {"DI": "1734-IB8", "DO": "1734-OB8"}[kind]
    items, frame = slot_sheet(slot=slot, part=part, points=f"POINTS: LTL.S{slot}.{kind}.00..07",
                              title=f"LTL PLC LANDWARD LEG POINT I/O SLOT {slot} {'DIGITAL OUTPUTS' if kind == 'DO' else 'DIGITAL INPUTS'}")
    add_sheet(root, "PLC", sheet, items, **frame)


def supply_note(root, text, ref):
    """Turned text as on 401103-015: the note runs up the sheet, its sheet reference on the next line (to its right)."""
    add_sheet(root, "PLC", "015", [((657, 320, 674, 401), "24VDC SUPPLY TO"), ((738, 279, 755, 443), text),
                                   ((747, 337, 770, 385), ref)])


def test_note_slots_disagree_with_the_referenced_sheets(tmp_path, cfg):
    add_slot(tmp_path, "050", 24, "DO")
    add_slot(tmp_path, "051", 25, "DO")
    supply_note(tmp_path, "DIGITAL OUTPUT MODULE SLOT 22 & 23", "(SHT 50 & 51)")
    [f] = by_rule(run_pipeline(tmp_path, cfg), "PLC-010")
    assert "SLOT 22 & 23" in f.message and "24 & 25" in f.message and "015" in f.message
    assert "SLOT 24 & 25" in f.recommendation


def test_note_that_agrees_is_quiet(tmp_path, cfg):
    add_slot(tmp_path, "036", 12)
    add_slot(tmp_path, "037", 13)
    supply_note(tmp_path, "DIGITAL INPUT MODULE SLOT 12 TO 13", "(SHT 36 TO 37)")
    assert by_rule(run_pipeline(tmp_path, cfg), "PLC-010") == []


def test_note_names_the_wrong_module_type(tmp_path, cfg):
    add_slot(tmp_path, "036", 13)
    supply_note(tmp_path, "DIGITAL OUTPUT MODULE SLOT 13", "(SHT 036)")
    [f] = by_rule(run_pipeline(tmp_path, cfg), "PLC-010")
    assert "digital output" in f.message.lower() and "DI" in f.message


def test_reference_to_a_sheet_that_is_not_a_slot_sheet_is_not_checked(tmp_path, cfg):
    add_slot(tmp_path, "050", 24, "DO")
    supply_note(tmp_path, "DIGITAL OUTPUT MODULE SLOT 22 & 23", "(SHT 50 & 51)")  # sheet 051 is not in the package
    assert by_rule(run_pipeline(tmp_path, cfg), "PLC-010") == []


def test_slot_list_naming_a_slot_twice(tmp_path, cfg):
    add_sheet(tmp_path, "PLC", "003", [((300, 200, 700, 212), "LAND LEG REMOTE I/O PANEL PLC SLOT 12,12,14 - TERMINATION DIAGRAM"),
                                       ((300, 400, 700, 412), "LAND LEG REMOTE I/O PANEL PLC SLOT 15,16,17 - TERMINATION DIAGRAM")],
              descriptor="INDEX PAGE")  # 401103-003 is the index: rules skip it, but its slot lists are real
    [f] = by_rule(run_pipeline(tmp_path, cfg), "PLC-010")
    assert "12,12,14" in f.message and "12,13,14" in f.recommendation
