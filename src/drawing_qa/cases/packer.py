"""Writes one QA run's cases, crops, none.png and CaseQueue.xlsx, then the manifest that announces them.

The manifest is written last, so the QA flow starts only when every file it lists
is in place. Paths in the manifest are relative to the synced folder.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from drawing_qa.cases.build import ReviewIndex, build_case
from drawing_qa.cases.case_queue import CASE_QUEUE_NAME, queue_row, write_case_queue
from drawing_qa.cases.crops import CropError, render_crop

# Package keys and job names become folder and file names, so only these characters are allowed.
KEY_PATTERN = re.compile(r"[A-Za-z0-9-]+")

# The keys the packer reads from the QA tool's two JSON files, and the type each must have.
FINDINGS_KEYS = {"run_id": str, "findings": list}
REVIEW_KEYS = {"run": dict, "drawings": list, "claims": list}
_TYPE_NAMES = {str: "text", list: "a list", dict: "an object"}


class RunFolderExists(FileExistsError):
    """A run's folder is already in synced/Runs, so that run was packed before."""

    def __init__(self, run_dir: Path, run_id: str):
        super().__init__(f"{run_dir} already exists; delete that folder to pack run {run_id} again")
        self.run_dir = run_dir


@dataclass(frozen=True)
class PackResult:
    """What pack_run wrote: the manifest's path, the manifest itself, each crop that could not be made as
    (fingerprint, side, problem), and the path of the run's CaseQueue.xlsx."""

    manifest_path: Path
    manifest: dict
    crop_problems: list[tuple[str, str, str]]
    case_queue_path: Path


def pack_run(output_dir: Path, input_dir: Path, synced: Path, package: str, tmp_dir: Path) -> PackResult:
    """Pack one QA tool run into synced/Runs and publish its manifest to Inbox.

    output_dir holds findings.json and review.json from the tool. input_dir is the
    package's drawing folder; each crop is rendered from the PDF the tool named for
    that side. Returns a PackResult. Raises ValueError when the package or the run id
    has characters that cannot go in a folder or file name, when one of the tool's
    JSON files cannot be read or lacks what is needed (the message names the file), or
    when review.json is from a different run than findings.json (its locations would be
    stale); and FileExistsError when that run's folder is already in synced/Runs.
    Nothing is written before these checks pass. If anything fails after the run folder
    is made, that folder is removed and the error is raised again.
    """
    _check_key(package, "package")
    findings_doc = _read_json(output_dir / "findings.json", FINDINGS_KEYS)
    review = _read_json(output_dir / "review.json", REVIEW_KEYS)
    run_id = _check_key(findings_doc.get("run_id"), "run_id")
    run_info = review.get("run")
    review_run = run_info.get("run_id") if isinstance(run_info, dict) else None
    if review_run != run_id:
        raise ValueError(
            f"review.json is from run {review_run} but findings.json is from run {run_id}; "
            "run the QA tool again"
        )
    findings = findings_doc["findings"]
    index = ReviewIndex.from_review(review, findings)

    run_rel = f"Runs/{package}/{run_id}"
    run_dir = synced / "Runs" / package / run_id
    # A run folder that is already there was published, and the flow may be reading
    # it, so it is refused rather than overwritten. Nothing is deleted here.
    if run_dir.exists():
        raise RunFolderExists(run_dir, run_id)
    # The run is written to <run_id>.partial and renamed to <run_id> only just before its manifest is
    # published, so a folder without ".partial" is always a complete run. A ".partial" folder left by a
    # stopped run was never published, so it is removed before this run starts.
    partial_dir = synced / "Runs" / package / f"{run_id}.partial"
    if partial_dir.exists():
        shutil.rmtree(partial_dir)
    cases_dir = partial_dir / "cases"
    crop_problems: list[tuple[str, str, str]] = []
    rows: list[dict] = []
    renamed = False
    try:
        cases_dir.mkdir(parents=True, exist_ok=True)

        entries = []
        for finding in findings:
            case = build_case(finding, index, package, run_id)
            crops = {"A": None, "B": None}
            for role, side in case["sides"].items():
                _render_side(side, input_dir, cases_dir)
                if side["crop"] is not None:
                    crops[role] = f"{run_rel}/cases/{side['crop']}"
                if side.get("crop_problem"):
                    crop_problems.append((case["fingerprint"], role, side["crop_problem"]))
            fingerprint = case["fingerprint"]
            case_path = cases_dir / f"{fingerprint}.json"
            _write_json(case_path, case)
            rows.append(queue_row(case, case_path.relative_to(partial_dir).as_posix()))
            entries.append(
                {
                    "ref": case["ref"],
                    "fingerprint": fingerprint,
                    "case": f"{run_rel}/cases/{fingerprint}.json",
                    "crops": crops,
                    "message_hash": case["message_hash"],
                }
            )

        _write_none_png(partial_dir / "none.png")
        write_case_queue(partial_dir / CASE_QUEUE_NAME, partial_dir, package, run_id, findings, rows)

        manifest = {
            "package": package,
            "run_id": run_id,
            "tool_version": index.tool_version,
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "finding_count": len(entries),
            "none_png": f"{run_rel}/none.png",
            "case_queue": f"{run_rel}/{CASE_QUEUE_NAME}",
            "cases": entries,
        }
        partial_dir.rename(run_dir)  # the manifest's paths point here, so the run must be in place first
        renamed = True
        manifest_path = _publish_manifest(manifest, synced, tmp_dir)
    except BaseException:
        # A run that failed part way is not left in synced/Runs. Its manifest was not published.
        shutil.rmtree(partial_dir, ignore_errors=True)
        if renamed:
            shutil.rmtree(run_dir, ignore_errors=True)
        raise
    return PackResult(manifest_path, manifest, crop_problems, run_dir / CASE_QUEUE_NAME)


