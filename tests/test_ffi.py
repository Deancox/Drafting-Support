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
