import pymupdf

from drawing_qa.model.enums import PageKind
from drawing_qa.pdf.extract import classify_page, extract_page


def test_classify_page():
    assert classify_page(word_count=40, image_count=0, min_words=5) is PageKind.VECTOR
    assert classify_page(word_count=40, image_count=3, min_words=5) is PageKind.VECTOR
    assert classify_page(word_count=1, image_count=1, min_words=5) is PageKind.RASTER
    assert classify_page(word_count=2, image_count=0, min_words=5) is PageKind.VECTOR
    assert classify_page(word_count=0, image_count=0, min_words=5) is PageKind.EMPTY


def test_words_lines_and_positions(tmp_path, make_pdf):
    path = make_pdf(tmp_path / "a.pdf", [[(50, 50, "SPL-JB1:1 TN39N6 S-TN39N4"), (50, 200, "SECOND LINE HERE")]])
    with pymupdf.open(path) as doc:
        content = extract_page(doc[0], min_words=5)
        assert content.kind is PageKind.VECTOR
        assert [w.text for w in content.words][:3] == ["SPL-JB1:1", "TN39N6", "S-TN39N4"]
        assert [line.text for line in content.lines] == ["SPL-JB1:1 TN39N6 S-TN39N4", "SECOND LINE HERE"]
        x0, y0, x1, y1 = content.words[0].bbox
        assert 45 < x0 < 55 and 40 < y1 < 56


def test_raster_page(tmp_path, make_pdf):
    path = make_pdf(tmp_path / "scan.pdf", [[]], raster_pages=[0])
    with pymupdf.open(path) as doc:
        content = extract_page(doc[0], min_words=5)
        assert content.kind is PageKind.RASTER
        assert content.words == []


def test_segments_are_loaded_on_demand(tmp_path, make_pdf):
    path = make_pdf(tmp_path / "cbd.pdf", [[(50, 50, "A B C D E F")]], lines=[[(100, 100, 400, 100)]])
    with pymupdf.open(path) as doc:
        content = extract_page(doc[0], min_words=5)
        segs = content.segments
        assert any(abs(s.x0 - 100) < 1 and abs(s.x1 - 400) < 1 and abs(s.y0 - 100) < 1 for s in segs)
