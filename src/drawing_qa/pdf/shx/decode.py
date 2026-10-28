"""Decoding an unreadable page's letters into words.

Each letter is looked up in the book. Letters drawn one after another along one baseline (or one
column, for rotated text) form a line; gaps wider than the learned word spacing split it into words.
A word is used only if every letter in it was recognised and nothing about it is in doubt; otherwise
it is dropped whole, so a partial read never becomes text. A letter at a text size no readable sheet uses
is matched scaled to its line's height, when a tall letter in the line shows that height is the text's.
A word is dropped when:
- a letter is not recognised, or its shape sits at the wrong height for its line (',' vs "'");
- the word runs into a letter of another text size (two texts touching);
- a neighbour drawn just before or after it sits right beside it but does not continue it
  (stacked parts of a character not learned, '=' or ':'; text in a reading direction not learned;
  a letter merged with line work);
- an unrecognised letter-sized shape sits on it or beside it;
- it is a single letter read by scale (a shape alone sets its own line's height: a bubble round a number
  is an 'O' at some size);
- in a line read by scale, a letter in it was learned only at other text sizes (a capital at one size can be
  a lower-case letter at a larger one, stroke for stroke);
- a letter in it matched only within a rounding step, and no letter in it matched exactly (one read by scale
  does not count: a near match can take an upper-case letter at one size for its lower case at another).
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from drawing_qa.model.enums import ExtractionMethod
from drawing_qa.model.source import BBox
from drawing_qa.pdf.extract import Word
from drawing_qa.pdf.shx.book import MAX_PARTS, QUANTUM, GlyphBook, combo_key
from drawing_qa.pdf.shx.glyphs import Glyph, glyph_groups


@dataclass
class DecodeStats:
    letters: int = 0  # letters recognised
    scaled: int = 0  # of which matched scaled to their line's height (a text size not learned)
    shapes: int = 0  # letter-sized shapes, recognised or not
    words_kept: int = 0
    words_dropped: int = 0


@dataclass
class _Letter:
    char: str | None  # None: not recognised
    box: BBox
    sizes: frozenset[float] = frozenset()  # text heights the shape was learned at
    positions: frozenset[float] = frozenset()  # where across its line the shape was learned (0 top .. 1 bottom)
    near: bool = False  # matched only within a rounding step
    scaled: bool = False  # matched scaled to its line's height
    big: bool = False  # line work, or a shape touching it: never a letter, ends any line
    clash: bool = False  # in doubt (see the module docstring): its word is dropped
    glyph: Glyph | None = None  # the shape, for a single-part letter


def _union(a: BBox, b: BBox) -> BBox:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _letters(page: pymupdf.Page, book: GlyphBook) -> list[_Letter]:
    """Letters and larger shapes, in drawing order."""
    groups = glyph_groups(page, quantum=QUANTUM)
    multi = book.multi
    # Text up to half as tall again as any learned can still be read by scale; anything larger is line work.
    largest = 1.5 * max(book.heights, default=0.0) + 0.5
    out: list[_Letter] = []
    i = 0
    while i < len(groups):
        for k in range(MAX_PARTS, 1, -1):
            parts = groups[i:i + k]
            if len(parts) == k and not any(p.on_line for p in parts) and (ch := multi.get(combo_key(parts))):
                box = parts[0].box
                for p in parts[1:]:
                    box = _union(box, p.box)
                out.append(_Letter(ch, box, book.sizes(combo_key(parts))))
                i += k
                break
        else:
            g = groups[i]
            w, h = g.box[2] - g.box[0], g.box[3] - g.box[1]
            if g.on_line or max(w, h) > largest:
                out.append(_Letter(None, g.box, big=True))
            else:
                ch = book.char(g.key)
                out.append(_Letter(ch, g.box, book.sizes(g.key) if ch else frozenset(),
                                   book.positions(g.key) if ch else frozenset(), near=bool(ch) and not book.exact(g.key),
                                   glyph=g))
            i += 1
    return out


def _same_size(a: frozenset[float], b: frozenset[float]) -> bool:
    """Text heights are rounded from a note's tallest letter, so one font differs by up to 0.5."""
    return not a or not b or any(abs(x - y) <= 0.5 for x in a for y in b)


def _beside(a: _Letter, b: _Letter) -> bool:
    """b is within 0.6 of the text height of a: close enough to be part of the same text. The text height is
    the one the shapes were learned at, as a mark ('-', '.') is much smaller than its text."""
    size = max(a.box[2] - a.box[0], a.box[3] - a.box[1], b.box[2] - b.box[0], b.box[3] - b.box[1],
               *a.sizes, *b.sizes)
    pad = 0.6 * size + 0.25
    a, b = a.box, b.box
    return b[0] <= a[2] + pad and b[2] >= a[0] - pad and b[1] <= a[3] + pad and b[3] >= a[1] - pad


