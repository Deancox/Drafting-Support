"""Rule engine and M1 rules, run end to end on synthetic packages laid out like the client's sheets."""

from __future__ import annotations

import pytest

from drawing_qa.config import RuleSetting, Settings
from drawing_qa.model.enums import IssueCode, Severity, StageStatus
from drawing_qa.pipeline import run_pipeline
from tests.conftest import build_pdf, cell_rect, sheet_frame

FOLDER = {"PLC": "PLC", "CBD": "CableBlocks", "TERM": "Terminations"}
DESCRIPTOR = {"PLC": "SCHEMATIC DIAGRAM", "CBD": "CABLING BLOCK DIAGRAM", "TERM": "TERMINATION DIAGRAM"}


def add_sheet(root, kind, sheet, items=(), *, drawing_no="401103", file_name=None, **frame):
    frame.setdefault("descriptor", DESCRIPTOR[kind])
    name = file_name or f"{drawing_no}-{sheet}.pdf"
    build_pdf(root / FOLDER[kind] / name, [[]], shx=[sheet_frame(drawing_no, sheet, **frame) + list(items)])


@pytest.fixture
def cfg() -> Settings:
    s = Settings()
    s.identifiers.areas = ["LTL", "RCL", "SPL", "EER1"]
    s.identifiers.sheet = r"\d{2,3}"
    s.identifiers.superseded = {"LTL-AUT1": "LTL-PLC"}
    return s


def by_rule(result, rule_id):
    return [f for f in result.findings if f.rule_id == rule_id]


# --- engine ------------------------------------------------------------------


def test_rules_needing_grid_are_skipped_without_one(tmp_path, cfg):
    build_pdf(tmp_path / "PLC" / "a.pdf", [[(50, 50, "S-TN23N2 A B C D")]])
    result = run_pipeline(tmp_path, cfg)
    runs = {r.rule_id: r for r in result.rule_runs}
    assert runs["TAG-003"].status is StageStatus.SKIPPED
    assert "GRID" in runs["TAG-003"].note
    assert runs["PLACEHOLDER-001"].status is StageStatus.DONE


def test_disabled_rule_and_severity_override(tmp_path, cfg):
    add_sheet(tmp_path, "TERM", "203", [(cell_rect("D2"), "CXXXXX"), (cell_rect("E2"), "HOLD")])
    cfg.rules = {"PLACEHOLDER-004": RuleSetting(enabled=False), "PLACEHOLDER-002": RuleSetting(severity="CRITICAL")}
    result = run_pipeline(tmp_path, cfg)
    runs = {r.rule_id: r for r in result.rule_runs}
    assert runs["PLACEHOLDER-004"].status is StageStatus.SKIPPED
    assert by_rule(result, "PLACEHOLDER-004") == []
    assert [f.severity for f in by_rule(result, "PLACEHOLDER-002")] == [Severity.CRITICAL]


def test_failing_rule_does_not_stop_others(tmp_path, cfg, monkeypatch):
    from drawing_qa.rules.tag import KnownPrefixFfi

    def boom(self, ctx, make):
        raise RuntimeError("bad rule")

    monkeypatch.setattr(KnownPrefixFfi, "evaluate", boom)
    add_sheet(tmp_path, "TERM", "203", [(cell_rect("D2"), "CXXXXX")])
    result = run_pipeline(tmp_path, cfg)
    runs = {r.rule_id: r for r in result.rule_runs}
    assert runs["TAG-002"].status is StageStatus.FAILED
    assert by_rule(result, "PLACEHOLDER-002")
    assert next(s for s in result.stages if s.name == "rules").status is StageStatus.FAILED


