"""M5: QA register and review carry-forward."""

import shutil

import pytest
from openpyxl import load_workbook

from drawing_qa.model.enums import Decision, IssueCode
from drawing_qa.pipeline import run_pipeline
from drawing_qa.report.register import COL_DECISION, FINDINGS_SHEET, HEADER_ROW, REGISTER_NAME
from drawing_qa.report.writer import write_outputs
from drawing_qa.review import STORE_NAME, carry_forward
from drawing_qa.review.register_reader import RegisterError, read_register


def run(package, settings, out):
    result = run_pipeline(package, settings)
    carry_forward(result, out)
    write_outputs(result, out)
    return result


def edit_register(path, fingerprint, decision, comment="", reviewer="DC", date="2026-10-22"):
    wb = load_workbook(path)
    ws = wb[FINDINGS_SHEET]
    header = [c.value for c in ws[HEADER_ROW]]
    col = {name: header.index(name) + 1 for name in header}
    for row in range(HEADER_ROW + 1, ws.max_row + 1):
        if ws.cell(row=row, column=col["Fingerprint"]).value == fingerprint:
            # .value = ... (ws.cell(value=None) would leave the cell unchanged)
            ws.cell(row=row, column=col[COL_DECISION]).value = decision
            ws.cell(row=row, column=col["Checker comment"]).value = comment
            ws.cell(row=row, column=col["Reviewed by"]).value = reviewer
            ws.cell(row=row, column=col["Date"]).value = date
            break
    else:
        raise AssertionError(f"{fingerprint} not in register")
    wb.save(path)


def by_rule(result, rule_id):
    return next(f for f in result.findings if f.rule_id == rule_id)


def test_register_is_written_with_every_finding(sample_package, settings, tmp_path):
    out = tmp_path / "out"
    result = run(sample_package, settings, out)
    wb = load_workbook(out / REGISTER_NAME)
    assert wb.sheetnames == ["Findings", "Tag Index", "No Longer Raised", "Summary", "Coverage", "Waivers", "Key"]
    ws = wb[FINDINGS_SHEET]
    header = [c.value for c in ws[HEADER_ROW]]
    assert header[:7] == ["Ref", "Severity", "Rule", "Category", "Tag / item", "PLC channel", "Conflict / issue"]
    fingerprints = [r[header.index("Fingerprint")] for r in ws.iter_rows(min_row=HEADER_ROW + 1, values_only=True)]
    assert sorted(fingerprints) == sorted(f.fingerprint for f in result.findings)
    assert (out / STORE_NAME).is_file()
    key = [r[0] for r in wb["Key"].iter_rows(values_only=True)]
    assert "TAG-001" in key and "TERM-001" in key  # built and planned rules both listed


def test_decision_is_carried_to_the_next_run(sample_package, settings, tmp_path):
    out = tmp_path / "out"
    first = run(sample_package, settings, out)
    target = by_rule(first, "PLACEHOLDER-004")
    assert target.status is Decision.OPEN and target.first_seen

    edit_register(out / REGISTER_NAME, target.fingerprint, "FALSE_POSITIVE", "HOLD is part of SPARE HOLD label")
    second = run(sample_package, settings, out)
    carried = next(f for f in second.findings if f.fingerprint == target.fingerprint)
    assert carried.status is Decision.FALSE_POSITIVE
    assert carried.review.comment == "HOLD is part of SPARE HOLD label" and carried.review.reviewer == "DC"
    assert carried.first_seen == target.first_seen
    assert second.review.decisions_recorded == 1
    assert second.review.new_findings == 0 and second.review.no_longer_raised == []

    third = run(sample_package, settings, out)  # register now written by run 2, decision still in it
    assert third.review.decisions_recorded == 0
    assert next(f for f in third.findings if f.fingerprint == target.fingerprint).status is Decision.FALSE_POSITIVE


def test_decision_survives_a_lost_register(sample_package, settings, tmp_path):
    out = tmp_path / "out"
    target = by_rule(run(sample_package, settings, out), "PLACEHOLDER-004")
    edit_register(out / REGISTER_NAME, target.fingerprint, "CONFIRMED")
    run(sample_package, settings, out)
    (out / REGISTER_NAME).unlink()  # the store keeps it
    again = run(sample_package, settings, out)
    assert next(f for f in again.findings if f.fingerprint == target.fingerprint).status is Decision.CONFIRMED