def _follows(line: list[_Letter], vertical: bool | None, letter: _Letter, book: GlyphBook) -> tuple[bool, bool, bool]:
    """(letter continues the line, the line is vertical, a space comes before it). Text reads left to right,
    or bottom to top when turned."""
    nxt = letter.box
    prev = line[-1].box
    # Turned text has full-height letters across the column; two stacked marks ('=' as bars) are not a column.
    can_turn = any(g.sizes and (g.box[2] - g.box[0]) >= 0.75 * max(g.sizes) for g in (line[-1], letter))
    for vert in ([vertical] if vertical is not None else [False] + ([True] if can_turn else [])):
        height = max((b.box[2] - b.box[0]) if vert else (b.box[3] - b.box[1]) for b in line)
        height = max(height, (nxt[2] - nxt[0]) if vert else (nxt[3] - nxt[1]))
        space, letter_gap = book.space_gap(height), book.letter_gap(height)
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
            if space and letter_gap:
                threshold = (space + letter_gap) / 2
            elif space:
                threshold = 0.8 * space
            else:  # no spaces learned at this size: wider than any gap seen between letters
                threshold = max(1.2 * letter_gap, 0.6 * height) if letter_gap else 0.6 * height
            return True, vert, gap >= threshold
    return False, bool(vertical), False


def _read_by_scale(letters: list[_Letter], book: GlyphBook) -> int:
    """Match the line's unrecognised letters scaled to its height (horizontal text): its tallest shape, leaving
    out marks already recognised ('(' reaches lower than the letters). The height is used only if a letter
    that tall is a capital or digit that sets it (book.sets_height): a line of only 'c's is as tall as a 'c',
    and scaled to that a 'c' is a 'C'. Once the line's text size is known, a letter matched by its fingerprint
    but learned only at other sizes is in doubt: a capital at one size can be a lower-case letter at a larger
    one stroke for stroke. Returns how many letters were read."""
    if not any(g.char is None and g.glyph for g in letters):
        return 0
    height = max((g.box[3] - g.box[1] for g in letters if g.char is None or g.char.isalnum()), default=0.0)
    reads = {id(g): r for g in letters if g.char is None and g.glyph and (r := book.scaled(g.glyph, height))}
    tallest = [g for g in letters if g.box[3] - g.box[1] >= height - 0.25]
    if not reads or not any((c := g.char or reads.get(id(g), (None,))[0]) and book.sets_height(c) for g in tallest):
        return 0
    for g in letters:
        if r := reads.get(id(g)):
            g.char, g.positions = r
            g.scaled = True
        elif g.char and g.sizes and not _same_size(g.sizes, frozenset({round(height * 2) / 2})):
            g.clash = True
    return len(reads)


def _check_positions(letters: list[_Letter], vertical: bool) -> None:
    """A small mark (',' "'" '.' '-' '_') is known by its shape and its height in the line; one learned only
    at other heights than it sits at here is not recognised."""
    lo, hi = (0, 2) if vertical else (1, 3)
    full = [g for g in letters if g.char and not g.big]
    if not full:
        return
    tallest = max(g.box[hi] - g.box[lo] for g in full)
    body = [g for g in full if g.box[hi] - g.box[lo] >= 0.6 * tallest]
    top, bottom = min(g.box[lo] for g in body), max(g.box[hi] for g in body)
    if bottom <= top:
        return
    for g in letters:
        if g.char and g.positions and g.box[hi] - g.box[lo] < 0.5 * tallest:
            at = ((g.box[lo] + g.box[hi]) / 2 - top) / (bottom - top)
            if not any(abs(at - p) <= 0.2 for p in g.positions):
                g.char = None


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
    lines: list[tuple[list[_Letter], list[bool], bool]] = []  # letters, whether a space precedes each, vertical
    line: list[_Letter] = []
    spaces: list[bool] = []
    vertical: bool | None = None
    previous: _Letter | None = None  # the shape drawn just before, letter or not

    def close() -> None:
        if line:
            lines.append((line, spaces, bool(vertical)))

    for letter in _letters(page, book):
        if not letter.big:
            stats.shapes += 1
            stats.letters += letter.char is not None
        if letter.big:
            if previous is not None and not previous.big and _beside(previous, letter):
                previous.clash = True  # a letter run into line work: the text may go on through it
            close()
            line, spaces, vertical = [], [], None
        elif not line:
            if previous is not None and _beside(previous, letter):
                letter.clash = True  # right beside the shape before it, yet not continuing it
            line, spaces, vertical = [letter], [False], None
        else:
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
                if _beside(line[-1], letter):
                    # Right beside the letter before it, yet not continuing it: stacked parts of a character
                    # not learned ('=' read as two '-'), or a reading direction not learned. Both sides go.
                    line[-1].clash = letter.clash = True
                close()
                line, spaces, vertical = [letter], [False], None
        previous = letter
    close()

    for letters, _, vert in lines:
        if not vert:
            n = _read_by_scale(letters, book)
            stats.letters += n
            stats.scaled += n
        _check_positions(letters, vert)
    unknown = [g.box for ln, _, _ in lines for g in ln if g.char is None]
    words: list[Word] = []
    for n, (letters, before, _) in enumerate(lines):
        groups: list[list[_Letter]] = []
        for letter, spaced in zip(letters, before):
            if spaced or not groups:
                groups.append([])
            groups[-1].append(letter)
        for group in groups:
            exact = any(not g.near and not g.scaled for g in group)
            alone = len(group) == 1 and group[0].scaled
            if any(g.char is None or g.clash for g in group) or (any(g.near for g in group) and not exact) or alone:
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
