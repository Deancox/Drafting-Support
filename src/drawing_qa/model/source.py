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


class GridFrame(BaseModel):
    """The sheet's border zones: row letters down the side, column numbers across the top.

    Each label is stored with the centre of its band; a point belongs to the
    nearest row band and the nearest column band.
    """

    rows: list[tuple[str, float]]  # (label, centre y) in top-to-bottom order
    cols: list[tuple[str, float]]  # (label, centre x) in left-to-right order

    def cell_at(self, x: float, y: float) -> str:
        row = min(self.rows, key=lambda r: abs(r[1] - y))[0]
        col = min(self.cols, key=lambda c: abs(c[1] - x))[0]
        return f"{row}{col}"

    @property
    def row_labels(self) -> list[str]:
        return [r[0] for r in self.rows]


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
