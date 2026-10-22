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
from drawing_qa.model.source import Drawing, Issue
from drawing_qa.parsers.base import ParseContext
from drawing_qa.parsers.plc.wires import sheet_ffi
from drawing_qa.parsers.registry import build_parsers
from drawing_qa.parsers.titleblock import drawing_for_page
from drawing_qa.pdf.extract import extract_page, open_pdf
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
                        if content.kind is PageKind.NO_TEXT:
                            continue
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
                    if parser is None:
                        continue
                    try:
                        parsed = parser.parse(content, drawing, ctx)
                    except Exception as exc:  # one bad sheet must not stop the run
                        log.exception("Parser failed on %s", drawing.id)
                        result.issues.append(
                            Issue(code=IssueCode.PARSER_ERROR, message=f"{type(exc).__name__}: {exc}", file_path=record.path, drawing_id=drawing.id)
                        )
                        continue
                    result.claims.extend(parsed.claims)
                    result.issues.extend(parsed.issues)
        except Exception as exc:
            record.readable = False
            result.issues.append(
                Issue(code=IssueCode.UNREADABLE_FILE, message=f"{type(exc).__name__}: {exc}", file_path=record.path)
            )

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
