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


def test_two_digit_terminal_numbers_are_not_channels(tmp_path, cfg):
    """Like the RTD sheets (401104-176): terminals 1..12 are numbered in their own column under
    "TERMINAL / CHANNEL", beside the channel numbers, so 10, 11, 12 look like addresses."""
    rows = [
        channel_row(113, "00", "1", wire="D6"),
        channel_row(182, "01", "3", wire="F6"),
        channel_row(251, "02", "10", wire="H6"),
        channel_row(320, "03", "12", wire="K6"),
        [((779, 280, 800, 294), "11")],
    ]
    items, frame = slot_sheet(part="1756-IR8I", points="POINTS: LTL.S13.RI.00..03", rows=rows)
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    ch = {c.subject_key: c.value for c in claims(run_pipeline(tmp_path, cfg), Predicate.HAS_CHANNEL)}
    assert set(ch) == {"LTL/S13/CH00", "LTL/S13/CH01", "LTL/S13/CH02", "LTL/S13/CH03"}


def test_terminal_column_as_long_as_the_address_column(tmp_path, cfg):
    """Like 401104-147: channels 24..31 beside terminals 27..34, neither with a leading zero."""
    rows = [channel_row(113 + 69 * i, f"{24 + i}", f"{27 + i}", wire=f"{'DFHKMPSU'[i]}6") for i in range(8)]
    items, frame = slot_sheet(part="1756-OB32", points="POINTS: LTL.S13.DO.24..31", rows=rows)
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    ch = {c.subject_key for c in claims(run_pipeline(tmp_path, cfg), Predicate.HAS_CHANNEL)}
    assert ch == {f"LTL/S13/CH{n}" for n in range(24, 32)}


@pytest.mark.parametrize(("label", "cell", "expected"), [
    ("C6", "C6", 0),  # drawn where it starts
    ("D6", "C6", 0),  # label above its wire
    ("D6", "D7", 0),  # on the column boundary
    ("C2", "C6", 1),  # like 401104-176: numbered from the device end, drawn at the PLC terminal
    ("K6", "C6", 1),
])
def test_wire_005_number_matches_where_it_is_drawn(tmp_path, cfg, label, cell, expected):
    add_sheet(tmp_path, "PLC", "036", LEGEND + [(cell_rect(cell, 18), label)])
    findings = by_rule(run_pipeline(tmp_path, cfg), "WIRE-005")
    assert len(findings) == expected
    if expected:
        assert f"TN36{label}" in findings[0].message and cell in findings[0].message


def test_boxed_terminal_marking_is_not_a_wire(tmp_path, cfg):
    """Like the relay terminals A1 / T1 on 401103-018: boxed, in the wire's column but rows away."""
    x0, y0, x1, y1 = cell_rect("H1", 12)
    d = 2.5  # the test PDF's text box is 1 pt larger than asked
    box = [(x0 - d, y0 - d, x1 + d, y0 - d), (x0 - d, y1 + d, x1 + d, y1 + d), (x0 - d, y0 - d, x0 - d, y1 + d), (x1 + d, y0 - d, x1 + d, y1 + d)]
    add_sheet(tmp_path, "PLC", "036", LEGEND + [((x0, y0, x1, y1), "T1")], lines=box[:3])
    add_sheet(tmp_path, "PLC", "037", LEGEND + [((x0, y0, x1, y1), "T1")], lines=box)
    wires = {c.subject_key for c in run_pipeline(tmp_path, cfg).claims if c.subject_type.value == "WIRE"}
    assert wires == {"TN36T1"}  # open on one side: still read (and reported by WIRE-005)


def test_wire_005_checks_a_transferred_wire_at_its_origin(tmp_path, cfg):
    """'16D2' on another sheet is checked where it starts: on sheet 016, at D2."""
    add_sheet(tmp_path, "PLC", "036", LEGEND + [(cell_rect("B3", 24), "16D2")])
    add_sheet(tmp_path, "PLC", "016", LEGEND + [(cell_rect("D6", 18), "D2")])
    findings = by_rule(run_pipeline(tmp_path, cfg), "WIRE-005")
    assert [f.subject_key for f in findings] == ["TN16D2"]
    assert "sh 016" in findings[0].message


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


