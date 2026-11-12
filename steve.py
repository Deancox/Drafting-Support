"""The two double-click programs' shared code: "Set Up" and "Steve".

    Set Up.cmd  ->  python steve.py setup    creates .venv, installs the requirements, then a box says it worked or why not
    Steve.cmd   ->  python steve.py review   reviews input/Main, saves the outputs in reports/Main, then a box says how it went

Both are started by their .cmd file (Windows) or shell script (Mac / Linux), which find Python first; Set Up
installs Python if there is none. Any other arguments go straight to the tool:
    python steve.py run --input input/LL_Main      is   python -m drawing_qa run --input input/LL_Main

The drawings are only read, never changed. Setup only installs Python packages into .venv in this folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
STAMP = VENV / "steve-installed.txt"
WINDOWS = os.name == "nt"
VPY = VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")
MODULES = ("drawing_qa", "pymupdf", "pydantic", "networkx", "openpyxl", "ezdxf")
PACKAGE = HERE / "input" / "Main"
OUTPUT = HERE / "reports" / "Main"


class Failed(Exception):
    """Stops with a message for the error box."""


def say(text: str = "") -> None:
    print(f"[steve] {text}" if text else "", flush=True)


def box(title: str, text: str, error: bool = False) -> None:
    """A message box: the Windows one, a macOS dialog, or zenity on Linux; the console if none of those."""
    print(f"\n{title}: {text}\n", flush=True)
    if os.environ.get("STEVE_NO_BOX"):  # scripted use and tests: the console message only
        return
    try:
        if WINDOWS:
            import ctypes

            flags = (0x10 if error else 0x40) | 0x40000  # error / information icon, on top of other windows
            ctypes.windll.user32.MessageBoxW(0, text, title, flags)
        elif sys.platform == "darwin":
            icon = "stop" if error else "note"
            safe = text.replace('"', "'")
            subprocess.run(["osascript", "-e", f'display dialog "{safe}" with title "{title}" buttons {{"OK"}} with icon {icon}'],
                           check=False)
        else:
            subprocess.run(["zenity", "--error" if error else "--info", f"--title={title}", f"--text={text}"], check=False)
    except Exception:  # no GUI (a server, a remote session): the console message above is enough
        pass


def run(cmd: list, **kw) -> int:
    return subprocess.run([str(c) for c in cmd], cwd=HERE, **kw).returncode


# --- set up ----------------------------------------------------------------------------------------

def fingerprint() -> str:
    h = hashlib.sha256()
    for name in ("requirements.txt", "pyproject.toml"):
        f = HERE / name
        h.update(f.read_bytes() if f.exists() else b"")
    return h.hexdigest()


def missing_modules() -> list[str]:
    code = ("import importlib, sys\n"
            "for m in sys.argv[1:]:\n"
            "    try: importlib.import_module(m)\n"
            "    except Exception: print(m)")
    out = subprocess.run([str(VPY), "-c", code, *MODULES], cwd=HERE, capture_output=True, text=True).stdout
    return out.split()


def ready() -> bool:
    """Set up and up to date: .venv exists, the requirements have not changed since they were installed, all import."""
    stamp = STAMP.read_text(encoding="utf-8").strip() if STAMP.exists() else ""
    return VPY.exists() and stamp == fingerprint() and not missing_modules()


def setup(force: bool = False) -> str:
    if sys.version_info < (3, 11):
        raise Failed(f"Python 3.11 or newer is needed; this computer has {sys.version.split()[0]}. "
                     "Install Python 3.12 from python.org and run Set Up again.")
    if not VPY.exists():
        say(f"Creating the tool's own Python environment ({VENV.name}) ...")
        try:
            venv.EnvBuilder(with_pip=True, clear=VENV.exists()).create(VENV)
        except Exception as exc:
            raise Failed(f"Could not create the .venv folder: {exc}. Check this folder is not read-only.") from exc
    if not force and ready():
        say("Everything is already installed and up to date.")
        return "Everything is already installed and up to date."
    say("Downloading and installing the requirements (a few minutes the first time) ...")
    run([VPY, "-m", "pip", "install", "--upgrade", "pip"], stdout=subprocess.DEVNULL)
    broken = [m for m in missing_modules() if m != "drawing_qa"]  # files removed but pip still lists it
    if broken:
        run([VPY, "-m", "pip", "install", "--force-reinstall", "--no-deps", *broken])
    if run([VPY, "-m", "pip", "install", *(["--force-reinstall"] if force else []), "-r", "requirements.txt"]) != 0:
        raise Failed("Installing the requirements failed. Check the internet connection (or a proxy / firewall "
                     "blocking pypi.org) and run Set Up again. The details are in the black window.")
    still = missing_modules()
    if still:
        raise Failed(f"These did not install properly: {', '.join(still)}. Run Set Up again; the details are in the black window.")
    STAMP.write_text(fingerprint() + "\n", encoding="utf-8")
    return "Set up is complete. Put the drawings in input\\Main and double-click Steve to run the review."


def oda_note() -> str:
    code = "from drawing_qa.dwg.convert import find_converter; import sys; sys.exit(0 if find_converter() else 1)"
    if run([VPY, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
        return ""
    return ("\n\nOptional: the ODA File Converter is not installed. It is only needed to read a sheet's DWG when its PDF "
            "has no text (free from opendesign.com).")


# --- review -----------------------------------------------------------------------------------------

def review() -> str:
    if not ready():
        raise Failed("The tool is not set up on this computer yet (or its requirements changed). Double-click Set Up first.")
    if not PACKAGE.is_dir() or not any(PACKAGE.rglob("*.pdf")):
        raise Failed(f"No drawings found. Put the package's PDFs in {PACKAGE} (folders PLC, CableBlocks, Terminations, "
                     "EquipmentList), then run Steve again.")
    say(f"Reviewing {PACKAGE.relative_to(HERE)} ... (this can take a few minutes)")
    proc = subprocess.Popen([str(VPY), "-m", "drawing_qa", "run", "--input", str(PACKAGE), "--output", str(OUTPUT)],
                            cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    lines = []
    for line in proc.stdout:  # show it as it runs, keep it for the box
        print(line, end="", flush=True)
        lines.append(line)
    if proc.wait() != 0:
        text = "".join(lines)
        if "PermissionError" in text:
            raise Failed(f"Could not save the results: a file in {OUTPUT.relative_to(HERE)} is open, probably "
                         "QA_Register.xlsx in Excel. Close it and run Steve again.")
        last = next((ln.strip() for ln in reversed(lines) if ln.strip()), "no message")
        raise Failed(f"The review stopped with an error:\n{last}\n\nThe details are in the black window.")
    count = ""
    try:
        data = json.loads((OUTPUT / "findings.json").read_text(encoding="utf-8"))
        findings = data["findings"] if isinstance(data, dict) else data
        sev = {}
        for f in findings:
            sev[f.get("severity", "?")] = sev.get(f.get("severity", "?"), 0) + 1
        count = f"{len(findings)} finding(s): " + ", ".join(f"{n} {s.lower()}" for s, n in sorted(sev.items()))
    except Exception:
        pass
    warns = [ln.split("WARNING", 1)[1].replace("drawing_qa.report.register:", "").lstrip(" :").strip()
             for ln in lines if "WARNING" in ln]
    return (f"The review is finished. {count}\n\nSaved in {OUTPUT}:\nQA_Register.xlsx, report.md, system_map.html"
            + "".join(f"\n\nNote: {w}" for w in warns[:4]))


def main(argv: list[str]) -> int:
    action = argv[0] if argv else ""
    try:
        if action == "setup":
            message = setup(force="--force" in argv)
            box("Set Up: success", message + oda_note())
            return 0
        if action == "review":
            box("Steve: review finished", review())
            if WINDOWS and OUTPUT.is_dir() and not os.environ.get("STEVE_NO_BOX"):
                os.startfile(OUTPUT)  # noqa: S606 - open the results folder
            return 0
        if not ready():
            raise Failed("The tool is not set up on this computer yet. Double-click Set Up first.")
        return run([VPY, "-m", "drawing_qa", *argv])
    except Failed as exc:
        box("Set Up: error" if action == "setup" else "Steve: error", str(exc), error=True)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
