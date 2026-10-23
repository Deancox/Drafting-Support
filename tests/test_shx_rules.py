"""SHX stroke decoding and the rules: decoded sheets are checked, but never reported for what they lack."""

from __future__ import annotations

from tests.conftest import cell_rect
from tests.test_rules import add_sheet, by_rule
from tests.test_shx_pipeline import _sheet, cfg, package, run  # noqa: F401  (cfg is a fixture)


def _cites(finding, drawing) -> bool:
    return any(e.source and e.source.drawing_id == drawing.id for e in finding.evidence)


def test_presence_findings_raised_and_marked(tmp_path, cfg):
    # S-TN37T4 drawn at J4: its grid says T4, eight rows away (TAG-004).
    result = run(package(tmp_path, extra=[(cell_rect("J4"), "S-TN37T4")]), cfg)
    [f] = [f for f in by_rule(result, "TAG-004") if f.subject_key == "S-TN37T4"]
    assert f.decoded is True


def test_confirm_decoded_holds_back(tmp_path, cfg):
    cfg.ocr.confirm_decoded = True
    result = run(package(tmp_path, extra=[(cell_rect("J4"), "S-TN37T4")]), cfg)
    assert not [f for f in by_rule(result, "TAG-004") if f.subject_key == "S-TN37T4"]


def test_findings_with_readable_evidence_are_not_marked(tmp_path, cfg):
    result = run(package(tmp_path, extra=[(cell_rect("J4"), "S-TN37T4")]), cfg)
    d037 = _sheet(result, "037")
    assert all(f.decoded == _cites(f, d037) for f in result.findings)


def test_tag_003_quiet_when_home_is_decoded(tmp_path, cfg):
    package(tmp_path)
    add_sheet(tmp_path, "CBD", "014", [(cell_rect("G6"), "S-TN37F4")], drawing_no="401310")
    assert not [f for f in by_rule(run(tmp_path, cfg), "TAG-003") if f.subject_key == "S-TN37F4"]


def test_wire_002_quiet_when_home_is_decoded(tmp_path, cfg):
    # Sheet 036 refers to wire TN37K6, which decoded sheet 037 does not show.
    result = run(package(tmp_path, readable_extra=[(cell_rect("H6", 24), "37K6")]), cfg)
    assert not [f for f in by_rule(result, "WIRE-002") if f.subject_key == "TN37K6"]


def test_comp_001_quiet_on_decoded_row(tmp_path, cfg):
    # Channel 00's device tag uses a letter (Q) no readable sheet shows at that size: dropped, not missing.
    result = run(package(tmp_path, device="S-TN37Q4"), cfg)
    d037 = _sheet(result, "037")
    assert d037.decoded is not None
    assert not [f for f in by_rule(result, "COMP-001") if _cites(f, d037)]


def test_drw_003_skips_decoded(tmp_path, cfg):
    result = run(package(tmp_path), cfg)
    assert not [f for f in by_rule(result, "DRW-003") if "sh 037" in f.subject_key]


def recheck(result, cfg, decoded):
    """Re-run the rules as if the sheets `decoded` picks had been decoded from their strokes (--ocr)."""
    from drawing_qa.model.source import DecodeInfo
    from drawing_qa.rules.context import RuleContext
    from drawing_qa.rules.engine import RuleEngine

    for d in result.drawings:
        if decoded(d):
            d.decoded = DecodeInfo(letters=1, shapes=1, words_kept=1, words_dropped=0)
    ctx = RuleContext(result.drawings, result.claims, cfg, model=result.model, traces=result.traces)
    return RuleEngine().run(ctx)[0]


def rule_ids(findings, rule_id):
    return [f for f in findings if f.rule_id == rule_id]


def test_path_004_quiet_on_a_decoded_legend_sheet(tmp_path, cfg):
    """On real decoded sheets 041/044 the legend decoded but the channel rows did not."""
    from drawing_qa.pipeline import run_pipeline
    from tests.test_plc import slot_sheet

    items, frame = slot_sheet()  # channel 00 routes through LTL-JB2
    items += [((881, 300, 930, 312), "LEGEND"), ((881, 316, 1000, 328), "=K07.11+LTL-JB9")]
    add_sheet(tmp_path, "PLC", "036", items, **frame)
    result = run_pipeline(tmp_path, cfg)
    assert rule_ids(result.findings, "PATH-004")
    assert not rule_ids(recheck(result, cfg, lambda d: d.sheet == "036"), "PATH-004")
