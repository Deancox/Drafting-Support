"""Page extraction: words with positions, text lines, vector segments, page kind.

Text sources, in the order they are tried:
  1. the PDF text layer (TrueType text);
  2. AutoCAD SHX text annotations. AutoCAD plots SHX fonts as line geometry,
     which has no text layer, but it also embeds each text string as an invisible
     "AutoCAD SHX Text" annotation with its bounding box. The client's drawings use this;
  3. OCR, for scanned pages (hook only; milestone M6).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import pymupdf

from drawing_qa.model.enums import ExtractionMethod, PageKind
from drawing_qa.model.source import BBox
from drawing_qa.pdf.ocr import OcrEngine

# Block numbers for SHX annotations start here so they never collide with text-layer blocks.
_SHX_BLOCK_BASE = 100_000

# AutoCAD control codes found in SHX annotation text.
_AUTOCAD_CODES = {"%%D": "°", "%%C": "Ø", "%%P": "±", "%%%": "%", "%%U": "", "%%O": ""}
_AUTOCAD_CODE_RE = re.compile("|".join(re.escape(k) for k in _AUTOCAD_CODES), re.IGNORECASE)


def decode_autocad_text(text: str) -> str:
    return _AUTOCAD_CODE_RE.sub(lambda m: _AUTOCAD_CODES[m.group(0).upper()], text)


@dataclass(frozen=True)
class Word:
    text: str
    bbox: BBox
    block: int = 0
    line: int = 0
    method: ExtractionMethod = ExtractionMethod.TEXT_LAYER


@dataclass(frozen=True)
class TextLine:
    text: str
    bbox: BBox
    words: tuple[Word, ...]


@dataclass(frozen=True)
class Segment:
    x0: float
    y0: float
    x1: float
    y1: float
    width: float = 0.0  # stroke width in points (CBD cables 0.96, enclosure outlines 0.24)

    @property
    def length(self) -> float:
        return ((self.x1 - self.x0) ** 2 + (self.y1 - self.y0) ** 2) ** 0.5

    @property
    def horizontal(self) -> bool:
        return abs(self.y1 - self.y0) < 0.5

    @property
    def vertical(self) -> bool:
        return abs(self.x1 - self.x0) < 0.5


@dataclass
class PageContent:
    page_index: int
    width: float
    height: float
    kind: PageKind
    words: list[Word]
    image_count: int = 0
    _segment_loader: Callable[[], list[Segment]] | None = field(default=None, repr=False)

    @property
    def methods(self) -> set[ExtractionMethod]:
        return {w.method for w in self.words}

    @cached_property
    def lines(self) -> list[TextLine]:
        """Words grouped into text lines: PDF text-layer lines, or one line per SHX annotation."""
        grouped: dict[tuple[int, int], list[Word]] = {}
        for w in self.words:
            grouped.setdefault((w.block, w.line), []).append(w)
        result = []
        for key in sorted(grouped):
            ws = grouped[key]
            result.append(
                TextLine(
                    text=" ".join(w.text for w in ws),
                    bbox=(
                        min(w.bbox[0] for w in ws),
                        min(w.bbox[1] for w in ws),
                        max(w.bbox[2] for w in ws),
                        max(w.bbox[3] for w in ws),
                    ),
                    words=tuple(ws),
                )
            )
        return result

    @cached_property
    def segments(self) -> list[Segment]:
        """Vector line segments. Loaded only when a parser asks (CBD routing), as it is slow on large sheets."""
        return self._segment_loader() if self._segment_loader else []


def _load_segments(page: pymupdf.Page) -> list[Segment]:
    segments: list[Segment] = []
    for path in page.get_drawings():
        w = float(path.get("width") or 0.0)
        for item in path["items"]:
            op = item[0]
            if op == "l":
                p1, p2 = item[1], item[2]
                segments.append(Segment(p1.x, p1.y, p2.x, p2.y, w))
            elif op == "re":
                r = item[1]
                corners = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1]):
                    segments.append(Segment(ax, ay, bx, by, w))
            elif op == "qu":
                q = item[1]
                pts = [q.ul, q.ur, q.lr, q.ll]
                for a, b in zip(pts, pts[1:] + pts[:1]):
                    segments.append(Segment(a.x, a.y, b.x, b.y, w))
    return segments


def _split_annotation(text: str, rect: pymupdf.Rect, block: int) -> list[Word]:
    """Split one SHX annotation into words, giving each word a share of the box.

    The annotation box covers the whole string. Word boxes are estimated by
    character position along the longer side (vertical text runs bottom to top).
    """
    tokens = [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", text)]
    n = max(len(text), 1)
    horizontal = rect.width >= rect.height or len(tokens) <= 1
    words = []
    for start, end, token in tokens:
        a, b = start / n, end / n
        if horizontal:
            bbox = (rect.x0 + a * rect.width, rect.y0, rect.x0 + b * rect.width, rect.y1)
        else:
            bbox = (rect.x0, rect.y1 - b * rect.height, rect.x1, rect.y1 - a * rect.height)
        words.append(Word(text=token, bbox=bbox, block=block, line=0, method=ExtractionMethod.SHX_ANNOTATION))
    return words


def _shx_words(page: pymupdf.Page, titles: Sequence[str]) -> list[Word]:
    wanted = {t.lower() for t in titles}
    words: list[Word] = []
    for i, annot in enumerate(page.annots() or []):
        info = annot.info
        if info.get("title", "").lower() not in wanted:
            continue
        text = decode_autocad_text(info.get("content", "")).strip()
        if text:
            words.extend(_split_annotation(text, annot.rect, _SHX_BLOCK_BASE + i))
    return words


def classify_page(word_count: int, image_count: int, min_words: int) -> PageKind:
    if word_count >= min_words:
        return PageKind.VECTOR
    if image_count > 0:
        return PageKind.RASTER
    return PageKind.VECTOR if word_count > 0 else PageKind.EMPTY


def extract_page(
    page: pymupdf.Page,
    min_words: int,
    ocr: OcrEngine | None = None,
    shx_titles: Sequence[str] = ("AutoCAD SHX Text",),
) -> PageContent:
    words = [
        Word(text=w[4], bbox=(w[0], w[1], w[2], w[3]), block=w[5], line=w[6])
        for w in page.get_text("words", sort=True)
    ]
    words.extend(_shx_words(page, shx_titles))
    image_count = len(page.get_images(full=False))
    kind = classify_page(len(words), image_count, min_words)
    if kind is PageKind.RASTER and ocr is not None:
        words = ocr.recognise(page)
    return PageContent(
        page_index=page.number,
        width=page.rect.width,
        height=page.rect.height,
        kind=kind,
        words=words,
        image_count=image_count,
        _segment_loader=lambda: _load_segments(page),
    )


@contextmanager
def open_pdf(path: Path) -> Iterator[pymupdf.Document]:
    doc = pymupdf.open(path)
    try:
        if doc.needs_pass:
            raise ValueError("PDF is password protected")
        yield doc
    finally:
        doc.close()
