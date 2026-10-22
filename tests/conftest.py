"""Shared fixtures. Test PDFs are generated here, so tests never depend on client drawings."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pymupdf
import pytest

from drawing_qa.config import Settings

# (x, y, text) placed on the page; y is the text baseline in PDF points.
TextItem = tuple[float, float, str]
LineItem = tuple[float, float, float, float]
# (x0, y0, x1, y1) box and the text AutoCAD stores in the annotation.
ShxItem = tuple[tuple[float, float, float, float], str]


def build_pdf(
    path: Path,
    pages: Sequence[Sequence[TextItem]],
    lines: Sequence[Sequence[LineItem]] | None = None,
    raster_pages: Sequence[int] = (),
    shx: Sequence[Sequence[ShxItem]] | None = None,
) -> Path:
    """Write a PDF. Pages listed in raster_pages get an image and no text (like a scan).

    shx adds AutoCAD-style "AutoCAD SHX Text" annotations (text with no text layer),
    as produced by AutoCAD when plotting SHX fonts.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for i, items in enumerate(pages):
        page = doc.new_page(width=1190, height=842)  # A3 landscape
        if i in raster_pages:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 64), False)
            pix.clear_with(200)
            page.insert_image(pymupdf.Rect(100, 100, 900, 700), pixmap=pix)
            continue
        for x, y, text in items:
            page.insert_text((x, y), text, fontsize=8)
        for line in (lines[i] if lines and i < len(lines) else []):
            x0, y0, x1, y1, *rest = line  # optional 5th value: stroke width
            page.draw_line((x0, y0), (x1, y1), width=rest[0] if rest else 1)
        for rect, content in (shx[i] if shx and i < len(shx) else []):
            annot = page.add_rect_annot(pymupdf.Rect(rect))
            annot.set_info(title="AutoCAD SHX Text", content=content)
            annot.set_border(width=0)  # AutoCAD's SHX text notes are invisible
            annot.update()
    doc.save(path)
    doc.close()
    return path


ROW_LABELS = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def cell_rect(cell: str, width: float = 60) -> tuple[float, float, float, float]:
    """A text box centred in a grid cell of the standard frame below, e.g. 'N2'."""
    y = 58 + 28 * ROW_LABELS.index(cell[0])
    x = 88 + 112 * int(cell[1:])
    return (x - width / 2, y - 5, x + width / 2, y + 5)


def sheet_frame(
    drawing_no: str = "401103",
    sheet: str = "203",
    rev: str = "A",
    status: str = "PRELIMINARY",
    title: str = "LAND LEG REMOTE I/O PANEL",
    descriptor: str = "TERMINATION DIAGRAM",
) -> list[ShxItem]:
    """Grid frame and title block laid out like the client's sheets (positions taken from 401103-036)."""
    items: list[ShxItem] = []
    for i, row in enumerate(ROW_LABELS):
        y = 58 + 28 * i
        items.append(((28, y - 6, 38, y + 6), row))
        items.append(((1145, y - 6, 1155, y + 6), row))
    for col in range(10):
        x = 88 + 112 * col
        items.append(((x - 4, 26, x + 4, 39), str(col)))
    items += [
        ((1028, 745, 1068, 757), "Drawing N%%D"),
        ((1008, 751, 1079, 780), drawing_no),
        ((1001, 784, 1038, 799), "Sheet N%%d"),
        ((1038, 779, 1074, 799), sheet),
        ((1111, 745, 1137, 756), "Rev N%%D"),
        ((1100, 751, 1147, 780), rev),
        ((1041, 715, 1098, 731), "Drawing Status"),
        ((1017, 727, 1123, 743), status),
        ((634, 719, 893, 738), "PORT WARATAH COAL SERVICES LTD - KOORAGANG"),
        ((623, 731, 724, 752), "07.11 SHIPLOADER"),
        ((634, 746, 924, 765), title),
        ((630, 760, 740, 779), descriptor),
    ]
    return items


@pytest.fixture
def make_pdf() -> Callable[..., Path]:
    return build_pdf


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def sample_package(tmp_path: Path) -> Path:
    """A small package with one sheet of each document type."""
    root = tmp_path / "Main"
    build_pdf(
        root / "PLC" / "401310-014.pdf",
        [[(50, 50, "LTL-PLC DI SLOT 16"), (50, 70, "CH05 S-TN39N4 PUMP RUNNING"), (50, 90, "SPARE HOLD")]],
    )
    build_pdf(
        root / "CableBlocks" / "401200-001.pdf",
        [[(50, 50, "S-TN39N4"), (300, 50, "SPL-JB1"), (600, 50, "LTL-PLC"), (50, 90, "CXXXXX")]],
        lines=[[(100, 48, 290, 48), (350, 48, 590, 48)]],
    )
    build_pdf(
        root / "Terminations" / "401103-203.pdf",
        [[(50, 50, "SPL-JB1:1 TN39N6 S-TN39N4 LTL-PLC DI16 CH05"), (50, 70, "SPL-JB1:2 REF TERM XXX")]],
    )
    return root
