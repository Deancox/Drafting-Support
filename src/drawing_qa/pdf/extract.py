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
    shx_word_count: int = 0  # words from AutoCAD SHX text notes
    dwg_word_count: int = 0  # words read from the sheet's DWG (drawing_qa.dwg)
    drawn: bool = False  # substantial line work on the page
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


def split_annotation(text: str, rect, block: int, method: ExtractionMethod = ExtractionMethod.SHX_ANNOTATION,
                     vertical: bool | None = None) -> list[Word]:
    """Split one string with a box (an SHX annotation, a DWG text) into words, giving each word a share of the box.

    The box covers the whole string. Word boxes are estimated by character position along the
    text's direction: the longer side unless `vertical` says (vertical text runs bottom to top).
    """
    x0, y0, x1, y1 = (rect.x0, rect.y0, rect.x1, rect.y1) if hasattr(rect, "x0") else rect
    width, height = x1 - x0, y1 - y0
    tokens = [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", text)]
    n = max(len(text), 1)
    horizontal = (width >= height if vertical is None else not vertical) or len(tokens) <= 1
    words = []
    for start, end, token in tokens:
        a, b = start / n, end / n
        if horizontal:
            bbox = (x0 + a * width, y0, x0 + b * width, y1)
        else:
            bbox = (x0, y1 - b * height, x1, y1 - a * height)
        words.append(Word(text=token, bbox=bbox, block=block, line=0, method=method))
    return words


_split_annotation = split_annotation


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


def classify_page(word_count: int, image_count: int, min_words: int, drawn: bool = False) -> PageKind:
    """drawn: the page has substantial line work (its content stream is large)."""
    if word_count >= min_words:
        return PageKind.VECTOR
    if image_count > 0:
        return PageKind.RASTER
    if word_count > 0:
        return PageKind.VECTOR
    return PageKind.NO_TEXT if drawn else PageKind.EMPTY


def _content_bytes(page: pymupdf.Page) -> int:
    """Size of the page's drawing instructions: cheap, and tells a drawn sheet from a blank one."""
    try:
        return len(page.read_contents())
    except Exception:  # an odd content stream: treat as drawn rather than blank
        return 1 << 20


def extract_page(
    page: pymupdf.Page,
    min_words: int,
    ocr: OcrEngine | None = None,
    shx_titles: Sequence[str] = ("AutoCAD SHX Text",),
    min_drawing_bytes: int = 5000,
) -> PageContent:
    words = [
        Word(text=w[4], bbox=(w[0], w[1], w[2], w[3]), block=w[5], line=w[6])
        for w in page.get_text("words", sort=True)
    ]
    shx = _shx_words(page, shx_titles)
    words.extend(shx)
    image_count = len(page.get_images(full=False))
    drawn = _content_bytes(page) >= min_drawing_bytes
    kind = classify_page(len(words), image_count, min_words, drawn)
    if kind is PageKind.RASTER and ocr is not None:
        words = ocr.recognise(page)
    return PageContent(
        page_index=page.number,
        width=page.rect.width,
        height=page.rect.height,
        kind=kind,
        words=words,
        image_count=image_count,
        shx_word_count=len(shx),
        drawn=drawn,
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
