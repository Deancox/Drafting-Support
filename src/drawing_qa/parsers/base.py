"""Parser framework.

A DrawingParser handles one document type. For each sheet it:
  1. always runs the generic OccurrenceReader (identifiers and placeholders anywhere on the sheet);
  2. runs the first TemplateReader whose matches() accepts the sheet, for the
     type-specific content (terminal strips, card tables, routing).
Template readers are added per drafting template as milestones M2-M4 land.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from drawing_qa.common.identifiers import IdentifierGrammar
from drawing_qa.common.placeholders import PlaceholderDetector
from drawing_qa.config import Settings
from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import DocType, EntityType, ExtractionMethod, IssueCode, Predicate
from drawing_qa.model.source import BBox, Drawing, Issue, SourceRef
from drawing_qa.pdf.extract import PageContent


@dataclass
class ParseResult:
    claims: list[Claim] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    def extend(self, other: ParseResult) -> None:
        self.claims.extend(other.claims)
        self.issues.extend(other.issues)


class ParseContext:
    """Shared services and claim construction for one run."""

    def __init__(self, settings: Settings, learned_areas=()) -> None:
        self.settings = settings
        self.grammar = IdentifierGrammar(settings.identifiers, learned_areas)
        self.placeholders = PlaceholderDetector(settings.placeholders)
        self._counters: dict[str, int] = {}

    def claim(
        self,
        *,
        parser: DocType,
        parser_version: str,
        drawing: Drawing,
        bbox: BBox,
        raw_text: str,
        subject_type: EntityType,
        subject_key: str,
        predicate: Predicate,
        method: ExtractionMethod,
        object_type: EntityType | None = None,
        object_key: str | None = None,
        value: dict[str, Any] | None = None,
        confidence: float = 1.0,
    ) -> Claim:
        n = self._counters.get(drawing.id, 0) + 1
        self._counters[drawing.id] = n
        grid_cell = drawing.grid.cell_at((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2) if drawing.grid else None
        return Claim(
            id=f"{drawing.id}/c{n:05d}",
            parser=parser,
            parser_version=parser_version,
            method=method,
            confidence=confidence,
            subject_type=subject_type,
            subject_key=subject_key,
            predicate=predicate,
            object_type=object_type,
            object_key=object_key,
            value=value or {},
            source=SourceRef(drawing_id=drawing.id, bbox=bbox, raw_text=raw_text, grid_cell=grid_cell),
        )


class TemplateReader(ABC):
    """Reads the type-specific content of one drafting template."""

    name: ClassVar[str]

    @abstractmethod
    def matches(self, page: PageContent, drawing: Drawing) -> bool: ...

    @abstractmethod
    def read(
        self, page: PageContent, drawing: Drawing, ctx: ParseContext, parser: DrawingParser, prior: list[Claim]
    ) -> ParseResult:
        """prior: the claims already made for this sheet (identifiers, placeholders, wires)."""


class DrawingParser(ABC):
    doc_type: ClassVar[DocType]
    version: ClassVar[str] = "0.1.0"
    template_readers: ClassVar[tuple[type[TemplateReader], ...]] = ()
    # False when many sheets of this type legitimately have no table (title pages, power, relays).
    report_unmatched: ClassVar[bool] = True

    def __init__(self) -> None:
        # Imported here to avoid a cycle: the occurrence reader uses ParseResult from this module.
        from drawing_qa.parsers.occurrences import OccurrenceReader

        self._occurrences = OccurrenceReader()
        self._templates = [cls() for cls in self.template_readers]

    def parse(self, page: PageContent, drawing: Drawing, ctx: ParseContext) -> ParseResult:
        result = self._occurrences.read(page, drawing, ctx, self)
        result.claims.extend(self.supplement(page, drawing, ctx, result.claims))
        if not self._templates:
            return result
        for reader in self._templates:
            if reader.matches(page, drawing):
                try:
                    result.extend(reader.read(page, drawing, ctx, self, result.claims))
                except Exception as exc:  # keep the sheet's identifiers; report what could not be read
                    result.issues.append(
                        Issue(
                            code=IssueCode.PARSER_ERROR,
                            message=f"{reader.name} failed ({type(exc).__name__}: {exc}); identifiers were still read",
                            file_path=drawing.file_path,
                            drawing_id=drawing.id,
                        )
                    )
                return result
        if not self.report_unmatched:
            return result
        result.issues.append(
            Issue(
                code=IssueCode.NO_TEMPLATE,
                message=f"No {self.doc_type.value} template reader recognised this sheet; only identifiers were extracted",
                file_path=drawing.file_path,
                drawing_id=drawing.id,
            )
        )
        return result

    def supplement(self, page: PageContent, drawing: Drawing, ctx: ParseContext, prior: list[Claim]) -> list[Claim]:
        """Extra claims for every sheet of this type, after occurrences and before templates."""
        return []

    @property
    def planned_content(self) -> str:
        """What this parser will extract once its template readers exist (for reports)."""
        return ""

    @property
    def built_content(self) -> str:
        """What this parser extracts today beyond identifiers and placeholders (for reports)."""
        return ""