def test_clearing_a_decision_reopens_it(sample_package, settings, tmp_path):
    out = tmp_path / "out"
    target = by_rule(run(sample_package, settings, out), "PLACEHOLDER-004")
    edit_register(out / REGISTER_NAME, target.fingerprint, "DEFERRED", "later")
    run(sample_package, settings, out)
    edit_register(out / REGISTER_NAME, target.fingerprint, None, None, None, None)
    reopened = next(f for f in run(sample_package, settings, out).findings if f.fingerprint == target.fingerprint)
    assert reopened.status is Decision.OPEN


def test_unknown_decision_keeps_the_previous_one(sample_package, settings, tmp_path):
    out = tmp_path / "out"
    target = by_rule(run(sample_package, settings, out), "PLACEHOLDER-004")
    edit_register(out / REGISTER_NAME, target.fingerprint, "CONFIRMED")
    run(sample_package, settings, out)
    edit_register(out / REGISTER_NAME, target.fingerprint, "maybe")
    result = run(sample_package, settings, out)
    assert next(f for f in result.findings if f.fingerprint == target.fingerprint).status is Decision.CONFIRMED
    assert IssueCode.REGISTER_ROW_SKIPPED in [i.code for i in result.issues]


def test_decision_spelling_is_forgiving(sample_package, settings, tmp_path):
    out = tmp_path / "out"
    target = by_rule(run(sample_package, settings, out), "PLACEHOLDER-004")
    edit_register(out / REGISTER_NAME, target.fingerprint, "false positive")
    result = run(sample_package, settings, out)
    assert next(f for f in result.findings if f.fingerprint == target.fingerprint).status is Decision.FALSE_POSITIVE


def test_fixed_finding_is_listed_as_no_longer_raised(sample_package, settings, tmp_path):
    out = tmp_path / "out"
    target = by_rule(run(sample_package, settings, out), "PLACEHOLDER-003")  # REF TERM XXX on the termination sheet
    edit_register(out / REGISTER_NAME, target.fingerprint, "CONFIRMED")
    shutil.rmtree(sample_package / "Terminations")
    result = run(sample_package, settings, out)
    gone = {g.fingerprint: g for g in result.review.no_longer_raised}
    assert gone[target.fingerprint].decision is Decision.CONFIRMED
    rows = list(load_workbook(out / REGISTER_NAME)["No Longer Raised"].iter_rows(min_row=4, values_only=True))
    assert any(r[-1] == target.fingerprint for r in rows)


def test_locked_register_writes_a_copy_that_is_read_next_time(sample_package, settings, tmp_path, monkeypatch):
    from openpyxl.workbook.workbook import Workbook

    out = tmp_path / "out"
    first = run(sample_package, settings, out)
    target = by_rule(first, "PLACEHOLDER-004")

    real_save = Workbook.save

    def locked(self, filename):
        if str(filename).endswith(REGISTER_NAME):
            raise PermissionError("open in Excel")
        return real_save(self, filename)

    monkeypatch.setattr(Workbook, "save", locked)
    second = run(sample_package, settings, out)
    monkeypatch.setattr(Workbook, "save", real_save)
    copy = out / f"QA_Register-{second.info.run_id}.xlsx"
    assert copy.is_file()

    edit_register(copy, target.fingerprint, "WAIVED", "accepted")  # the checker works in the copy
    third = run(sample_package, settings, out)
    assert next(f for f in third.findings if f.fingerprint == target.fingerprint).status is Decision.WAIVED
    assert not copy.exists() and (out / "register_history" / copy.name).is_file()


def test_repeated_occurrences_get_their_own_fingerprints(tmp_path, make_pdf, settings):
    make_pdf(tmp_path / "pkg" / "PLC" / "a.pdf", [[(50, 50, "CXXXXX"), (50, 80, "CXXXXX")]])
    result = run_pipeline(tmp_path / "pkg", settings)
    fps = [f.fingerprint for f in result.findings if f.subject_key == "CXXXXX"]
    assert len(fps) == 2 and len(set(fps)) == 2
    assert sorted(fps)[1] == sorted(fps)[0] + "-2"


