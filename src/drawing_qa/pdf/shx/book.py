"""Learning letter shapes from readable sheets.

On a readable sheet each SHX text note lies over the strokes AutoCAD drew for it. The letters
inside the note, in drawing order, are paired with the note's characters; each fingerprint then
means that character. A fingerprint seen as two different characters is never used.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence

import pymupdf

from drawing_qa.pdf.extract import decode_autocad_text
from drawing_qa.pdf.shx.glyphs import Glyph, GlyphKey, glyph_groups

log = logging.getLogger(__name__)

QUANTUM = 0.25
MAX_PARTS = 3  # a character drawn in up to three separate parts (%, ", =, :)


def _within_a_step(a: GlyphKey, b: GlyphKey) -> bool:
    if abs(a[0] - b[0]) > 1 or abs(a[1] - b[1]) > 1:
        return False
    pa, pb = a[3], b[3]
    for order in (None, lambda p: (p[1], p[0])):
        xs, ys = (sorted(pa, key=order), sorted(pb, key=order)) if order else (pa, pb)
        if all(abs(x[0] - y[0]) <= 1 and abs(x[1] - y[1]) <= 1 for x, y in zip(xs, ys)):
            return True
    return False


def combo_key(parts: Sequence[Glyph]) -> tuple:
    """Fingerprint of a character drawn in several separate parts: each part and its offset from the first."""
    x0, y0 = parts[0].box[0], parts[0].box[1]
    return tuple((p.key, round((p.box[0] - x0) / QUANTUM), round((p.box[1] - y0) / QUANTUM)) for p in parts)


def text_height(glyphs: Sequence[Glyph]) -> tuple[float, bool]:
    """(height of the text, whether it runs vertically) from its letters' boxes."""
    first, last = glyphs[0].box, glyphs[-1].box
    dx = (last[0] + last[2]) / 2 - (first[0] + first[2]) / 2
    dy = (last[1] + last[3]) / 2 - (first[1] + first[3]) / 2
    vertical = abs(dy) > abs(dx)
    return max((g.box[2] - g.box[0]) if vertical else (g.box[3] - g.box[1]) for g in glyphs), vertical


def _alignments(chars: str, n_groups: int, limit: int = 32) -> list[list[int]]:
    """Ways to give each character 1 to MAX_PARTS consecutive groups (more than one only for
    punctuation), as lists of group counts. At most `limit` are kept per step."""
    ways: dict[tuple[int, int], list[list[int]]] = {(0, 0): [[]]}
    for i, ch in enumerate(chars):
        sizes = range(1, MAX_PARTS + 1) if not ch.isalnum() else (1,)
        for j in range(n_groups + 1):
            for prev in ways.get((i, j), []):
                for k in sizes:
                    if j + k <= n_groups:
                        bucket = ways.setdefault((i + 1, j + k), [])
                        if len(bucket) < limit:
                            bucket.append(prev + [k])
    return ways.get((len(chars), n_groups), [])


