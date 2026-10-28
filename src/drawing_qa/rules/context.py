"""Read-only view of a run for rules, with the lookups rules need."""

from __future__ import annotations

from collections import defaultdict
from functools import cached_property

from drawing_qa.config import Settings
from drawing_qa.model.claims import Claim
from drawing_qa.model.engineering import EngineeringModel, SignalTrace
from drawing_qa.model.enums import DocType, EntityType, ExtractionMethod, Predicate
from drawing_qa.model.source import Drawing
from drawing_qa.rules.base import Requirement

# Rules that say nothing for the whole package once a sheet of this type is decoded (--ocr): a route or a
# terminal 'not shown' could sit on any sheet of the type. Reported, so their silence is not read as clean.
QUIET_WHEN_DECODED: dict[DocType, tuple[str, ...]] = {
    DocType.CBD: ("PATH-001", "COMP-005"),
    DocType.TERM: ("DEV-003", "WIRE-004", "TERM-003 (sheet references)"),
}


def quiet_rules(drawings) -> list[str]:
    types = {d.doc_type for d in drawings if d.decoded is not None}
    return [r for t, rules in QUIET_WHEN_DECODED.items() if t in types for r in rules]


def reference_sheet_ids(drawings: list[Drawing], settings: Settings) -> set[str]:
    """Legend, symbol, title and index sheets (their example tags are not real content), and sheets of
    document types the config marks as not checked ([ingest] unchecked_types)."""
    markers = [m.upper() for m in settings.title_block.reference_descriptors]
    unchecked = set(settings.ingest.unchecked_types)
    return {
        d.id
        for d in drawings
        if d.doc_type in unchecked or (d.descriptor and any(m in d.descriptor.upper() for m in markers))
    }


