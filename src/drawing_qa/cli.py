"""Command line: python -m drawing_qa run --input input/Main [--output reports/Main]"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tomllib
from pathlib import Path

from pydantic import ValidationError

from drawing_qa import __version__
from drawing_qa.cases import make_cases
from drawing_qa.config import DEFAULT_FOLDERS, Settings
from drawing_qa.ingest.ffi import describe
from drawing_qa.model.enums import DocType, IssueCode
from drawing_qa.pipeline import run_pipeline
from drawing_qa.report.writer import write_outputs
from drawing_qa.review import carry_forward
from drawing_qa.survey import render as render_survey
from drawing_qa.survey import survey

DEFAULT_CONFIG = Path("config/project.toml")
PROJECT = Path(__file__).resolve().parents[2]  # the folder with config/, input/, reports/
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
    run.add_argument(
        "--cases", action=argparse.BooleanOptionalAction, default=None,
        help="Make the Copilot review cases for this run (--no-cases leaves them out). Without either flag, "
             "[cases] enabled in the config decides",
    )
    run.add_argument("--job", help="Job name the review cases are keyed by (letters, digits and hyphens); replaces [cases] job for this run. "
                     "Empty (--job=): the most common drawing number on the PLC schematics")
    run.add_argument("--cases-folder", help="Where the review cases are saved: a full path, or a name for a folder inside the output folder; "
                     "replaces [cases] folder for this run. Empty (--cases-folder=): the default, the output folder's cases folder")
    run.add_argument("-v", "--verbose", action="store_true")
    sv = sub.add_parser("survey", help="Count which PDFs can be read from their SHX text and which need decoding (--ocr); checks nothing")
    sv.add_argument("--input", required=True, type=Path, help="Folder to survey: every PDF under it, any layout")
    sv.add_argument("--config", type=Path, help=f"Project config TOML (default: {DEFAULT_CONFIG} if present)")
    sv.add_argument("-v", "--verbose", action="store_true")
    cs = sub.add_parser("cases", help="Make the Copilot review cases from a finished review's output folder, with no pipeline run")
    cs.add_argument("--report", required=True, type=Path, help="Output folder of a finished review (holds findings.json and review.json)")
    cs.add_argument("--input", type=Path, help="Drawing package folder that review was made from (default: the one review.json records)")
    cs.add_argument("--config", type=Path, help=f"Project config TOML (default: {DEFAULT_CONFIG} if present)")
    cs.add_argument("--job", help="Job name the review cases are keyed by (letters, digits and hyphens); replaces [cases] job. "
                    "Empty (--job=): the most common drawing number on the PLC schematics")
    cs.add_argument("--cases-folder", help="Where the review cases are saved: a full path, or a name for a folder inside the report folder; "
                    "replaces [cases] folder. Empty (--cases-folder=): the default, the report folder's cases folder")
    cs.add_argument("-v", "--verbose", action="store_true")
    return p


def _recorded_input_root(review: object) -> str | None:
    """The drawing folder review.json records (run.input_root), or None when it is not there."""
    run = review.get("run") if isinstance(review, dict) else None
    root = run.get("input_root") if isinstance(run, dict) else None
    return root if isinstance(root, str) and root.strip() else None


def _load_settings(config_path: Path | None) -> Settings | None:
    """The project settings a run uses, or None after one line says why the config cannot be read or checked."""
    try:
        return Settings.load(config_path)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "config"
        print(f"Could not read the project settings {config_path}: {where}: {first['msg']}", file=sys.stderr)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        print(f"Could not read the project settings {config_path}: {exc}", file=sys.stderr)
    return None


def _candidate_folders(recorded: str) -> list[Path]:
    """Where a recorded drawing folder may be: as written when absolute, else from the current folder, then the project folder."""
    path = Path(recorded)
    if path.is_absolute():
        return [path]
    return [(Path.cwd() / path).resolve(), (PROJECT / path).resolve()]


def _cases(args: argparse.Namespace, config_path: Path | None) -> int:
    """Make the review cases from a finished review's output folder, with no pipeline run. Errors print one line."""
    report = args.report
    if report.is_file():
        print(f"{report} is a file; give the review's output folder.", file=sys.stderr)
        return 1
    if not report.is_dir():
        print(f"Report folder not found: {report}. It should be the output folder of a finished review.", file=sys.stderr)
        return 1
    missing = [name for name in ("findings.json", "review.json") if not (report / name).is_file()]
    if missing:
        print(f"No {' or '.join(missing)} in {report}. It should be the output folder of a finished review.", file=sys.stderr)
        return 1
    review_path = report / "review.json"
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Could not read {review_path}: {exc}", file=sys.stderr)
        return 1
    if args.input is None:
        recorded = _recorded_input_root(review)
        if recorded is None:
            print("This report doesn't say which drawing folder it was made from. Give it with --input <folder>.", file=sys.stderr)
            return 1
        input_root = next((folder for folder in _candidate_folders(recorded) if folder.is_dir()), None)
        if input_root is None:
            print(f"The drawing folder this report was made from, {_candidate_folders(recorded)[0]}, is not there. "
                  "Give it with --input <folder>.", file=sys.stderr)
            return 1
    else:
        input_root = args.input
        if not input_root.is_dir():
            print(f"Input folder not found: {input_root}", file=sys.stderr)
            return 1
    settings = _load_settings(config_path)
    if settings is None:
        return 2
    if args.job is not None:
        settings.cases.job = args.job
    if args.cases_folder is not None:
        settings.cases.folder = args.cases_folder  # empty: the default folder, whatever the config says
    outcome = make_cases(report, input_root, settings)
    for line in outcome.lines:
        print(line)
    return 0 if outcome.made else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    config_path = args.config or (DEFAULT_CONFIG if DEFAULT_CONFIG.is_file() else None)
    if args.config and not args.config.is_file():
        print(f"Config not found: {args.config}", file=sys.stderr)
        return 2
    if args.command == "cases":
        return _cases(args, config_path)
    if not args.input.is_dir():
        print(f"Input folder not found: {args.input}", file=sys.stderr)
        return 2

    settings = _load_settings(config_path)
    if settings is None:
        return 2
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
    if args.cases is not None:
        settings.cases.enabled = args.cases  # the flag decides; without it [cases] enabled in the config does
    if args.job is not None:
        settings.cases.job = args.job
    if args.cases_folder is not None:
        settings.cases.folder = args.cases_folder  # empty: the default folder, whatever the config says
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
    if settings.cases.enabled:
        for line in make_cases(out_dir, args.input, settings).lines:
            print(line)
    return 0
