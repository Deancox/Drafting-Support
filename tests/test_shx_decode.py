"""SHX stroke decoding: turning an unreadable page's letters into words."""

from __future__ import annotations

import pymupdf

from drawing_qa.model.enums import ExtractionMethod
from drawing_qa.pdf.shx.book import GlyphBook
from drawing_qa.pdf.shx.decode import decode_page
from tests.conftest import build_pdf
from tests.shx_font import ADVANCE, WIDTH, stroke_text
from tests.test_shx_book import TITLES, note, readable


def unreadable(tmp_path, lines, name="u.pdf"):
    return pymupdf.open(build_pdf(tmp_path / name, [[]], lines=[lines]))[0]


def learned(tmp_path, items, lines=None) -> GlyphBook:
    book = GlyphBook()
    book.learn_page(readable(tmp_path, items, lines), TITLES)
    return book


def texts(words):
    return [w.text for w in words]


def test_decodes_words_with_spaces(tmp_path):
    book = learned(tmp_path, [note("HOIST HIGH SPEED", 100, 200)])
    words, _ = decode_page(unreadable(tmp_path, stroke_text("HOIST HIGH SPEED", 300, 500)), book)
    assert texts(words) == ["HOIST", "HIGH", "SPEED"]
    assert len({w.block for w in words}) == 1
    assert all(w.method is ExtractionMethod.SHX_STROKES for w in words)


def test_unknown_letter_drops_its_word(tmp_path):
    book = learned(tmp_path, [note("-TL60F4 OK", 100, 200)])  # never sees an S
    words, _ = decode_page(unreadable(tmp_path, stroke_text("S-TL60F4 OK", 300, 500)), book)
    assert texts(words) == ["OK"]


def test_letter_touching_line_work_drops_word(tmp_path):
    book = learned(tmp_path, [note("BHB-JB1", 100, 200)])
    crossing = [(301, 495, 301, 500, WIDTH)]  # a letter-sized stroke (a revision cloud arc) over the first B
    words, _ = decode_page(unreadable(tmp_path, stroke_text("BHB-JB1", 300, 500) + crossing), book)
    assert not any("JB1" in w for w in texts(words))


def test_multi_part_char_decoded(tmp_path):
    book = learned(tmp_path, [note("A=B", 100, 200), note("K07.11 SH", 100, 300)])
    words, _ = decode_page(unreadable(tmp_path, stroke_text("K07.11=SH", 300, 500)), book)
    assert texts(words) == ["K07.11=SH"]


def test_vertical_text_reads_bottom_to_top(tmp_path):
    h = 6.0
    rect = (100 - h, 200 - 2 * ADVANCE * h, 100, 200)
    book = learned(tmp_path, [(rect, "D6")], stroke_text("D6", 100, 200, vertical=True))
    words, _ = decode_page(unreadable(tmp_path, stroke_text("D6", 400, 600, vertical=True)), book)
    assert texts(words) == ["D6"]


def test_stats_count_letters_and_words(tmp_path):
    book = learned(tmp_path, [note("-TL60F4 OK", 100, 200)])
    _, stats = decode_page(unreadable(tmp_path, stroke_text("S-TL60F4 OK", 300, 500)), book)
    assert (stats.letters, stats.shapes, stats.words_kept, stats.words_dropped) == (9, 10, 1, 1)


def test_text_of_another_size_in_one_word_is_dropped(tmp_path):
    """Letters of different text sizes run together (two texts touching) are not one word, and splitting them
    could make fragments look like identifiers (401104-020: K-TL20F6 split into K-TL and 20F6). Dropped."""
    book = learned(tmp_path, [note("AB", 100, 200), note("CD", 100, 300, h=9.0)])
    lines = stroke_text("AB", 300, 500) + stroke_text("CD", 300 + 2 * ADVANCE * 6, 500, h=9.0)
    words, _ = decode_page(unreadable(tmp_path, lines), book)
    assert texts(words) == []


def test_texts_of_two_sizes_with_a_space_between_stay_apart(tmp_path):
    book = learned(tmp_path, [note("AB", 100, 200), note("CD", 100, 300, h=9.0), note("A B", 100, 400)])
    lines = stroke_text("AB", 300, 500) + stroke_text("CD", 300 + 4 * ADVANCE * 6, 500, h=9.0)
    words, _ = decode_page(unreadable(tmp_path, lines), book)
    assert texts(words) == ["AB", "CD"]


def test_half_point_size_differences_are_one_size(tmp_path):
    """A note's height is its tallest letter, so one font is learned at 5.0 in one note and 5.5 in another
    (here a taller '(' in the second note). Those letters still make one word."""
    lines = stroke_text("AB", 100, 200, h=5.0) + stroke_text("CD", 100, 300, h=5.0) + stroke_text("(", 110, 300, h=5.4)
    book = learned(tmp_path, [note("AB", 100, 200, h=5.0), ((100, 294, 116, 301), "CD(")], lines)
    words, _ = decode_page(unreadable(tmp_path, stroke_text("ABCD", 300, 500, h=5.0)), book)
    assert texts(words) == ["ABCD"]



def test_letter_never_swallowed_into_a_multi_part_character(tmp_path):
    """On real sheet 077, 'BH-JB10' decoded as 'B-JB10': a note whose box caught a neighbour taught
    H + '-' as one two-part '-'. A part that is a known letter or digit is never part of another character."""
    plain = [note("A B C D - (", 100, 100, h=5.0)]
    tall = stroke_text("(", 100, 300, h=10.0)  # the tall '(' makes the note's text height 10
    lines = stroke_text("A B C D - (", 100, 100, h=5.0) + tall + stroke_text("ABD-C", 108, 300, h=5.0)
    from tests.shx_font import as_strokes  # noqa: F401
    book = learned(tmp_path, plain + [((100, 289, 140, 301), "(AB-C")], lines)
    words, _ = decode_page(unreadable(tmp_path, stroke_text("AD-C", 300, 500, h=5.0)), book)
    assert texts(words) == ["AD-C"]
