"""AutoCAD SHX text: the client's drawings have no text layer, only SHX annotations."""

import pymupdf

from drawing_qa.model.enums import DocType, EntityType, ExtractionMethod, PageKind, Predicate
from drawing_qa.parsers.base import ParseContext
from drawing_qa.parsers.termination import TerminationParser
from drawing_qa.parsers.titleblock import drawing_for_page
from drawing_qa.pdf.extract import decode_autocad_text, extract_page

SHEET = [
    ((100, 100, 200, 110), "=K07.11+LTL-JB1"),
    ((100, 120, 200, 130), "=K07.11+LTL-AUT1"),
    ((100, 140, 160, 150), "S-TN35T4"),
    ((170, 140, 220, 150), "TN35T6"),
    ((100, 160, 200, 170), "(REF TERM. XXX)"),
    ((100, 180, 160, 190), "CXXXXX"),
    ((100, 200, 300, 210), "LAND LEG REMOTE I/O PANEL"),
    ((10, 300, 20, 400), "VERTICAL TEXT"),
    ((1000, 750, 1040, 760), "Drawing N%%D"),
]


def test_decode_autocad_codes():
    assert decode_autocad_text("Drawing N%%D") == "Drawing N°"
    assert decode_autocad_text("%%c25 %%p0.5 100%%%") == "Ø25 ±0.5 100%"


def test_shx_annotations_become_words(tmp_path, make_pdf):
    path = make_pdf(tmp_path / "t.pdf", [[]], shx=[SHEET])
    with pymupdf.open(path) as doc:
        content = extract_page(doc[0], min_words=5)
    assert content.kind is PageKind.VECTOR
    assert content.methods == {ExtractionMethod.SHX_ANNOTATION}
    assert "LAND LEG REMOTE I/O PANEL" in [line.text for line in content.lines]
    assert "Drawing N°" in [line.text for line in content.lines]
    # Multi-word annotations are split; word boxes share the annotation box in order.
    land, leg = [w for w in content.words if w.text in ("LAND", "LEG")]
    assert abs(land.bbox[0] - 100) <= 2 and land.bbox[2] <= leg.bbox[0]  # annot rect includes its border
    # Vertical text is split along y, reading bottom to top.
    vertical, text = [w for w in content.words if w.text in ("VERTICAL", "TEXT")]
    assert vertical.bbox[1] > text.bbox[1]


def test_termination_sheet_claims(tmp_path, make_pdf, settings):
    settings.identifiers.areas = ["LTL", "RCL", "SPL"]  # generic locations need known areas
    path = make_pdf(tmp_path / "401103-203.pdf", [[]], shx=[SHEET])
    with pymupdf.open(path) as doc:
        content = extract_page(doc[0], min_words=5)
        drawing = drawing_for_page("Terminations/401103-203.pdf", DocType.TERM, content, 1)
        result = TerminationParser().parse(content, drawing, ParseContext(settings))

    occurs = {(c.subject_type, c.subject_key) for c in result.claims if c.predicate is Predicate.OCCURS}
    assert occurs == {
        (EntityType.JUNCTION_BOX, "LTL-JB1"),
        (EntityType.LOCATION, "LTL-AUT1"),
        (EntityType.DEVICE, "S-TN35T4"),
        (EntityType.WIRE, "TN35T6"),
    }
    jb = next(c for c in result.claims if c.subject_key == "LTL-JB1")
    assert jb.value["function"] == "K07.11"
    assert jb.value["is_variant"] is False
    assert jb.method is ExtractionMethod.SHX_ANNOTATION

    placeholders = sorted((c.subject_key, c.value["pattern"]) for c in result.claims if c.predicate is Predicate.PLACEHOLDER)
    # "(REF TERM. XXX)" is one phrase hit, not also a separate XXX hit.
    assert placeholders == [("AUT1", "COMPONENT"), ("CXXXXX", "TOKEN"), ("REF TERM XXX", "PHRASE")]


def test_superseded_designation_carries_replacement(tmp_path, make_pdf, settings):
    """Checker-confirmed example: AUT was renamed PLC, so LTL-AUT1 is an incorrect reference to LTL-PLC."""
    settings.identifiers.areas = ["LTL"]
    settings.identifiers.superseded = {"LTL-AUT1": "LTL-PLC"}
    path = make_pdf(tmp_path / "401103-201.pdf", [[]], shx=[[((100, 100, 200, 110), "=K07.11+LTL-AUT1")]])
    with pymupdf.open(path) as doc:
        content = extract_page(doc[0], min_words=1)
        drawing = drawing_for_page("Terminations/401103-201.pdf", DocType.TERM, content, 1)
        result = TerminationParser().parse(content, drawing, ParseContext(settings))

    location = next(c for c in result.claims if c.predicate is Predicate.OCCURS)
    assert location.subject_key == "LTL-AUT1"  # evidence keeps what the drawing says
    assert location.value["superseded_by"] == "LTL-PLC"
    placeholder = next(c for c in result.claims if c.predicate is Predicate.PLACEHOLDER)
    assert placeholder.subject_key == "AUT1"
    assert placeholder.value["replacement"] == "LTL-PLC"
