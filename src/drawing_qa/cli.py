"""Command line: python -m drawing_qa run --input input/Main [--output reports/Main]"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from drawing_qa import __version__
from drawing_qa.config import DEFAULT_FOLDERS, Settings
from drawing_qa.ingest.ffi import describe
from drawing_qa.model.enums import DocType, IssueCode
from drawing_qa.pipeline import run_pipeline
from drawing_qa.report.writer import write_outputs
from drawing_qa.review import carry_forward
from drawing_qa.survey import render as render_survey
from drawing_qa.survey import survey

DEFAULT_CONFIG = Path("config/project.toml")
_INPUT_TYPES = (DocType.PLC, DocType.CBD, DocType.TERM, DocType.EQUIP)


def _doc_types(values: list[str] | None) -> list[DocType]:
    """'term', 'Terminations', 'cbd,equip' -> document types (type codes or input folder names)."""
    names = {t.value.lower(): t for t in _INPUT_TYPES} | dict(DEFAULT_FOLDERS)
    out: list[DocType] = []
    for value in values or []:
        for part in value.split(","):
            key = part.strip().lower()
            if not key:
                continue
            if key not in names:
                choices = ", ".join(t.value.lower() for t in _INPUT_TYPES)
                raise argparse.ArgumentTypeError(f"unknown input type '{part.strip()}' (use {choices}, or a folder name)")
            if names[key] not in out:
                out.append(names[key])
    return out


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="drawing-qa", description="Controls drawing package QA")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Review one input package")
    run.add_argument("--input", required=True, type=Path, help="Folder containing PLC/, CableBlocks/, Terminations/, EquipmentList/ (any may be missing)")
    run.add_argument("--output", type=Path, help="Output folder (default: reports/<input folder name>)")
    run.add_argument("--config", type=Path, help=f"Project config TOML (default: {DEFAULT_CONFIG} if present)")
    run.add_argument(
        "--exclude", action="append", metavar="TYPE",
        help="Leave an input type out of the review: plc, cbd, term or equip (or its folder name, e.g. Terminations). "
             "Repeat or comma-separate for several. Its files are still listed, but not checked. All four are included by default",
    )
    run.add_argument(
        "--include", action="append", metavar="TYPE",
        help="Put back an input type that [ingest] unchecked_types in the config leaves out",
    )
    run.add_argument(
        "--ocr", action="store_true",
        help="Read sheets plotted without SHX text (NO_SHX_TEXT) by decoding their letter strokes, "
             "learned from the readable sheets",
    )
    run.add_argument("-v", "--verbose", action="store_true")
    sv = sub.add_parser("survey", help="Count which PDFs can be read from their SHX text and which need decoding (--ocr); checks nothing")
    sv.add_argument("--input", required=True, type=Path, help="Folder to survey: every PDF under it, any layout")
    sv.add_argument("--config", type=Path, help=f"Project config TOML (default: {DEFAULT_CONFIG} if present)")
    sv.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    config_path = args.config or (DEFAULT_CONFIG if DEFAULT_CONFIG.is_file() else None)
    if args.config and not args.config.is_file():
        print(f"Config not found: {args.config}", file=sys.stderr)
        return 2
    if not args.input.is_dir():
        print(f"Input folder not found: {args.input}", file=sys.stderr)
        return 2

    settings = Settings.load(config_path)
    if args.command == "survey":
        print(render_survey(survey(args.input, settings), args.input))
        return 0
    try:
        excluded, included = _doc_types(args.exclude), _doc_types(args.include)
    except argparse.ArgumentTypeError as exc:
        print(f"drawing-qa run: {exc}", file=sys.stderr)
        return 2
    both = set(excluded) & set(included)
    if both:
        print(f"drawing-qa run: {', '.join(t.value for t in both)} is both excluded and included", file=sys.stderr)
        return 2
    unchecked = [t for t in settings.ingest.unchecked_types if t not in included]
    settings.ingest.unchecked_types = unchecked + [t for t in excluded if t not in unchecked]
    settings.ocr.enabled = settings.ocr.enabled or args.ocr
    result = run_pipeline(args.input, settings)
    out_dir = args.output or Path("reports") / args.input.resolve().name
    review = carry_forward(result, out_dir, settings) if settings.review.enabled else None
    paths = write_outputs(result, out_dir)

    print(f"Run {result.info.run_id}: {len(result.files)} file(s), {len(result.drawings)} sheet(s), "
          f"{len(result.claims)} claim(s), {len(result.issues)} issue(s)")
    unreadable = [i for i in result.issues if i.code is IssueCode.NO_SHX_TEXT]
    if unreadable:
        print(f"  WARNING: {len(unreadable)} sheet(s) have no readable text (plotted without SHX text notes): "
              "re-plot them from AutoCAD with PDFSHX = 1. They are listed under Parse issues in report.md")
        if settings.ocr.enabled:
            decoded = sum(1 for d in result.drawings if d.decoded is not None)
            print(f"  Decoded {decoded} of {len(unreadable)} unreadable sheet(s) from their line work (--ocr); "
                  "see 'Sheets decoded from line work' in report.md")
    for issue in (i for i in result.issues if i.code in (IssueCode.FFI_MAPPING, IssueCode.PANEL_MAPPING)):
        print(f"  WARNING: {issue.message}")
    if result.ffi_map:
        print("  FFIs: " + describe(result.ffi_map))
    reviewed = [t.value for t in _INPUT_TYPES if t not in settings.ingest.unchecked_types]
    left_out = [t.value for t in settings.ingest.unchecked_types]
    print(f"  reviewed: {', '.join(reviewed) or 'nothing'}" + (f"; left out: {', '.join(left_out)}" if left_out else ""))
    if review is not None:
        print(f"  review: {review.decisions_recorded} decision(s) read from the QA register, "
              f"{review.new_findings} new finding(s), {len(review.no_longer_raised)} no longer raised")
    for path in paths:
        print(f"  wrote {path}")
    return 0
