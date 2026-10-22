import json

from drawing_qa.cli import main
from drawing_qa.model.enums import IssueCode, StageStatus
from drawing_qa.pipeline import run_pipeline


def test_cli_writes_three_outputs(sample_package, tmp_path, capsys):
    out = tmp_path / "out"
    config = tmp_path / "project.toml"  # independent of the project's own config/project.toml
    config.write_text('[project]\ncode = "TEST"\n', encoding="utf-8")
    assert main(["run", "--input", str(sample_package), "--output", str(out), "--config", str(config)]) == 0

    review = json.loads((out / "review.json").read_text(encoding="utf-8"))
    findings = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    report = (out / "report.md").read_text(encoding="utf-8")

    assert review["schema_version"] == findings["schema_version"]
    assert len(review["files"]) == 3
    assert len(review["drawings"]) == 3
    assert review["summary"]["by_doc_type"]["TERM"]["identifiers_by_kind"]["TERMINAL"] == 2
    keys = {c["subject_key"] for c in review["claims"]}
    assert {"S-TN39N4", "TN39N6", "SPL-JB1", "SPL-JB1:1", "LTL-PLC", "CXXXXX", "HOLD", "REF TERM XXX"} <= keys
    rules = {f["rule_id"] for f in findings["findings"]}
    assert {"PLACEHOLDER-002", "PLACEHOLDER-003", "PLACEHOLDER-004"} <= rules
    assert {r["rule_id"] for r in review["rule_runs"]} >= {"TAG-001", "DRW-006", "PLACEHOLDER-001"}
    assert "# Drawing QA Report" in report
    assert "## Findings by rule" in report
    assert "wrote" in capsys.readouterr().out


def test_stages_report_what_is_not_built(sample_package, settings):
    result = run_pipeline(sample_package, settings)
    status = {s.name: s.status for s in result.stages}
    assert status["ingest"] is StageStatus.DONE
    assert status["parse"] is StageStatus.DONE
    assert status["rules"] is StageStatus.DONE
    assert status["link"] is StageStatus.DONE
    assert status["trace"] is StageStatus.DONE


def test_raster_and_unreadable_files_do_not_stop_the_run(tmp_path, make_pdf, settings):
    root = tmp_path / "pkg"
    make_pdf(root / "PLC" / "scan.pdf", [[], [(50, 50, "S-TN39N4 A B C D")]], raster_pages=[0])
    (root / "Terminations").mkdir(parents=True)
    (root / "Terminations" / "broken.pdf").write_bytes(b"not a pdf")
    result = run_pipeline(root, settings)
    codes = [i.code for i in result.issues]
    assert IssueCode.NEEDS_OCR in codes
    assert IssueCode.UNREADABLE_FILE in codes
    assert any(c.subject_key == "S-TN39N4" for c in result.claims)


def test_missing_input_folder_returns_error(tmp_path, capsys):
    assert main(["run", "--input", str(tmp_path / "nope")]) == 2


def test_equipment_list_is_listed_not_parsed(sample_package, settings):
    (sample_package / "EquipmentList").mkdir()
    (sample_package / "EquipmentList" / "eel.csv").write_text("TAG,DESCRIPTION\nS-TN39N4,PUMP\n", encoding="utf-8")
    result = run_pipeline(sample_package, settings)
    assert [f.path for f in result.files if f.doc_type.value == "EQUIP"] == ["EquipmentList/eel.csv"]
    assert len(result.drawings) == 3  # no sheets made from the list
    parse = next(s for s in result.stages if s.name == "parse")
    assert "EQUIP: 1 equipment list file(s) listed" in parse.note


# --- choosing which inputs are reviewed (--exclude / --include) --------------------------

def _cli_run(package, tmp_path, *extra, config_text='[project]\ncode = "TEST"\n'):
    config = tmp_path / "project.toml"
    config.write_text(config_text, encoding="utf-8")
    out = tmp_path / "out"
    code = main(["run", "--input", str(package), "--output", str(out), "--config", str(config), *extra])
    review = json.loads((out / "review.json").read_text(encoding="utf-8")) if code == 0 else None
    return code, review


def test_all_inputs_are_reviewed_by_default(sample_package, tmp_path, capsys):
    code, review = _cli_run(sample_package, tmp_path)
    assert code == 0
    assert "reviewed: PLC, CBD, TERM, EQUIP" in capsys.readouterr().out
    assert "left out" not in next(s for s in review["stages"] if s["name"] == "rules")["note"]


