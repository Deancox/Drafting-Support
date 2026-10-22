"""Shared enumerations. One definition used by parsing, storage and output."""

from enum import StrEnum


class DocType(StrEnum):
    PLC = "PLC"
    CBD = "CBD"
    TERM = "TERM"
    EQUIP = "EQUIP"  # electrical equipment list: a reference input, not a drawing
    OTHER = "OTHER"


class PageKind(StrEnum):
    VECTOR = "VECTOR"
    RASTER = "RASTER"
    EMPTY = "EMPTY"


class ExtractionMethod(StrEnum):
    TEXT_LAYER = "TEXT_LAYER"
    SHX_ANNOTATION = "SHX_ANNOTATION"
    TEMPLATE = "TEMPLATE"
    VECTOR = "VECTOR"
    OCR = "OCR"
    AI_VISION = "AI_VISION"
    AI_MATCH = "AI_MATCH"
    FILENAME = "FILENAME"


class EntityType(StrEnum):
    DEVICE = "DEVICE"
    WIRE = "WIRE"
    JUNCTION_BOX = "JUNCTION_BOX"
    TERMINAL = "TERMINAL"
    PANEL = "PANEL"
    LOCATION = "LOCATION"  # other <AREA>-<CODE><n> locations, e.g. LTL-LCS3 (PathNode kind to confirm)
    PLC_MODULE = "PLC_MODULE"
    PLC_CHANNEL = "PLC_CHANNEL"
    ROUTE = "ROUTE"
    DRAWING = "DRAWING"
    TEXT = "TEXT"


class Predicate(StrEnum):
    """Claim predicates (DATA_MODEL.md section 4.3)."""

    OCCURS = "OCCURS"
    PLACEHOLDER = "PLACEHOLDER"
    TITLE_BLOCK = "TITLE_BLOCK"
    CONNECTS = "CONNECTS"
    LANDS_ON = "LANDS_ON"
    REFERS_TO_DEVICE = "REFERS_TO_DEVICE"
    DESTINED_FOR = "DESTINED_FOR"
    DESTINED_PANEL = "DESTINED_PANEL"
    ROUTED = "ROUTED"
    HAS_SPEC = "HAS_SPEC"
    INSTALLED_IN = "INSTALLED_IN"
    HAS_CHANNEL = "HAS_CHANNEL"
    DESCRIBES = "DESCRIBES"
    SAFETY_REF = "SAFETY_REF"
    ROUTE_LEGEND = "ROUTE_LEGEND"
    CROSS_REF = "CROSS_REF"
    CONTACT_REF = "CONTACT_REF"  # relay coil contact table entry "43/D4": auxiliary contact on sheet 43 at D4
    SLOT_SHEET = "SLOT_SHEET"  # rack layout: this slot's schematic is on sheet N
    WIRED_TO = "WIRED_TO"  # schematic channel row: channel -> device / JB / wire drawn in that row
    IN_ENCLOSURE = "IN_ENCLOSURE"  # CBD: device drawn inside a JB / LCS / panel outline
    WORD = "WORD"  # a plain word of drawing text (5+ letters), for the spelling check


class IssueCode(StrEnum):
    """Ingestion and parsing problems. These are not QA findings: they say what
    the platform could not read, so the checker knows what is not covered."""

    MISSING_FOLDER = "MISSING_FOLDER"
    EMPTY_FOLDER = "EMPTY_FOLDER"
    UNKNOWN_FOLDER = "UNKNOWN_FOLDER"
    IGNORED_FILE = "IGNORED_FILE"
    DUPLICATE_FILE = "DUPLICATE_FILE"
    UNREADABLE_FILE = "UNREADABLE_FILE"
    NEEDS_OCR = "NEEDS_OCR"
    EMPTY_PAGE = "EMPTY_PAGE"
    PARSER_ERROR = "PARSER_ERROR"
    NO_TEMPLATE = "NO_TEMPLATE"
    DUPLICATE_SHEET = "DUPLICATE_SHEET"
    NO_TITLE_BLOCK = "NO_TITLE_BLOCK"
    NO_GRID = "NO_GRID"
    REGISTER_UNREADABLE = "REGISTER_UNREADABLE"  # QA register could not be read; its decisions were not carried
    REGISTER_ROW_SKIPPED = "REGISTER_ROW_SKIPPED"  # a register row's decision was not recognised


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    INFO = "INFO"


class Confidence(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    AI_ASSISTED = "AI_ASSISTED"


class Decision(StrEnum):
    """The checker's decision on a finding (DATA_MODEL.md section 4.7), carried forward by fingerprint."""

    OPEN = "OPEN"  # not reviewed yet
    CONFIRMED = "CONFIRMED"  # a real problem
    FALSE_POSITIVE = "FALSE_POSITIVE"  # not a problem; greyed out on later runs
    FIXED = "FIXED"  # fixed in the drawings
    WAIVED = "WAIVED"  # known exception, accepted
    DEFERRED = "DEFERRED"  # decide later


class StageStatus(StrEnum):
    DONE = "DONE"
    SKIPPED = "SKIPPED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    FAILED = "FAILED"
