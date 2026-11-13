import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from drawing_qa.api import ROOT, run_qa_project

API_KEY = os.environ.get("QA_API_KEY", "")
INPUT_ROOT = Path(os.environ.get("QA_INPUT_ROOT", ROOT / "input")).resolve()

RUNS = {}
LOCK = threading.Lock()  # one run at a time

app = FastAPI(title="Drawing QA API")


class RunRequest(BaseModel):
    project_path: str
    extra_args: List[str] = []


def check_key(key):
    if not API_KEY or key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def work(run_id, project, extra_args):
    with LOCK:
        RUNS[run_id]["status"] = "running"
        try:
            RUNS[run_id].update(
                run_qa_project(project, project_id=run_id, extra_args=extra_args)
            )
        except Exception as e:
            RUNS[run_id].update(status="failed", error=str(e))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/runs", status_code=202)
def start_run(req: RunRequest, bg: BackgroundTasks,
              x_api_key: str = Header(default="")):
    check_key(x_api_key)
    project = (INPUT_ROOT / req.project_path).resolve()
    if not project.is_relative_to(INPUT_ROOT):
        raise HTTPException(400, "Project must be inside the input folder")
    if not project.is_dir():
        raise HTTPException(404, "Project folder not found")

    run_id = f"{project.name}-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    RUNS[run_id] = {"status": "queued", "project_id": run_id}
    bg.add_task(work, run_id, project, req.extra_args)
    return {"run_id": run_id, "status": "queued"}


@app.get("/runs/{run_id}")
def get_run(run_id: str, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    if run_id not in RUNS:
        raise HTTPException(404, "Run not found")
    return RUNS[run_id]


@app.get("/runs/{run_id}/findings")
def get_findings(run_id: str, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["status"] != "complete":
        raise HTTPException(409, f"Run is {run['status']}")
    return FileResponse(run["findings_file"], media_type="application/json")