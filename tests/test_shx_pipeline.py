"""SHX stroke decoding in a run: --ocr decodes NO_SHX_TEXT sheets in a second pass."""

from __future__ import annotations

import pytest

from drawing_qa.config import Settings
from drawing_qa.model.enums import EntityType, ExtractionMethod, IssueCode, Predicate
from drawing_qa.pipeline import run_pipeline
from tests.conftest import build_pdf, sheet_frame
from tests.shx_font import as_strokes
from tests.test_plc import channel_row, slot_sheet
from tests.test_rules import DESCRIPTOR, FOLDER

DRAWING = "401103"
FILL = 0.4  # text height as a share of its note's height, as on the client's sheets


@pytest.fixture
def cfg() -> Settings:
    s = Settings()
    s.identifiers.areas = ["LTL", "RCL", "SPL", "EER1"]
    s.identifiers.sheet = r"\d{2,3}"
    s.pdf.min_drawing_bytes = 100  # the stroke-only test sheets are small
    return s


# The wire number legend with its three lines spaced apart, so no note's box covers another's text.
_LEGEND = {"WIRE NUMBER": (1032, 44, 1092, 54), "FIRST FIELD": (1032, 56, 1085, 66), "IDENTIFIER": (1032, 68, 1079, 78)}


def _items(kind, sheet, items, frame, title_sheet=None):
    items = [(_LEGEND.get(text, rect), text) for rect, text in items]
    frame = dict(frame)
    frame.setdefault("descriptor", DESCRIPTOR[kind])
    # The title block's sheet number sits a little apart from its "Sheet N°" label, as on the client's sheets.
    title = [((r[0] + 8, r[1], r[2] + 8, r[3]), t) if r == (1038, 779, 1074, 799) else (r, t)
             for r, t in sheet_frame(DRAWING, title_sheet or sheet, **frame)]
    return title + list(items)


def readable_sheet(root, kind, sheet, items, **frame):
    """A sheet plotted with SHX notes: notes over their own strokes."""
    everything = _items(kind, sheet, items, frame)
    build_pdf(root / FOLDER[kind] / f"{DRAWING}-{sheet}.pdf", [[]], lines=[as_strokes(everything, FILL)], shx=[everything])


def unreadable_sheet(root, kind, sheet, items, *, title_sheet=None, text=(), scale=1.0, italic=False, **frame):
    """The same sheet plotted without SHX notes (PDFSHX=0): strokes only, plus any real text words."""
    everything = _items(kind, sheet, items, frame, title_sheet)
    if scale != 1.0:  # every text taller, as if plotted in another font size
        everything = [((r[0], r[3] - (r[3] - r[1]) * scale, r[2] + (r[2] - r[0]) * (scale - 1), r[3]), t) for r, t in everything]
    strokes = as_strokes(everything, FILL)
    if italic:  # every letter slanted: another font, not another size
        strokes = [(x0 + 0.3 * (r[3] - y0), y0, x1 + 0.3 * (r[3] - y1), y1, w)
                   for r, t in everything for x0, y0, x1, y1, w in as_strokes([(r, t)], FILL)]
    build_pdf(root / FOLDER[kind] / f"{DRAWING}-{sheet}.pdf", [list(text)], lines=[strokes])


def slot_037(device="S-TN37D4", extra=()):
    rows = [
        channel_row(113, "00", "IN-0", device, "B4", "D6", "LTL-JB2", ["LONG TRAVEL BRAKE", "RELEASED SWITCH"]),
        channel_row(182, "01", "IN-1", wire="F6", description=["SPARE"]),
        list(extra),
    ]
    return slot_sheet(sheet="037", slot=14, rows=rows)


def package(tmp_path, *, readable=True, device="S-TN37D4", extra=(), readable_extra=(), **unreadable_kw):
    """Readable slot sheet 036 to learn from; slot sheet 037 plotted without SHX notes."""
    if readable:
        items, frame = slot_sheet()
        # Characters sheet 037 uses that 036 does not show at those sizes: a 7 like the device tags,
        # a 4 like the rack and POINTS lines.
        items = items + [((505, 400, 567, 410), "NOTE 7"), ((300, 600, 340, 614), "N 14"), ((300, 640, 340, 652), "S14")]
        readable_sheet(tmp_path, "PLC", "036", items + list(readable_extra), **frame)
    items, frame = slot_037(device, extra)
    unreadable_sheet(tmp_path, "PLC", "037", items, **frame, **unreadable_kw)
    return tmp_path


def _sheet(result, sheet):
    return next(d for d in result.drawings if d.sheet == sheet)


def _claims_on(result, sheet):
    d = _sheet(result, sheet)
    return [c for c in result.claims if c.source.drawing_id == d.id]


def run(root, cfg, ocr=True):
    cfg.ocr.enabled = ocr
    return run_pipeline(root, cfg)


