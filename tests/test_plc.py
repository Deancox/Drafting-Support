"""PLC parser (rack layout, slot sheets, abbreviated wires) and the PLC / WIRE rules."""

from __future__ import annotations

import pytest

from drawing_qa.config import Settings
from drawing_qa.model.enums import Predicate
from drawing_qa.pipeline import run_pipeline
from tests.conftest import cell_rect
from tests.test_rules import add_sheet, by_rule

LEGEND = [
    ((1032, 47, 1092, 59), "WIRE NUMBER"),
    ((1108, 53, 1126, 67), "TN"),
    ((1032, 54, 1085, 71), "FIRST FIELD"),
    ((1032, 64, 1079, 76), "IDENTIFIER"),
]
COLUMNS = [
    ((792, 53, 847, 67), "TERMINAL"),
    ((792, 68, 854, 89), "/ADDRESS"),
    ((881, 57, 958, 79), "DESCRIPTION"),
]


def header(part="1734-IB8", rack_line="RACK N° LTL SLOT N°13", points="POINTS: LTL.S13.DI.00..07"):
    return [((881, 610, 1010, 622), f"TYPE: {part}"), ((881, 626, 1010, 640), rack_line), ((881, 644, 1010, 656), points)]


def channel_row(y, address, terminal, device=None, device_cell=None, wire=None, jb=None, description=()):
    items = [((801, y, 842, y + 22), address), ((779, y + 6, 800, y + 20), terminal)]
    if device:
        items.append((cell_rect(device_cell), device))
    if wire:
        items.append(((725, y + 1, 743, y + 13), wire))
    if jb:
        items.append(((599, y + 15, 631, y + 26), jb))
    for i, text in enumerate(description):
        items.append(((881, y + 3 + 14 * i, 1020, y + 15 + 14 * i), text))
    return items


def slot_sheet(sheet="036", slot=13, part="1734-IB8", title=None, points=None, rows=None):
    rows = rows if rows is not None else [
        channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["LONG TRAVEL BRAKE", "RELEASED SWITCH"]),
        channel_row(182, "01", "IN-1", wire="F6", description=["SPARE"]),
    ]
    items = LEGEND + COLUMNS + header(part, f"RACK N° LTL SLOT N°{slot}", points or f"POINTS: LTL.S{slot}.DI.00..07")
    for r in rows:
        items += r
    return items, dict(title=title or f"LTL PLC LANDWARD LEG POINT I/O SLOT {slot} DIGITAL INPUT", descriptor="SCHEMATIC DIAGRAM")


def rack_layout(slots: dict[int, tuple[str, list[str]]]):
    """slots: slot -> (module label, [sheet numbers])"""
    items = [((94, 151, 133, 165), "SLOT No."), ((71, 558, 115, 573), "SHEET No.")]
    for slot, (label, sheets) in slots.items():
        x = 200 + 40 * slot
        items.append(((x - 5, 151, x + 5, 162), str(slot)))
        items.append(((x - 5, 198, x + 5, 290), label))
        for s in sheets:
            items.append(((x - 12, 570, x + 12, 586), s))
    return items, dict(title="LTL PLC LANDWARD LEG POINT I/O RACK LAYOUT", descriptor="SCHEMATIC DIAGRAM")


@pytest.fixture
def cfg() -> Settings:
    s = Settings()
    s.identifiers.areas = ["LTL", "RCL", "SPL", "EER1"]
    s.identifiers.sheet = r"\d{2,3}"
    return s


def claims(result, predicate):
    return [c for c in result.claims if c.predicate is predicate]


# --- parser -------------------------------------------------------------------


