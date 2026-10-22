import pytest

from drawing_qa.ingest.discovery import discover
from drawing_qa.model.enums import DocType, IssueCode


def codes(result):
    return [i.code for i in result.issues]


def test_sample_package_discovers_all_three_types(sample_package, settings):
    result = discover(sample_package, settings)
    assert sorted(f.record.doc_type for f in result.files) == [DocType.CBD, DocType.PLC, DocType.TERM]
    assert result.issues == []
    assert all(len(f.record.sha256) == 64 for f in result.files)


def test_folder_names_are_case_insensitive_with_aliases(tmp_path, make_pdf, settings):
    make_pdf(tmp_path / "plc" / "a.pdf", [[(50, 50, "A")]])
    make_pdf(tmp_path / "CBD" / "b.pdf", [[(50, 50, "B")]])
    make_pdf(tmp_path / "Terms" / "sub" / "c.pdf", [[(50, 50, "C")]])
    result = discover(tmp_path, settings)
    assert {f.record.path: f.record.doc_type for f in result.files} == {
        "plc/a.pdf": DocType.PLC,
        "CBD/b.pdf": DocType.CBD,
        "Terms/sub/c.pdf": DocType.TERM,
    }


def test_missing_folder_is_reported(tmp_path, make_pdf, settings):
    make_pdf(tmp_path / "PLC" / "a.pdf", [[(50, 50, "A")]])
    result = discover(tmp_path, settings)
    assert codes(result).count(IssueCode.MISSING_FOLDER) == 2


def test_ignored_unknown_and_duplicate_files(tmp_path, make_pdf, settings):
    make_pdf(tmp_path / "PLC" / "a.pdf", [[(50, 50, "A")]])
    (tmp_path / "PLC" / "a copy.pdf").write_bytes((tmp_path / "PLC" / "a.pdf").read_bytes())
    (tmp_path / "PLC" / "notes.txt").write_text("x")
    (tmp_path / "PLC" / "~$lock.pdf").write_text("x")
    (tmp_path / "readme.txt").write_text("x")
    make_pdf(tmp_path / "Vendor" / "v.pdf", [[(50, 50, "V")]])
    result = discover(tmp_path, settings)
    found = codes(result)
    assert [f.record.path for f in result.files] == ["PLC/a copy.pdf"] or [f.record.path for f in result.files] == ["PLC/a.pdf"]
    assert IssueCode.DUPLICATE_FILE in found
    assert found.count(IssueCode.IGNORED_FILE) == 2  # notes.txt and readme.txt; ~$lock is skipped silently
    assert IssueCode.UNKNOWN_FOLDER in found


def test_empty_type_folder(tmp_path, settings):
    (tmp_path / "PLC").mkdir()
    result = discover(tmp_path, settings)
    assert IssueCode.EMPTY_FOLDER in codes(result)


def test_missing_root_raises(tmp_path, settings):
    with pytest.raises(FileNotFoundError):
        discover(tmp_path / "nope", settings)


def test_equipment_list_folder_accepts_spreadsheets_and_is_optional(sample_package, settings):
    folder = sample_package / "EquipmentList"
    folder.mkdir()
    (folder / "EEL rev C.xlsx").write_bytes(b"PK\x03\x04 stand-in workbook")
    (folder / "notes.docx").write_bytes(b"x")
    result = discover(sample_package, settings)
    equip = [f.record for f in result.files if f.record.doc_type is DocType.EQUIP]
    assert [r.path for r in equip] == ["EquipmentList/EEL rev C.xlsx"]
    assert codes(result) == [IssueCode.IGNORED_FILE]  # the .docx only; no MISSING_FOLDER without it either


def test_package_without_equipment_list_reports_nothing_missing(sample_package, settings):
    assert IssueCode.MISSING_FOLDER not in codes(discover(sample_package, settings))
