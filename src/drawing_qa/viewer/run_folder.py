"""Finds a run's cases and register from the output folder the QA tool wrote.

A run folder does not record its output folder, and the cases folder can be elsewhere (the config's
[cases] folder), so the viewer starts from the output folder: findings.json gives the run id, the config
gives the cases folder, and the run is <cases folder>/Runs/<job>/<run id>. Nothing is created here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from drawing_qa.cases.make import _clean
from drawing_qa.config import Settings
from drawing_qa.report.register import REGISTER_NAME


class RunNotFound(Exception):
    """The output folder has no run the viewer can open; the message is the line to show."""


@dataclass(frozen=True)
class RunLocation:
    output_dir: Path
    run_id: str
    run_dir: Path | None  # None when the run's cases were not found
    register: Path
    input_root: Path | None  # the drawing package folder, for the wider view; None when unknown or gone


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunNotFound(f"Can't read {path.name} in {path.parent}: {exc}") from exc
    if not isinstance(data, dict):
        raise RunNotFound(f"{path.name} in {path.parent} is not a QA tool output file")
    return data


def _cases_folder(output_dir: Path, config_path: Path | None) -> Path:
    text = _clean(Settings.load(config_path).cases.folder) if config_path else ""
    if not text:
        return output_dir / "cases"
    folder = Path(text)
    return folder if folder.is_absolute() else output_dir / folder


def _input_root(output_dir: Path) -> Path | None:
    try:
        root = json.loads((output_dir / "review.json").read_text(encoding="utf-8"))["run"]["input_root"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    path = Path(root) if isinstance(root, str) else None
    return path if path is not None and path.is_dir() else None


def find_run(output_dir: Path, config_path: Path | None) -> RunLocation:
    """The run in output_dir: its id from findings.json, its cases from the config's cases folder."""
    output_dir = Path(output_dir)
    run_id = _read(output_dir / "findings.json").get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise RunNotFound(f"findings.json in {output_dir} has no run id")
    runs = _cases_folder(output_dir, config_path) / "Runs"
    found = sorted(p for p in runs.glob(f"*/{run_id}") if (p / "cases").is_dir()) if runs.is_dir() else []
    return RunLocation(
        output_dir=output_dir,
        run_id=run_id,
        run_dir=found[0] if found else None,
        register=output_dir / REGISTER_NAME,
        input_root=_input_root(output_dir),
    )


def with_run_dir(loc: RunLocation, run_dir: Path) -> RunLocation:
    """The same run with a run folder the checker picked by hand."""
    run_dir = Path(run_dir)
    if not (run_dir / "cases").is_dir():
        raise RunNotFound(f"{run_dir} has no cases folder. Pick the folder named after the run, e.g. {loc.run_id}.")
    return replace(loc, run_dir=run_dir)
