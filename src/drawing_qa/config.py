"""Project configuration, loaded from config/project.toml."""

from __future__ import annotations

import hashlib
import tomllib
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field

from drawing_qa.model.enums import DocType

DEFAULT_FOLDERS: dict[str, DocType] = {
    "plc": DocType.PLC,
    "schematic": DocType.PLC,
    "schem": DocType.PLC,
    "schematics": DocType.PLC,
    "schems": DocType.PLC,
    "cableblocks": DocType.CBD,
    "cableblock": DocType.CBD,
    "cbd": DocType.CBD,
    "terminations": DocType.TERM,
    "termination": DocType.TERM,
    "terms": DocType.TERM,
    "equipmentlist": DocType.EQUIP,
    "equipment": DocType.EQUIP,
    "equiplist": DocType.EQUIP,
    "eel": DocType.EQUIP,
}


class IngestConfig(BaseModel):
    folders: dict[str, DocType] = Field(default_factory=lambda: dict(DEFAULT_FOLDERS))
    # Document types left out of the review (e.g. drawings that are not up to date). Their files are
    # still read and listed, but produce no findings and are not used by other checks or traces.
    # The command line adds to this with --exclude and removes from it with --include.
    unchecked_types: list[DocType] = Field(default_factory=list)


class PdfConfig(BaseModel):
    min_words_for_vector: int = 5
    # A page whose drawing content is at least this size (bytes) is drawn, not blank. A blank A1
    # sheet is a few hundred bytes; a plotted controls sheet is hundreds of kilobytes.
    min_drawing_bytes: int = 5000
    shx_annotation_titles: list[str] = Field(default_factory=lambda: ["AutoCAD SHX Text"])


class IdentifierConfig(BaseModel):
    prefix: str = r"[A-Z]{1,4}"
    ffi: str = r"[A-Z]{2}"
    sheet: str = r"\d{2}"
    grid: str = r"[A-Z]\d"
    prefixes: list[str] = Field(default_factory=list)
    ffis: list[str] = Field(default_factory=list)
    areas: list[str] = Field(default_factory=list)
    panel_types: list[str] = Field(default_factory=lambda: ["PLC"])
    # Device prefixes for equipment inside the panel (relays, breakers): not expected on cable block diagrams.
    panel_device_prefixes: list[str] = Field(default_factory=lambda: ["K", "Q"])
    # Device type, as the end of the caption under its tag names it -> the prefix it must carry (TAG-010).
    # {"SOLENOID": "Y"}: "... RELEASE SOLENOID SV3" must be Y-...; "SOLENOID DE-ENERGISED" (a switch) is not
    # a solenoid. Empty: no check.
    type_prefixes: dict[str, str] = Field(default_factory=dict)
    # Device prefix -> the I/O direction its signal must have (DEV-004): "IN" (DI / AI / safety inputs) or
    # "OUT" (DO / AO / safety outputs). {"S": "IN", "H": "OUT"}: a switch on a DO slot is reported for review.
    # Prefixes used both ways (relays K) are left out. Empty: no check.
    io_direction: dict[str, str] = Field(default_factory=dict)
    # FFI -> the drawing number whose sheets that FFI's tags and wires live on.
    # TN -> 401103: S-TN23N2 is drawn on 401103 sheet 023. Use "EXTERNAL" for an FFI from a
    # separate package that is not supplied (its references are listed, not checked).
    # Empty map: any sheet number is looked up across the PLC folder (older behaviour).
    ffi_drawings: dict[str, str] = Field(default_factory=dict)
    # Old designation -> current one, e.g. LTL-AUT1 -> LTL-PLC. Occurrences of the old
    # key are still recorded as written, flagged with the replacement.
    superseded: dict[str, str] = Field(default_factory=dict)


class PlaceholderConfig(BaseModel):
    tokens: list[str] = Field(
        default_factory=lambda: ["XXXX", "XXX", "TNXXXX", "PXXXXX", "CXXXXX", "HOLD", "AUT1"]
    )
    phrases: list[str] = Field(default_factory=lambda: ["REF TERM XXX"])
    hold_exceptions: list[str] = Field(default_factory=list)


