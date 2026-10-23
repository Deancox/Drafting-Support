"""Learning letter shapes from readable sheets.

On a readable sheet each SHX text note lies over the strokes AutoCAD drew for it. The letters
inside the note, in drawing order, are paired with the note's characters; each fingerprint then
means that character. A fingerprint seen as two different characters is never used.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

import pymupdf

from drawing_qa.pdf.extract import decode_autocad_text
from drawing_qa.pdf.shx.glyphs import Glyph, GlyphKey, glyph_groups

QUANTUM = 0.25
MAX_PARTS = 3  # a character drawn in up to three separate parts (%, ", =, :)


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


def _alignments(chars: str, n_groups: int) -> list[list[int]]:
    """Ways to give each character 1 to MAX_PARTS consecutive groups (more than one only for
    punctuation), as lists of group counts. Stops counting at two."""
    ways: dict[tuple[int, int], list[list[int]]] = {(0, 0): [[]]}
    for i, ch in enumerate(chars):
        sizes = range(1, MAX_PARTS + 1) if not ch.isalnum() else (1,)
        for j in range(n_groups + 1):
            for prev in ways.get((i, j), []):
                for k in sizes:
                    if j + k <= n_groups:
                        bucket = ways.setdefault((i + 1, j + k), [])
                        if len(bucket) < 2:
                            bucket.append(prev + [k])
    return ways.get((len(chars), n_groups), [])


class GlyphBook:
    def __init__(self) -> None:
        self._chars: dict[GlyphKey, set[str]] = defaultdict(set)
        self._multi: dict[tuple, set[str]] = defaultdict(set)
        self._letter_gaps: dict[float, list[float]] = defaultdict(list)
        self._space_gaps: dict[float, list[float]] = defaultdict(list)
        self.heights: set[float] = set()

    # --- lookups --------------------------------------------------------------------------------

    def char(self, key: GlyphKey) -> str | None:
        seen = self._chars.get(key)
        return next(iter(seen)) if seen and len(seen) == 1 else None

    @property
    def multi(self) -> dict[tuple, str]:
        return {k: next(iter(v)) for k, v in self._multi.items() if len(v) == 1}

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
        for annot in notes:
            text = decode_autocad_text(annot.info.get("content") or "").strip()
            r = annot.rect
            inside = [g for g in groups if r.x0 - 1 <= g.box[0] and g.box[2] <= r.x1 + 1 and r.y0 - 1 <= g.box[1] and g.box[3] <= r.y1 + 1]
            if text and inside:
                self._learn_note(text, inside)
        return len(self._chars) + len(self._multi) - before

    def _learn_note(self, text: str, glyphs: list[Glyph]) -> None:
        chars = text.replace(" ", "")
        options = _alignments(chars, len(glyphs))
        if len(options) != 1:
            return  # no alignment, or more than one: the note does not say which strokes are which
        spans: list[list[Glyph]] = []
        pos = 0
        for ch, k in zip(chars, options[0]):
            parts = glyphs[pos:pos + k]
            pos += k
            spans.append(parts)
            if k == 1:
                self._chars[parts[0].key].add(ch)
            else:
                self._multi[combo_key(parts)].add(ch)
        height, vertical = text_height(glyphs)
        h = round(height * 2) / 2
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
        quiet = 0 if book.learn_page(page, shx_titles) else quiet + 1
        if quiet >= patience:
            break
    return book
