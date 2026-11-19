"""The Drawing QA window: choose the input and output folders and the options, then survey or run the review.

    python -m drawing_qa.gui        (or double-click "Drawing QA.cmd")

It builds the same command as the command line (python -m drawing_qa run --input ... --output ... --ocr
--exclude ...) and runs it in the background, showing its progress in the window. Only standard library
(tkinter), so nothing extra to install. The last choices are remembered in ~/.drawing_qa_ui.json.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

PROJECT = Path(__file__).resolve().parents[2]  # the folder with config/, input/, reports/
DEFAULT_CONFIG = PROJECT / "config" / "project.toml"
PREFS = Path.home() / ".drawing_qa_ui.json"
TYPES = [  # (command line code, label, input folder)
    ("plc", "PLC schematics", "PLC"),
    ("cbd", "Cable block diagrams", "CableBlocks"),
    ("term", "Termination drawings", "Terminations"),
    ("equip", "Equipment list", "EquipmentList"),
]
OUTPUTS = [("QA register", "QA_Register.xlsx"), ("system map", "system_map.html"), ("report", "report.md")]
JOB_PATTERN = re.compile(r"[A-Za-z0-9-]+")  # the job name rule of the command line (drawing_qa.cases.packer)
CASES_LINE = re.compile(r"\s*cases: (\d+) cases?, (\d+) crops? in (.+?)\s*$")  # "  cases: 7 cases, 10 crops in <folder>"
CASE_QUEUE_LINE = re.compile(r"\s*case queue: (.+?)\s*$")  # "  case queue: <path of CaseQueue.xlsx>"
CASES_JOB_LINE = re.compile(r"\s*cases job: ([A-Za-z0-9-]+) \(from the drawing numbers; type a Job to use another\)\s*$")
# "  cases job: 900001 (from the drawing numbers; type a Job to use another)": the job was taken from the drawings
CASE_QUEUE_SENTENCE = "CaseQueue.xlsx lists every case (Ref, fingerprint and case file) for the Copilot workflow."


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - opens with its usual program
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def parse_cases_line(line: str) -> tuple[int, int, str] | None:
    """The cases, pictures and cases folder from a line like "  cases: 7 cases, 10 crops in <folder>", or None."""
    match = CASES_LINE.match(line)
    return (int(match[1]), int(match[2]), match[3]) if match else None


def parse_case_queue_line(line: str) -> Path | None:
    """The path of CaseQueue.xlsx from a line like "  case queue: <path of CaseQueue.xlsx>", or None."""
    match = CASE_QUEUE_LINE.match(line)
    return Path(match[1]) if match else None


def parse_cases_job_line(line: str) -> str | None:
    """The job from a line like "  cases job: 900001 (from the drawing numbers; type a Job to use another)", or None."""
    match = CASES_JOB_LINE.match(line)
    return match[1] if match else None


def _count(n: int, word: str) -> str:
    """'1 case', '2 cases': the count with its word in the right form."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _config_cases(path: str) -> dict:
    """The tick, job and cases folder a run would use from this config file, as drawing_qa.config reads them.
    Empty when there is no config file or it cannot be read."""
    empty = {"enabled": False, "job": "", "folder": ""}
    if not path.strip():
        return empty
    try:
        from drawing_qa.config import Settings

        cases = Settings.load(Path(path.strip())).cases
    except Exception:
        return empty
    return {"enabled": cases.enabled, "job": cases.job, "folder": cases.folder}


def _drawing_folder_of(review: object) -> tuple[str | None, Path | None]:
    """The drawing folder review.json names (None when it names none) and that folder when it is there.
    A relative name is looked for from the project folder, as the command line looks for it from the current folder."""
    run = review.get("run") if isinstance(review, dict) else None
    root = run.get("input_root") if isinstance(run, dict) else None
    if not isinstance(root, str) or not root.strip():
        return None, None
    path = Path(root.strip())
    if not path.is_absolute():
        path = PROJECT / path
    return root.strip(), (path if path.is_dir() else None)


def _shown_line(captured: list[str]) -> str:
    """The line a failed run shows: its last message line, or the last WARNING line when the last line is not a message."""
    lines = [ln.strip() for ln in captured if ln.strip()]
    if not lines:
        return "no message"
    if lines[-1].startswith("WARNING") or "error" in lines[-1].lower():
        return lines[-1]
    warnings = [ln for ln in lines if ln.startswith("WARNING")]
    return warnings[-1] if warnings else lines[-1]


