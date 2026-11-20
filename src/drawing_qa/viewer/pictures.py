"""Draws a case's crop straight from the PDF at the size of the panel it is shown in.

The case's PNG is made at a fixed zoom, so a wide crop (a whole channel row) is far bigger than the panel, and
tkinter can only shrink a picture by whole steps (1/2, 1/3, ...): it came out small and blurred. Drawing from
the PDF at exactly the panel's scale keeps the line work sharp. The same box, margin and red outline as the case
crop (drawing_qa.cases.crops), and the PDF is only read: the outline goes on the in-memory page.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from drawing_qa.cases.crops import OUTLINE_COLOR, OUTLINE_WIDTH

MARGIN = 120.0  # as the case crops
MAX_ZOOM = 6.0  # a tiny box is not blown up past this


class PictureError(Exception):
    """The crop could not be drawn from the PDF; the message names the file."""


def render_fit(pdf_path: Path, page_index: int, bbox: list[float], max_w: int, max_h: int,
               margin: float = MARGIN) -> bytes:
    """A PNG of bbox with margin points of page around it, outlined in red, scaled to fit max_w x max_h pixels."""
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise PictureError(f"PDF not found: {pdf_path.name}")
    try:
        with pymupdf.open(str(pdf_path)) as doc:
            if not 0 <= page_index < doc.page_count:
                raise PictureError(f"{pdf_path.name} has no page {page_index}")
            page = doc[page_index]
            box = pymupdf.Rect(bbox)
            clip = pymupdf.Rect(box.x0 - margin, box.y0 - margin, box.x1 + margin, box.y1 + margin) & page.rect
            if clip.is_empty:
                raise PictureError(f"the box is off page {page_index} of {pdf_path.name}")
            zoom = min(max_w / clip.width, max_h / clip.height, MAX_ZOOM)
            page.draw_rect(box, color=OUTLINE_COLOR, width=OUTLINE_WIDTH)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
            if pix.width > max_w or pix.height > max_h:  # rounding up by a pixel
                zoom *= min(max_w / pix.width, max_h / pix.height)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
            return pix.tobytes("png")
    except PictureError:
        raise
    except Exception as exc:  # any PyMuPDF or file error
        raise PictureError(f"could not draw a picture from {pdf_path.name}: {exc}") from exc