def test_decoded_sheet_yields_channel_claims(tmp_path, cfg):
    result = run(package(tmp_path), cfg)
    channels = {c.subject_key: c.value for c in result.claims if c.predicate is Predicate.HAS_CHANNEL}
    assert channels["LTL/S14/CH00"]["devices"] == ["S-TN37D4"]
    d = _sheet(result, "037")
    assert d.decoded is not None and d.decoded.words_kept > 0
    assert d.text_unreadable is True
    assert all(c.method is ExtractionMethod.SHX_STROKES for c in _claims_on(result, "037") if c.predicate is Predicate.OCCURS)


def test_without_flag_nothing_changes(tmp_path, cfg):
    result = run(package(tmp_path), cfg, ocr=False)
    d = _sheet(result, "037")
    assert _claims_on(result, "037") == [] and d.text_unreadable and d.decoded is None

    plain = tmp_path / "plain"
    items, frame = slot_sheet()
    readable_sheet(plain, "PLC", "036", items, **frame)
    off, on = run(plain, cfg, ocr=False), run(plain, cfg, ocr=True)
    assert [(c.subject_key, c.predicate, c.source.bbox) for c in off.claims] == [(c.subject_key, c.predicate, c.source.bbox) for c in on.claims]
    assert [i.message for i in off.issues] == [i.message for i in on.issues]
    assert [f.fingerprint for f in off.findings] == [f.fingerprint for f in on.findings]


def test_identity_from_file_name(tmp_path, cfg):
    result = run(package(tmp_path, title_sheet="063"), cfg)
    assert _sheet(result, "037").drawing_no == DRAWING
    assert any(i.code is IssueCode.DECODED_TITLE_MISMATCH and "063" in i.message for i in result.issues)


def test_grid_and_ffi_from_decoded_text(tmp_path, cfg):
    d = _sheet(run(package(tmp_path), cfg), "037")
    assert d.grid is not None
    assert d.ffi == "TN"


def test_unseen_font_leaves_sheet_unread(tmp_path, cfg):
    result = run(package(tmp_path, italic=True), cfg)  # a shape no size of the readable sheet's font has
    assert _claims_on(result, "037") == []
    assert any(i.code is IssueCode.SHX_NOT_DECODED and "not seen" in i.message for i in result.issues)
    d = _sheet(result, "037")
    assert not any(e.source and e.source.drawing_id == d.id for f in result.findings for e in f.evidence)


def test_nothing_to_learn_from(tmp_path, cfg):
    result = run(package(tmp_path, readable=False), cfg)
    assert any(i.code is IssueCode.SHX_NOT_DECODED and "nothing to learn from" in i.message for i in result.issues)
    assert _claims_on(result, "037") == []


def test_only_the_unreadable_page_is_decoded(tmp_path, cfg):
    items, frame = slot_sheet()
    everything = _items("PLC", "036", items, frame)
    path = tmp_path / FOLDER["PLC"] / "401103-040.pdf"
    build_pdf(path, [[], []], lines=[as_strokes(everything, FILL), as_strokes(everything, FILL)], shx=[everything, []])

    def page_one(result):
        d = next(d for d in result.drawings if d.page_index == 0)
        return [(c.subject_key, c.predicate, c.source.bbox) for c in result.claims if c.source.drawing_id == d.id]

    off, on = run(tmp_path, cfg, ocr=False), run(tmp_path, cfg, ocr=True)
    assert page_one(off) == page_one(on) != []
    second = next(d for d in on.drawings if d.page_index == 1)
    assert second.decoded is not None


def test_real_words_win_overlaps(tmp_path, cfg):
    # Three real text-layer words on the sheet, one over the decoded LTL-JB2 (channel 00's JB at y 128..139).
    words = [(600, 137, "LTL-JB2"), (200, 400, "STAMP"), (200, 420, "LOGO")]
    result = run(package(tmp_path, text=words), cfg)
    jbs = [c for c in _claims_on(result, "037") if c.subject_type is EntityType.JUNCTION_BOX and c.predicate is Predicate.OCCURS]
    assert len(jbs) == 1 and jbs[0].method is ExtractionMethod.TEXT_LAYER


def test_a_sheet_that_fails_to_decode_does_not_stop_the_run(tmp_path, cfg, monkeypatch):
    import drawing_qa.pipeline as pipeline

    def broken(page, book, **kw):
        raise ValueError("bad stroke data")

    monkeypatch.setattr(pipeline, "decode_page", broken)
    result = run(package(tmp_path), cfg)
    assert any(i.code is IssueCode.PARSER_ERROR and "bad stroke data" in i.message and "037" in (i.file_path or "")
               for i in result.issues)
    assert _sheet(result, "037").decoded is None and _claims_on(result, "037") == []


def test_a_readable_sheet_that_fails_to_learn_from_does_not_stop_the_run(tmp_path, cfg, monkeypatch):
    from drawing_qa.pdf.shx.book import GlyphBook

    def broken(self, page, titles):
        raise ValueError("odd drawing")

    monkeypatch.setattr(GlyphBook, "learn_page", broken)
    result = run(package(tmp_path), cfg)
    assert any(i.code is IssueCode.SHX_NOT_DECODED for i in result.issues)