class GlyphBook:
    def __init__(self) -> None:
        self._chars: dict[GlyphKey, set[str]] = defaultdict(set)
        self._multi: dict[tuple, set[str]] = defaultdict(set)
        self._letter_gaps: dict[float, list[float]] = defaultdict(list)
        self._space_gaps: dict[float, list[float]] = defaultdict(list)
        self.heights: set[float] = set()
        self._near_index_for: dict = {}
        self._near_index: dict[tuple, list] = defaultdict(list)
        self._near_cache: dict = {}
        self._key_heights: dict[GlyphKey | tuple, set[float]] = defaultdict(set)  # text heights a shape was learned at
        # Where a shape sits across its line (0 top .. 1 bottom), to one decimal: ',' and "'" are one stroke.
        self._positions: dict[GlyphKey, set[float]] = defaultdict(set)

    # --- lookups --------------------------------------------------------------------------------

    def char(self, key: GlyphKey) -> str | None:
        chars = set().union(*(self._chars[k] for k in self._matches(key)))
        return next(iter(chars)) if len(chars) == 1 else None

    def _matches(self, key: GlyphKey) -> list:
        """The learned fingerprints this one is: itself, or else those within one rounding step (0.25 pt) on
        every point. A point near a rounding boundary falls either side depending on where the letter is."""
        if key in self._chars:
            return [key]
        if len(self._near_index_for) != len(self._chars):
            self._near_index_for = dict.fromkeys(self._chars)
            self._near_index = defaultdict(list)
            for k in self._chars:
                self._near_index[(k[2], len(k[3]))].append(k)
            self._near_cache = {}
        if key not in self._near_cache:
            self._near_cache[key] = [k for k in self._near_index.get((key[2], len(key[3])), []) if _within_a_step(k, key)]
        return self._near_cache[key]

    @property
    def multi(self) -> dict[tuple, str]:
        """Characters drawn in parts. A combination with a part that is itself a letter or digit is never
        used: it was learned from a note whose box caught a neighbour (sheet 077: H + '-' taught as '-')."""
        return {k: next(iter(v)) for k, v in self._multi.items()
                if len(v) == 1 and not any((ch := self.char(part[0])) and ch.isalnum() for part in k)}

    def exact(self, key: GlyphKey) -> bool:
        """The fingerprint itself was learned (not only one within a rounding step of it)."""
        return key in self._chars

    def positions(self, key: GlyphKey) -> frozenset[float]:
        return frozenset().union(*(self._positions.get(k, ()) for k in self._matches(key)))

    def sizes(self, key: GlyphKey | tuple) -> frozenset[float]:
        """Text heights this shape (single or multi-part fingerprint) was learned at."""
        if key in self._key_heights:
            return frozenset(self._key_heights[key])
        return frozenset().union(*(self._key_heights.get(k, ()) for k in self._matches(key))) if len(key) == 4 else frozenset()

    @property
    def size(self) -> int:
        return sum(1 for v in self._chars.values() if len(v) == 1)

    def _near(self, table: dict[float, list[float]], height: float) -> list[float]:
        return [g for h, gaps in table.items() if abs(h - height) <= 0.5 for g in gaps]

    def space_gap(self, height: float) -> float | None:
        gaps = self._near(self._space_gaps, height)
        return min(gaps) if gaps else None

    def letter_gap(self, height: float) -> float | None:
        gaps = self._near(self._letter_gaps, height)
        return max(gaps) if gaps else None

    # --- learning -------------------------------------------------------------------------------

    def learn_page(self, page: pymupdf.Page, shx_titles: Sequence[str]) -> int:
        """Learn from every SHX note on the page; returns how many new fingerprints it added."""
        wanted = {t.lower() for t in shx_titles}
        notes = [a for a in page.annots() or [] if (a.info.get("title") or "").lower() in wanted]
        if not notes:
            return 0
        groups = glyph_groups(page, quantum=QUANTUM)
        before = len(self._chars) + len(self._multi)
        found = []
        for annot in notes:
            text = decode_autocad_text(annot.info.get("content") or "").strip()
            r = annot.rect
            inside = [g for g in groups if r.x0 - 1 <= g.box[0] and g.box[2] <= r.x1 + 1 and r.y0 - 1 <= g.box[1] and g.box[3] <= r.y1 + 1]
            if text and inside:
                found.append((text, inside))
        # Plain notes (one letter per character) first: what they teach settles the ambiguous ones.
        found.sort(key=lambda t: len(t[1]) != len(t[0].replace(" ", "")))
        for text, inside in found:
            self._learn_note(text, inside)
        return len(self._chars) + len(self._multi) - before

    def _learn_note(self, text: str, glyphs: list[Glyph]) -> None:
        chars = text.replace(" ", "")
        options = _alignments(chars, len(glyphs))
        if not options:
            return
        if len(options) > 1:
            # Several characters could be the one drawn in parts: take the alignment whose single
            # letters agree most with letters already learned, if it is the clear winner.
            def agree(counts: list[int]) -> int:
                pos, n = 0, 0
                for ch, k in zip(chars, counts):
                    n += k == 1 and self.char(glyphs[pos].key) == ch
                    pos += k
                return n

            scored = sorted(((agree(o), o) for o in options), key=lambda t: -t[0])
            if scored[0][0] == 0 or scored[0][0] == scored[1][0]:
                return  # the note does not say which strokes are which
            options = [scored[0][1]]
        height, vertical = text_height(glyphs)
        spans: list[list[Glyph]] = []
        pos = 0
        for k in options[0]:
            spans.append(glyphs[pos:pos + k])
            pos += k
        axis = (1, 3) if vertical else (0, 2)
        if any(len(p) > 1 and max(g.box[axis[1]] for g in p) - min(g.box[axis[0]] for g in p) > height for p in spans):
            return  # parts wider than a letter: the box caught a neighbour's strokes, not a multi-part character
        h = round(height * 2) / 2
        lo, hi = (0, 2) if vertical else (1, 3)
        band = min(g.box[lo] for g in glyphs), max(g.box[hi] for g in glyphs)
        for ch, parts in zip(chars, spans):
            key = parts[0].key if len(parts) == 1 else combo_key(parts)
            (self._chars if len(parts) == 1 else self._multi)[key].add(ch)
            self._key_heights[key].add(h)
            if len(parts) == 1 and band[1] > band[0]:
                g = parts[0]
                self._positions[key].add(round(((g.box[lo] + g.box[hi]) / 2 - band[0]) / (band[1] - band[0]), 1))
        self.heights.add(h)
        spaced = [False] * len(chars)  # spaced[i]: a space follows character i
        i = -1
        for ch in text:
            if ch == " ":
                if i >= 0:
                    spaced[i] = True
            else:
                i += 1
        for i in range(len(spans) - 1):
            a, b = spans[i][-1].box, spans[i + 1][0].box
            gap = (a[1] - b[3]) if vertical else (b[0] - a[2])
            (self._space_gaps if spaced[i] else self._letter_gaps)[h].append(gap)


def learn(pages: Iterable[pymupdf.Page], shx_titles: Sequence[str], *, patience: int = 3) -> GlyphBook:
    """A book learned from these pages, stopping once `patience` pages in a row add nothing new."""
    book = GlyphBook()
    quiet = 0
    for page in pages:
        try:
            added = book.learn_page(page, shx_titles)
        except Exception:  # one odd sheet must not stop the run: learn from the others
            log.exception("Learning letter shapes failed on page %s", getattr(page, "number", "?"))
            added = 0
        quiet = 0 if added else quiet + 1
        if quiet >= patience:
            break
    return book