def test_exclude_terminations(sample_package, tmp_path, capsys):
    code, review = _cli_run(sample_package, tmp_path, "--exclude", "Terminations")
    assert code == 0
    assert "left out: TERM" in capsys.readouterr().out
    assert len(review["files"]) == 3  # still listed
    findings = json.loads((tmp_path / "out" / "findings.json").read_text(encoding="utf-8"))["findings"]
    term = {d["id"] for d in review["drawings"] if d["doc_type"] == "TERM"}
    assert not [f for f in findings if any(e["source"] and e["source"]["drawing_id"] in term for e in f["evidence"])]
    assert "left out of the review: TERM" in next(s for s in review["stages"] if s["name"] == "rules")["note"]


def test_exclude_several_types_and_aliases(sample_package, tmp_path, capsys):
    code, _ = _cli_run(sample_package, tmp_path, "--exclude", "cbd,term", "--exclude", "equipment")
    assert code == 0
    assert "reviewed: PLC; left out: CBD, TERM, EQUIP" in capsys.readouterr().out


def test_excluding_everything_still_writes_outputs(sample_package, tmp_path):
    code, review = _cli_run(sample_package, tmp_path, "--exclude", "plc,cbd,term,equip")
    assert code == 0 and review["files"]
    assert json.loads((tmp_path / "out" / "findings.json").read_text(encoding="utf-8"))["findings"] == []


def test_excluded_missing_folder_is_not_a_warning(tmp_path, make_pdf, capsys):
    make_pdf(tmp_path / "pkg" / "PLC" / "a.pdf", [[(50, 50, "A")]])
    code, review = _cli_run(tmp_path / "pkg", tmp_path, "--exclude", "cbd,term")
    assert code == 0
    assert [i for i in review["issues"] if i["code"] == "MISSING_FOLDER"] == []


def test_include_overrides_the_config(sample_package, tmp_path, capsys):
    code, _ = _cli_run(sample_package, tmp_path, "--include", "term",
                       config_text='[ingest]\nunchecked_types = ["TERM"]\n')
    assert code == 0 and "reviewed: PLC, CBD, TERM, EQUIP" in capsys.readouterr().out


def test_unknown_or_contradictory_input_type_is_an_error(sample_package, tmp_path, capsys):
    assert _cli_run(sample_package, tmp_path, "--exclude", "drawings")[0] == 2
    assert "unknown input type 'drawings'" in capsys.readouterr().err
    assert _cli_run(sample_package, tmp_path, "--exclude", "term", "--include", "term")[0] == 2


# --- sheets plotted without AutoCAD's SHX text notes -------------------------------------

def _line_work(n=400):
    return [(40 + (i % 50) * 20, 40 + (i // 50) * 60, 50 + (i % 50) * 20, 90 + (i // 50) * 60) for i in range(n)]


def test_drawn_sheet_without_text_is_reported_for_replot(tmp_path, make_pdf, settings):
    make_pdf(tmp_path / "pkg" / "PLC" / "401002-492.pdf", [[]], lines=[_line_work()])
    result = run_pipeline(tmp_path / "pkg", settings)
    issues = [i for i in result.issues if i.code is IssueCode.NO_SHX_TEXT]
    assert len(issues) == 1 and "PDFSHX set to 1" in issues[0].message and "no text at all" in issues[0].message
    assert IssueCode.EMPTY_PAGE not in [i.code for i in result.issues]
    assert result.drawings[0].page_kind.value == "NO_TEXT"


def test_blank_page_is_still_empty(tmp_path, make_pdf, settings):
    make_pdf(tmp_path / "pkg" / "PLC" / "blank.pdf", [[]])
    codes = [i.code for i in run_pipeline(tmp_path / "pkg", settings).issues]
    assert IssueCode.EMPTY_PAGE in codes and IssueCode.NO_SHX_TEXT not in codes


def test_a_few_truetype_words_do_not_make_a_drawn_sheet_readable(tmp_path, make_pdf, settings):
    words = [(60 + 40 * i, 60, w) for i, w in enumerate(["PORT", "WARATAH", "COAL", "SERVICES", "KOORAGANG", "MRA"])]
    make_pdf(tmp_path / "pkg" / "PLC" / "401002-490.pdf", [words], lines=[_line_work()])
    issues = [i for i in run_pipeline(tmp_path / "pkg", settings).issues if i.code is IssueCode.NO_SHX_TEXT]
    assert len(issues) == 1 and "no title block" in issues[0].message


def test_cli_warns_about_unreadable_sheets(tmp_path, make_pdf, capsys):
    make_pdf(tmp_path / "pkg" / "PLC" / "401002-492.pdf", [[]], lines=[_line_work()])
    config = tmp_path / "project.toml"
    config.write_text('[project]\ncode = "T"\n', encoding="utf-8")
    assert main(["run", "--input", str(tmp_path / "pkg"), "--output", str(tmp_path / "out"), "--config", str(config)]) == 0
    assert "1 sheet(s) have no readable text" in capsys.readouterr().out
