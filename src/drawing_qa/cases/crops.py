"""Renders a PNG of part of a PDF page around an evidence box.

The box comes from the QA tool's output. This module only draws the location it
is given; it never searches the PDF for text or works out where evidence is.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

OUTLINE_COLOR = (1, 0, 0)
OUTLINE_WIDTH = 1.5


class CropError(Exception):
    """A crop could not be rendered. The message names the PDF file."""


def render_crop(
    pdf_path: Path,
    page_index: int,
    bbox: list[float],
    out_png: Path,
    margin: float = 120.0,
    zoom: float = 3.0,
) -> None:
    """Save a PNG of bbox with margin points of page around it, outlined in red.

    bbox is [x0, y0, x1, y1] in PDF points, origin top left. The PDF is opened
    read-only and the outline is drawn on the in-memory page only, so the file
    is never changed. Any failure to open or render raises CropError.
    """
    pdf_path = Path(pdf_path)
    name = pdf_path.name
    if not pdf_path.is_file():
        raise CropError(f"PDF not found: {name}")
    try:
        with pymupdf.open(str(pdf_path)) as doc:
            if not 0 <= page_index < doc.page_count:
                raise CropError(f"{name} has no page {page_index}; it has {doc.page_count}")
            page = doc[page_index]
            box = pymupdf.Rect(bbox)
            clip = pymupdf.Rect(
                box.x0 - margin, box.y0 - margin, box.x1 + margin, box.y1 + margin
            ) & page.rect
            if clip.is_empty:
                raise CropError(f"the box is off page {page_index} of {name}")
            page.draw_rect(box, color=OUTLINE_COLOR, width=OUTLINE_WIDTH)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
            pix.save(str(out_png))
    except CropError:
        raise
    except Exception as exc:  # any PyMuPDF or file error becomes a CropError
        raise CropError(f"could not render a crop from {name}: {exc}") from exc