class TitleBlockConfig(BaseModel):
    # Title block region as fractions of the page (bottom-right corner).
    x_min: float = 0.45
    y_min: float = 0.80
    # Furthest a value may sit from its label, in PDF points.
    max_value_distance: float = 60.0
    # Sheets whose descriptor contains one of these are reference material (legends,
    # symbol keys, title and index pages). Their example tags are not checked as devices.
    reference_descriptors: list[str] = Field(
        default_factory=lambda: ["GRAPHICS SYMBOLS", "LEGEND", "TITLE PAGE", "INDEX", "COVER SHEET"]
    )
    # Descriptor keyword -> document type, checked in order.
    descriptor_types: dict[str, DocType] = Field(
        default_factory=lambda: {
            "TERMINATION": DocType.TERM,
            "BLOCK DIAGRAM": DocType.CBD,
            "SCHEMATIC": DocType.PLC,
        }
    )


class GridConfig(BaseModel):
    # Row labels sit within this fraction of the page width from the left edge;
    # column labels within this fraction of the page height from the top.
    margin: float = 0.06
    min_labels: int = 3


class ModuleSpec(BaseModel):
    type: str  # DI, DO, AI, AO
    channels: int
    safety: bool = False


class PlcConfig(BaseModel):
    # Module catalogue: part number -> what it is. Used to check capacity and type.
    modules: dict[str, ModuleSpec] = Field(
        default_factory=lambda: {
            "1734-IB8": ModuleSpec(type="DI", channels=8),
            "1734-IB8S": ModuleSpec(type="DI", channels=8, safety=True),
            "1734-OB8": ModuleSpec(type="DO", channels=8),
            "1734-OB8S": ModuleSpec(type="DO", channels=8, safety=True),
            "1734-IE4C": ModuleSpec(type="AI", channels=4),
            "1734-IE8C": ModuleSpec(type="AI", channels=8),
            "1734-OE4C": ModuleSpec(type="AO", channels=4),
        }
    )
    # Expected form of the POINTS line on slot sheets: <rack>.S<slot>.<type>.<first>..<last>
    points_format: str = r"^[A-Z0-9]+\.S\d+\.(DI|DO|AI|AO)\.\d{2}\.\.\d{2}$"
    # Rack name -> the panel that holds it. Unlisted racks default to <rack>-PLC (LTL -> LTL-PLC); the run
    # warns when that default names a panel no drawing shows.
    rack_panels: dict[str, str] = Field(default_factory=dict)
    # The project / area text that starts every channel description ("07.11 SHIPLOADER / LONG TRAVEL ...").
    # Left unset, it is detected: the first description line shared by at least half of the channels.
    # Set "" for none.
    description_prefix: str | None = None


class CbdConfig(BaseModel):
    """How cable block diagrams are drawn: line weights (pt) that tell cables and enclosure outlines
    from text and symbols. Defaults are the client's CAD standard (cables 0.96 pt, outlines 0.24 pt);
    another drafting house may need its own values here."""

    outline_max_width: float = 0.3  # enclosure outlines (dash-dot) are at most this wide
    cable_min_width: float = 0.9  # cables are at least this wide
    solid_min_width: float = 0.3  # solid boxes (symbols, some enclosures) are at least this wide
    cable_label_gap: float = 40.0  # a cable is broken by its label box; rejoin across gaps up to this


class SpellingConfig(BaseModel):
    """A word used at most `max_rare` times that is one letter away from a word used at least
    `min_common` times in the same package is reported (STOBE vs STROBE). Real words that happen
    to be one letter from a common one go in `known_words`."""

    min_length: int = 5
    max_rare: int = 2
    min_common: int = 3
    known_words: list[str] = Field(default_factory=lambda: ["CLOSE", "LATER", "LOWER", "POWER", "TOWER", "WATER", "LEVEL"])


class RuleSetting(BaseModel):
    enabled: bool = True
    severity: str | None = None  # overrides the rule's default


class Waiver(BaseModel):
    """A known, accepted exception (config/waivers.toml): findings of `rule` whose subject matches
    `subject` are marked WAIVED. Patterns use * and ?, e.g. rule "COMP-*", subject "SH-*"."""

    id: str
    rule: str
    subject: str = "*"
    reason: str
    approved_by: str = ""
    expires: date | None = None  # not applied after this date


