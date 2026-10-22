"""Accuracy against checker-confirmed issues in the real sample package (IMPLEMENTATION_PLAN.md section 9).

AK-0nn entries are real errors the platform must report. AK-Nnn entries are
correct drafting the platform must NOT report (confirmed false alarms).
Runs only when the client drawings are present in input/Main; skipped otherwise.
"""

from pathlib import Path

import pytest

from drawing_qa.config import Settings
from drawing_qa.model.enums import DocType
from drawing_qa.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "input" / "Main"

pytestmark = pytest.mark.skipif(
    not (PACKAGE / "Terminations" / "401103-201.pdf").is_file(), reason="client sample package not present"
)


ALL = Settings.load(ROOT / "config" / "project.toml")
# The sample package's termination drawings are out of date, so the checker runs it with
# `--exclude term` (decision 2026-10-21). Most entries are checked that way; the ones found only on
# the termination drawings (AK-001 to AK-003) are checked with all four inputs included.
SETTINGS = ALL.model_copy(deep=True)
SETTINGS.ingest.unchecked_types = [DocType.TERM]


@pytest.fixture(scope="module")
def result():
    return run_pipeline(PACKAGE, SETTINGS)


@pytest.fixture(scope="module")
def result_all():
    return run_pipeline(PACKAGE, ALL)


def findings(result, rule_id, subject_key=None):
    return [f for f in result.findings if f.rule_id == rule_id and (subject_key is None or f.subject_key == subject_key)]


def sheet_of(result, finding):
    drawing_id = finding.evidence[0].source.drawing_id
    return next(d for d in result.drawings if d.id == drawing_id)


def test_ak_001_ltl_aut1_superseded_by_ltl_plc(result_all):
    fs = findings(result_all, "DRW-006", "LTL-AUT1")
    assert len(fs) == 57
    assert {sheet_of(result_all, f).sheet for f in fs} == {"201", "202", "203", "204", "205", "206", "207"}
    assert {f.recommendation for f in fs} == {"Replace with =K07.11+LTL-PLC"}


def test_ak_002_file_219_is_numbered_sheet_217(result_all):
    assert findings(result_all, "DRW-008", "401103-219.pdf")
    assert len(findings(result_all, "DRW-007", "401103 sh 217")) == 1


def test_ak_003_file_220_has_wrong_drawing_number(result_all):
    fs = findings(result_all, "DRW-008", "401103-220.pdf")
    assert fs and "401105" in fs[0].message


@pytest.mark.parametrize(
    ("reference", "drawn"),
    [
        ("H-TN50D8", "H-TN50D7"),
        ("H-TN50H8", "H-TN50H7"),
        ("H-TN50L8", "H-TN50L7"),
        ("H-TN51D8", "H-TN51D7"),
        ("H-TN51H8", "H-TN51H7"),
        ("H-TN51L8", "H-TN51L7"),
        ("H-TN51N8", "H-TN51N7"),
        ("H-TN51R8", "H-TN51R7"),
        ("H-TN51W8", "H-TN51W7"),
    ],
)
def test_ak_004_cbd_references_column_8_tags_drawn_in_column_7(result, reference, drawn):
    fs = findings(result, "TAG-003", reference)
    assert fs and drawn in fs[0].recommendation


@pytest.mark.parametrize(
    ("reference", "drawn"),
    [("S-TN20D4", "S-TN20D3"), ("S-TN20N4", "S-TN20N3"), ("S-TN25D4", "S-TN25D3"), ("S-TN26D4", "S-TN26D3")],
)
def test_ak_005_references_to_column_4_tags_drawn_in_column_3(result, reference, drawn):
    fs = findings(result, "TAG-003", reference)
    assert fs and drawn in fs[0].recommendation


def test_ak_006_malformed_tag_h_tn51f(result):
    assert findings(result, "TAG-001", "H-TN51F")


@pytest.mark.parametrize("sheet", ["401103 sh 035", "401103 sh 056", "401310 sh 015"])
def test_ak_007_status_inconsistent_within_revision(result, sheet):
    assert findings(result, "DRW-002", sheet)


@pytest.mark.parametrize("contact", ["K-TN19D4", "K-TN19H4", "K-TN19L4"])
def test_ak_008_sheet_043_contacts_not_on_sheet_019(result, contact):
    """C-05, confirmed real: sheet 043 labels contacts K-TN19D4/H4/L4, which do not exist on sheet 019."""
    assert findings(result, "TAG-003", contact)


