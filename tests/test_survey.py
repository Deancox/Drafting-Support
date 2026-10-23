"""Survey: which PDFs in a set can be read from their SHX notes, and which would need decoding or OCR."""

from __future__ import annotations

from drawing_qa import cli
from drawing_qa.config import Settings
from drawing_qa.survey import survey
from tests.conftest import build_pdf
from tests.shx_font import as_strokes
from tests.test_rules import add_sheet
from tests.conftest import sheet_frame


def _cfg() -> Settings:
    s = Settings()
    s.pdf.min_drawing_bytes = 100  # the stroke-only test sheets are small
    return s


def _package(root):
    add_sheet(root, "PLC", "036")  # readable: SHX notes
    build_pdf(root / "PLC" / "401103-037.pdf", [[]], lines=[as_strokes(sheet_frame("401103", "037"))])  # strokes only
    build_pdf(root / "Terminations" / "401103-203.pdf", [[]], raster_pages=[0])  # a scan
    build_pdf(root / "Terminations" / "401103-204.pdf", [[]])  # blank
    # one file, two pages: page 1 readable, page 2 strokes only
    frame = sheet_frame("401103", "050")
    build_pdf(root / "PLC" / "401103-050.pdf", [[], []], lines=[[], as_strokes(frame)], shx=[frame, []])
    return root


def test_each_sheet_gets_a_status(tmp_path):
    rows = {(r.path, r.page): r.status for r in survey(_package(tmp_path), _cfg())}
    assert rows == {
        ("PLC/401103-036.pdf", 1): "readable",
        ("PLC/401103-037.pdf", 1): "decode",
        ("PLC/401103-050.pdf", 1): "readable",
        ("PLC/401103-050.pdf", 2): "decode",
        ("Terminations/401103-203.pdf", 1): "scanned",
        ("Terminations/401103-204.pdf", 1): "blank",
    }


def test_unopenable_file_is_listed_not_fatal(tmp_path):
    root = _package(tmp_path)
    (root / "PLC" / "broken.pdf").write_bytes(b"not a pdf")
    rows = {r.path: r.status for r in survey(root, _cfg())}
    assert rows["PLC/broken.pdf"] == "unreadable file"


def test_cli_survey_counts(tmp_path, capsys, monkeypatch):
    root = _package(tmp_path)
    monkeypatch.setattr(cli.Settings, "load", classmethod(lambda c, path=None: _cfg()))
    assert cli.main(["survey", "--input", str(root)]) == 0
    out = capsys.readouterr().out
    assert "5 PDF file(s), 6 sheet(s)" in out
    assert "SHX readable" in out and "Need decoding (--ocr)" in out
    lines = {ln.split(":")[0].strip(): ln for ln in out.splitlines() if ":" in ln}
    assert "2 sheet(s) in 2 file(s)" in lines["SHX readable"]
    assert "2 sheet(s) in 2 file(s)" in lines["Need decoding (--ocr)"]
    assert "1 sheet(s) in 1 file(s)" in lines["Scanned (need OCR)"]
    assert "401103-037" in out and "401103-050 (page 2)" in out