class OcrConfig(BaseModel):
    """Reading sheets plotted without SHX text notes (NO_SHX_TEXT) by decoding their letter strokes,
    learned from the package's readable sheets. Off unless `run --ocr` (or enabled = true)."""

    enabled: bool = False
    # Hold back findings whose every piece of evidence is on decoded sheets (off: raise them, marked).
    confirm_decoded: bool = False
    # Stop learning once this many readable sheets in a row teach nothing new.
    patience: int = 3


class DwgConfig(BaseModel):
    """Reading the text of a sheet plotted without SHX notes from its own DWG, put beside the PDF with the same
    name (401104-052.pdf + 401104-052.dwg). Needs the free ODA File Converter (opendesign.com)."""

    enabled: bool = True
    # ODAFileConverter.exe. Empty: the ODA_FILE_CONVERTER variable, PATH, Program Files\ODA or ~/Tools.
    converter: str = ""
    # Width of one letter as a fraction of the text height (SHX isocp / romans: about 0.72).
    char_width: float = 0.72
    # Share of the DWG's straight lines that must land on the PDF's for the DWG to be used.
    min_line_match: float = 0.9
    # Share of the DWG's texts that may have no ink under them on the PDF before the DWG is refused.
    max_unplaced: float = 0.1
    # A DWG saved more than this many hours after the PDF was plotted is used, but reported (DWG_NEWER).
    newer_hours: float = 1.0


class ReviewConfig(BaseModel):
    """Recording decisions in the register (Decision / comment columns, carried forward between runs
    in qa_review.sqlite, waivers, No Longer Raised). Off: the register is for printing and marking up
    by hand, with no review columns. The review code is kept; set enabled = true to use it again."""

    enabled: bool = False


class ProjectInfo(BaseModel):
    code: str = ""  # labels for whoever reads the file; checks take drawing numbers from the sheets
    name: str = ""


# Settings specific to one project, written in its [project] section and read into the section that uses them.
# They may also sit in the old places ([identifiers], [plc]); the [project] section wins.
_PROJECT_KEYS = {
    "areas": "identifiers",
    "ffi_drawings": "identifiers",
    "superseded": "identifiers",
    "rack_panels": "plc",
    "description_prefix": "plc",
}


class Settings(BaseModel):
    project: ProjectInfo = Field(default_factory=ProjectInfo)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    pdf: PdfConfig = Field(default_factory=PdfConfig)
    identifiers: IdentifierConfig = Field(default_factory=IdentifierConfig)
    placeholders: PlaceholderConfig = Field(default_factory=PlaceholderConfig)
    title_block: TitleBlockConfig = Field(default_factory=TitleBlockConfig)
    grid: GridConfig = Field(default_factory=GridConfig)
    plc: PlcConfig = Field(default_factory=PlcConfig)
    spelling: SpellingConfig = Field(default_factory=SpellingConfig)
    cbd: CbdConfig = Field(default_factory=CbdConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    dwg: DwgConfig = Field(default_factory=DwgConfig)
    rules: dict[str, RuleSetting] = Field(default_factory=dict)  # from rules.toml beside the project config
    waivers: list[Waiver] = Field(default_factory=list)  # from waivers.toml beside the project config
    config_hash: str = ""

    @classmethod
    def load(cls, path: Path | None) -> Settings:
        """Load settings from a TOML file (and rules.toml next to it); defaults apply when path is None."""
        if path is None:
            return cls()
        raw = path.read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
        rules_path = path.with_name("rules.toml")
        if rules_path.is_file():
            rules_raw = rules_path.read_bytes()
            data["rules"] = tomllib.loads(rules_raw.decode("utf-8")).get("rules", {})
            raw += rules_raw
        waivers_path = path.with_name("waivers.toml")
        if waivers_path.is_file():
            waivers_raw = waivers_path.read_bytes()
            data["waivers"] = tomllib.loads(waivers_raw.decode("utf-8")).get("waiver", [])
            raw += waivers_raw
        project = data.get("project", {})
        for key, section in _PROJECT_KEYS.items():
            if key in project:
                data.setdefault(section, {})[key] = project.pop(key)
        settings = cls.model_validate(data)
        settings.config_hash = hashlib.sha256(raw).hexdigest()[:16]
        return settings