@pytest.mark.parametrize(
    ("coil", "label"),
    [("K-TN19F1", "K-TN19D4"), ("K-TN19F2", "K-TN19F4"), ("K-TN19F3", "K-TN19H4"), ("K-TN19F4", "K-TN19L4")],
)
def test_ak_008_sheet_043_contacts_should_carry_coil_tags(result, coil, label):
    """Sheet 019 contact tables point to 43/D4, F4, H4, L4; each contact there should carry its coil tag."""
    fs = findings(result, "TAG-007", coil)
    assert fs and f"labelled {label}" in fs[0].message
    assert fs[0].recommendation == f"Relabel the contact {coil}"


def test_ak_009_rack_layout_lists_two_sheets_for_slot_20(result):
    """C-08, confirmed real: rack layout 013 shows 043 and 045 for slot 20; 045 is slot 22."""
    fs = findings(result, "PLC-007", "LTL/S20")
    assert fs and "043, 045" in fs[0].message


@pytest.mark.parametrize(("sheet", "expected"), [("401103 sh 035", "LTL.S12.DI.00..07"), ("401103 sh 056", "LTL.S26.AI.00..03"), ("401103 sh 057", "LTL.S27.AI.00..03")])
def test_ak_010_points_notation(result, sheet, expected):
    """C-09, confirmed real."""
    fs = [f for f in findings(result, "PLC-008") if sheet in f.message]
    assert fs and fs[0].recommendation == f"Write as {expected}"


@pytest.mark.parametrize("channel", ["LTL/S5/CH02", "LTL/S5/CH03"])
def test_ak_011_rail_striker_on_sheet_024_has_no_device_tag(result, channel):
    """C-14, confirmed real: the CBD and terminations call it S-TN24H3."""
    fs = findings(result, "COMP-001", channel)
    assert fs and "S-TN24H3" in fs[0].message and fs[0].severity.value == "MAJOR"


def test_ak_012_sheet_028_reuses_s_tn27d2(result):
    """C-15, confirmed real: sheet 028 copies S-TN27D2 from sheet 027; it should be S-TN28D2."""
    fs = findings(result, "TAG-005", "S-TN27D2")
    assert fs and "401103 sh 028" in fs[0].message and "S-TN28D2" in fs[0].recommendation


def test_ak_013_tn16d0_not_on_sheet_016(result):
    """C-11, confirmed real: TN16D0 is used on sheet 046 but sheet 016 has TN16D1."""
    fs = findings(result, "WIRE-002", "TN16D0")
    assert fs and "TN16D1" in fs[0].message


@pytest.mark.parametrize(("channel", "device"), [("LTL/S24/CH00", "H-TN50D7"), ("LTL/S7/CH04", "S-TN26N3"), ("LTL/S7/CH05", "S-TN26N3")])
def test_ak_014_no_jb_in_channel_row(result, channel, device):
    """C-16, confirmed real: H-TN50D7 (sheet 050) and S-TN26N3 (sheet 026) show no JB, unlike their neighbours."""
    fs = findings(result, "COMP-001", channel)
    assert fs and device in fs[0].message and "JB / field location" in fs[0].message


@pytest.mark.parametrize(("word", "sheet"), [("STOBE", "401310 sh 015"), ("PRSSURE", "401103 sh 057")])
def test_ak_015_spelling(result, word, sheet):
    """C-19, confirmed real (STOBE); PRSSURE on sheet 057 found by the same rule."""
    fs = findings(result, "DRW-010", word)
    assert fs and sheet in fs[0].message


def test_spelling_ignores_word_endings(result):
    assert not findings(result, "DRW-010", "SUPPLIES")


@pytest.mark.parametrize(
    ("device", "schematic_jb", "cbd_jb"),
    [
        ("S-TN36D4", "LTL-JB2", "LTL-JB1"),  # AK-016 (C-20): brakes 9-12 on 036 vs brake 3-6 switches on CBD 015
        ("S-TN36F4", "LTL-JB2", "LTL-JB1"),
        ("S-TN36H4", "LTL-JB2", "LTL-JB1"),
        ("S-TN36L4", "LTL-JB2", "LTL-JB1"),
        ("S-TN44D4", "LTL-JB3", "SH-LCS8"),  # AK-017 (C-21)
        ("U-TN57P1", "LTL-JB3", "SH-JB3"),  # AK-018 (C-22)
        ("U-TN57S1", "LTL-JB3", "SH-JB3"),
        ("S-TN44F4", "LTL-JB3", "SH-JB11"),  # AK-019 (C-24)
        ("S-TN44H4", "LTL-JB3", "SH-JB9"),
        ("S-TN37D4", "LTL-JB13", "SH-JB3"),  # AK-020 (C-26)
        ("S-TN37F4", "LTL-JB13", "SH-JB3"),
    ],
)
def test_ak_016_to_018_schematic_and_cbd_jbs_disagree(result, device, schematic_jb, cbd_jb):
    fs = findings(result, "PATH-002", device)
    assert fs and f"via {schematic_jb}" in fs[0].message and f"via {cbd_jb}" in fs[0].message


