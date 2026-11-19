"""Makes the Copilot review cases for one run, after the QA tool has written its report and register.

Every location comes from the run's own findings.json and review.json (see packer.py). A problem is
printed as a WARNING line and never raises, so it cannot change the run's exit code.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from drawing_qa.cases.packer import KEY_PATTERN, PackResult, RunFolderExists, _read_json, pack_run
from drawing_qa.config import Settings

log = logging.getLogger(__name__)

NO_JOB_WARNING = "  WARNING: cases not made: no job given, and no drawing number found in the title blocks. Type a Job."
MAX_CROP_PROBLEMS_SHOWN = 10


@dataclass(frozen=True)
class CasesOutcome:
    """What make_cases did: whether the run's cases were made, the lines to print, and the run's cases folder
    (None when they were not made)."""

    made: bool
    lines: list[str]
    cases_dir: Path | None


class _Refused(Exception):
    """The cases cannot be made in the folder given; the message is the one WARNING line to print."""


def make_cases(out_dir: Path, input_dir: Path, settings: Settings) -> CasesOutcome:
    """Make the cases for the run whose output is in out_dir; return what happened and the lines to print.

    input_dir is the drawing package folder the QA tool read. Never raises: the report and
    register are already written, so a failure here only adds a WARNING line.
    """
    try:
        return _make(out_dir, input_dir, settings)
    except Exception as exc:
        log.debug("cases not made", exc_info=True)
        return _not_made(f"  WARNING: cases not made: {type(exc).__name__}: {exc} (run with -v for details)")


def _not_made(*lines: str) -> CasesOutcome:
    return CasesOutcome(made=False, lines=list(lines), cases_dir=None)


def _make(out_dir: Path, input_dir: Path, settings: Settings) -> CasesOutcome:
    """Make the cases with the job given, or else with the job the drawing numbers give (its line comes first)."""
    job = _clean(settings.cases.job)
    found: list[str] = []
    if not job:
        try:
            derived = _job_from_drawings(out_dir)
        except ValueError as exc:
            return _not_made(f"  WARNING: cases not made: {exc}")
        if derived is None:
            return _not_made(NO_JOB_WARNING)
        job = derived
        found = [f"  cases job: {job} (from the drawing numbers; type a Job to use another)"]
    made = _make_for_job(out_dir, input_dir, settings, job)
    return CasesOutcome(made=made.made, lines=found + made.lines, cases_dir=made.cases_dir)


def _make_for_job(out_dir: Path, input_dir: Path, settings: Settings, job: str) -> CasesOutcome:
    if KEY_PATTERN.fullmatch(job) is None:
        return _not_made(f'  WARNING: cases not made: the job "{job}" can only have letters, digits and hyphens, e.g. 900001-Main.')
    try:
        folder = _cases_folder(out_dir, _clean(settings.cases.folder))
    except _Refused as exc:
        return _not_made(str(exc))

    tmp = Path(tempfile.mkdtemp())
    try:
        result = pack_run(out_dir, input_dir, folder, job, tmp)
    except RunFolderExists as exc:
        return _not_made(
            f"  WARNING: cases not made: this run's cases are already in {exc.run_dir}; "
            "delete that folder to make them again"
        )
    except (OSError, ValueError) as exc:
        return _not_made(f"  WARNING: cases not made: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    run_cases = folder / "Runs" / job / result.manifest["run_id"] / "cases"
    return CasesOutcome(made=True, lines=_summary(result, run_cases), cases_dir=run_cases)


def _is_plain_name(out_dir: Path, text: str) -> bool:
    """True when a relative folder text has no drive or root and resolves to a place inside out_dir."""
    path = Path(text)
    return not (path.drive or path.root) and (out_dir / path).resolve().is_relative_to(out_dir.resolve())


def _cases_folder(out_dir: Path, folder_text: str) -> Path:
    """The folder the cases are saved in, made when it may be; raises _Refused with the WARNING line when it may not.

    Empty: the output folder's cases folder. A relative name: a folder inside the output folder, made with its parents.
    An absolute path: used when it is there; made when only it is missing and the folder above it is there. Otherwise
    it is refused and nothing is created.
    """
    if not folder_text:
        folder = out_dir / "cases"
    elif not Path(folder_text).is_absolute():
        if not _is_plain_name(out_dir, folder_text):
            raise _Refused(
                f"  WARNING: cases not made: {folder_text} is not a full path or a plain folder name. "
                "Use Browse, or a name for a folder inside the output folder."
            )
        folder = out_dir / folder_text
    else:
        folder = Path(folder_text)
        above = folder.parent
        if not folder.exists() and not above.is_dir():
            if above.exists():
                raise _Refused(f"  WARNING: cases not made: {above} is not a folder")
            raise _Refused(
                f"  WARNING: cases not made: {folder} can't be made because {above} is not there. "
                "Check the folder name, and that OneDrive is running if it's a synced folder."
            )
    if folder.exists() and not folder.is_dir():
        raise _Refused(f"  WARNING: cases not made: {folder} is not a folder")
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _Refused(f"  WARNING: cases not made: {exc}") from exc
    return folder


def _job_from_drawings(out_dir: Path) -> str | None:
    """The job the drawing numbers in review.json give, or None when no drawing number is usable.

    Duplicate sheets are left out. A number read from a title block is used; a number taken from a file name only
    when no title block gave a usable one. Within that, the most common number on the PLC sheets, else on all the
    sheets; on a tie, the first in sorted order. Each number is cleaned to letters, digits and hyphens first (any other
    character becomes a hyphen, repeated hyphens collapse, the ends go), and one that cleans to nothing does not count.
    Raises ValueError, naming review.json, when it cannot be read.
    """
    review = _read_json(out_dir / "review.json", {"drawings": list})
    sheets = [sheet for sheet in review["drawings"] if isinstance(sheet, dict) and not sheet.get("duplicate_of")]
    job = _job_of([sheet for sheet in sheets if sheet.get("title_block_method") != "FILENAME"])
    if job is None:
        job = _job_of([sheet for sheet in sheets if sheet.get("title_block_method") == "FILENAME"])
    return job


def _job_of(sheets: list[dict]) -> str | None:
    """The most common job among these sheets: on the PLC sheets when any of them has one, else on all of them."""
    plc = _most_common(sheet.get("drawing_no") for sheet in sheets if sheet.get("doc_type") == "PLC")
    return plc if plc is not None else _most_common(sheet.get("drawing_no") for sheet in sheets)


def _most_common(numbers: Iterable[object]) -> str | None:
    """The job that the most drawing numbers clean to, ignoring any that clean to nothing; on a tie, the first sorted."""
    counts = Counter(job for job in map(_clean_number, numbers) if job)
    return max(sorted(counts), key=counts.__getitem__) if counts else None


def _clean_number(number: object) -> str:
    """A drawing number as a job: letters, digits and hyphens only. Other characters become hyphens, repeats collapse, the ends go."""
    if not isinstance(number, str):
        return ""
    return re.sub(r"-{2,}", "-", re.sub(r"[^A-Za-z0-9-]", "-", number)).strip("-")


def _summary(result: PackResult, run_cases: Path) -> list[str]:
    """The lines that describe a packed run: its counts, any crops that could not be made, and the manifest.

    Built from what pack_run returned, with no file read, so nothing that happens to the files after
    publishing can turn a good run into a failure.
    """
    manifest = result.manifest
    cases = manifest["cases"]
    crops = sum(1 for entry in cases for crop in entry["crops"].values() if crop is not None)
    lines = [
        f"  cases: {_count(len(cases), 'case')}, {_count(crops, 'crop')} in {run_cases}",
        f"  case queue: {result.case_queue_path}",
    ]
    problems = result.crop_problems
    if problems:
        lines.append(
            f"  WARNING: {_count(len(problems), 'picture')} for the review cases could not be made; "
            "the run's output lists which, and each case file says why"
        )
        lines.extend(
            f"    {fingerprint} {side}: {problem}"
            for fingerprint, side, problem in problems[:MAX_CROP_PROBLEMS_SHOWN]
        )
        if len(problems) > MAX_CROP_PROBLEMS_SHOWN:
            lines.append(f"    ... and {len(problems) - MAX_CROP_PROBLEMS_SHOWN} more")
    lines.append(f"  wrote {result.manifest_path}")
    return lines


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _clean(value: str) -> str:
    """A setting with the spaces and quotes around it removed, as it may be typed into the config."""
    return value.strip().strip("\"'").strip()
