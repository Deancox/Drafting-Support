import pymupdf
import pytest

from drawing_qa.common.grid import read_grid_frame
from drawing_qa.common.titleblock import read_title_block
from drawing_qa.config import GridConfig, TitleBlockConfig
from drawing_qa.model.enums import DocType, ExtractionMethod
from drawing_qa.parsers.titleblock import drawing_for_page
from drawing_qa.pdf.extract import extract_page
from tests.conftest import cell_rect, sheet_frame


def _page(tmp_path, make_pdf, items):
    path = make_pdf(tmp_path / "s.pdf", [[]], shx=[items])
    doc = pymupdf.open(path)
    return doc, extract_page(doc[0], min_words=1)


@pytest.mark.parametrize(
    ("descriptor", "doc_type"),
    [("SCHEMATIC DIAGRAM", DocType.PLC), ("TERMINATION DIAGRAM", DocType.TERM), ("CABLING BLOCK DIAGRAM", DocType.CBD)],
)
def test_title_block_fields(tmp_path, make_pdf, descriptor, doc_type):
    items = sheet_frame("401103", "036", "0", "FOR CONSTRUCTION", "LTL PLC SLOT 13 DIGITAL INPUT", descriptor)
    doc, page = _page(tmp_path, make_pdf, items)
    tb = read_title_block(page, TitleBlockConfig())
    doc.close()
    assert (tb.drawing_no, tb.sheet, tb.rev, tb.status) == ("401103", "036", "0", "FOR CONSTRUCTION")
    assert tb.title == "LTL PLC SLOT 13 DIGITAL INPUT"
    assert tb.descriptor == descriptor
    assert tb.doc_type is doc_type
    assert tb.missing == []


def test_title_page_has_no_drawing_type(tmp_path, make_pdf):
    doc, page = _page(tmp_path, make_pdf, sheet_frame(descriptor="TITLE PAGE", title="ELECTRICAL DETAILS"))
    tb = read_title_block(page, TitleBlockConfig())
    doc.close()
    assert tb.descriptor == "TITLE PAGE"
    assert tb.doc_type is None
    assert tb.title == "ELECTRICAL DETAILS"


def test_missing_title_block_falls_back_to_file_name(tmp_path, make_pdf):
    doc, page = _page(tmp_path, make_pdf, [((100, 100, 200, 110), "S-TN23N2")])
    drawing = drawing_for_page("PLC/401103-023.pdf", DocType.PLC, page, 1)
    doc.close()
    assert (drawing.drawing_no, drawing.sheet) == ("401103", "023")
    assert drawing.title_block_method is ExtractionMethod.FILENAME
    assert set(drawing.missing_title_fields) == {"drawing_no", "sheet", "rev", "title", "status"}


def test_grid_frame_and_cells(tmp_path, make_pdf):
    doc, page = _page(tmp_path, make_pdf, sheet_frame())
    grid = read_grid_frame(page, GridConfig())
    doc.close()
    assert grid is not None
    assert grid.row_labels == list("ABCDEFGHJKLMNPQRSTUVWXYZ")
    assert [c[0] for c in grid.cols] == [str(i) for i in range(10)]
    x0, y0, x1, y1 = cell_rect("N2")
    assert grid.cell_at((x0 + x1) / 2, (y0 + y1) / 2) == "N2"


def test_grid_ignores_stray_margin_letters(tmp_path, make_pdf):
    items = sheet_frame() + [((54, 762, 62, 772), "B"), ((53, 775, 63, 785), "A")]  # revision table letters
    doc, page = _page(tmp_path, make_pdf, items)
    grid = read_grid_frame(page, GridConfig())
    doc.close()
    assert grid.row_labels[0] == "A" and grid.row_labels[-1] == "Z" and len(grid.rows) == 24


def test_no_grid_frame(tmp_path, make_pdf):
    doc, page = _page(tmp_path, make_pdf, [((100, 100, 200, 110), "TEXT ONLY")])
    assert read_grid_frame(page, GridConfig()) is None
    doc.close()


def test_claims_carry_grid_cells(tmp_path, make_pdf, settings):
    from drawing_qa.parsers.base import ParseContext
    from drawing_qa.parsers.termination import TerminationParser

    items = sheet_frame() + [(cell_rect("D5"), "S-TN35T4")]
    doc, page = _page(tmp_path, make_pdf, items)
    drawing = drawing_for_page("Terminations/401103-203.pdf", DocType.TERM, page, 1, settings)
    result = TerminationParser().parse(page, drawing, ParseContext(settings))
    doc.close()
    claim = next(c for c in result.claims if c.subject_key == "S-TN35T4")
    assert claim.source.grid_cell == "D5"


def test_grid_fills_a_missing_label(tmp_path, make_pdf):
    """A row letter that could not be read (decoded sheet 069 lost its S) must not shift its cells to a neighbour."""
    items = [it for it in sheet_frame() if it[1] not in ("S", "4")]
    doc, page = _page(tmp_path, make_pdf, items)
    grid = read_grid_frame(page, GridConfig())
    doc.close()
    assert grid.row_labels == list("ABCDEFGHJKLMNPQRSTUVWXYZ")
    assert [c[0] for c in grid.cols] == [str(i) for i in range(10)]
    x0, y0, x1, y1 = cell_rect("S4")
    assert grid.cell_at((x0 + x1) / 2, (y0 + y1) / 2 + 3) == "S4"


def test_grid_puts_back_end_columns_inside_the_frame(tmp_path, make_pdf):
    """Decoded sheets lose their end column numbers (0, 8, 9 in a size not learned): without them every
    cell in columns 8-9 was put in column 7 (E-TL18Q9 'at Q7' on 401104-018)."""
    items = [it for it in sheet_frame() if it[1] not in ("0", "8", "9")]
    doc, page = _page(tmp_path, make_pdf, items)
    grid = read_grid_frame(page, GridConfig())
    doc.close()
    assert [c[0] for c in grid.cols] == [str(i) for i in range(10)]
    x0, y0, x1, y1 = cell_rect("Q9")
    assert grid.cell_at((x0 + x1) / 2, (y0 + y1) / 2) == "Q9"


def test_point_past_the_grid_read_has_no_cell(tmp_path, make_pdf):
    """Without the right-edge row letters the end columns cannot be put back: a point there gets no cell
    rather than the last column read."""
    items = [it for it in sheet_frame() if it[1] not in ("8", "9") and it[0][0] != 1145]
    doc, page = _page(tmp_path, make_pdf, items)
    grid = read_grid_frame(page, GridConfig())
    doc.close()
    assert grid.cols[-1][0] == "7"
    x0, y0, x1, y1 = cell_rect("Q9")
    assert grid.cell_at((x0 + x1) / 2, (y0 + y1) / 2) is None
    x0, y0, x1, y1 = cell_rect("Q7")
    assert grid.cell_at((x0 + x1) / 2, (y0 + y1) / 2) == "Q7"