def test_ak_021_s_tn23t4_copied_onto_cow_catcher_contact_2(result):
    """C-27, confirmed real (2026-10-22): S-TN23T4 (south rail, sheet 023) also tags LTL/S5/CH01, cow catcher
    contact 2 on sheet 024; its partner CH00 is S-TN24D2, so CH01 should be S-TN24D2 too."""
    fs = findings(result, "COMP-004", "S-TN23T4")
    assert len(fs) == 1 and fs[0].severity.value == "MAJOR"
    assert "LTL/S5/CH01" in fs[0].message and "S-TN24D2" in fs[0].recommendation


def test_ak_022_sheet_051_legend_lists_ltl_sh8(result):
    """C-35, confirmed real (2026-10-22): the legend says =K07.11+LTL-SH8; the rows use SH-LCS8."""
    fs = [f for f in findings(result, "PATH-004", "401103 sh 051") if "LTL-SH8" in f.message]
    assert len(fs) == 1 and fs[0].severity.value == "MAJOR" and "SH-LCS8" in fs[0].recommendation


@pytest.mark.parametrize("sheet", ["401103 sh 021", "401103 sh 045"])
def test_ak_023_empty_legend(result, sheet):
    """C-36, confirmed real (2026-10-22): the LEGEND box has no entries although the channel rows use JBs / LCSs."""
    fs = findings(result, "PATH-004", sheet)
    assert len(fs) == 1 and "is empty" in fs[0].message


def test_c37_sheet_022_long_legend_is_read_in_full(result):
    """C-37 withdrawn: sheet 022's legend lists eight locations, LTL-JB2 and LTL-JB1 last."""
    assert findings(result, "PATH-004", "401103 sh 022") == []


def test_ak_n06_s_tn25h4_routes_via_sh_jb9(result):
    """C-23 (S-TN25H4), confirmed correct: S-TN25H4 -> SH-JB9 -> LTL-PLC on CBD 016."""
    assert not findings(result, "PATH-001", "S-TN25H4")
    trace = next(t for t in result.traces if t.device == "S-TN25H4" and t.cbd_route)
    assert trace.cbd_route == ["S-TN25H4", "SH-JB9", "LTL-PLC"]


@pytest.mark.parametrize(("device", "route"), [("S-TN36T4", ["S-TN36T4", "LTL-JB3", "LTL-JB1", "LTL-PLC"]), ("U-TN57J1", ["U-TN57J1", "LTL-JB3", "LTL-JB1", "LTL-PLC"])])
def test_ak_n07_devices_attached_to_ltl_jb3(result, device, route):
    """C-25, confirmed correct: these devices are attached to LTL-JB3 on CBD 016."""
    assert not findings(result, "PATH-001", device)
    assert next(t for t in result.traces if t.device == device and t.cbd_route).cbd_route == route


def test_every_channel_device_on_a_cbd_has_a_route(result):
    on_cbd = [t for t in result.traces if t.on_cbd and t.status.value in ("PARTIAL", "COMPLETE")]
    assert on_cbd and all(t.cbd_route for t in on_cbd)


def test_termination_drawings_produce_no_findings(result):
    """With --exclude term (checker decision 2026-10-21): termination drawings are read but not checked."""
    term = {d.id for d in result.drawings if d.doc_type is DocType.TERM}
    assert term
    assert not [f for f in result.findings if any(e.source and e.source.drawing_id in term for e in f.evidence)]


def test_ak_n05_s_tn36r4_is_cabled_direct_to_the_panel(result):
    """C-16 (first item): no JB for S-TN36R4 is correct; the CBD shows it direct to the panel."""
    assert not [f for f in findings(result, "COMP-001") if "S-TN36R4" in f.message]


def test_ak_n04_tv_wires_are_a_separate_document(result):
    """C-13: TV20T6 / TV20U6 on sheet 015 belong to a separate package (FFI TV). Listed, not reported as errors."""
    assert not [f for f in findings(result, "WIRE-002") if f.subject_key.startswith("TV")]
    fs = findings(result, "DRW-009", "TV")
    assert fs and "TV20T6" in fs[0].message and "Separate document" in fs[0].message


def test_ak_n02_sheet_042_contacts_labelled_with_coil_tag(result):
    """Sheet 042 labels the auxiliary contacts with the coil tag (K-TN18F1...). Provisional: not yet checker-confirmed."""
    assert not [f for f in findings(result, "TAG-007") if "TN18" in f.subject_key]


def test_ak_n03_legend_sheet_examples_are_not_checked(result):
    """Sheet 008 (GRAPHICS SYMBOLS) shows example tags such as K-CD011M4. Provisional: not yet checker-confirmed."""
    assert not [f for f in result.findings if "CD011" in f.subject_key or "CD011" in f.message]