def test_findings_are_deterministic(tmp_path, cfg):
    add_sheet(tmp_path, "TERM", "203", [(cell_rect("D2"), "CXXXXX"), (cell_rect("F2"), "CXXXXX"), (cell_rect("H2"), "H-TN51F")])
    a = run_pipeline(tmp_path, cfg).findings
    b = run_pipeline(tmp_path, cfg).findings
    assert [(f.id, f.fingerprint, f.message) for f in a] == [(f.id, f.fingerprint, f.message) for f in b]
    assert len({f.fingerprint for f in a}) == len(a)  # two CXXXXX on one sheet stay distinct


# --- TAG ---------------------------------------------------------------------


def test_tag_001_malformed_tag(tmp_path, cfg):
    add_sheet(tmp_path, "CBD", "014", [(cell_rect("G6"), "H-TN51F"), (cell_rect("H6"), "SPL-JB")], drawing_no="401310")
    keys = sorted(f.subject_key for f in by_rule(run_pipeline(tmp_path, cfg), "TAG-001"))
    assert keys == ["H-TN51F", "SPL-JB"]


def test_tag_003_reference_missing_from_home_sheet_suggests_near_match(tmp_path, cfg):
    add_sheet(tmp_path, "PLC", "051", [(cell_rect("C7"), "H-TN51D7")])
    add_sheet(tmp_path, "CBD", "014", [(cell_rect("G6"), "H-TN51D8"), (cell_rect("H6"), "H-TN51D7")], drawing_no="401310")
    findings = by_rule(run_pipeline(tmp_path, cfg), "TAG-003")
    assert [f.subject_key for f in findings] == ["H-TN51D8"]
    assert "401103 sh 051" in findings[0].message
    assert "H-TN51D7" in findings[0].recommendation


def test_tag_003_skips_tags_whose_home_sheet_is_not_supplied(tmp_path, cfg):
    add_sheet(tmp_path, "TERM", "203", [(cell_rect("D2"), "S-TN35T4")])
    result = run_pipeline(tmp_path, cfg)
    assert by_rule(result, "TAG-003") == []
    assert [f.subject_key for f in by_rule(result, "DRW-005")] == ["Sheet 035"]


@pytest.mark.parametrize(("label_cell", "expected"), [("N2", 0), ("M2", 0), ("L3", 0), ("P2", 0), ("K2", 1), ("N4", 1)])
def test_tag_004_label_tolerance(tmp_path, cfg, label_cell, expected):
    add_sheet(tmp_path, "PLC", "023", [(cell_rect(label_cell), "S-TN23N2")])
    assert len(by_rule(run_pipeline(tmp_path, cfg), "TAG-004")) == expected


def _coil_sheet(tmp_path, pointers):
    """Sheet 019 with coil K-TN19F1 and its contact table entries below it."""
    items = [(cell_rect("W1"), "K-TN19F1"), (cell_rect("E1"), "K-TN19F1")]
    for i, p in enumerate(pointers):
        x0, y0, x1, y1 = cell_rect("X1", 20)
        items.append(((x0, y0 + 10 * i, x1, y1 + 10 * i), p))
    add_sheet(tmp_path, "PLC", "019", items)


def test_auxiliary_contact_labelled_with_coil_tag_is_correct(tmp_path, cfg):
    """Contact on sheet 043 at D4 labelled with its coil tag K-TN19F1 (sheet 042 style)."""
    _coil_sheet(tmp_path, ["43/D4"])
    add_sheet(tmp_path, "PLC", "043", [(cell_rect("B4"), "K-TN19F1")])
    result = run_pipeline(tmp_path, cfg)
    assert by_rule(result, "TAG-003") == []
    assert by_rule(result, "TAG-007") == []


def test_auxiliary_contact_labelled_with_sheet_and_grid_is_wrong(tmp_path, cfg):
    """C-05 confirmed: K-TN19D4 (coil sheet + own grid) instead of K-TN19F1 is a labelling error."""
    _coil_sheet(tmp_path, ["43/D4"])
    add_sheet(tmp_path, "PLC", "043", [(cell_rect("B4"), "K-TN19D4")])
    result = run_pipeline(tmp_path, cfg)
    assert [f.subject_key for f in by_rule(result, "TAG-003")] == ["K-TN19D4"]
    findings = by_rule(result, "TAG-007")
    assert len(findings) == 1
    assert "labelled K-TN19D4" in findings[0].message
    assert findings[0].recommendation == "Relabel the contact K-TN19F1"