def test_plc_006_no_slot_sheet_quiet_when_listed_sheet_is_decoded(tmp_path, cfg):
    from tests.test_shx_rules import recheck, rule_ids

    items, frame = rack_layout({13: ("1734-IB8 INPUT MODULE", ["036"]), 14: ("1734-IB8 INPUT MODULE", ["037"])})
    add_sheet(tmp_path, "PLC", "013", items, **frame)
    s_items, s_frame = slot_sheet()
    add_sheet(tmp_path, "PLC", "036", s_items, **s_frame)
    add_sheet(tmp_path, "PLC", "037")  # slot 14's sheet, its slot not read
    result = run_pipeline(tmp_path, cfg)
    assert [f for f in by_rule(result, "PLC-006") if f.subject_key == "LTL/S14"]
    assert not [f for f in rule_ids(recheck(result, cfg, lambda d: d.sheet == "037"), "PLC-006") if f.subject_key == "LTL/S14"]


def test_boxed_terminal_marking_with_a_tight_text_box(tmp_path, cfg):
    """Decoded text has a tight box (its strokes), so a relay's terminal frame sits further out (401104-020: 3.8 pt)."""
    x0, y0, x1, y1 = cell_rect("H1", 12)
    d = 4.5  # 3.5 pt outside the text box the test PDF reports
    box = [(x0 - d, y0 - d, x1 + d, y0 - d), (x0 - d, y1 + d, x1 + d, y1 + d), (x0 - d, y0 - d, x0 - d, y1 + d), (x1 + d, y0 - d, x1 + d, y1 + d)]
    add_sheet(tmp_path, "PLC", "037", LEGEND + [((x0, y0, x1, y1), "T1")], lines=box)
    assert not [c for c in run_pipeline(tmp_path, cfg).claims if c.subject_type.value == "WIRE"]


def test_points_value_beside_its_label(tmp_path, cfg):
    """Decoded sheets (401104-060) give 'POINTS:' and its value as two texts on one line."""
    items, frame = slot_sheet()
    items = [it for it in items if not it[1].startswith("POINTS")]
    items += [((881, 644, 925, 656), "POINTS:"), ((935, 644, 1010, 656), "LTL.S13.DI.00..07")]
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    result = run_pipeline(tmp_path, cfg)
    [module] = claims(result, Predicate.INSTALLED_IN)
    assert module.value["points"] == "LTL.S13.DI.00..07" and module.value["points_valid"] is True
    assert by_rule(result, "PLC-008") == []


def test_row_with_no_address_lends_its_device_to_no_channel(tmp_path, cfg):
    """A channel number that did not decode (or was not drawn) must not hand its row's device to a neighbour."""
    rows = [
        channel_row(113, "00", "IN-0", "S-TN36D4", "B4", description=["A"]),
        channel_row(182, "01", "IN-1", "S-TN36F4", "E4", description=["B"]),
        [(cell_rect("G4"), "S-TN36H4")],  # row 02 at y 251: its device, but no address
        channel_row(320, "03", "IN-3", "S-TN36L4", "K4", description=["D"]),
        channel_row(389, "04", "IN-4", "S-TN36N4", "M4", description=["E"]),
    ]
    items, frame = slot_sheet(rows=rows)
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    ch = {c.subject_key: c.value["devices"] for c in claims(run_pipeline(tmp_path, cfg), Predicate.HAS_CHANNEL)}
    assert ch["LTL/S13/CH01"] == ["S-TN36F4"] and ch["LTL/S13/CH03"] == ["S-TN36L4"]


def test_missing_row_device_close_to_the_next_address(tmp_path, cfg):
    """401104-069: CH12's number did not decode, and its device S-TL69P4 sits 0.65 of a row above CH13's number."""
    rows = [
        channel_row(113, "00", "IN-0", "S-TN36D4", "B4", description=["A"]),
        channel_row(182, "01", "IN-1", "S-TN36F4", "E4", description=["B"]),
        [(cell_rect("G4"), "S-TN36J4")],  # row 02's device: encodes row J, 45 pt above CH03's number
        channel_row(320, "03", "IN-3", "S-TN36L4", "K4", description=["D"]),
        channel_row(389, "04", "IN-4", "S-TN36N4", "M4", description=["E"]),
    ]
    items, frame = slot_sheet(rows=rows)
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    ch = {c.subject_key: c.value["devices"] for c in claims(run_pipeline(tmp_path, cfg), Predicate.HAS_CHANNEL)}
    assert ch["LTL/S13/CH03"] == ["S-TN36L4"]
