"""WIRE-001 (wire number format), WIRE-003 (duplicate wire number) and PATH-004 (route legend match)."""

from __future__ import annotations

from drawing_qa.model.enums import Severity
from drawing_qa.pipeline import run_pipeline
from tests.conftest import cell_rect
from tests.test_plc import cfg, channel_row, slot_sheet  # noqa: F401  (cfg is a fixture)
from tests.test_rules import add_sheet, by_rule
from tests.test_termination import slot_term_sheet

LEGEND = ((47, 646, 84, 660), "LEGEND")


def legend_entry(i: int, designation: str, short: str):
    y = 661 + 13 * i
    return [((84, y, 157, y + 17), designation), ((56, y + 10, 88, y + 21), short)]


def schematic_with_legend(tmp_path, jb, entries):
    items, frame = slot_sheet(rows=[channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", jb, ["BRAKE"])])
    items.append(LEGEND)
    for i, (designation, short) in enumerate(entries):
        items += legend_entry(i, designation, short)
    add_sheet(tmp_path, "PLC", "036", items, **frame)


def test_wire_001_malformed_wire_number(tmp_path, cfg):
    cfg.identifiers.ffi_drawings = {"TN": "401103"}
    add_sheet(tmp_path, "PLC", "036", [(cell_rect("D4"), "TN36D"), (cell_rect("E4"), "TN36E6"), (cell_rect("F4"), "THERMISTOR")])
    findings = by_rule(run_pipeline(tmp_path, cfg), "WIRE-001")
    assert [f.subject_key for f in findings] == ["TN36D"]


def test_wire_001_needs_a_known_ffi(tmp_path, cfg):
    add_sheet(tmp_path, "PLC", "036", [(cell_rect("D4"), "TN36D")])  # no FFI mapping configured
    assert by_rule(run_pipeline(tmp_path, cfg), "WIRE-001") == []


def test_wire_003_wire_on_two_modules(tmp_path, cfg):
    slot_term_sheet(tmp_path, "211", slot=12, rows=[("1", "TN36D6"), ("2", "TN15D5")])
    slot_term_sheet(tmp_path, "212", slot=13, rows=[("1", "TN36D6"), ("2", "TN15D5")])
    findings = by_rule(run_pipeline(tmp_path, cfg), "WIRE-003")
    assert sorted(f.subject_key for f in findings) == ["TN15D5", "TN36D6"]


def test_wire_003_common_on_one_module_is_fine(tmp_path, cfg):
    slot_term_sheet(tmp_path, "211", slot=12, rows=[("1", "TN36D6"), ("2", "TN20X6"), ("3", "TN20X6")], last=3)
    assert by_rule(run_pipeline(tmp_path, cfg), "WIRE-003") == []


def test_path_004_row_jb_missing_from_legend(tmp_path, cfg):
    schematic_with_legend(tmp_path, "LTL-JB3", [("=K07.11+LTL-PLC", "LTL-PLC")])
    findings = by_rule(run_pipeline(tmp_path, cfg), "PATH-004")
    assert len(findings) == 1 and "LTL-JB3, which the legend does not list" in findings[0].message


def test_path_004_legend_matches_rows(tmp_path, cfg):
    schematic_with_legend(tmp_path, "LTL-JB3", [("=K07.11+LTL-PLC", "LTL-PLC"), ("=K07.11+LTL-JB3", "LTL-JB3")])
    assert by_rule(run_pipeline(tmp_path, cfg), "PATH-004") == []


def test_path_004_empty_legend(tmp_path, cfg):
    schematic_with_legend(tmp_path, "LTL-JB3", [])
    findings = by_rule(run_pipeline(tmp_path, cfg), "PATH-004")
    assert len(findings) == 1 and "legend on 401103 sh 036 is empty" in findings[0].message


def test_path_004_unknown_legend_entry_suggests_the_rows_location(tmp_path, cfg):
    """Like sheet 051: the legend says =K07.11+LTL-SH8 where the rows use SH-LCS8."""
    schematic_with_legend(tmp_path, "LTL-JB2", [("=K07.11+LTL-XY2", "LTL-XY2")])
    findings = by_rule(run_pipeline(tmp_path, cfg), "PATH-004")
    assert len(findings) == 1 and findings[0].severity is Severity.MAJOR
    assert "the rows use LTL-JB2" in findings[0].message and "=K07.11+LTL-JB2" in findings[0].recommendation