def test_auxiliary_contact_without_pointer_is_flagged(tmp_path, cfg):
    _coil_sheet(tmp_path, [])
    add_sheet(tmp_path, "PLC", "043", [(cell_rect("B4"), "K-TN19D4")])
    assert [f.subject_key for f in by_rule(run_pipeline(tmp_path, cfg), "TAG-003")] == ["K-TN19D4"]


def test_pointer_to_missing_contact_is_flagged(tmp_path, cfg):
    _coil_sheet(tmp_path, ["43/D4"])
    add_sheet(tmp_path, "PLC", "043")
    findings = by_rule(run_pipeline(tmp_path, cfg), "TAG-007")
    assert len(findings) == 1 and "43/D4" in findings[0].message


def test_legend_sheet_examples_are_not_checked(tmp_path, cfg):
    add_sheet(tmp_path, "PLC", "008", [(cell_rect("P8"), "K-CD011M4"), (cell_rect("Q8"), "XXXX")], descriptor="GRAPHICS SYMBOLS")
    result = run_pipeline(tmp_path, cfg)
    assert result.findings == []


def test_tag_005_device_copied_to_another_sheet(tmp_path, cfg):
    """Like S-TN27D2 drawn on sheets 027 and 028: label at its own grid (B2 for D2) on both."""
    add_sheet(tmp_path, "PLC", "027", [(cell_rect("B2"), "S-TN27D2")])
    add_sheet(tmp_path, "PLC", "028", [(cell_rect("B2"), "S-TN27D2"), (cell_rect("P7"), "S-TN27D2")])
    add_sheet(tmp_path, "CBD", "015", [(cell_rect("Q0"), "S-TN28D2")], drawing_no="401310")
    findings = by_rule(run_pipeline(tmp_path, cfg), "TAG-005")
    assert len(findings) == 1
    assert "401103 sh 028" in findings[0].message and "already use" in findings[0].message
    assert findings[0].recommendation.startswith("Renumber the device on 401103 sh 028 to S-TN28D2")


def test_tag_005_ignores_references_away_from_the_tag_grid(tmp_path, cfg):
    add_sheet(tmp_path, "PLC", "027", [(cell_rect("B2"), "S-TN27D2")])
    add_sheet(tmp_path, "PLC", "028", [(cell_rect("P7"), "S-TN27D2")])  # a cross reference, far from D2
    assert by_rule(run_pipeline(tmp_path, cfg), "TAG-005") == []


def test_tag_006_spelling_variant(tmp_path, cfg):
    add_sheet(tmp_path, "TERM", "203", [(cell_rect("D2"), "SPL-JB-1")])
    findings = by_rule(run_pipeline(tmp_path, cfg), "TAG-006")
    assert [f.recommendation for f in findings] == ["Write as SPL-JB1"]


# --- PLACEHOLDER ---------------------------------------------------------------


