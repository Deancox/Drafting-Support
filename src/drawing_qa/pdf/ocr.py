"""OCR hook. Not implemented yet (IMPLEMENTATION_PLAN.md section 3, milestone M6).

An OcrEngine turns a raster page into the same Word records the text layer gives,
in PDF coordinates, so parsers do not need to know where the text came from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import pymupdf

    from drawing_qa.pdf.extract import Word


class OcrEngine(Protocol):
    name: str

    def recognise(self, page: pymupdf.Page) -> list[Word]:
        """Return recognised words with bounding boxes in PDF points."""
        ...
