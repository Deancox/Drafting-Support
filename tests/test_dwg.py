"""Text of a sheet plotted without SHX notes, read from its DWG (drawing_qa.dwg).

The ODA File Converter is not needed here: the DXF it would make is written with ezdxf, and the PDF with
the same line work with pymupdf, scaled and shifted the way a plot is.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import ezdxf
import pymupdf
import pytest

from drawing_qa import pipeline
from drawing_qa.config import Settings
from drawing_qa.dwg.text import dwg_saved, fit, pdf_created, place, read_dxf
from drawing_qa.ingest.discovery import discover
from drawing_qa.model.enums import ExtractionMethod, IssueCode
from drawing_qa.pdf.extract import _load_segments
from drawing_qa.report.summary import NOT_READ, READ_DWG, sheet_readability

S, OX, OY = 2.75, 40.0, 800.0  # the plot: x' = 40 + 2.75 x, y' = 800 - 2.75 y (mm -> pt)
LINES = [(0, 0, 400, 0), (400, 0, 400, 280), (400, 280, 0, 280), (0, 280, 0, 0),  # border
         (20, 100, 380, 100), (20, 150, 380, 150), (20, 200, 380, 200),
         (50, 20, 50, 260), (200, 20, 200, 260), (300, 20, 300, 260)]
# (text, x, y, height) of the visible texts, left-aligned on their baseline
TEXTS = [("S-TN52T3", 100, 210, 2.5), ("SHUTTLE WEST STAIR ACCESS", 100, 205, 2.5)]


def _pt(x, y):
    return OX + S * x, OY - S * y


def _dxf(path: Path) -> Path:
    doc = ezdxf.new("R2018")
    doc.layers.add("HIDDEN").off()
    msp = doc.modelspace()
    for x0, y0, x1, y1 in LINES:
        msp.add_line((x0, y0), (x1, y1))
    for text, x, y, h in TEXTS:
        msp.add_text(text, height=h, dxfattribs={"insert": (x, y)})
    msp.add_text("NOT PLOTTED", height=2.5, dxfattribs={"insert": (100, 230), "layer": "HIDDEN"})
    block = doc.blocks.new("TAG")
    block.add_attdef("TAG", (0, 0), dxfattribs={"height": 2.5})
    msp.add_blockref("TAG", (210, 180)).add_auto_attribs({"TAG": "S-TN52C2"})
    msp.add_mtext("LINE ONE\\PLINE TWO", dxfattribs={"insert": (250, 130), "char_height": 2.5, "attachment_point": 1})
    doc.saveas(path)
    return path


def _ink(page, text, x, y, h):
    """Short strokes where the plotted letters of a text would be."""
    x0, base = _pt(x, y)
    for i in range(len(text)):
        cx = x0 + (i + 0.4) * 0.72 * h * S
        page.draw_line((cx, base), (cx, base - h * S * 0.9), width=0.3)


def _pdf(path: Path, lines=LINES, ink=True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    for x0, y0, x1, y1 in lines:
        page.draw_line(_pt(x0, y0), _pt(x1, y1), width=0.5)
    if ink:
        for text, x, y, h in TEXTS + [("S-TN52C2", 210, 180, 2.5), ("LINE ONE", 250, 127.5, 2.5),
                                      ("LINE TWO", 250, 127.5 - 2.5 * 5 / 3, 2.5)]:
            _ink(page, text, x, y, h)
    doc.set_metadata({"creationDate": "D:20261030062140"})
    doc.save(path)
    doc.close()
    return path


def _segments(pdf: Path):
    return _load_segments(pymupdf.open(pdf)[0])


def test_read_dxf_keeps_plotted_text_and_attributes(tmp_path):
    sheet = read_dxf(_dxf(tmp_path / "a.dxf"))
    texts = {t.text for t in sheet.texts}
    assert {"S-TN52T3", "SHUTTLE WEST STAIR ACCESS", "S-TN52C2", "LINE ONE", "LINE TWO"} <= texts
    assert "NOT PLOTTED" not in texts  # on a layer that is off: not on the PDF
    assert len(sheet.lines) == len(LINES)


def test_text_is_placed_where_the_pdf_draws_it(tmp_path):
    sheet = read_dxf(_dxf(tmp_path / "a.dxf"))
    placed = place(sheet, _segments(_pdf(tmp_path / "a.pdf")))
    assert placed.reason == "" and placed.unplaced == []
    [tag] = [w for w in placed.words if w.text == "S-TN52T3"]
    assert tag.method is ExtractionMethod.DWG_TEXT
    x0, base = _pt(100, 210)
    assert abs(tag.bbox[0] - x0) < 3 and abs(tag.bbox[3] - base) < 3  # left end and baseline
    assert tag.bbox[1] < base - 2.5 * S * 0.8  # spans the letter height


def test_a_dwg_that_does_not_line_up_is_not_used(tmp_path):
    sheet = read_dxf(_dxf(tmp_path / "a.dxf"))
    other = [(0, 0, 400, 0), (400, 0, 400, 280), (400, 280, 0, 280), (0, 280, 0, 0),
             (20, 60, 380, 60), (20, 90, 380, 90), (120, 20, 120, 260), (250, 20, 250, 260)]
    where, reason = fit(sheet, _segments(_pdf(tmp_path / "b.pdf", lines=other, ink=False)))
    assert where is None and "straight lines" in reason


def test_dates(tmp_path):
    dwg = tmp_path / "a.dwg"
    xml = ('<prop_set fmt_id="{f29f85e0-4ff9-1068-ab91-08002b27b3d9}"><prop id="8"><string>bmaher</string></prop>'
           '<prop id="10"><datetime>2026-10-19T01:42:40</datetime></prop>'
           '<prop id="12"><datetime>2025-09-04T03:53:21</datetime></prop></prop_set>')
    dwg.write_bytes(b"AC1032\x00\x01\x02" + b"\x00" + xml.encode("utf-16le"))  # at an odd byte
    assert dwg_saved(dwg) == datetime(2026, 10, 19, 1, 42, 40, tzinfo=timezone.utc)
    assert pdf_created({"creationDate": "D:20261030062140+11'00'"}) == datetime(2026, 10, 29, 19, 21, 40, tzinfo=timezone.utc)
    assert pdf_created({"creationDate": "D:20261030062140"}).tzinfo is not None  # local time


# --- pipeline ------------------------------------------------------------------


@pytest.fixture
def cfg() -> Settings:
    s = Settings()
    s.pdf.min_drawing_bytes = 100  # the test PDF is small but drawn
    return s


@pytest.fixture
def fake_oda(monkeypatch, tmp_path):
    """Stand in for the ODA File Converter: every DWG 'converts' to the DXF written by the test."""
    dxf = _dxf(tmp_path / "made.dxf")
    monkeypatch.setattr(pipeline, "find_converter", lambda configured="": Path("ODAFileConverter.exe"))
    monkeypatch.setattr(pipeline, "convert", lambda dwgs, converter, out: {d: dxf for d in dwgs})
    return dxf


def test_pipeline_reads_text_from_the_dwg(tmp_path, cfg, fake_oda):
    root = tmp_path / "pkg"
    _pdf(root / "PLC" / "401104-052.pdf")
    (root / "PLC" / "401104-052.dwg").write_bytes(b"AC1032")
    result = pipeline.run_pipeline(root, cfg)
    [drawing] = result.drawings
    assert drawing.text_source == "401104-052.dwg" and not drawing.text_unreadable and drawing.decoded is None
    assert {"S-TN52T3", "S-TN52C2"} <= {c.subject_key for c in result.claims}
    assert not [i for i in result.issues if i.code in (IssueCode.NO_SHX_TEXT, IssueCode.IGNORED_FILE)]
    assert [s.status for s in sheet_readability(result)] == [READ_DWG]


def test_pipeline_refuses_a_dwg_that_is_not_the_plotted_one(tmp_path, cfg, fake_oda):
    root = tmp_path / "pkg"
    _pdf(root / "PLC" / "401104-052.pdf", lines=LINES[:4] + [(20, 60, 380, 60), (120, 20, 120, 260)], ink=False)
    (root / "PLC" / "401104-052.dwg").write_bytes(b"AC1032")
    result = pipeline.run_pipeline(root, cfg)
    assert result.drawings[0].text_source is None
    assert [i.code for i in result.issues if i.code is IssueCode.DWG_NOT_USED] == [IssueCode.DWG_NOT_USED]
    assert [s.status for s in sheet_readability(result)] == [NOT_READ]


def test_without_the_converter_the_sheet_says_why(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(pipeline, "find_converter", lambda configured="": None)
    root = tmp_path / "pkg"
    _pdf(root / "PLC" / "401104-052.pdf")
    (root / "PLC" / "401104-052.dwg").write_bytes(b"AC1032")
    result = pipeline.run_pipeline(root, cfg)
    [issue] = [i for i in result.issues if i.code is IssueCode.DWG_NOT_USED]
    assert "ODA File Converter was not found" in issue.message


def test_a_dwg_on_its_own_is_listed_as_ignored(tmp_path, cfg):
    _pdf(tmp_path / "PLC" / "401104-052.pdf")
    (tmp_path / "PLC" / "401104-052.dwg").write_bytes(b"AC1032")
    (tmp_path / "PLC" / "401104-099.dwg").write_bytes(b"AC1032")
    ignored = [i.file_path for i in discover(tmp_path, cfg).issues if i.code is IssueCode.IGNORED_FILE]
    assert ignored == ["PLC/401104-099.dwg"]
