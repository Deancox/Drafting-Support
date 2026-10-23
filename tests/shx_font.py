"""A made-up stroke "font" for SHX decoding tests.

AutoCAD plotted without SHX text notes draws every letter as a few short strokes. These shapes are
not real letterforms: each character is a vertical stem with ticks chosen by the bits of its code,
so every character has its own shape and all its strokes touch. '=' and ':' are drawn in two
separate parts, like the real font, to test multi-part characters.
"""

from __future__ import annotations

from drawing_qa.pdf.extract import decode_autocad_text

ADVANCE = 0.8  # letter advance, in text heights
SPACE = 1.4  # advance of a space
WIDTH = 0.5  # stroke width (pt)

Line = tuple[float, float, float, float, float]


def _glyph(ch: str, x: float, baseline: float, h: float) -> list[tuple[float, float, float, float]]:
    top = baseline - h
    if ch == "=":
        return [(x, baseline - 0.6 * h, x + 0.5 * h, baseline - 0.6 * h), (x, baseline - 0.3 * h, x + 0.5 * h, baseline - 0.3 * h)]
    if ch == ":":
        return [(x, baseline - 0.6 * h, x, baseline - 0.5 * h), (x, baseline - 0.2 * h, x, baseline - 0.1 * h)]
    lines = [(x, top, x, baseline)]
    for bit in range(8):
        if ord(ch) >> bit & 1:
            y = top + h * (bit + 1) / 9
            lines.append((x, y, x + 0.5 * h, y))
    return lines


def stroke_text(text: str, x: float, baseline: float, h: float = 6.0, vertical: bool = False) -> list[Line]:
    """Line items for build_pdf(lines=...). vertical: rotated 90 degrees counter-clockwise about
    (x, baseline), reading bottom to top as AutoCAD's rotated text does."""
    out: list[Line] = []
    pos = x
    for ch in text:
        if ch == " ":
            pos += SPACE * h
            continue
        for x0, y0, x1, y1 in _glyph(ch, pos, baseline, h):
            if vertical:
                x0, y0 = x + (y0 - baseline), baseline - (x0 - x)
                x1, y1 = x + (y1 - baseline), baseline - (x1 - x)
            out.append((x0, y0, x1, y1, WIDTH))
        pos += ADVANCE * h
    return out


def as_strokes(items: list[tuple[tuple[float, float, float, float], str]]) -> list[Line]:
    """The strokes AutoCAD would draw for these SHX note items: text at the note's left edge,
    on its bottom edge, as tall as the note."""
    out: list[Line] = []
    for rect, text in items:
        out += stroke_text(decode_autocad_text(text), rect[0], rect[3], h=rect[3] - rect[1])
    return out
