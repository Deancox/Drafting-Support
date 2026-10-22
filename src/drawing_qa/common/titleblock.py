"""Title block reader.

Label-anchored rather than coordinate-templated: it finds the label text
("Drawing N°", "Sheet N°", "Rev N°", "Drawing Status") inside the bottom-right
title block region and takes the nearest value beside or below it that fits the
field's format. The descriptor line ("SCHEMATIC DIAGRAM", "TERMINATION DIAGRAM",
"CABLING BLOCK DIAGRAM") gives the document type, and the line above it the title.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from drawing_qa.config import TitleBlockConfig
from drawing_qa.model.enums import DocType
from drawing_qa.pdf.extract import PageContent, TextLine

_LABELS: dict[str, re.Pattern[str]] = {
    "drawing_no": re.compile(r"^DRAWING\s+N[O°º0]\.?$", re.I),
    "sheet": re.compile(r"^SHEET\s+N[O°º0]\.?$", re.I),
    "rev": re.compile(r"^REV(ISION)?\s+N[O°º0]\.?$", re.I),
    "status": re.compile(r"^DRAWING\s+STATUS$", re.I),
}
_VALUES: dict[str, re.Pattern[str]] = {
    "drawing_no": re.compile(r"^(?=.*\d)[A-Z0-9][A-Z0-9-]{2,}$", re.I),
    "sheet": re.compile(r"^\d{1,4}[A-Z]?$", re.I),
    "rev": re.compile(r"^[A-Z0-9]{1,3}$", re.I),
    "status": re.compile(r"^[A-Z][A-Z ]{3,}$", re.I),
}
_DESCRIPTOR = re.compile(r"\b(DIAGRAM|SCHEDULE|LAYOUT|ARRANGEMENT|TITLE PAGE|COVER SHEET|INDEX|LEGEND)\b", re.I)
REQUIRED_FIELDS = ("drawing_no", "sheet", "rev", "title", "status")


@dataclass
class TitleBlock:
    drawing_no: str | None = None
    sheet: str | None = None
    rev: str | None = None
    status: str | None = None
    title: str | None = None
    descriptor: str | None = None
    doc_type: DocType | None = None
    missing: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.drawing_no is not None and self.sheet is not None


def _centre(line: TextLine) -> tuple[float, float]:
    x0, y0, x1, y1 = line.bbox
    return (x0 + x1) / 2, (y0 + y1) / 2


def read_title_block(page: PageContent, config: TitleBlockConfig) -> TitleBlock:
    region = [
        ln
        for ln in page.lines
        if _centre(ln)[0] >= page.width * config.x_min and _centre(ln)[1] >= page.height * config.y_min
    ]
    label_lines = {ln for ln in region if any(p.match(ln.text.strip()) for p in _LABELS.values())}
    values = [ln for ln in region if ln not in label_lines]

    tb = TitleBlock()
    for name, label_re in _LABELS.items():
        label = next((ln for ln in region if label_re.match(ln.text.strip())), None)
        if label is None:
            continue
        lx, ly = _centre(label)
        best, best_d = None, config.max_value_distance
        for cand in values:
            cx, cy = _centre(cand)
            if cy < ly - 4:  # values sit beside or below their label, never above
                continue
            if not _VALUES[name].match(cand.text.strip()):
                continue
            d = math.hypot(cx - lx, cy - ly)
            if d < best_d:
                best, best_d = cand, d
        if best is not None:
            setattr(tb, name, best.text.strip())

    # The descriptor is the bottom line of the title stack ("SCHEMATIC DIAGRAM" under the title).
    candidates = [ln for ln in values if _DESCRIPTOR.search(ln.text)]
    descriptor = max(candidates, key=lambda ln: ln.bbox[1]) if candidates else _stack_bottom(values)
    if descriptor is not None:
        tb.descriptor = descriptor.text.strip()
        upper = tb.descriptor.upper()
        tb.doc_type = next((t for kw, t in config.descriptor_types.items() if kw.upper() in upper), None)
        dx0, dy0 = descriptor.bbox[0], descriptor.bbox[1]
        above = [
            ln
            for ln in region
            if ln is not descriptor and abs(ln.bbox[0] - dx0) <= 30 and 0 < dy0 - ln.bbox[1] <= 25
        ]
        if above:
            tb.title = min(above, key=lambda ln: dy0 - ln.bbox[1]).text.strip()

    tb.missing = [f for f in REQUIRED_FIELDS if getattr(tb, f) is None]
    return tb


def _stack_bottom(lines: list[TextLine]) -> TextLine | None:
    """Fallback when no descriptor keyword matches: the bottom line of the title stack.

    The stack is the run of left-aligned lines, in the same text size, under the
    longest line in the title block, ending at the first vertical gap of more than 20 pt.
    Field captions such as "Date Drawn" are smaller text and so are not part of it.
    """
    if not lines:
        return None
    anchor = max(lines, key=lambda ln: len(ln.text))
    height = anchor.bbox[3] - anchor.bbox[1]
    stack = sorted(
        (
            ln
            for ln in lines
            if abs(ln.bbox[0] - anchor.bbox[0]) <= 12
            and ln.bbox[1] >= anchor.bbox[1]
            and (ln.bbox[3] - ln.bbox[1]) >= 0.8 * height
        ),
        key=lambda ln: ln.bbox[1],
    )
    bottom = stack[0]
    for ln in stack[1:]:
        if ln.bbox[1] - bottom.bbox[1] > 20:
            break
        bottom = ln
    return bottom if bottom is not anchor else None
