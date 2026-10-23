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


def _glyph(ch: str, x: float, baseline: float, h: float, w: float = 0.5) -> list[tuple[float, float, float, float]]:
    """w: letter width, in text heights."""
    top = baseline - h
    if ch == "=":
        return [(x, baseline - 0.6 * h, x + w * h, baseline - 0.6 * h), (x, baseline - 0.3 * h, x + w * h, baseline - 0.3 * h)]
    if ch == ":":
        return [(x, baseline - 0.6 * h, x, baseline - 0.5 * h), (x, baseline - 0.2 * h, x, baseline - 0.1 * h)]
    lines = [(x, top, x, baseline)]
    for bit in range(8):
        if ord(ch) >> bit & 1:
            y = top + h * (bit + 1) / 9
            lines.append((x, y, x + w * h, y))
    return lines


def stroke_text(text: str, x: float, baseline: float, h: float = 6.0, vertical: bool = False, squeeze: float = 1.0) -> list[Line]:
    """Line items for build_pdf(lines=...). vertical: rotated 90 degrees counter-clockwise about
    (x, baseline), reading bottom to top as AutoCAD's rotated text does. squeeze < 1 narrows the spacing."""
    out: list[Line] = []
    pos = x
    for ch in text:
        if ch == " ":
            pos += SPACE * h * squeeze
            continue
        for x0, y0, x1, y1 in _glyph(ch, pos, baseline, h, 0.5 * squeeze):
            if vertical:
                x0, y0 = x + (y0 - baseline), baseline - (x0 - x)
                x1, y1 = x + (y1 - baseline), baseline - (x1 - x)
            out.append((x0, y0, x1, y1, WIDTH))
        pos += ADVANCE * h * squeeze
    return out


def as_strokes(items: list[tuple[tuple[float, float, float, float], str]], fill: float = 1.0) -> list[Line]:
    """The strokes AutoCAD would draw for these SHX note items: text at the note's left edge,
    `fill` of the note's height tall (real notes are taller than their text; fill 1.0 sits the
    text on the bottom edge), spaced to fit inside the note."""
    out: list[Line] = []
    for rect, text in items:
        text = decode_autocad_text(text)
        box = rect[3] - rect[1]
        h = box * fill
        baseline = rect[3] - (box - h) / 2
        units = sum(SPACE if ch == " " else ADVANCE for ch in text) or 1
        out += stroke_text(text, rect[0], baseline, h=h, squeeze=min(1.0, (rect[2] - rect[0]) / (units * h)))
    return out