def _render_side(side: dict, input_dir: Path, cases_dir: Path) -> None:
    """Render one side's crop from the location the tool gave, or record why it can't.

    On success side["crop"] stays the file name build_case gave. Otherwise crop
    becomes None and crop_problem says why. The PDF is never searched for the text,
    and a PDF outside the package folder is never opened.
    """
    if side["source_pdf"] is None:
        side["crop"] = None
        side["crop_problem"] = (
            f"{side['drawing_id']} is not in review.json, so there is no PDF to crop"
        )
        return
    if side["bbox"] is None:
        side["crop"] = None
        side["crop_problem"] = "no box: the tool gave no bbox for this evidence"
        return
    pdf = input_dir / side["source_pdf"]
    # Crops go to the cloud, so a crop never comes from a file outside the package folder.
    if not pdf.resolve().is_relative_to(input_dir.resolve()):
        side["crop"] = None
        side["crop_problem"] = (
            f"{side['source_pdf']} is outside the package folder, so no crop is made from it"
        )
        return
    try:
        render_crop(
            pdf,
            side["page_index"],
            side["bbox"],
            cases_dir / side["crop"],
        )
    except CropError as exc:
        side["crop"] = None
        side["crop_problem"] = str(exc)


def _publish_manifest(manifest: dict, synced: Path, tmp_dir: Path) -> Path:
    """Write the manifest to tmp_dir, then move it into Inbox in one step."""
    name = f"{manifest['package']}__{manifest['run_id']}.manifest.json"
    staged = tmp_dir / name
    _write_json(staged, manifest)
    inbox = synced / "Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    dest = inbox / name
    try:
        os.replace(staged, dest)
    except OSError:  # tmp_dir and the synced folder are on different drives
        # Copy beside the destination under a .part name, then rename it into place, so
        # the flow never sees a half-written .manifest.json. The flow ignores .part files.
        part = inbox / f"{name}.part"
        shutil.copyfile(staged, part)
        os.replace(part, dest)
        with contextlib.suppress(OSError):  # the manifest is published by now, so a copy left in tmp is no failure
            staged.unlink()
    return dest


def _write_none_png(path: Path) -> None:
    """A 16 x 16 white image, sent in place of a crop that is missing."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 16, 16), False)
    pix.clear_with(255)
    pix.save(str(path))


def _check_key(value: object, label: str) -> str:
    if not isinstance(value, str) or KEY_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} {value!r} may use only letters, digits and hyphens")
    return value


def _read_json(path: Path, keys: dict[str, type]) -> dict:
    """Read one of the QA tool's JSON files and check the keys the packer needs, each with its type.

    Raises ValueError naming the file when it cannot be read, is not a JSON object or lacks a key.
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"could not read {path}: {exc.strerror or exc}") from exc
    except ValueError as exc:  # bad JSON, or text that is not UTF-8
        raise ValueError(f"could not read {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"could not read {path}: the top level is not a JSON object")
    for key, kind in keys.items():
        if key not in doc:
            raise ValueError(f'could not read {path}: missing "{key}"')
        if not isinstance(doc[key], kind):
            raise ValueError(f'could not read {path}: "{key}" has the wrong type, expected {_TYPE_NAMES[kind]}')
    return doc


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