def test_unreadable_register_is_reported_not_fatal(sample_package, settings, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / REGISTER_NAME).write_bytes(b"not a workbook")
    with pytest.raises(RegisterError):
        read_register(out / REGISTER_NAME)
    result = run(sample_package, settings, out)
    assert IssueCode.REGISTER_UNREADABLE in [i.code for i in result.issues]


# --- No Longer Raised keeps decided findings until FIXED -------------------------------

def gone_ids(result):
    return {g.fingerprint for g in result.review.no_longer_raised}


def edit_gone(path, fingerprint, decision):
    from drawing_qa.report.register import GONE_SHEET

    wb = load_workbook(path)
    ws = wb[GONE_SHEET]
    header = [c.value for c in ws[HEADER_ROW]]
    for row in range(HEADER_ROW + 1, ws.max_row + 1):
        if ws.cell(row=row, column=header.index("Fingerprint") + 1).value == fingerprint:
            ws.cell(row=row, column=header.index(COL_DECISION) + 1).value = decision
            break
    else:
        raise AssertionError(f"{fingerprint} not on {GONE_SHEET}")
    wb.save(path)


def test_decided_finding_stays_no_longer_raised_until_fixed(sample_package, settings, tmp_path):
    out = tmp_path / "out"
    first = run(sample_package, settings, out)
    decided = by_rule(first, "PLACEHOLDER-003")
    edit_register(out / REGISTER_NAME, decided.fingerprint, "CONFIRMED")
    run(sample_package, settings, out)
    shutil.rmtree(sample_package / "Terminations")  # the REF TERM XXX sheet is gone: "fixed"

    second = run(sample_package, settings, out)
    assert decided.fingerprint in gone_ids(second)
    third = run(sample_package, settings, out)  # still listed: it has a decision and is not FIXED
    gone = {g.fingerprint: g for g in third.review.no_longer_raised}
    assert gone[decided.fingerprint].decision is Decision.CONFIRMED and gone[decided.fingerprint].last_raised

    edit_gone(out / REGISTER_NAME, decided.fingerprint, "FIXED")
    fourth = run(sample_package, settings, out)
    assert fourth.review.decisions_recorded == 1  # read from the No Longer Raised tab
    assert decided.fingerprint not in gone_ids(fourth)


def test_undecided_finding_is_listed_once(sample_package, settings, tmp_path):
    out = tmp_path / "out"
    first = run(sample_package, settings, out)
    undecided = by_rule(first, "PLACEHOLDER-003")
    shutil.rmtree(sample_package / "Terminations")
    assert undecided.fingerprint in gone_ids(run(sample_package, settings, out))
    assert undecided.fingerprint not in gone_ids(run(sample_package, settings, out))


# --- Waivers -----------------------------------------------------------------------------

def waiver(**kw):
    from drawing_qa.config import Waiver

    base = dict(id="W-001", rule="PLACEHOLDER-*", subject="HOLD", reason="HOLD is part of the spare label", approved_by="DC")
    return Waiver(**{**base, **kw})


def run_waived(package, settings, out, today=None):
    from datetime import date

    result = run_pipeline(package, settings)
    carry_forward(result, out, settings, today=today or date(2026, 10, 22))
    write_outputs(result, out)
    return result


def test_waiver_marks_matching_findings(sample_package, settings, tmp_path):
    settings.waivers = [waiver()]
    result = run_waived(sample_package, settings, tmp_path / "out")
    hold = by_rule(result, "PLACEHOLDER-004")
    assert hold.status is Decision.WAIVED and hold.review.waiver == "W-001"
    assert hold.review.comment == "Waiver W-001: HOLD is part of the spare label"
    assert by_rule(result, "PLACEHOLDER-002").status is Decision.OPEN  # CXXXXX does not match
    use = result.review.waivers[0]
    assert (use.status, use.matched, use.overridden) == ("ACTIVE", 1, 0)
    rows = list(load_workbook(tmp_path / "out" / REGISTER_NAME)["Waivers"].iter_rows(min_row=4, values_only=True))
    assert rows[0][:3] == ("W-001", "PLACEHOLDER-*", "HOLD")


