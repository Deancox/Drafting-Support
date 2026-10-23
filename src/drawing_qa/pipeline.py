"""Pipeline orchestration: ingest -> parse -> link -> trace -> rules -> report.

Stages not yet built are recorded as NOT_IMPLEMENTED in the run result, so every
report states plainly what was and was not checked.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from drawing_qa import __version__
from drawing_qa.config import Settings
from drawing_qa.ingest.discovery import discover
from drawing_qa.ingest.ffi import describe, resolve_ffis
from drawing_qa.graph import build_graph
from drawing_qa.linker import link
from drawing_qa.model.enums import DocType, ExtractionMethod, IssueCode, PageKind, StageStatus
from drawing_qa.model.run import RunInfo, RunResult, StageResult
from drawing_qa.model.source import DecodeInfo, Drawing, Issue
from drawing_qa.parsers.base import ParseContext
from drawing_qa.parsers.plc.wires import sheet_ffi
from drawing_qa.parsers.registry import build_parsers
from drawing_qa.parsers.titleblock import drawing_for_page
from drawing_qa.common.grid import read_grid_frame
from drawing_qa.common.titleblock import read_title_block
from drawing_qa.pdf.extract import PageContent, _load_segments, extract_page, open_pdf
from drawing_qa.pdf.shx.book import learn
from drawing_qa.pdf.shx.decode import DecodeStats, decode_page
from drawing_qa.pdf.ocr import OcrEngine
from drawing_qa.rules.context import RuleContext, reference_sheet_ids
from drawing_qa.rules.engine import RuleEngine
from drawing_qa.traces import build_traces
from drawing_qa.traces.coverage import compute_coverage

log = logging.getLogger(__name__)


def run_pipeline(input_root: Path, settings: Settings, ocr: OcrEngine | None = None) -> RunResult:
    started = datetime.now(UTC)
    result = RunResult(
        info=RunInfo(
            run_id=f"{started:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}",
            tool_version=__version__,
            started_at=started,
            input_root=input_root.as_posix(),
            config_hash=settings.config_hash,
        )
    )

    result.left_out = [t.value for t in settings.ingest.unchecked_types]
    discovery = discover(input_root, settings)
    result.issues.extend(discovery.issues)
    result.stages.append(StageResult(name="ingest", status=StageStatus.DONE, note=f"{len(discovery.files)} PDF(s) found"))

    _parse(discovery.files, settings, ocr, result)
    # Which drawing each FFI belongs to, learned from the schematic sheets' FFI box (ingest/ffi.py).
    settings, result.ffi_map, ffi_issues = resolve_ffis(settings, result.drawings, result.claims)
    result.issues.extend(ffi_issues)
    result.stages.append(StageResult(name="ffi", status=StageStatus.DONE, note=describe(result.ffi_map)))
    _link_and_trace(settings, result)
    _run_rules(settings, result)
    result.info.finished_at = datetime.now(UTC)
    return result


def _link_and_trace(settings: Settings, result: RunResult) -> None:
    skip = reference_sheet_ids(result.drawings, settings)
    result.model = link(result.claims, result.drawings, settings, skip)
    edges = len(result.model.edges)
    result.stages.append(
        StageResult(name="link", status=StageStatus.DONE, note=f"{len(result.model.nodes)} nodes, {edges} edges; "
                    f"channel description prefix: {result.model.description_prefix or 'none'}")
    )
    result.issues.extend(_unseen_panels(result.model, settings))
    graph = build_graph(result.model)
    # Hops only an excluded or not-yet-parsed document type can give are "not sourced", never gaps.
    parsed = {t for t, p in build_parsers().items() if p.built_content} - set(settings.ingest.unchecked_types)
    external = {ffi.upper() for ffi, d in settings.identifiers.ffi_drawings.items() if d.upper() == "EXTERNAL"}
    result.traces = build_traces(result.model, graph, parsed, external)
    result.coverage = compute_coverage(result.traces, result.model)
    counts = Counter(t.status.value for t in result.traces)
    note = ", ".join(f"{k.lower()} {v}" for k, v in sorted(counts.items()))
    conflicted = sum(t.conflicted for t in result.traces)
    if conflicted:
        note += f" ({conflicted} conflicted)"
    if DocType.TERM not in parsed:
        note += "; JB terminal hops not sourced (termination drawings left out of the review)"
    result.stages.append(StageResult(name="trace", status=StageStatus.DONE, note=note))


def _unseen_panels(model, settings: Settings) -> list[Issue]:
    """Racks placed in a panel that no drawing shows: the <rack>-PLC default guessed wrong (MCC1 -> MCC1-PLC
    when the drawings say MCC-PLC). Traces would end at a panel nothing else refers to."""
    racks: dict[str, set[str]] = {}
    for e in model.edges:
        if e.type == "MODULE_PANEL":
            panel = model.nodes[e.dst]
            rack = model.nodes[e.src].attrs.get("rack")
            if rack and not panel.parsers:  # derived only: no drawing shows this panel
                racks.setdefault(panel.key, set()).add(rack)
    shown = sorted(n.key for n in model.nodes.values() if n.type == "PANEL" and n.parsers)
    issues = []
    for panel, rack_names in sorted(racks.items()):
        names = ", ".join(sorted(rack_names))
        issues.append(Issue(
            code=IssueCode.PANEL_MAPPING,
            message=(f"Rack {names} is assumed to be in panel {panel}, which no drawing shows"
                     + (f" (the drawings show {', '.join(shown)})" if shown else "")
                     + f". Set the rack's panel under [plc.rack_panels] in the config, e.g. {sorted(rack_names)[0]} = \"{shown[0] if shown else '<panel>'}\""),
        ))
    return issues


def _run_rules(settings: Settings, result: RunResult) -> None:
    ctx = RuleContext(result.drawings, result.claims, settings, model=result.model, traces=result.traces)
    result.findings, result.rule_runs = RuleEngine().run(ctx)
    ran = sum(r.status is StageStatus.DONE for r in result.rule_runs)
    skipped = sum(r.status is StageStatus.SKIPPED for r in result.rule_runs)
    failed = [r.rule_id for r in result.rule_runs if r.status is StageStatus.FAILED]
    note = f"{ran} rule(s) run, {skipped} skipped" + (f", FAILED: {', '.join(failed)}" if failed else "")
    if settings.ingest.unchecked_types:
        types = ", ".join(t.value for t in settings.ingest.unchecked_types)
        note += f"; left out of the review: {types} (read and listed, not checked)"
    result.stages.append(StageResult(name="rules", status=StageStatus.FAILED if failed else StageStatus.DONE, note=note))


def _parse(files, settings: Settings, ocr: OcrEngine | None, result: RunResult) -> None:
    ctx = ParseContext(settings)
    parsers = build_parsers()
    min_words = settings.pdf.min_words_for_vector
    # (drawing no, sheet) -> first drawing seen with that identity, to catch copies of one sheet.
    seen_sheets: dict[tuple[str, str], Drawing] = {}
    # --ocr: NO_SHX_TEXT pages to decode after every sheet is read, and pages with SHX notes to learn from.
    decode = settings.ocr.enabled
    pending: list[tuple] = []
    readable: list[tuple[Path, int]] = []

    for found in files:
        record = found.record
        result.files.append(record)
        if record.doc_type is DocType.EQUIP:
            continue  # listed in the manifest; the equipment list reader comes later
        parser = parsers.get(record.doc_type)
        try:
            with open_pdf(found.path) as doc:
                record.page_count = doc.page_count
                for page in doc:
                    content = extract_page(page, min_words, ocr, settings.pdf.shx_annotation_titles, settings.pdf.min_drawing_bytes)
                    drawing = drawing_for_page(record.path, record.doc_type, content, doc.page_count, settings)
                    drawing.ffi = sheet_ffi(content)
                    result.drawings.append(drawing)
                    unreadable = _unreadable_text(content, drawing)
                    drawing.text_unreadable = bool(unreadable)
                    if unreadable:
                        creator = (doc.metadata or {}).get("creator") or "unknown software"
                        result.issues.append(
                            Issue(
                                code=IssueCode.NO_SHX_TEXT,
                                message=(f"Sheet is drawn but its text cannot be read ({unreadable}); it was plotted without AutoCAD's "
                                         f"SHX text notes (plotted by {creator}). Re-plot it with PDFSHX set to 1"),
                                file_path=record.path,
                                drawing_id=drawing.id,
                            )
                        )
                        if decode:
                            pending.append((found, page.number, drawing, content, parser))
                            continue
                        if content.kind is PageKind.NO_TEXT:
                            continue
                    elif decode and content.shx_word_count:
                        readable.append((found.path, page.number))
                    if drawing.grid is None and content.kind is PageKind.VECTOR and not unreadable:
                        result.issues.append(
                            Issue(
                                code=IssueCode.NO_GRID,
                                message="No grid frame found; grid checks skip this sheet",
                                file_path=record.path,
                                drawing_id=drawing.id,
                            )
                        )
                    if _is_duplicate(drawing, seen_sheets, result):
                        continue
                    if content.kind is PageKind.RASTER and ocr is None:
                        result.issues.append(
                            Issue(
                                code=IssueCode.NEEDS_OCR,
                                message="Page has no text layer (scanned); skipped until OCR is available",
                                file_path=record.path,
                                drawing_id=drawing.id,
                            )
                        )
                        continue
                    if content.kind is PageKind.EMPTY:
                        result.issues.append(
                            Issue(code=IssueCode.EMPTY_PAGE, message="Page has no text or images", file_path=record.path, drawing_id=drawing.id)
                        )
                        continue
                    _parse_page(parser, content, drawing, ctx, result)
        except Exception as exc:
            record.readable = False
            result.issues.append(
                Issue(code=IssueCode.UNREADABLE_FILE, message=f"{type(exc).__name__}: {exc}", file_path=record.path)
            )

    if pending:
        _decode_unreadable(pending, readable, settings, ctx, result)

    notes = "; ".join(
        f"{p.doc_type.value}: identifiers, placeholders"
        + (f", {p.built_content}" if p.built_content else "")
        + f" (planned: {p.planned_content})"
        for p in parsers.values()
    )
    equip = sum(f.record.doc_type is DocType.EQUIP for f in files)
    if equip:
        notes += f"; EQUIP: {equip} equipment list file(s) listed, not read yet (reader waits for the list format)"
    result.stages.append(StageResult(name="parse", status=StageStatus.DONE, note=notes))


def _parse_page(parser, content: PageContent, drawing: Drawing, ctx: ParseContext, result: RunResult) -> None:
    if parser is None:
        return
    try:
        parsed = parser.parse(content, drawing, ctx)
    except Exception as exc:  # one bad sheet must not stop the run
        log.exception("Parser failed on %s", drawing.id)
        result.issues.append(
            Issue(code=IssueCode.PARSER_ERROR, message=f"{type(exc).__name__}: {exc}", file_path=drawing.file_path, drawing_id=drawing.id)
        )
        return
    result.claims.extend(parsed.claims)
    result.issues.extend(parsed.issues)


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _decode_unreadable(pending: list[tuple], readable: list[tuple[Path, int]], settings: Settings, ctx: ParseContext,
                       result: RunResult) -> None:
    """--ocr: decode NO_SHX_TEXT pages from their letter strokes, learned from the readable pages.

    A decoded page keeps its file-name identity; its grid and FFI come from the decoded text. It stays
    text_unreadable (never fully read). Where nothing decodes, the page is handled as without --ocr.
    """

    def pages():
        for path, index in readable:
            with open_pdf(path) as doc:
                yield doc[index]

    book = learn(pages(), settings.pdf.shx_annotation_titles, patience=settings.ocr.patience)
    for found, index, drawing, content, parser in pending:
        with open_pdf(found.path) as doc:
            page = doc[index]
            decoded, stats = decode_page(page, book) if book.size else ([], DecodeStats())
            decoded = [w for w in decoded if not any(_overlaps(w.bbox, r.bbox) for r in content.words)]  # real text wins
            if not decoded:
                reason = ("nothing to learn from: no readable sheet in the package has SHX text notes" if not book.size
                          else "its font or text size was not seen on the readable sheets")
                result.issues.append(Issue(code=IssueCode.SHX_NOT_DECODED, message=f"Not decoded (--ocr): {reason}",
                                           file_path=drawing.file_path, drawing_id=drawing.id))
                if content.kind is not PageKind.NO_TEXT:
                    _parse_page(parser, content, drawing, ctx, result)  # as without --ocr
                continue
            merged = PageContent(page_index=index, width=content.width, height=content.height, kind=PageKind.VECTOR,
                                 words=list(content.words) + decoded, image_count=content.image_count, drawn=True,
                                 _segment_loader=lambda page=page: _load_segments(page))
            drawing.grid = read_grid_frame(merged, settings.grid)
            drawing.ffi = sheet_ffi(merged) or drawing.ffi
            drawing.decoded = DecodeInfo(letters=stats.letters, shapes=stats.shapes, words_kept=len(decoded),
                                         words_dropped=stats.words_dropped)
            for issue in result.issues:
                if issue.code is IssueCode.NO_SHX_TEXT and issue.drawing_id == drawing.id:
                    issue.message += (f"; decoded from line work (--ocr): {stats.letters}/{stats.shapes} letters, "
                                      f"{len(decoded)} words")
            tb = read_title_block(merged, settings.title_block)
            said = [v for v, own in ((tb.drawing_no, drawing.drawing_no), (tb.sheet, drawing.sheet))
                    if v and v.lstrip("0") != own.lstrip("0")]
            if said:
                result.issues.append(Issue(
                    code=IssueCode.DECODED_TITLE_MISMATCH,
                    message=(f"Decoded title block reads drawing {tb.drawing_no or '?'} sheet {tb.sheet or '?'}; "
                             f"the sheet keeps {drawing.label} from its file name"),
                    file_path=drawing.file_path, drawing_id=drawing.id))
            _parse_page(parser, merged, drawing, ctx, result)


def _unreadable_text(content, drawing: Drawing) -> str:
    """Why a drawn sheet's text cannot be read, or "" when it can.

    The client's sheets carry their text only as AutoCAD "SHX Text" notes; plotted without them
    (PDFSHX=0), a sheet is all line work. A few TrueType words (a logo, a stamp) do not make it
    readable: with no SHX notes and no title block found, it is reported too.
    """
    if content.kind is PageKind.NO_TEXT:
        return "no text at all"
    if content.drawn and content.shx_word_count == 0 and drawing.title_block_method is not ExtractionMethod.TEMPLATE:
        return f"only {len(content.words)} word(s) and no title block"
    return ""


def _is_duplicate(drawing: Drawing, seen: dict[tuple[str, str], Drawing], result: RunResult) -> bool:
    """True when this sheet is an identical copy of one already read (e.g. a merged set).

    Identical copies are skipped so their identifiers are not counted twice. Copies
    whose content differs are both kept and cross-linked; DRW-007 reports them.
    """
    if drawing.title_block_method is not ExtractionMethod.TEMPLATE:
        return False
    key = (drawing.drawing_no, drawing.sheet)
    first = seen.get(key)
    if first is None:
        seen[key] = drawing
        return False
    if first.content_hash == drawing.content_hash:
        drawing.duplicate_of = first.id
        result.issues.append(
            Issue(
                code=IssueCode.DUPLICATE_SHEET,
                message=f"Identical copy of {first.file_path} ({first.label}); not read again",
                file_path=drawing.file_path,
                drawing_id=drawing.id,
            )
        )
        return True
    first.conflicts_with.append(drawing.id)
    drawing.conflicts_with.append(first.id)
    return False
