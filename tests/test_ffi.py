"""FFI -> drawing mapping learned from the schematic sheets' "WIRE NUMBER / FIRST FIELD IDENTIFIER" box."""

from __future__ import annotations

from drawing_qa.model.enums import IssueCode
from drawing_qa.pipeline import run_pipeline
from tests.conftest import cell_rect
from tests.test_plc import cfg  # noqa: F401  (fixture)
from tests.test_rules import add_sheet, by_rule


def ffi_box(ffi):
    return [((1060, 60, 1120, 70), "FIRST FIELD"), ((1140, 58, 1160, 74), ffi)]


def ffi_issues(result):
    return [i.message for i in result.issues if i.code is IssueCode.FFI_MAPPING]


def test_mapping_is_learned_from_the_ffi_box(tmp_path, cfg):
    add_sheet(tmp_path, "PLC", "036", ffi_box("TH") + [(cell_rect("D4"), "S-TH36D4")], drawing_no="401002")
    result = run_pipeline(tmp_path, cfg)
    assert result.ffi_map["TH"] == {"drawing": "401002", "source": "sheets"}
    assert ffi_issues(result) == []


def test_sheets_override_a_stale_external_and_say_so(tmp_path, cfg):
    """The 401103 config says TH is a separate package; in the 401002 package TH is its own FFI."""
    cfg.identifiers.ffi_drawings = {"TN": "401103", "TH": "EXTERNAL"}
    add_sheet(tmp_path, "PLC", "036", ffi_box("TH") + [(cell_rect("D4"), "S-TH36D4")], drawing_no="401002")
    add_sheet(tmp_path, "CBD", "015", [(cell_rect("C0"), "S-TH36D8")], drawing_no="401310")
    result = run_pipeline(tmp_path, cfg)
    assert result.ffi_map["TH"]["drawing"] == "401002"
    issues = ffi_issues(result)
    assert len(issues) == 1 and 'Set TH = "401002"' in issues[0]
    # TH tags are now checked: the CBD's S-TH36D8 is not on 401002 sheet 036
    assert [f.subject_key for f in by_rule(result, "TAG-003")] == ["S-TH36D8"]


def test_mapping_to_a_drawing_not_in_the_package_is_loud(tmp_path, cfg):
    cfg.identifiers.ffi_drawings = {"TN": "401103"}
    add_sheet(tmp_path, "PLC", "036", [(cell_rect("D4"), "S-TN36D4")], drawing_no="401105")  # no FFI box
    result = run_pipeline(tmp_path, cfg)
    issues = ffi_issues(result)
    assert len(issues) == 1 and "401103, which is not in this package" in issues[0] and "not checked" in issues[0]
    assert result.ffi_map["TN"]["source"] == "config, drawing not supplied"


def test_agreeing_config_and_external_ffis_raise_nothing(tmp_path, cfg):
    cfg.identifiers.ffi_drawings = {"TN": "401103", "TV": "EXTERNAL"}
    add_sheet(tmp_path, "PLC", "036", ffi_box("TN") + [(cell_rect("D4"), "S-TN36D4"), (cell_rect("E4"), "TV20T6")])
    result = run_pipeline(tmp_path, cfg)
    assert ffi_issues(result) == []
    assert result.ffi_map["TN"]["source"] == "sheets, config" and result.ffi_map["TV"]["drawing"] == "EXTERNAL"


# --- TAG-008: a reference whose FFI is one letter off a tag drawn in this package ---------------


def _typo_package(tmp_path, cfg, refs):
    cfg.identifiers.ffi_drawings = {"TN": "401103", "TH": "EXTERNAL", "HV": "EXTERNAL"}
    add_sheet(tmp_path, "PLC", "061", [(cell_rect("H2"), "B-TN61H2"), (cell_rect("K2"), "B-TN61K2")])
    add_sheet(tmp_path, "CBD", "016", [(cell_rect(f"C{i}"), r) for i, r in enumerate(refs)], drawing_no="401310")
    return run_pipeline(tmp_path, cfg)


def test_reference_one_ffi_letter_off_a_drawn_tag_is_a_probable_typo(tmp_path, cfg):
    """401310 sh 016 references B-TH61H2 (TH: a separate package); 401103 sh 061 draws B-TN61H2."""
    result = _typo_package(tmp_path, cfg, ["B-TH61H2", "R-TH157V0"])
    [f] = by_rule(result, "TAG-008")
    assert f.subject_key == "B-TH61H2"
    assert "B-TN61H2" in f.message and "401103 sh 061" in f.message and "B-TN61H2" in f.recommendation
    [external] = [f for f in by_rule(result, "DRW-009") if f.subject_key == "TH"]
    assert "R-TH157V0" in external.message and "B-TH61H2" not in external.message


def test_genuine_separate_document_reference_is_left_alone(tmp_path, cfg):
    result = _typo_package(tmp_path, cfg, ["R-TH157V0"])
    assert by_rule(result, "TAG-008") == []


def test_ffi_two_letters_off_is_not_a_typo(tmp_path, cfg):
    result = _typo_package(tmp_path, cfg, ["B-HV61H2"])
    assert by_rule(result, "TAG-008") == []


def test_match_must_be_drawn_on_its_home_sheet(tmp_path, cfg):
    """B-TN61K5 is only mentioned on another sheet (CBD 015), not drawn on 061: no evidence of what was meant."""
    add_sheet(tmp_path, "CBD", "015", [(cell_rect("D4"), "B-TN61K5")], drawing_no="401310")
    result = _typo_package(tmp_path, cfg, ["B-TH61K5"])
    assert by_rule(result, "TAG-008") == []


def test_wire_numbers_are_not_matched(tmp_path, cfg):
    """TV20T6 on 401103 sh 015 is the DB3 supply from a separate document; 401103 sh 020 has its own TN20T6.
    A wire number is only a sheet and a grid, so two drawings' wires often share one: not a typo."""
    from tests.test_plc import LEGEND

    cfg.identifiers.ffi_drawings = {"TN": "401103", "TV": "EXTERNAL"}
    add_sheet(tmp_path, "PLC", "020", LEGEND + [(cell_rect("T6", 18), "T6")])
    add_sheet(tmp_path, "PLC", "015", LEGEND + [(cell_rect("C0", 30), "TV20T6")])
    result = run_pipeline(tmp_path, cfg)
    assert by_rule(result, "TAG-008") == []
    assert "TV20T6" in next(f for f in by_rule(result, "DRW-009") if f.subject_key == "TV").message