def test_removing_a_waiver_reopens_its_findings(sample_package, settings, tmp_path):
    out = tmp_path / "out"
    settings.waivers = [waiver()]
    target = by_rule(run_waived(sample_package, settings, out), "PLACEHOLDER-004")
    settings.waivers = []
    result = run_waived(sample_package, settings, out)  # the register still says WAIVED / "Waiver W-001: ..."
    assert next(f for f in result.findings if f.fingerprint == target.fingerprint).status is Decision.OPEN
    assert result.review.decisions_recorded == 0


def test_checker_decision_overrides_a_waiver(sample_package, settings, tmp_path):
    out = tmp_path / "out"
    settings.waivers = [waiver()]
    target = by_rule(run_waived(sample_package, settings, out), "PLACEHOLDER-004")
    edit_register(out / REGISTER_NAME, target.fingerprint, "CONFIRMED", "HOLD really is a hold here")
    result = run_waived(sample_package, settings, out)
    f = next(f for f in result.findings if f.fingerprint == target.fingerprint)
    assert f.status is Decision.CONFIRMED and f.review.waiver is None
    assert result.review.waivers[0].overridden == 1


def test_expired_waiver_is_listed_not_applied(sample_package, settings, tmp_path):
    from datetime import date

    settings.waivers = [waiver(expires=date(2026, 10, 1))]
    result = run_waived(sample_package, settings, tmp_path / "out", today=date(2026, 10, 22))
    assert by_rule(result, "PLACEHOLDER-004").status is Decision.OPEN
    assert result.review.waivers[0].status == "EXPIRED"
    assert "expired: W-001" in next(s for s in result.stages if s.name == "review").note


def test_waivers_load_from_waivers_toml(tmp_path):
    from datetime import date

    from drawing_qa.config import Settings

    (tmp_path / "project.toml").write_text('[project]\ncode = "T"\n', encoding="utf-8")
    (tmp_path / "waivers.toml").write_text(
        '[[waiver]]\nid = "W-001"\nrule = "COMP-002"\nsubject = "K-TN32*"\nreason = "Interface"\nexpires = 2027-06-30\n',
        encoding="utf-8",
    )
    s = Settings.load(tmp_path / "project.toml")
    assert [(w.id, w.rule, w.subject, w.expires) for w in s.waivers] == [("W-001", "COMP-002", "K-TN32*", date(2027, 6, 30))]


# --- review off (the default): a register for printing and marking up by hand ----------

def _cli(package, out, config_text):
    from drawing_qa.cli import main

    config = out.parent / "project.toml"
    config.write_text(config_text, encoding="utf-8")
    assert main(["run", "--input", str(package), "--output", str(out), "--config", str(config)]) == 0


def test_default_register_has_no_review_columns_or_tabs(sample_package, tmp_path, capsys):
    out = tmp_path / "out"
    _cli(sample_package, out, '[project]\ncode = "TEST"\n')
    wb = load_workbook(out / REGISTER_NAME)
    assert wb.sheetnames == ["Findings", "Tag Index", "Summary", "Coverage", "Key"]
    header = [c.value for c in wb[FINDINGS_SHEET][HEADER_ROW]]
    assert header[-1] == "Recommendation"
    assert COL_DECISION not in header and "Fingerprint" not in header
    assert not wb[FINDINGS_SHEET].data_validations.dataValidation
    assert not (out / STORE_NAME).exists()
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "| Decision |" not in report
    assert "review:" not in capsys.readouterr().out


def test_review_enabled_keeps_the_decision_columns(sample_package, tmp_path, capsys):
    out = tmp_path / "out"
    _cli(sample_package, out, '[project]\ncode = "TEST"\n[review]\nenabled = true\n')
    wb = load_workbook(out / REGISTER_NAME)
    assert wb.sheetnames == ["Findings", "Tag Index", "No Longer Raised", "Summary", "Coverage", "Waivers", "Key"]
    assert COL_DECISION in [c.value for c in wb[FINDINGS_SHEET][HEADER_ROW]]
    assert (out / STORE_NAME).exists()
    assert "review:" in capsys.readouterr().out