class App(ttk.Frame):
    def __init__(self, root: tk.Tk) -> None:
        super().__init__(root, padding=14)
        self.root = root
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue = queue.Queue()
        self.captured: list[str] = []
        self.output_auto = True  # the output follows the input until the user picks one

        prefs = self._load_prefs()
        self.input = tk.StringVar(value=prefs.get("input", str(PROJECT / "input" / "Main")))
        self.output = tk.StringVar(value=prefs.get("output", ""))
        self.config_path = tk.StringVar(value=prefs.get("config", str(DEFAULT_CONFIG) if DEFAULT_CONFIG.is_file() else ""))
        self.include = {code: tk.BooleanVar(value=code not in prefs.get("excluded", [])) for code, _, _ in TYPES}
        self.ocr = tk.BooleanVar(value=prefs.get("ocr", False))
        self.verbose = tk.BooleanVar(value=prefs.get("verbose", False))
        config = _config_cases(self.config_path.get())  # what a run would use from this config file
        self.cases_on = tk.BooleanVar(value=bool(prefs["cases"]) if "cases" in prefs else config["enabled"])
        self.job = tk.StringVar(value=prefs.get("job") or config["job"])
        self.cases_folder = tk.StringVar(value=prefs.get("cases_folder") or config["folder"])
        self.status = tk.StringVar(value="Choose the package folder, then Run review.")
        if not self.output.get():
            self._follow_input()
        else:
            self.output_auto = self.output.get() == self._default_output()
        self.input.trace_add("write", lambda *_: self._follow_input())

        self._build()
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self._update_found()
        self.after(100, self._drain)

    # --- layout ----------------------------------------------------------------------------------

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        folders = ttk.LabelFrame(self, text="Folders", padding=10)
        folders.grid(row=0, column=0, sticky="ew")
        folders.columnconfigure(1, weight=1)
        self._path_row(folders, 0, "Input (the package)", self.input, self._browse_input,
                       "The folder holding PLC, CableBlocks, Terminations and EquipmentList (any may be missing).")
        self._path_row(folders, 2, "Output (the results)", self.output, self._browse_output,
                       "QA_Register.xlsx, report.md and system_map.html are saved here. Default: reports\\<input folder name>.")
        self._path_row(folders, 4, "Project settings", self.config_path, self._browse_config,
                       "The project's config file (FFIs, panels, prefixes). Leave as it is unless told otherwise.")

        options = ttk.Frame(self)
        options.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        options.columnconfigure(0, weight=1)
        options.columnconfigure(1, weight=1)
        check = ttk.LabelFrame(options, text="Drawings to check (untick to leave a type out)", padding=10)
        check.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.found: dict[str, ttk.Label] = {}
        for i, (code, label, _) in enumerate(TYPES):
            ttk.Checkbutton(check, text=label, variable=self.include[code]).grid(row=i, column=0, sticky="w")
            self.found[code] = ttk.Label(check, foreground="#6b6b66")
            self.found[code].grid(row=i, column=1, sticky="w", padx=(10, 0))
        ttk.Label(check, text="Left-out drawings are still listed, but nothing on them is checked.",
                  foreground="#6b6b66", wraplength=330).grid(row=len(TYPES), column=0, columnspan=2, sticky="w", pady=(6, 0))

        extra = ttk.LabelFrame(options, text="Extras", padding=10)
        extra.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        ttk.Checkbutton(extra, text="Read sheets with no SHX text (OCR)", variable=self.ocr).grid(row=0, column=0, sticky="w")
        ttk.Label(extra, text="Decodes the letter strokes of sheets plotted without SHX text. Slower; "
                  "Survey shows whether any sheet needs it.", foreground="#6b6b66", wraplength=330).grid(row=1, column=0, sticky="w", padx=(22, 0))
        ttk.Checkbutton(extra, text="Detailed log", variable=self.verbose).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Label(extra, text="More messages below, for tracking down a problem.", foreground="#6b6b66",
                  wraplength=330).grid(row=3, column=0, sticky="w", padx=(22, 0))

        cases = ttk.LabelFrame(self, text="Copilot review cases", padding=(10, 6))
        cases.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        top = ttk.Frame(cases)
        top.pack(fill="x")
        ttk.Checkbutton(top, text="Make review cases for Copilot", variable=self.cases_on).pack(side="left")
        ttk.Label(top, text="Job").pack(side="left", padx=(14, 6))
        ttk.Entry(top, textvariable=self.job, width=16).pack(side="left")
        ttk.Label(top, text="Cases folder").pack(side="left", padx=(14, 6))
        ttk.Button(top, text="Browse...", command=self._browse_cases).pack(side="right", padx=(6, 0))
        ttk.Entry(top, textvariable=self.cases_folder).pack(side="left", fill="x", expand=True)
        bottom = ttk.Frame(cases)
        bottom.pack(fill="x", pady=(6, 0))
        self.cases_btn = ttk.Button(bottom, text="Make cases from a finished report...", command=self.make_cases_from_report)
        self.cases_btn.pack(side="right")
        ttk.Label(bottom, text="Tick to make cases after each run. Job: empty uses the title-block drawing number. "
                  "Cases folder: empty or a name = inside the output folder, or Browse. Button: cases from a finished review.",
                  foreground="#6b6b66", wraplength=520).pack(side="left", fill="x")

        buttons = ttk.Frame(self)
        buttons.grid(row=3, column=0, sticky="ew", pady=(8, 4))
        self.run_btn = ttk.Button(buttons, text="Run review", command=self.run_review)
        self.run_btn.pack(side="left")
        self.survey_btn = ttk.Button(buttons, text="Survey (quick check of the PDFs)", command=self.run_survey)
        self.survey_btn.pack(side="left", padx=6)
        self.stop_btn = ttk.Button(buttons, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left")
        self.open_btns = []
        ttk.Button(buttons, text="Open output folder", command=lambda: self._open("")).pack(side="right")
        for label, name in reversed(OUTPUTS):
            b = ttk.Button(buttons, text=f"Open {label}", command=lambda n=name: self._open(n))
            b.pack(side="right", padx=(0, 6))
            self.open_btns.append(b)

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.grid(row=4, column=0, sticky="ew")
        self.progress.grid_remove()  # shown while a run is going
        ttk.Label(self, textvariable=self.status).grid(row=5, column=0, sticky="w", pady=(2, 2))
        log_frame = ttk.Frame(self)
        log_frame.grid(row=6, column=0, sticky="nsew")
        self.rowconfigure(6, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=10, wrap="word", font=("Consolas", 9), state="disabled",
                           background="#fbfaf7", relief="solid", borderwidth=1)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log["yscrollcommand"] = scroll.set

    def _path_row(self, parent, row, label, var, browse, hint) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew")
        ttk.Button(parent, text="Browse...", command=browse).grid(row=row, column=2, padx=(6, 0))
        ttk.Label(parent, text=hint, foreground="#6b6b66").grid(row=row + 1, column=1, columnspan=2, sticky="w", pady=(0, 6))

    # --- folders --------------------------------------------------------------------------------------

    def _default_output(self) -> str:
        name = Path(self.input.get()).name if self.input.get().strip() else "Main"
        return str(PROJECT / "reports" / name)

    def _follow_input(self) -> None:
        if self.output_auto:
            self.output.set(self._default_output())
        if hasattr(self, "found"):
            self._update_found()

    def _update_found(self) -> None:
        root = Path(self.input.get())
        for code, _, folder in TYPES:
            n = len(list((root / folder).rglob("*.pdf"))) if (root / folder).is_dir() else 0
            self.found[code].config(text=f"{n} PDF(s)" if n else "none in the input")

    def _browse_input(self) -> None:
        start = self.input.get() if Path(self.input.get()).is_dir() else str(PROJECT / "input")
        chosen = filedialog.askdirectory(title="Choose the package folder", initialdir=start, mustexist=True)
        if chosen:
            self.input.set(str(Path(chosen)))

    def _browse_output(self) -> None:
        start = self.output.get() if Path(self.output.get()).is_dir() else str(PROJECT / "reports")
        chosen = filedialog.askdirectory(title="Choose where to save the results", initialdir=start)
        if chosen:
            self.output_auto = False
            self.output.set(str(Path(chosen)))

    def _browse_config(self) -> None:
        start = Path(self.config_path.get()).parent if self.config_path.get() else PROJECT / "config"
        chosen = filedialog.askopenfilename(title="Choose the project settings file", initialdir=str(start),
                                            filetypes=[("Settings (TOML)", "*.toml"), ("All files", "*.*")])
        if chosen:
            self.config_path.set(str(Path(chosen)))

    def _browse_cases(self) -> None:
        folder = self.cases_folder.get().strip()
        start = folder if folder and Path(folder).is_dir() else str(PROJECT / "reports")
        chosen = filedialog.askdirectory(title="Choose the cases folder", initialdir=start)
        if chosen:
            self.cases_folder.set(str(Path(chosen)))

    def _open(self, name: str) -> None:
        path = Path(self.output.get()) / name if name else Path(self.output.get())
        if not path.exists():
            messagebox.showinfo("Not there yet", f"{path} does not exist yet. Run the review first.", parent=self.root)
            return
        open_path(path)

    # --- running ----------------------------------------------------------------------------------------

    def _check(self, survey: bool) -> bool:
        root = Path(self.input.get().strip())
        if not root.is_dir():
            messagebox.showerror("Input folder", "Choose the package folder first (Browse...).", parent=self.root)
            return False
        if not any(root.rglob("*.pdf")):
            messagebox.showerror("Input folder", f"There are no PDFs in {root}.", parent=self.root)
            return False
        if self.config_path.get().strip() and not Path(self.config_path.get().strip()).is_file():
            messagebox.showerror("Project settings", f"Settings file not found:\n{self.config_path.get()}", parent=self.root)
            return False
        if not survey and not any(v.get() for v in self.include.values()):
            messagebox.showerror("Drawings to check", "Tick at least one type of drawing to check.", parent=self.root)
            return False
        if not survey and not self.output.get().strip():
            messagebox.showerror("Output folder", "Choose where to save the results.", parent=self.root)
            return False
        if not survey and self.cases_on.get() and not self._job_ok():
            return False
        return True

    def _job_ok(self) -> bool:
        """An empty Job is fine: the command sends --job= and the drawing numbers give the job. A typed one must be letters, digits and hyphens."""
        job = self.job.get().strip()
        if job and JOB_PATTERN.fullmatch(job) is None:
            messagebox.showerror("Job name", "Type the job name: letters, digits and hyphens only, e.g. 900001-Main.", parent=self.root)
            return False
        return True

    def command(self, survey: bool = False) -> list[str]:
        cmd = [sys.executable, "-m", "drawing_qa", "survey" if survey else "run", "--input", self.input.get().strip()]
        if self.config_path.get().strip():
            cmd += ["--config", self.config_path.get().strip()]
        if not survey:
            cmd += ["--output", self.output.get().strip()]
            for code, _, _ in TYPES:
                cmd += ["--include" if self.include[code].get() else "--exclude", code]
            if self.ocr.get():
                cmd.append("--ocr")
            cmd.append("--cases" if self.cases_on.get() else "--no-cases")
            if self.cases_on.get():
                cmd += [f"--job={self.job.get().strip()}", f"--cases-folder={self.cases_folder.get().strip()}"]
        if self.verbose.get():
            cmd.append("--verbose")
        return cmd

    def run_review(self) -> None:
        if self._check(survey=False):
            self._start(self.command(), "Reviewing ... (this can take a few minutes)", "review")

    def run_survey(self) -> None:
        if self._check(survey=True):
            self._start(self.command(survey=True), "Surveying the PDFs ...", "survey")

    def make_cases_from_report(self) -> None:
        out = self.output.get().strip()
        start = out if out and Path(out).is_dir() else str(PROJECT / "reports")
        chosen = filedialog.askdirectory(title="Choose the output folder of a finished review", initialdir=start, mustexist=True)
        if not chosen:
            return
        report = Path(chosen)
        if not (report / "findings.json").is_file() or not (report / "review.json").is_file():
            messagebox.showerror("Review folder", "That folder is not a finished review: it needs findings.json and review.json. "
                                 "Choose the output folder of a finished review.", parent=self.root)
            return
        try:
            review = json.loads((report / "review.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            messagebox.showerror("Review folder", f"Could not read {report / 'review.json'}. "
                                 "Choose the output folder of a finished review.", parent=self.root)
            return
        if not self._job_ok():
            return
        recorded, existing = _drawing_folder_of(review)
        extra: list[str] = []
        if existing is None:
            package = self.input.get().strip()
            if not package or not Path(package).is_dir():
                messagebox.showerror("Drawing folder", "The drawing folder this report was made from is not there, and the "
                                     "Input folder is not a folder either. Choose the package folder in Input first, "
                                     "then make the cases again.", parent=self.root)
                return
            if recorded is None:
                question = f"This report doesn't say which drawing folder it was made from. Use the Input folder instead?\n\n{package}"
            else:
                question = f"This report was made from {recorded}, which is not there now. Use the Input folder instead?\n\n{package}"
            if not messagebox.askyesno("Drawing folder", question, parent=self.root):
                return
            extra = [f"--input={package}"]
        cmd = [sys.executable, "-m", "drawing_qa", "cases", "--report", str(report), *extra,
               f"--job={self.job.get().strip()}", f"--cases-folder={self.cases_folder.get().strip()}"]
        if self.config_path.get().strip():
            cmd += ["--config", self.config_path.get().strip()]
        if self.verbose.get():
            cmd.append("--verbose")
        self._start(cmd, "Making the review cases ...", "cases")

    def _start(self, cmd: list[str], status: str, kind: str) -> None:
        self._save_prefs()
        self.kind = kind
        self.captured = []
        self._write("> " + " ".join(f'"{c}"' if " " in c else c for c in cmd[1:]) + "\n\n", clear=True)
        self.status.set(status)
        for b in (self.run_btn, self.survey_btn, self.cases_btn):
            b.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress.grid()
        self.progress.start(12)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(cmd, cwd=PROJECT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace", creationflags=flags, env=env)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            self.lines.put(line)
        self.lines.put(("done", proc.wait()))

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.status.set("Stopping ...")

    def _drain(self) -> None:
        try:
            while True:
                item = self.lines.get_nowait()
                if isinstance(item, tuple):
                    self._finish_safely(item[1])
                else:
                    self.captured.append(item)
                    self._write(item)
        except queue.Empty:
            pass
        finally:
            self.after(100, self._drain)  # keeps reading output even when a line or the result fails

    def _idle(self) -> None:
        """Stop the progress bar and put the buttons back as they are between runs."""
        self.progress.stop()
        self.progress.grid_remove()
        for b in (self.run_btn, self.survey_btn, self.cases_btn):
            b.config(state="normal")
        self.stop_btn.config(state="disabled")

    def _finish_safely(self, code: int) -> None:
        """Show the run's result. If that fails, the buttons come back, the failure is shown, and the window goes on."""
        try:
            self._finished(code)
        except Exception as exc:
            self._idle()
            self.status.set("The run finished, but its result could not be shown.")
            messagebox.showerror("Drawing QA: error", f"The result of the run could not be shown:\n\n{exc}", parent=self.root)

    def _finished(self, code: int) -> None:
        self._idle()
        text = "".join(self.captured)
        if code != 0:
            stopped = code in (1, -15, 15) and "Traceback" not in text and self.status.get() == "Stopping ..."
            self.status.set("Stopped." if stopped else "The run stopped with an error: see the messages below.")
            if not stopped:
                last = _shown_line(self.captured)
                if self.kind == "cases":
                    reason = re.sub(r"^cases not made:\s*", "", re.sub(r"^WARNING:\s*", "", last))
                    text = f"The review cases were not made:\n\n{reason}"
                else:
                    text = f"The {self.kind} stopped with an error:\n\n{last}"
                job = self._cases_job()
                if job:
                    text += f"\n\nJob {job}, from the drawing numbers."
                messagebox.showerror("Drawing QA: error", text, parent=self.root)
            return
        if self.kind == "survey":
            self.status.set("Survey finished: see below.")
            return
        if self.kind == "cases":
            self._cases_finished()
            return
        summary = self._summary()
        cases_line = self._cases_line()
        self.status.set(f"Finished. {summary}  Saved in {self.output.get()}")
        message = f"The review is finished.\n\n{summary}\n\nSaved in:\n{self.output.get()}"
        if cases_line:
            message += f"\n\nReview cases: {_count(cases_line[0], 'case')} made, with {_count(cases_line[1], 'picture')}."
            message += f"\n\n{CASE_QUEUE_SENTENCE}"
        job = self._cases_job()
        if job:
            message += f"\n\nJob {job}, from the drawing numbers."
        message += "".join(f"\n\nNote: {n}" for n in self._notes()[:4])
        if messagebox.askyesno("Drawing QA: finished", message + "\n\nOpen the QA register now?", parent=self.root):
            self._open("QA_Register.xlsx")

    def _notes(self) -> list[str]:
        """The WARNING lines of the output, as short notes for the finish popup."""
        return [ln.split("WARNING", 1)[1].replace("drawing_qa.report.register:", "").lstrip(" :").strip()
                for ln in self.captured if "WARNING" in ln]

    def _cases_line(self) -> tuple[int, int, str] | None:
        """The cases, pictures and cases folder from the output's cases line, when the cases were made."""
        return next((parsed for parsed in map(parse_cases_line, self.captured) if parsed), None)

    def _cases_job(self) -> str | None:
        """The job from the output's cases job line, when the job was taken from the drawing numbers."""
        return next((job for job in map(parse_cases_job_line, self.captured) if job), None)

    def _case_queue_folder(self) -> Path | None:
        """The folder holding CaseQueue.xlsx, from the case queue line of the output, when there is one."""
        return next((path.parent for path in map(parse_case_queue_line, self.captured) if path is not None), None)

    def _open_folder(self, folder: Path) -> None:
        """Open a folder in the file manager. A folder that is not there, or one the system cannot open, is said in a message."""
        if not folder.is_dir():
            messagebox.showinfo("Not there yet", f"{folder} is not there, so it cannot be opened. Check the folder name, "
                                "and that OneDrive is running if it is a synced folder.", parent=self.root)
            return
        try:
            open_path(folder)
        except OSError as exc:
            messagebox.showerror("Drawing QA", f"Could not open {folder}:\n\n{exc}", parent=self.root)

    def _cases_finished(self) -> None:
        found = self._cases_line()
        if found is None:
            self.status.set("Review cases finished: see below.")
            return
        cases, pictures, cases_folder = found
        # The folder the status and the popup name, and the one the popup opens: the run folder that holds
        # CaseQueue.xlsx when the output names it, else the cases folder.
        folder = self._case_queue_folder() or Path(cases_folder)
        cases_text = f"{_count(cases, 'case')}, {_count(pictures, 'picture')}"
        self.status.set(f"Review cases made: {cases_text}, in {folder}")
        job = self._cases_job()
        job_text = f"\n\nJob {job}, from the drawing numbers." if job else ""
        message = f"The review cases are made: {cases_text}.{job_text}\n\n{CASE_QUEUE_SENTENCE}\n\nSaved in:\n{folder}"
        message += "".join(f"\n\nNote: {n}" for n in self._notes()[:4])
        if messagebox.askyesno("Drawing QA: cases made", message + "\n\nOpen the cases folder now?", parent=self.root):
            self._open_folder(folder)

    def _summary(self) -> str:
        try:
            data = json.loads((Path(self.output.get()) / "findings.json").read_text(encoding="utf-8"))
            findings = data["findings"] if isinstance(data, dict) else data
            counts: dict[str, int] = {}
            for f in findings:
                counts[f.get("severity", "?")] = counts.get(f.get("severity", "?"), 0) + 1
            return f"{len(findings)} finding(s): " + ", ".join(f"{n} {s.lower()}" for s, n in sorted(counts.items()))
        except Exception:
            return ""

    def _write(self, text: str, clear: bool = False) -> None:
        self.log.config(state="normal")
        if clear:
            self.log.delete("1.0", "end")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.config(state="disabled")

    # --- remembered choices -----------------------------------------------------------------------------

    def _load_prefs(self) -> dict:
        try:
            return json.loads(PREFS.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_prefs(self) -> None:
        prefs = {"input": self.input.get(), "output": self.output.get(), "config": self.config_path.get(),
                 "excluded": [c for c, v in self.include.items() if not v.get()], "ocr": self.ocr.get(),
                 "verbose": self.verbose.get(), "cases": self.cases_on.get(), "job": self.job.get(),
                 "cases_folder": self.cases_folder.get()}
        try:
            PREFS.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
        except OSError:
            pass


def main() -> None:
    root = tk.Tk()
    root.title("Drawing QA")
    root.geometry("980x720")
    root.minsize(820, 600)
    try:
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass
    app = App(root)

    def closing() -> None:
        if app.proc and app.proc.poll() is None:
            if not messagebox.askyesno("Drawing QA", "A run is still going. Stop it and close?", parent=root):
                return
            app.proc.terminate()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", closing)
    root.mainloop()


if __name__ == "__main__":
    main()