def test_slot_sheet_module_and_channels(tmp_path, cfg):
    items, frame = slot_sheet()
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    result = run_pipeline(tmp_path, cfg)

    [module] = claims(result, Predicate.INSTALLED_IN)
    assert module.subject_key == "LTL/S13"
    assert (module.value["part"], module.value["module_type"], module.value["channels"]) == ("1734-IB8", "DI", 8)
    assert module.value["points_valid"] is True

    ch = {c.subject_key: c.value for c in claims(result, Predicate.HAS_CHANNEL)}
    assert set(ch) == {"LTL/S13/CH00", "LTL/S13/CH01"}
    c0 = ch["LTL/S13/CH00"]
    assert c0["devices"] == ["S-TN36D4"]  # label drawn a row above, assigned by its encoded row D
    assert c0["wires"] == ["TN36D6"]  # "D6" expanded with the sheet's FFI legend
    assert c0["jbs"] == ["LTL-JB2"]
    assert c0["module_terminals"] == ["IN-0"]
    assert c0["description"] == "LONG TRAVEL BRAKE / RELEASED SWITCH"
    assert ch["LTL/S13/CH01"]["spare"] is True
    wired = {(c.subject_key, c.object_key) for c in claims(result, Predicate.WIRED_TO)}
    assert ("LTL/S13/CH00", "S-TN36D4") in wired and ("LTL/S13/CH00", "LTL-JB2") in wired


def test_abbreviated_wires(tmp_path, cfg):
    items = LEGEND + [
        (cell_rect("C6", 18), "D6"),  # same-sheet wire, next to its cell
        (cell_rect("C2", 18), "K2"),  # same column, long vertical run (supply sheet)
        (cell_rect("D6", 18), "T0"),  # module terminal: neither near T0 nor in its row/column
        (cell_rect("B1", 24), "15D5"),  # wire from sheet 015
    ]
    add_sheet(tmp_path, "PLC", "036", items)
    wires = {c.subject_key: c.value["abbreviated"] for c in run_pipeline(tmp_path, cfg).claims if c.subject_type.value == "WIRE"}
    assert wires == {"TN36D6": "D6", "TN36K2": "K2", "TN15D5": "15D5"}


def test_no_wire_expansion_without_ffi_legend(tmp_path, cfg):
    add_sheet(tmp_path, "PLC", "036", [(cell_rect("C6", 18), "D6")])
    assert not [c for c in run_pipeline(tmp_path, cfg).claims if c.subject_type.value == "WIRE"]


def test_safety_test_output_rows_are_not_channels(tmp_path, cfg):
    rows = [
        channel_row(113, "00", "IN-0", wire="D6"),
        channel_row(150, "10", "T0", wire="E6"),
        channel_row(190, "01", "IN-1", wire="F6"),
    ]
    items, frame = slot_sheet(rows=rows)
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    result = run_pipeline(tmp_path, cfg)
    ch = {c.subject_key: c.value for c in claims(result, Predicate.HAS_CHANNEL)}
    assert set(ch) == {"LTL/S13/CH00", "LTL/S13/CH01"}
    assert "TN36E6" not in ch["LTL/S13/CH00"]["wires"]  # the T0 row keeps its own wire


def test_rack_layout(tmp_path, cfg):
    items, frame = rack_layout({1: ("1734-IB8S SAFETY INPUT MODULE", ["020"]), 13: ("1734-IB8 INPUT MODULE", ["036"])})
    add_sheet(tmp_path, "PLC", "013", items, **frame)
    result = run_pipeline(tmp_path, cfg)
    modules = {c.subject_key: c.value["part"] for c in claims(result, Predicate.INSTALLED_IN)}
    assert modules == {"LTL/S1": "1734-IB8S", "LTL/S13": "1734-IB8"}
    sheets = {(c.subject_key, c.value["sheet"]) for c in claims(result, Predicate.SLOT_SHEET)}
    assert sheets == {("LTL/S1", 20), ("LTL/S13", 36)}


# --- rules --------------------------------------------------------------------


def test_plc_007_rack_layout_lists_two_sheets(tmp_path, cfg):
    """Like slot 20 on 401103-013, which shows both 043 and 045."""
    items, frame = rack_layout({13: ("1734-IB8 INPUT MODULE", ["036", "045"])})
    add_sheet(tmp_path, "PLC", "013", items, **frame)
    s_items, s_frame = slot_sheet()
    add_sheet(tmp_path, "PLC", "036", s_items, **s_frame)
    findings = by_rule(run_pipeline(tmp_path, cfg), "PLC-007")
    assert len(findings) == 1 and "036, 045" in findings[0].message


