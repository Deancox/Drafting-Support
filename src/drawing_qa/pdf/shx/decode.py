"""Decoding an unreadable page's letters into words.

Each letter is looked up exactly in the book. Letters drawn one after another along one baseline
(or one column, for rotated text) form a line; gaps wider than the learned word spacing split it
into words. A word with any unknown letter is dropped whole, so a partial read never becomes text.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from drawing_qa.model.enums import ExtractionMethod
from drawing_qa.model.source import BBox
from drawing_qa.pdf.extract import Word
from drawing_qa.pdf.shx.book import MAX_PARTS, QUANTUM, GlyphBook, combo_key
from drawing_qa.pdf.shx.glyphs import glyph_groups


@dataclass
class DecodeStats:
    letters: int = 0  # letters recognised
    shapes: int = 0  # letter-sized shapes, recognised or not
    words_kept: int = 0
    words_dropped: int = 0


@dataclass
class _Letter:
    char: str | None  # None: letter-sized but not recognised
    box: BBox
    sizes: frozenset[float] = frozenset()  # text heights the shape was learned at
    clash: bool = False  # follows a letter of another text size with no space: the word is not one text


def _union(a: BBox, b: BBox) -> BBox:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _letters(page: pymupdf.Page, book: GlyphBook) -> list[_Letter | None]:
    """Letters in drawing order; None marks larger shapes, which end any line."""
    groups = glyph_groups(page, quantum=QUANTUM)
    multi = book.multi
    largest = max(book.heights, default=0.0) + 0.5
    out: list[_Letter | None] = []
    i = 0
    while i < len(groups):
        for k in range(MAX_PARTS, 1, -1):
            parts = groups[i:i + k]
            if len(parts) == k and (ch := multi.get(combo_key(parts))):
                box = parts[0].box
                for p in parts[1:]:
                    box = _union(box, p.box)
                out.append(_Letter(ch, box, book.sizes(combo_key(parts))))
                i += k
                break
        else:
            g = groups[i]
            ch = book.char(g.key)
            w, h = g.box[2] - g.box[0], g.box[3] - g.box[1]
            out.append(_Letter(ch, g.box, book.sizes(g.key) if ch else frozenset()) if ch or max(w, h) <= largest else None)
            i += 1
    return out


def _same_size(a: frozenset[float], b: frozenset[float]) -> bool:
    """Text heights are rounded from a note's tallest letter, so one font differs by up to 0.5."""
    return not a or not b or any(abs(x - y) <= 0.5 for x in a for y in b)


def _follows(line: list[_Letter], vertical: bool | None, letter: _Letter, book: GlyphBook) -> tuple[bool, bool, bool]:
    """(letter continues the line, the line is vertical, a space comes before it)."""
    nxt = letter.box
    prev = line[-1].box
    for vert in ([vertical] if vertical is not None else [False, True]):
        height = max((b.box[2] - b.box[0]) if vert else (b.box[3] - b.box[1]) for b in line)
        height = max(height, (nxt[2] - nxt[0]) if vert else (nxt[3] - nxt[1]))
        space, letter = book.space_gap(height), book.letter_gap(height)
        widest = 2 * space if space else 1.5 * height
        if vert:
            gap = prev[1] - nxt[3]
            band = min(b.box[0] for b in line) - 0.25 * height, max(b.box[2] for b in line) + 0.25 * height
            across = nxt[2] >= band[0] and nxt[0] <= band[1]
        else:
            gap = nxt[0] - prev[2]
            band = min(b.box[1] for b in line) - 0.25 * height, max(b.box[3] for b in line) + 0.25 * height
            across = nxt[3] >= band[0] and nxt[1] <= band[1]
        if across and -0.5 <= gap <= widest:
            if space and letter:
                threshold = (space + letter) / 2
            else:
                threshold = 0.8 * space if space else 0.6 * height
            return True, vert, gap >= threshold
    return False, bool(vertical), False


def _crowded(box: BBox, unknown: list[BBox], letters: list[_Letter]) -> bool:
    """An unrecognised letter-sized shape on the word or within a letter's width of its ends,
    wherever it was drawn: the word may be missing a letter, or one may be overdrawn."""
    pad = max(max(b - a for a, b in ((g.box[0], g.box[2]), (g.box[1], g.box[3]))) for g in letters) * 0.6
    x0, y0, x1, y1 = box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad
    return any(u[0] <= x1 and u[2] >= x0 and u[1] <= y1 and u[3] >= y0 and _overlaps_core(u, box) for u in unknown)


def _overlaps_core(u: BBox, box: BBox) -> bool:
    """The shape shares the word's baseline band (horizontal text) or column (vertical text)."""
    horizontal = (box[2] - box[0]) >= (box[3] - box[1])
    return (u[1] < box[3] and u[3] > box[1]) if horizontal else (u[0] < box[2] and u[2] > box[0])


def decode_page(page: pymupdf.Page, book: GlyphBook, *, block_base: int = 200_000) -> tuple[list[Word], DecodeStats]:
    stats = DecodeStats()
    lines: list[tuple[list[_Letter], list[bool]]] = []  # letters, and whether a space precedes each
    line: list[_Letter] = []
    spaces: list[bool] = []
    vertical: bool | None = None
    for letter in _letters(page, book):
        if letter is not None:
            stats.shapes += 1
            stats.letters += letter.char is not None
        if letter is None or not line:
            if line:
                lines.append((line, spaces))
            line, spaces, vertical = ([letter], [False], None) if letter else ([], [], None)
            continue
        joins, vert, spaced = _follows(line, vertical, letter, book)
        if joins and not _same_size(letter.sizes, line[-1].sizes):
            # AutoCAD gives a text one height. After a space, a letter of another size starts another text;
            # run together with no space, they are two texts touching, and splitting them could make
            # fragments look like identifiers (K-TL20F6 -> 20F6), so the word is dropped.
            if spaced:
                joins = False
            else:
                letter.clash = True
        if joins:
            line.append(letter)
            spaces.append(spaced)
            vertical = vert if len(line) >= 2 else vertical
        else:
            lines.append((line, spaces))
            line, spaces, vertical = [letter], [False], None
    if line:
        lines.append((line, spaces))

    unknown = [g.box for ln, _ in lines for g in ln if g.char is None]
    words: list[Word] = []
    for n, (letters, before) in enumerate(lines):
        groups: list[list[_Letter]] = []
        for letter, spaced in zip(letters, before):
            if spaced or not groups:
                groups.append([])
            groups[-1].append(letter)
        for group in groups:
            if any(g.char is None or g.clash for g in group):
                stats.words_dropped += 1
                continue
            box = group[0].box
            for g in group[1:]:
                box = _union(box, g.box)
            if _crowded(box, unknown, group):
                stats.words_dropped += 1
                continue
            words.append(Word(text="".join(g.char for g in group), bbox=box, block=block_base + n, line=0,
                              method=ExtractionMethod.SHX_STROKES))
            stats.words_kept += 1
    return words, stats
