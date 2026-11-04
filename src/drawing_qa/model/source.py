"""Source layer: input files, drawing sheets and exact places on a sheet."""

from __future__ import annotations

from pydantic import BaseModel, Field

from drawing_qa.model.enums import DocType, ExtractionMethod, IssueCode, PageKind

BBox = tuple[float, float, float, float]


class InputFile(BaseModel):
    """One input file found during ingestion (a drawing PDF, or an equipment list)."""

    path: str  # relative to the input root, forward slashes
    doc_type: DocType
    sha256: str
    size_bytes: int
    page_count: int = 0
    readable: bool = True


def _band(bands: list[tuple[str, float]], at: float) -> str | None:
    """The nearest band's label, or None more than 3/4 of a band's spacing past either end."""
    label, centre = min(bands, key=lambda b: abs(b[1] - at))
    if len(bands) >= 2:
        steps = sorted(abs(b[1] - a[1]) for a, b in zip(bands, bands[1:]))
        spacing = steps[len(steps) // 2]
        lo, hi = min(b[1] for b in bands), max(b[1] for b in bands)
        if at < lo - 0.75 * spacing or at > hi + 0.75 * spacing:
            return None
    return label


class GridFrame(BaseModel):
    """The sheet's border zones: row letters down the side, column numbers across the top.

    Each label is stored with the centre of its band; a point belongs to the
    nearest row band and the nearest column band. A point well past the outermost
    band read (a label not read, or the title block below the frame) has no cell:
    it is not given to the last band.
    """

    rows: list[tuple[str, float]]  # (label, centre y) in top-to-bottom order
    cols: list[tuple[str, float]]  # (label, centre x) in left-to-right order

    def cell_at(self, x: float, y: float) -> str | None:
        row, col = _band(self.rows, y), _band(self.cols, x)
        return f"{row}{col}" if row and col else None

    @property
    def row_labels(self) -> list[str]:
        return [r[0] for r in self.rows]


class UnresolvedText(BaseModel):
    """A word on a decoded sheet missing a letter or two ('?' for each): shown to the checker, never used."""

    text: str  # e.g. MO?ULE
    grid: str | None = None  # grid reference, when the sheet's grid was read
    bbox: BBox


class DecodeInfo(BaseModel):
    """How much of a NO_SHX_TEXT sheet was read by decoding its letter strokes (--ocr)."""

    letters: int  # letters recognised
    scaled: int = 0  # of which matched scaled to their line's height (a text size the readable sheets do not use)
    shapes: int  # letter-sized shapes, recognised or not
    words_kept: int
    words_dropped: int  # words never used, the four reasons below together
    unknown_letter: int = 0  # dropped: a letter in it not recognised
    clash: int = 0  # dropped: touching another text or line work, or a shape beside it not continuing it
    crowded: int = 0  # dropped: an unrecognised shape on it or at its ends
    doubtful: int = 0  # dropped: matched only within a rounding step, or a single letter read by scale
    lone_shapes: int = 0  # unrecognised shapes with nothing joined to them: mostly symbols and line work, not words
    unresolved: list[UnresolvedText] = []  # words missing a letter or two: for the checker, never used


class Drawing(BaseModel):
    """One sheet (PDF page) of one drawing at one revision."""

    id: str
    file_path: str
    page_index: int
    doc_type: DocType  # from the input folder
    drawing_no: str
    sheet: str
    rev: str | None = None
    title: str | None = None
    status: str | None = None  # title block "Drawing Status", e.g. FOR CONSTRUCTION
    descriptor: str | None = None  # e.g. SCHEMATIC DIAGRAM, TERMINATION DIAGRAM
    ffi: str | None = None  # from the sheet's "WIRE NUMBER / FIRST FIELD IDENTIFIER" box, e.g. TN
    text_unreadable: bool = False  # drawn, but no readable text (NO_SHX_TEXT): in the package, nothing on it checked
    decoded: DecodeInfo | None = None  # read by decoding its letter strokes (--ocr): checked, but never fully read
    text_source: str | None = None  # the DWG its text was read from (a PDF plotted without SHX notes); fully read
    title_doc_type: DocType | None = None  # document type implied by the descriptor
    title_block_method: ExtractionMethod = ExtractionMethod.FILENAME
    missing_title_fields: list[str] = Field(default_factory=list)
    grid: GridFrame | None = None
    page_kind: PageKind
    width: float
    height: float
    word_count: int = 0
    content_hash: str = ""
    duplicate_of: str | None = None  # identical copy of this drawing id; not parsed again
    conflicts_with: list[str] = Field(default_factory=list)  # same sheet, different content

    @property
    def sheet_number(self) -> int | None:
        digits = "".join(ch for ch in self.sheet if ch.isdigit())
        return int(digits) if digits else None

    @property
    def label(self) -> str:
        """How the checker refers to the sheet: 401103 sh 036."""
        return f"{self.drawing_no} sh {self.sheet}"


class SourceRef(BaseModel):
    """An exact place on a sheet. Every claim points to one."""

    drawing_id: str
    bbox: BBox
    raw_text: str
    grid_cell: str | None = None

    @property
    def centre(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return (x0 + x1) / 2, (y0 + y1) / 2


class Issue(BaseModel):
    """Something the platform could not read or had to skip."""

    code: IssueCode
    message: str
    file_path: str | None = None
    drawing_id: str | None = None
