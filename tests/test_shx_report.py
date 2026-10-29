"""SHX stroke decoding in the outputs: what was decoded, and which findings rest on decoded text."""

from __future__ import annotations

from drawing_qa import cli
from drawing_qa.model.enums import IssueCode
from drawing_qa.report.markdown import render_report
from drawing_qa.report.register import build_register
from drawing_qa.report.summary import summarise
from tests.conftest import cell_rect
from tests.test_shx_pipeline import cfg, package, run  # noqa: F401  (cfg is a fixture)

FAR_TAG = [(cell_rect("J4"), "S-TN37T4")]  # decoded at J4, its grid says T4: a decoded TAG-004 finding


def test_no_shx_text_message_says_decoded(tmp_path, cfg):
    result = run(package(tmp_path), cfg)
    [issue] = [i for i in result.issues if i.code is IssueCode.NO_SHX_TEXT and "037" in (i.file_path or "")]
    assert "decoded from line work" in issue.message and "PDFSHX" in issue.message


def test_report_has_decoding_section(tmp_path, cfg):
    result = run(package(tmp_path), cfg)
    text = render_report(result, summarise(result))
    assert "## Sheets decoded from line work" in text
    row = next(line for line in text.split("## Sheets decoded from line work")[1].splitlines() if line.startswith("| 401103 sh 037"))
    d = next(d for d in result.drawings if d.sheet == "037")
    assert f"{d.decoded.letters} / {d.decoded.shapes}" in row and str(d.decoded.words_kept) in row


def test_report_lists_sheets_left_unread(tmp_path, cfg):
    result = run(package(tmp_path, readable=False), cfg)
    text = render_report(result, summarise(result))
    assert "nothing to learn from" in text.split("## Sheets decoded from line work")[1]


def test_report_marks_decoded_findings(tmp_path, cfg):
    result = run(package(tmp_path, extra=FAR_TAG), cfg)
    [f] = [f for f in result.findings if f.rule_id == "TAG-004" and f.subject_key == "S-TN37T4"]
    line = next(line for line in render_report(result, summarise(result)).splitlines() if line.startswith(f"| {f.id} |"))
    assert "(decoded from line work)" in line


def test_register_marks_decoded_findings(tmp_path, cfg):
    result = run(package(tmp_path, extra=FAR_TAG), cfg)
    [f] = [f for f in result.findings if f.rule_id == "TAG-004" and f.subject_key == "S-TN37T4"]
    ws = build_register(result).active
    row = next(r for r in ws.iter_rows(values_only=True) if r and r[0] == f.id)
    assert "(decoded from line work)" in row[6]
    others = [r for r in ws.iter_rows(values_only=True) if r and r[0] and str(r[0]).startswith("F") and r[0] != f.id]
    assert all("(decoded from line work)" not in (r[6] or "") for r in others
               if not next(x for x in result.findings if x.id == r[0]).decoded)


def test_cli_says_how_many_were_decoded(tmp_path, cfg, capsys, monkeypatch):
    package(tmp_path / "pkg")
    monkeypatch.setattr(cli.Settings, "load", classmethod(lambda c, path=None: cfg))
    assert cli.main(["run", "--input", str(tmp_path / "pkg"), "--output", str(tmp_path / "out"), "--ocr"]) == 0
    assert "Decoded 1 of 1 unreadable sheet(s) from their line work" in capsys.readouterr().out


def test_report_names_rules_quiet_for_the_package(tmp_path, cfg):
    """A decoded termination or CBD sheet silences some rules for the whole package; silence must not read as clean."""
    from drawing_qa.model.enums import DocType

    result = run(package(tmp_path), cfg)
    d = next(d for d in result.drawings if d.sheet == "037")
    result.drawings.append(d.model_copy(update={"id": "TERM/x.pdf#p1", "doc_type": DocType.TERM, "sheet": "203"}))
    section = render_report(result, summarise(result)).split("## Sheets decoded from line work")[1]
    assert "DEV-003" in section and "WIRE-004" in section and "PATH-001" not in section


def test_decode_stage_is_recorded(tmp_path, cfg):
    result = run(package(tmp_path), cfg)
    [stage] = [s for s in result.stages if s.name == "decode"]
    assert "1 of 1" in stage.note


def test_report_counts_letters_read_by_scale(tmp_path, cfg):
    result = run(package(tmp_path), cfg)
    d = next(d for d in result.drawings if d.sheet == "037")
    d.decoded.scaled = 7
    text = render_report(result, summarise(result))
    row = next(line for line in text.split("## Sheets decoded from line work")[1].splitlines() if line.startswith("| 401103 sh 037"))
    assert f"{d.decoded.letters} / {d.decoded.shapes} (7 by scale)" in row


def _readability(result):
    from drawing_qa.report.summary import sheet_readability

    return {s.sheet: s for s in sheet_readability(result)}


def test_readability_of_each_sheet(tmp_path, cfg):
    """Read from SHX notes, decoded (in full or in part) with --ocr, or not read at all: where the checker looks harder."""
    result = run(package(tmp_path), cfg)
    sheets = _readability(result)
    assert sheets["401103 sh 036"].status == "Read"
    d = next(d for d in result.drawings if d.sheet == "037")
    assert sheets["401103 sh 037"].status == ("Decoded" if d.decoded.words_dropped == 0 else "Partially decoded")
    assert f"{d.decoded.letters}/{d.decoded.shapes} letters" in sheets["401103 sh 037"].detail

    assert _readability(run(package(tmp_path / "plain"), cfg, ocr=False))["401103 sh 037"].status == "Not read"
    assert "PDFSHX" in _readability(run(package(tmp_path / "plain"), cfg, ocr=False))["401103 sh 037"].detail


def test_summary_breaks_down_sheet_readability(tmp_path, cfg):
    result = run(package(tmp_path), cfg, ocr=False)
    summary = render_report(result, summarise(result)).split("## Findings by rule")[0]
    assert "### Sheets read" in summary
    assert "| PLC | 2 | 1 | 0 | 0 | 1 |" in summary  # sheets, read, decoded, partially decoded, not read
    row = next(line for line in summary.splitlines() if line.startswith("| 401103 sh 037"))
    assert "Not read" in row


def test_register_summary_lists_sheets_to_check_harder(tmp_path, cfg):
    result = run(package(tmp_path), cfg, ocr=False)
    rows = list(build_register(result)["Summary"].iter_rows(values_only=True))
    assert any(r[:6] == ("PLC", 2, 1, 0, 0, 1) for r in rows)
    sheet = next(r for r in rows if r[0] == "401103 sh 037")
    assert sheet[1] == "PLC" and sheet[2] == "Not read"
