"""SHX stroke decoding: learning letter shapes from SHX notes on readable sheets."""

from __future__ import annotations

import pymupdf

from drawing_qa.pdf.shx.book import GlyphBook, learn
from drawing_qa.pdf.shx.glyphs import glyph_groups
from tests.conftest import build_pdf
from tests.shx_font import ADVANCE, SPACE, stroke_text

TITLES = ("AutoCAD SHX Text",)


def note(text: str, x: float, baseline: float, h: float = 6.0):
    width = sum(SPACE if ch == " " else ADVANCE for ch in text) * h
    return ((x, baseline - h, x + width, baseline), text)


def readable(tmp_path, items, lines=None, name="r.pdf"):
    """A page with SHX notes over the strokes of their own text (or over the given lines)."""
    from tests.shx_font import as_strokes

    path = build_pdf(tmp_path / name, [[]], lines=[lines if lines is not None else as_strokes(items)], shx=[items])
    return pymupdf.open(path)[0]


def book_from(tmp_path, items, lines=None) -> GlyphBook:
    book = GlyphBook()
    book.learn_page(readable(tmp_path, items, lines), TITLES)
    return book


def test_learns_characters_under_notes(tmp_path):
    page = readable(tmp_path, [note("S-TL36D4", 100, 200)])
    book = GlyphBook()
    assert book.learn_page(page, TITLES) == 8  # eight distinct letters
    assert [book.char(g.key) for g in glyph_groups(page)] == list("S-TL36D4")


def test_autocad_codes_are_decoded(tmp_path):
    page = readable(tmp_path, [note("N%%D", 100, 200)])
    book = GlyphBook()
    book.learn_page(page, TITLES)
    assert [book.char(g.key) for g in glyph_groups(page)] == ["N", "°"]


def test_conflicting_fingerprint_is_discarded(tmp_path):
    """A note box catching the wrong strokes (as real notes sometimes do) must not teach a wrong letter."""
    a, b = note("A", 100, 200), note("B", 300, 200)
    lines = stroke_text("A", 100, 200) + stroke_text("A", 300, 200)
    page = readable(tmp_path, [a, b], lines)
    book = GlyphBook()
    book.learn_page(page, TITLES)
    assert book.char(glyph_groups(page)[0].key) is None


def test_multi_part_char_learned(tmp_path):
    book = book_from(tmp_path, [note("A=B", 100, 200)])
    assert list(book.multi.values()) == ["="]
    assert book.size == 2  # A and B


def test_spacing_learned(tmp_path):
    book = book_from(tmp_path, [note("AB CD", 100, 200)])
    assert book.letter_gap(6.0) < book.space_gap(6.0)
    assert book.space_gap(12.0) is None  # nothing learned at that size


def test_learning_stops_when_nothing_new(tmp_path):
    items = [note("S-TL36D4", 100, 200)]
    pages = [readable(tmp_path, items, name=f"r{i}.pdf") for i in range(10)]
    read = []

    def feed():
        for p in pages:
            read.append(p)
            yield p

    book = learn(feed(), TITLES, patience=3)
    assert book.size > 0
    assert len(read) <= 4


def test_wide_combination_is_not_learned(tmp_path):
    """A note box catching a neighbour's letter must not teach '-' + that letter as one wide '-'
    (on real sheets this decoded S-TL60J4 as S-60J4)."""
    item = ((100, 194, 120, 200), "-AB")  # the box covers four letters' strokes
    lines = stroke_text("-X", 100, 200) + stroke_text("AB", 100 + 2 * ADVANCE * 6, 200)
    book = book_from(tmp_path, [item], lines)
    assert book.multi == {}


def test_known_letters_settle_an_ambiguous_note(tmp_path):
    """'(A=B)' could have '(', '=' or ')' as the two-part character; letters already learned say it is '='."""
    book = book_from(tmp_path, [note("A B ( )", 100, 200), note("(A=B)", 100, 300)])
    assert list(book.multi.values()) == ["="]


def test_unsettled_ambiguity_is_skipped(tmp_path):
    book = book_from(tmp_path, [note("(A=B)", 100, 300)])  # nothing known yet: no way to tell
    assert book.multi == {}
