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
    row = next(line for line in text.splitlines() if line.startswith("| 401103 sh 037"))
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