class RuleContext:
    def __init__(
        self,
        drawings: list[Drawing],
        claims: list[Claim],
        settings: Settings,
        model: EngineeringModel | None = None,
        traces: list[SignalTrace] | None = None,
    ) -> None:
        unchecked = set(settings.ingest.unchecked_types)
        self.drawings = [d for d in drawings if d.doc_type not in unchecked]
        self.settings = settings
        self.model = model
        self.traces = traces
        self.reference_sheets = reference_sheet_ids(drawings, settings)
        # Legend and symbol sheets show example tags and placeholders; rules see only real content. A note naming
        # slots is real wherever it is: the index lists the drawings by slot ("PLC SLOT 12,12,14", 401103-003).
        unchecked_ids = {d.id for d in drawings if d.doc_type in unchecked}
        self.claims = [c for c in claims if c.source.drawing_id not in self.reference_sheets
                       or (c.predicate is Predicate.SLOT_REF and c.source.drawing_id not in unchecked_ids)]

    @cached_property
    def available(self) -> frozenset[Requirement]:
        have = {Requirement.DRAWINGS, Requirement.CLAIMS}
        if any(d.grid for d in self.drawings):
            have.add(Requirement.GRID)
        if self.model is not None:
            have.add(Requirement.GRAPH)
        if self.traces is not None:
            have.add(Requirement.TRACES)
        if any(c.parser is DocType.TERM and c.method is ExtractionMethod.TEMPLATE for c in self.claims):
            have.add(Requirement.TERMINATIONS)
        return frozenset(have)

    @cached_property
    def drawing(self) -> dict[str, Drawing]:
        return {d.id: d for d in self.drawings}

    @cached_property
    def active_drawings(self) -> list[Drawing]:
        """Drawings that were read (identical duplicate copies and sheets with no readable text excluded;
        sheets decoded from their letter strokes with --ocr included)."""
        return [d for d in self.drawings if d.duplicate_of is None and (not d.text_unreadable or d.decoded is not None)]

    @cached_property
    def decoded_ids(self) -> frozenset[str]:
        """Sheets read by decoding their letter strokes (--ocr). Checked, but never fully read: a word that
        could not be decoded looks like a missing one, so nothing is reported as absent from them."""
        return frozenset(d.id for d in self.drawings if d.decoded is not None)

    @cached_property
    def decoded_types(self) -> frozenset:
        """Document types with a decoded sheet: a route or terminal 'not shown' on them may not have decoded."""
        return frozenset(d.doc_type for d in self.drawings if d.decoded is not None)

    @cached_property
    def unreadable_sheets(self) -> set[tuple[str, int]]:
        """(drawing no, sheet number) of sheets that are in the package but have no readable text
        (NO_SHX_TEXT). They are not missing: references to them are not reported as missing sheets."""
        return {(d.drawing_no, d.sheet_number) for d in self.drawings if d.text_unreadable and d.sheet_number is not None}

    @cached_property
    def sheets_by_number(self) -> dict[tuple[str, int], list[Drawing]]:
        """(drawing no, sheet number) -> sheets. 401103 sheet "036" is (401103, 36)."""
        index: dict[tuple[str, int], list[Drawing]] = defaultdict(list)
        for d in self.active_drawings:
            if d.sheet_number is not None:
                index[(d.drawing_no, d.sheet_number)].append(d)
        return index

    @cached_property
    def sheet_numbers(self) -> dict[int, list[Drawing]]:
        """Sheet number -> sheets, across drawing numbers (tags encode the sheet only)."""
        index: dict[int, list[Drawing]] = defaultdict(list)
        for d in self.active_drawings:
            if d.sheet_number is not None:
                index[d.sheet_number].append(d)
        return index

    @cached_property
    def drawing_numbers(self) -> set[str]:
        return {d.drawing_no for d in self.active_drawings}

    def home(self, ffi: str, sheet: int) -> tuple[str, str | None, list[Drawing]]:
        """Where a tag or wire with this FFI and sheet number is drawn.

        Returns (status, drawing number, sheets):
          "package"  - the FFI's drawing is in the package; sheets is empty if that sheet is not supplied
          "external" - the FFI belongs to a separate package (config "EXTERNAL", or its drawing is not here)
          "unmapped" - the FFI is not in [identifiers.ffi_drawings]
        With no mapping configured, any PLC sheet with that number is the home (older behaviour).
        """
        mapping = {k.upper(): v for k, v in self.settings.identifiers.ffi_drawings.items()}
        if not mapping:
            from drawing_qa.model.enums import DocType

            return "package", None, [d for d in self.sheet_numbers.get(sheet, []) if d.doc_type is DocType.PLC]
        drawing = mapping.get(ffi.upper())
        if drawing is None:
            return "unmapped", None, []
        if drawing.upper() == "EXTERNAL" or drawing not in self.drawing_numbers:
            return "external", None if drawing.upper() == "EXTERNAL" else drawing, []
        return "package", drawing, self.sheets_by_number.get((drawing, sheet), [])

    def claims_of(self, predicate: Predicate, subject_type: EntityType | None = None) -> list[Claim]:
        return [
            c
            for c in self.claims
            if c.predicate is predicate and (subject_type is None or c.subject_type is subject_type)
        ]

    @cached_property
    def occurrences_by_key(self) -> dict[tuple[EntityType, str], list[Claim]]:
        index: dict[tuple[EntityType, str], list[Claim]] = defaultdict(list)
        for c in self.claims:
            if c.predicate is Predicate.OCCURS:
                index[(c.subject_type, c.subject_key)].append(c)
        return index

    def sheet_of(self, claim: Claim) -> Drawing:
        return self.drawing[claim.source.drawing_id]

    @cached_property
    def claim_by_id(self) -> dict[str, Claim]:
        return {c.id: c for c in self.claims}

    @cached_property
    def _links(self) -> dict[tuple[str, str], list]:
        index: dict[tuple[str, str], list] = defaultdict(list)
        for e in self.model.edges if self.model else []:
            index[(e.src, e.type)].append((e.dst, e))
            index[(e.dst, e.type)].append((e.src, e))
        return index

    def linked(self, node_id: str, edge_type: str) -> list[str]:
        """Node ids joined to `node_id` by edges of one type, either direction, sorted."""
        return sorted({other for other, _ in self._links.get((node_id, edge_type), [])})

    def link_edges(self, node_id: str, edge_type: str) -> list:
        """(other node id, edge) pairs for `node_id` over edges of one type."""
        return list(self._links.get((node_id, edge_type), []))

    def edges_of(self, edge_type: str) -> list:
        return [e for e in (self.model.edges if self.model else []) if e.type == edge_type]

    def evidence(self, claim_ids: list[str]) -> list[Claim]:
        """The claims behind an edge or node that this context can see (checked sheets only)."""
        return [self.claim_by_id[i] for i in claim_ids if i in self.claim_by_id]
