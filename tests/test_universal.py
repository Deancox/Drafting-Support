"""Package-independent behaviour: the channel description prefix is detected, and a rack's assumed
panel that no drawing shows is a warning."""

from __future__ import annotations

from drawing_qa.common.descriptions import detect_prefix, heading, without_prefix
from drawing_qa.model.enums import IssueCode
from drawing_qa.pipeline import run_pipeline
from tests.conftest import cell_rect
from tests.test_plc import cfg, channel_row, slot_sheet  # noqa: F401  (cfg is a fixture)
from tests.test_rules import add_sheet


def test_project_prefix_is_detected_not_assumed():
    descriptions = [
        "07.11 SHIPLOADER / LONG TRAVEL BRAKE / RELEASED",
        "07.11 SHIPLOADER / BOOM CONVEYOR / RUNNING",
        "07.11 SHIPLOADER / LUFFING / AT LIMIT",
        "07.11 SHIPLOADER / SLEW / FAULT",
        "SPARE / SPARE",
        "COMMON / 24VDC",
    ]
    assert detect_prefix(descriptions) == "07.11 SHIPLOADER"
    other_project = [d.replace("07.11 SHIPLOADER", "K04 STACKER RECLAIMER") for d in descriptions]
    assert detect_prefix(other_project) == "K04 STACKER RECLAIMER"
    assert heading("07.11 SHIPLOADER / LONG TRAVEL BRAKE / RELEASED", "07.11 SHIPLOADER") == "LONG TRAVEL BRAKE"
    assert without_prefix("07.11 SHIPLOADER / SLEW / FAULT", "07.11 SHIPLOADER") == "SLEW / FAULT"


def test_a_shared_heading_is_not_a_prefix():
    """Two contacts of one device share their first line; that line is the heading."""
    assert detect_prefix(["SKEW EAST / CONTACT 1", "SKEW EAST / CONTACT 2"]) == ""
    assert detect_prefix(["SKEW EAST / CONTACT 1"] * 3 + ["SKEW EAST / CONTACT 2"] * 3) == ""


def test_prefix_can_be_set_in_config(tmp_path, cfg):
    cfg.plc.description_prefix = "LONG TRAVEL"
    items, frame = slot_sheet(rows=[channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["LONG TRAVEL", "BRAKE 9"])])
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    assert run_pipeline(tmp_path, cfg).model.description_prefix == "LONG TRAVEL"


def test_rack_in_a_panel_no_drawing_shows_is_a_warning(tmp_path, cfg):
    items, frame = slot_sheet(rows=[channel_row(113, "00", "IN-0", "S-TN36D4", "B4", "D6", "LTL-JB2", ["BRAKE"])])
    add_sheet(tmp_path, "PLC", "036", items + [(cell_rect("Y8", 120), "=K07.11+RCL-PLC")], **frame)
    result = run_pipeline(tmp_path, cfg)
    warnings = [i.message for i in result.issues if i.code is IssueCode.PANEL_MAPPING]
    assert len(warnings) == 1 and "Rack LTL is assumed to be in panel LTL-PLC" in warnings[0] and "LTL = \"RCL-PLC\"" in warnings[0]

    cfg.plc.rack_panels = {"LTL": "RCL-PLC"}
    result = run_pipeline(tmp_path, cfg)
    assert [i for i in result.issues if i.code is IssueCode.PANEL_MAPPING] == []
    assert {n.key for n in result.model.nodes.values() if n.type == "PANEL"} == {"RCL-PLC"}
