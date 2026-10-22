"""Write all run outputs to one folder."""

from __future__ import annotations

from pathlib import Path

from drawing_qa.model.run import RunResult
from drawing_qa.report.json_writer import write_findings, write_review
from drawing_qa.report.markdown import write_report
from drawing_qa.report.register import write_register
from drawing_qa.report.summary import summarise


def write_outputs(result: RunResult, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = summarise(result)
    return [
        write_review(result, summary, out_dir),
        write_findings(result, summary, out_dir),
        write_report(result, summary, out_dir),
        write_register(result, out_dir),
    ]
