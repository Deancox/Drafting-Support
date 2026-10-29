"""config/project.toml: everything specific to one project sits in its [project] section."""

from __future__ import annotations

from pathlib import Path

from drawing_qa.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def _load(tmp_path, text: str) -> Settings:
    path = tmp_path / "project.toml"
    path.write_text(text, encoding="utf-8")
    return Settings.load(path)


def test_project_section_holds_the_project_specifics(tmp_path):
    s = _load(tmp_path, """
[project]
code = "401103"
areas = ["LTL", "EER1"]
description_prefix = "07.11 SHIPLOADER"

[project.ffi_drawings]
TN = "401103"
TV = "EXTERNAL"

[project.superseded]
"LTL-AUT1" = "LTL-PLC"

[project.rack_panels]
MCC1 = "MCC-PLC"
""")
    assert s.project.code == "401103"
    assert s.identifiers.areas == ["LTL", "EER1"]
    assert s.identifiers.ffi_drawings == {"TN": "401103", "TV": "EXTERNAL"}
    assert s.identifiers.superseded == {"LTL-AUT1": "LTL-PLC"}
    assert s.plc.rack_panels == {"MCC1": "MCC-PLC"}
    assert s.plc.description_prefix == "07.11 SHIPLOADER"


def test_old_places_still_load(tmp_path):
    s = _load(tmp_path, """
[identifiers]
areas = ["LTL"]
[identifiers.ffi_drawings]
TN = "401103"
[plc.rack_panels]
MCC1 = "MCC-PLC"
""")
    assert s.identifiers.areas == ["LTL"] and s.identifiers.ffi_drawings == {"TN": "401103"}
    assert s.plc.rack_panels == {"MCC1": "MCC-PLC"}


def test_project_section_wins_over_an_old_place(tmp_path):
    s = _load(tmp_path, """
[project]
areas = ["LTL"]
[identifiers]
areas = ["OLD"]
""")
    assert s.identifiers.areas == ["LTL"]


def test_shipped_config_keeps_its_project_specifics():
    s = Settings.load(ROOT / "config" / "project.toml")
    assert s.identifiers.ffi_drawings["TV"] == "EXTERNAL"
    assert s.identifiers.superseded == {"LTL-AUT1": "LTL-PLC"}
    assert s.plc.rack_panels["MCC1"] == "MCC-PLC"
    assert "EER1" in s.identifiers.areas
