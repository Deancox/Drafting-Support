import json
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

def _find_root():
    for p in Path(__file__).resolve().parents:
        if (p / "config" / "project.toml").is_file():
            return p
    raise RuntimeError("config/project.toml not found above api.py")


ROOT = _find_root()


def qa(*args):
    """Same as: python -m drawing_qa <args>, run from the Drafting Support folder."""
    return subprocess.run(
        [sys.executable, "-m", "drawing_qa", *map(str, args)],
        cwd=ROOT, capture_output=True, text=True,
    )


def run_qa_project(project_path, output_root=None, extra_args=None, project_id=None):
    PACKAGE = Path(project_path).resolve()
    if not PACKAGE.is_dir():
        raise FileNotFoundError(f"Project folder not found: {PACKAGE}")

    project_id = project_id or f"{PACKAGE.name}-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    OUTPUT = Path(output_root or ROOT / "reports").resolve() / project_id

    proc = qa("run", "--input", PACKAGE,
              *(["--output", OUTPUT] if OUTPUT else []),
              *(extra_args or []))

    findings = OUTPUT / "findings.json"

    if proc.returncode != 0 or not findings.is_file():
        return {
            "status": "failed",
            "project_id": project_id,
            "error": (proc.stderr or proc.stdout)[-2000:],
        }

    json.loads(findings.read_text(encoding="utf-8"))  # confirms valid JSON

    files = {
        "findings_file": findings,
        "register_file": OUTPUT / "QA_Register.xlsx",
        "report_file": OUTPUT / "report.md",
        "review_file": OUTPUT / "review.json",
    }
    missing = [k for k, p in files.items() if not p.is_file()]

    return {
        "status": "complete",
        "project_id": project_id,
        "output_folder": str(OUTPUT),
        **{k: str(p) for k, p in files.items()},
        "missing": missing,
    }

## def609d15e294123a4555953207111c7

# $env:QA_API_KEY = "def609d15e294123a4555953207111c7" .venv\Scripts\python -m uvicorn drawing_qa.server:app --host 127.0.0.1 --port 8000