def test_placeholder_rules_and_status_severity(tmp_path, cfg):
    items = [
        (cell_rect("B2"), "XXXX"),
        (cell_rect("C2"), "CXXXXX"),
        (cell_rect("D2", 120), "(REF TERM. XXX)"),
        (cell_rect("E2"), "HOLD"),
        (cell_rect("F2"), "AUT1"),
    ]
    add_sheet(tmp_path, "TERM", "203", items, status="PRELIMINARY")
    add_sheet(tmp_path, "TERM", "204", [(cell_rect("C2"), "CXXXXX")], status="FOR CONSTRUCTION")
    result = run_pipeline(tmp_path, cfg)
    assert {r: len(by_rule(result, r)) for r in ("PLACEHOLDER-001", "PLACEHOLDER-002", "PLACEHOLDER-003", "PLACEHOLDER-004", "PLACEHOLDER-005")} == {
        "PLACEHOLDER-001": 1,
        "PLACEHOLDER-002": 2,
        "PLACEHOLDER-003": 1,
        "PLACEHOLDER-004": 1,
        "PLACEHOLDER-005": 1,
    }
    severities = {f.subject_key + f.message.split(" at ")[1][:13]: f.severity for f in by_rule(result, "PLACEHOLDER-002")}
    assert severities == {"CXXXXX401103 sh 203": Severity.MINOR, "CXXXXX401103 sh 204": Severity.MAJOR}
    assert "cable number" in by_rule(result, "PLACEHOLDER-002")[0].recommendation


# --- DRW -----------------------------------------------------------------------


def test_ak_001_superseded_designation(tmp_path, cfg):
    """Answer key AK-001: =K07.11+LTL-AUT1 must be reported with the fix =K07.11+LTL-PLC."""
    add_sheet(tmp_path, "TERM", "201", [(cell_rect("A1", 100), "=K07.11+LTL-AUT1"), (cell_rect("G2", 100), "=K07.11+LTL-AUT1")])
    result = run_pipeline(tmp_path, cfg)
    findings = by_rule(result, "DRW-006")
    assert len(findings) == 2
    assert {f.recommendation for f in findings} == {"Replace with =K07.11+LTL-PLC"}
    assert all(f.severity is Severity.MAJOR for f in findings)
    assert by_rule(result, "PLACEHOLDER-005") == []  # not double-reported


def test_drw_001_cross_reference(tmp_path, cfg):
    add_sheet(tmp_path, "PLC", "036", [(cell_rect("A1"), "(SHT 35)"), (cell_rect("Y1"), "(SHT 37)"), (cell_rect("U9", 150), "EXAMPLE SHEET 011 OF 20")])
    add_sheet(tmp_path, "PLC", "037")
    findings = by_rule(run_pipeline(tmp_path, cfg), "DRW-001")
    assert [f.subject_key for f in findings] == ["401103"]
    assert "sheet(s) 35;" in findings[0].message


def test_drw_001_groups_by_referenced_drawing(tmp_path, cfg):
    """A partial package: many references to sheets that are not supplied are one finding per drawing."""
    refs = [(cell_rect(f"{r}1"), f"(SHT {n})") for r, n in zip("ABCDEF", (30, 31, 32, 33, 38, 30))]
    add_sheet(tmp_path, "PLC", "036", refs)
    add_sheet(tmp_path, "PLC", "037", [(cell_rect("A1"), "(SHT 34)")])
    findings = by_rule(run_pipeline(tmp_path, cfg), "DRW-001")
    assert len(findings) == 1
    assert "7 reference(s) point to 6 sheet(s) of 401103" in findings[0].message
    assert "sheet(s) 30-34, 38;" in findings[0].message