def test_plc_007_slot_number_disagreement(tmp_path, cfg):
    items, frame = slot_sheet(title="LTL PLC LANDWARD LEG POINT I/O SLOT 14 DIGITAL INPUT")
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    findings = by_rule(run_pipeline(tmp_path, cfg), "PLC-007")
    assert len(findings) == 1 and "title 14" in findings[0].message


def test_plc_006_rack_layout_and_slot_sheet_disagree(tmp_path, cfg):
    items, frame = rack_layout({13: ("1734-OB8 OUTPUT MODULE", ["036"])})
    add_sheet(tmp_path, "PLC", "013", items, **frame)
    s_items, s_frame = slot_sheet()
    add_sheet(tmp_path, "PLC", "036", s_items, **s_frame)
    findings = by_rule(run_pipeline(tmp_path, cfg), "PLC-006")
    assert len(findings) == 1
    assert "rack layout" in findings[0].message and "1734-OB8" in findings[0].message


def test_plc_008_points_notation(tmp_path, cfg):
    items, frame = slot_sheet(points="POINTS: LTL.S13DI.00..07")
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    findings = by_rule(run_pipeline(tmp_path, cfg), "PLC-008")
    assert [f.recommendation for f in findings] == ["Write as LTL.S13.DI.00..07"]


def test_plc_004_safety_module_on_standard_sheet(tmp_path, cfg):
    items, frame = slot_sheet(part="1734-IB8S")
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    assert len(by_rule(run_pipeline(tmp_path, cfg), "PLC-004")) == 1


def test_plc_005_points_beyond_capacity(tmp_path, cfg):
    items, frame = slot_sheet(part="1734-IE4C", points="POINTS: LTL.S13.AI.00..07")
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    assert by_rule(run_pipeline(tmp_path, cfg), "PLC-005")


def test_plc_003_device_on_two_standard_channels(tmp_path, cfg):
    rows = [
        channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6"),
        channel_row(182, "01", "IN-1", wire="F6"),
    ]
    items, frame = slot_sheet(rows=rows)
    items.append((cell_rect("E4"), "S-TN36D4"))  # same device label again in the CH01 row
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    # The second label is a reference on its home sheet, assigned by its encoded row (D) -> still CH00.
    assert by_rule(run_pipeline(tmp_path, cfg), "PLC-003") == []


def test_ffi_mapping_routes_home_sheets(tmp_path, cfg):
    """TN -> 401103 is checked; TV is a separate package (listed); TH is unmapped (asked for)."""
    cfg.identifiers.ffi_drawings = {"TN": "401103", "TV": "EXTERNAL"}
    items, frame = slot_sheet()
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    add_sheet(tmp_path, "PLC", "015", [(cell_rect("C0"), "TV20T6"), (cell_rect("D0"), "R-TH157V0"), (cell_rect("E0"), "TN36E6")])
    result = run_pipeline(tmp_path, cfg)
    assert [f.subject_key for f in by_rule(result, "WIRE-002")] == ["TN36E6"]
    external = {f.subject_key: f for f in by_rule(result, "DRW-009")}
    assert set(external) == {"TV", "TH"}
    assert external["TV"].severity.value == "INFO" and "Separate document" in external["TV"].message
    assert external["TH"].severity.value == "MINOR" and "ffi_drawings" in external["TH"].recommendation


def test_ffi_mapped_to_drawing_not_in_package_is_external(tmp_path, cfg):
    cfg.identifiers.ffi_drawings = {"TN": "401103", "TV": "401999"}
    add_sheet(tmp_path, "PLC", "015", [(cell_rect("C0"), "TV20T6")])
    assert [f.subject_key for f in by_rule(run_pipeline(tmp_path, cfg), "DRW-009")] == ["TV"]


def test_wire_002_wire_missing_from_home_sheet(tmp_path, cfg):
    items, frame = slot_sheet()
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    add_sheet(tmp_path, "TERM", "203", [(cell_rect("D2"), "TN36E6"), (cell_rect("E2"), "TN36D6")])
    findings = by_rule(run_pipeline(tmp_path, cfg), "WIRE-002")
    assert [f.subject_key for f in findings] == ["TN36E6"]
    assert "TN36D6" in findings[0].message
