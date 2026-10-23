"""SHX stroke decoding: grouping strokes into letters and fingerprinting them."""

from __future__ import annotations

import pymupdf

from drawing_qa.pdf.shx.glyphs import glyph_groups
from tests.conftest import build_pdf
from tests.shx_font import stroke_text


def _page(tmp_path, lines):
    path = build_pdf(tmp_path / "p.pdf", [[]], lines=[lines])
    return pymupdf.open(path)[0]


def test_one_group_per_letter(tmp_path):
    assert len(glyph_groups(_page(tmp_path, stroke_text("S-TL36D4", 100, 200)))) == 8


def test_same_letter_same_key_anywhere(tmp_path):
    groups = glyph_groups(_page(tmp_path, stroke_text("AA", 100, 200) + stroke_text("A", 500, 600) + stroke_text("B", 300, 400)))
    a1, a2, a3, b = groups  # drawing order
    assert a1.key == a2.key == a3.key
    assert b.key != a1.key


def test_pieces_out_of_order_still_join(tmp_path):
    h_letter = [(100, 100, 100, 106, 0.5), (104, 100, 104, 106, 0.5), (100, 103, 104, 103, 0.5)]
    assert len(glyph_groups(_page(tmp_path, h_letter))) == 1


def test_line_work_is_not_a_letter(tmp_path):
    lines = stroke_text("AB", 100, 200) + [(90, 220, 390, 220, 0.5)]
    assert len(glyph_groups(_page(tmp_path, lines))) == 2


def test_multi_part_char_gives_two_groups(tmp_path):
    assert len(glyph_groups(_page(tmp_path, stroke_text("=", 100, 200)))) == 2


def test_groups_in_drawing_order(tmp_path):
    groups = glyph_groups(_page(tmp_path, stroke_text("AB", 300, 200) + stroke_text("C", 100, 200)))
    assert [round(g.box[0]) for g in groups] == [300, 305, 100]  # drawn A, B, then C further left


def test_unstroked_paths_are_not_letters(tmp_path):
    """Filled shapes (a logo) and invisible text-note outlines have no stroke width; letters always do."""
    path = build_pdf(tmp_path / "p.pdf", [[]], lines=[stroke_text("AB", 100, 200)], shx=[[((99, 193, 110, 201), "AB")]])
    assert len(glyph_groups(pymupdf.open(path)[0])) == 2


def test_box_covers_every_stroke(tmp_path):
    """Straight strokes have zero-area boxes, which PyMuPDF's rectangle union skips."""
    [g] = glyph_groups(_page(tmp_path, stroke_text("A", 100, 200)))
    assert tuple(round(v, 1) for v in g.box) == (100, 194, 103, 200)
    [v] = glyph_groups(_page(tmp_path, stroke_text("A", 100, 200, vertical=True)))
    assert tuple(round(c, 1) for c in v.box) == (94, 197, 100, 200)
