import pymupdf

from drawing_qa.model.enums import DocType, EntityType, IssueCode, Predicate
from drawing_qa.parsers.base import ParseContext, ParseResult, TemplateReader
from drawing_qa.parsers.registry import build_parsers
from drawing_qa.parsers.termination import TerminationParser
from drawing_qa.parsers.titleblock import drawing_for_page
from drawing_qa.pdf.extract import extract_page


def _parse(tmp_path, make_pdf, settings, text_items, parser=None):
    path = make_pdf(tmp_path / "401103-203.pdf", [text_items])
    parser = parser or TerminationParser()
    with pymupdf.open(path) as doc:
        content = extract_page(doc[0], min_words=1)
        drawing = drawing_for_page("Terminations/401103-203.pdf", DocType.TERM, content, doc.page_count)
        return drawing, parser.parse(content, drawing, ParseContext(settings))


def test_registry_has_one_parser_per_type():
    parsers = build_parsers()
    assert set(parsers) == {DocType.PLC, DocType.CBD, DocType.TERM}
    assert all(p.doc_type is t for t, p in parsers.items())


def test_drawing_identity_from_file_name(tmp_path, make_pdf, settings):
    drawing, _ = _parse(tmp_path, make_pdf, settings, [(50, 50, "X")])
    assert drawing.drawing_no == "401103-203"
    assert drawing.sheet == "1"
    assert drawing.id == "Terminations/401103-203.pdf#p1"


def test_occurrence_claims(tmp_path, make_pdf, settings):
    drawing, result = _parse(
        tmp_path, make_pdf, settings, [(50, 50, "SPL-JB1:1 TN39N6 S-TN39N4 LTL-PLC SPL-JB-1 PUMP")]
    )
    occurs = [(c.subject_type, c.subject_key) for c in result.claims if c.predicate is Predicate.OCCURS]
    assert occurs == [
        (EntityType.TERMINAL, "SPL-JB1:1"),
        (EntityType.WIRE, "TN39N6"),
        (EntityType.DEVICE, "S-TN39N4"),
        (EntityType.PANEL, "LTL-PLC"),
        (EntityType.JUNCTION_BOX, "SPL-JB1"),
    ]
    variant = result.claims[-1]
    assert variant.source.raw_text == "SPL-JB-1"
    assert variant.value["is_variant"] is True
    first = result.claims[0]
    assert first.parser is DocType.TERM
    assert first.source.drawing_id == drawing.id
    assert first.id == f"{drawing.id}/c00001"


def test_placeholder_claims(tmp_path, make_pdf, settings):
    _, result = _parse(tmp_path, make_pdf, settings, [(50, 50, "SPL-JB1:2 REF TERM XXX"), (50, 80, "AUT1 S-TNXXXX")])
    hits = sorted((c.subject_key, c.value["pattern"]) for c in result.claims if c.predicate is Predicate.PLACEHOLDER)
    # The XXX inside "REF TERM XXX" is reported once, as the phrase.
    assert hits == [("AUT1", "TOKEN"), ("REF TERM XXX", "PHRASE"), ("S-TNXXXX", "X_RUN")]


def test_no_template_issue_only_when_readers_exist(tmp_path, make_pdf, settings):
    class NeverMatches(TemplateReader):
        name = "never"

        def matches(self, page, drawing):
            return False

        def read(self, page, drawing, ctx, parser, prior):
            return ParseResult()

    class WithTemplates(TerminationParser):
        template_readers = (NeverMatches,)

    class WithoutTemplates(TerminationParser):
        template_readers = ()

    _, plain = _parse(tmp_path, make_pdf, settings, [(50, 50, "SPL-JB1:1")], parser=WithoutTemplates())
    assert plain.issues == []
    _, templated = _parse(tmp_path / "b", make_pdf, settings, [(50, 50, "SPL-JB1:1")], parser=WithTemplates())
    assert [i.code for i in templated.issues] == [IssueCode.NO_TEMPLATE]