def test_unreadable_sheet_is_not_missing_or_an_incomplete_title_block(tmp_path, cfg, make_pdf):
    """A sheet plotted without SHX text (NO_SHX_TEXT) is in the package: references to it are not
    missing-sheet findings, and its unread title block is not DRW-003."""
    add_sheet(tmp_path, "PLC", "036", [(cell_rect("A1"), "(SHT 40)")])
    lines = [(40 + (i % 50) * 20, 40 + (i // 50) * 60, 50 + (i % 50) * 20, 90 + (i // 50) * 60) for i in range(400)]
    make_pdf(tmp_path / "PLC" / "401103-040.pdf", [[]], lines=[lines])
    result = run_pipeline(tmp_path, cfg)
    assert by_rule(result, "DRW-001") == [] and by_rule(result, "DRW-003") == []
    assert any(i.code.value == "NO_SHX_TEXT" for i in result.issues)


def test_drw_002_status_consistency(tmp_path, cfg):
    for sheet, status in (("014", "PRELIMINARY"), ("015", "AS BUILT"), ("016", "PRELIMINARY")):
        add_sheet(tmp_path, "CBD", sheet, drawing_no="401310", rev="B", status=status)
    findings = by_rule(run_pipeline(tmp_path, cfg), "DRW-002")
    assert [f.subject_key for f in findings] == ["401310 sh 015"]


def test_drw_004_folder_type_mismatch(tmp_path, cfg):
    add_sheet(tmp_path, "PLC", "203", descriptor="TERMINATION DIAGRAM")
    findings = by_rule(run_pipeline(tmp_path, cfg), "DRW-004")
    assert len(findings) == 1 and "Terminations" not in findings[0].message and "TERM" in findings[0].message


def test_identical_copy_is_skipped(tmp_path, cfg):
    add_sheet(tmp_path, "PLC", "036", [(cell_rect("D4"), "S-TN36D4")])
    add_sheet(tmp_path, "PLC", "036", [(cell_rect("D4"), "S-TN36D4")], file_name="SchematicSet.pdf")
    result = run_pipeline(tmp_path, cfg)
    assert [i.code for i in result.issues if i.code is not IssueCode.MISSING_FOLDER] == [IssueCode.DUPLICATE_SHEET]
    assert sum(c.subject_key == "S-TN36D4" for c in result.claims) == 1
    assert by_rule(result, "DRW-007") == []


def test_drw_007_and_008_misnumbered_sheet(tmp_path, cfg):
    """Like 401103-219.pdf, whose title block says sheet 217."""
    add_sheet(tmp_path, "TERM", "217", [(cell_rect("D2"), "S-TN35T4")])
    add_sheet(tmp_path, "TERM", "217", [(cell_rect("D2"), "S-TN36D4")], file_name="401103-219.pdf")
    add_sheet(tmp_path, "TERM", "219", drawing_no="401105", file_name="401103-220.pdf")
    result = run_pipeline(tmp_path, cfg)
    assert len(by_rule(result, "DRW-007")) == 1
    assert sorted(f.subject_key for f in by_rule(result, "DRW-008")) == ["401103-219.pdf", "401103-220.pdf"]


def test_unchecked_document_type_is_read_but_not_checked(tmp_path, cfg):
    from drawing_qa.model.enums import DocType

    add_sheet(tmp_path, "TERM", "203", [(cell_rect("D2"), "CXXXXX"), (cell_rect("E2"), "S-TN35T4")])
    add_sheet(tmp_path, "CBD", "014", [(cell_rect("D2"), "CXXXXX")], drawing_no="401310")
    cfg.ingest.unchecked_types = [DocType.TERM]
    result = run_pipeline(tmp_path, cfg)
    term = {d.id for d in result.drawings if d.doc_type is DocType.TERM}
    assert term and any(c.source.drawing_id in term for c in result.claims)  # still read
    assert len(by_rule(result, "PLACEHOLDER-002")) == 1  # only the CBD one
    assert by_rule(result, "DRW-005") == []  # S-TN35T4 on the termination sheet is not used


def test_drw_010_spelling(tmp_path, cfg):
    """Like 'SIREN & STOBE' on CBD 015 next to many 'STROBE'; real words and endings are not reported."""
    words = [(cell_rect(f"{r}1", 120), "SIREN & STROBE") for r in "BCDE"]
    words += [(cell_rect("F1", 120), "SIREN & STOBE"), (cell_rect("G1", 120), "POWER SUPPLIES")]
    words += [(cell_rect(f"{r}3", 120), "SUPPLIED POWER") for r in "BCDE"]
    add_sheet(tmp_path, "CBD", "015", words, drawing_no="401310")
    findings = by_rule(run_pipeline(tmp_path, cfg), "DRW-010")
    assert [(f.subject_key, f.recommendation) for f in findings] == [("STOBE", "Check the spelling; did you mean STROBE?")]
