# Controls Drawing QA Platform

Automated QA of controls drawing packages: PLC drawings, cable block diagrams and termination drawings. It only reports issues for a person to check and fix; it never changes a drawing.

**What it does:** the QA register is printed and marked up by hand by default; recording decisions in it is printed and marked up by hand by default; recording decisions in it is switched on with `[review] enabled = true`.
- Every sheet's title block and grid frame are read; identifiers, placeholders and words (for spelling) are extracted with their grid cells.
- Schematic sheets are read as a PLC system: rack layout, modules, channels (device, JB, wire, description per channel), abbreviated wire numbers.
- Cable block diagrams are read from the drawn lines: enclosures (JB / LCS / panel), the devices in them, and every cable between them with its spec and number.
- Everything is linked into one engineering graph; a **signal trace** is built for every PLC channel, with the device's **cable route** on the CBD (device → JB / LCS → panel) compared against the schematic.
- 50 rules run (TAG, WIRE, PLC, PATH, COMP, PLACEHOLDER, DRW). Per-rule settings are in `config/rules.toml`.

Termination drawings are read as JB terminal strips (terminal, wire, device, destination panel and slot, multicore) and PLC slot terminal strips, and cross-checked against the schematics and CBDs.

## Setup
**The easy way: double-click programs**, in the **`Windows`** folder (`.cmd`) and the **`Mac`** folder (`.command`). The same three on each:

- **Set Up**: run once on a new computer, and again after the tool is updated. It installs Python 3.12 if no Python 3.11+ is found (winget on Windows, Homebrew on a Mac), creates `.venv` in the project folder and downloads the requirements, then a box says it worked or what went wrong. When everything is already installed and unchanged it says so and installs nothing.
- **Steve**: reviews the drawings in `input/Main` and saves the results in `reports/Main`, then a box gives the number of findings (and any warnings, e.g. the register was open in Excel) and the results folder opens (Windows). If the tool is not set up, or `input/Main` has no PDFs, the box says what to do.
- **Drawing QA**: a window to choose everything yourself: browse for the input (package) folder, the output folder and the project settings file; tick or untick the drawing types to check (PLC, CBD, terminations, equipment list; unticked = `--exclude`) and the extras (OCR for sheets with no SHX text, a detailed log). Survey or Run review, watch the progress, then open the QA register, system map, report or output folder from its buttons. It remembers the last choices. Also `python -m drawing_qa.gui`.

On a Mac, the first time macOS may say a `.command` file "cannot be opened": right-click it, choose Open, then Open again. A Mac with Homebrew Python also needs `brew install python-tk@3.12` for the window (Set Up installs it with Python, and says if it is missing).

Set Up and Steve share `steve.py`, which also passes other commands to the tool once set up (`python steve.py run --input input/LL_Main`). Set `STEVE_NO_BOX=1` to print the messages instead of showing boxes.

**By hand:**
```bash
python -m venv .venv
```
```bash
.venv/Scripts/python -m pip install -r requirements.txt
```
`requirements.txt` installs the tool with its dependencies (listed in `pyproject.toml`).

## Run
Put one package in a folder with three subfolders:
```
input/Main/
├─ PLC/
├─ CableBlocks/
└─ Terminations/
```
```bash
.venv/Scripts/python -m drawing_qa run --input input/Main
```
All four inputs are reviewed by default. To leave one out (for example termination drawings that are not up to date):
```bash
.venv/Scripts/python -m drawing_qa run --input input/Main --exclude term
```
`--exclude` takes `plc`, `cbd`, `term` or `equip` (or a folder name), repeated or comma-separated. A left-out type is still listed, but nothing is checked against it.

Sheets plotted without SHX text notes (`PDFSHX = 0`, reported as `NO_SHX_TEXT`) have nothing to read. Re-plotting them with `PDFSHX = 1` is the fix. Next best is the sheet's DWG: put it beside the PDF with the same name (`PLC/401104-052.pdf` + `PLC/401104-052.dwg`) and its text is read from the DWG, exactly (it needs the free [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter); set its path under `[dwg]` in `config/project.toml`). The DWG is used only if its line work lines up with the PDF's and its text sits on the PDF's letters; otherwise the sheet is reported `DWG_NOT_USED`, and a DWG saved after the PDF was plotted is used but reported `DWG_NEWER`. Such sheets show as "Read from DWG" and are checked like readable ones. When neither is possible, add `--ocr`:
```bash
.venv/Scripts/python -m drawing_qa run --input input/Main --ocr
```
To see how many sheets a set has of each kind before running anything (quick: nothing is checked):
```bash
.venv/Scripts/python -m drawing_qa survey --input input/Main
```
It counts the sheets that are SHX readable, that need decoding (`--ocr`), that are scans and that are blank, by folder, and lists the files to re-plot or decode with the AutoCAD version that plotted them.

`--ocr` learns each letter's strokes from the package's readable sheets and decodes the unreadable ones by exact match (no OCR program or service is involved). Only words whose every letter matched are used, nothing is reported as missing from a decoded sheet, and findings resting on decoded text are marked "(decoded from line work)". A package with no readable sheets to learn from stays unread. `report.md` lists what was decoded under "Sheets decoded from line work", with dropped words counted by reason and words missing a letter or two (`MO?ULE`) under "Unreadable text" (also the register's Unreadable Text tab); those are for the checker only and no rule uses them.
The outputs are written to `reports/Main/` (change this with `--output`):
- `review.json`: files, sheets, every extracted claim with its position, and parse issues
- `findings.json`: QA findings, each with rule, severity, evidence (sheet + grid) and recommendation
- `report.md`: a summary for the checker, including which sheets could not be fully read
- `QA_Register.xlsx`: the findings register to print and mark up
- `system_map.html`: the package as an interactive map (panel → PLC module → JB → field device); opens in any browser, offline

Configuration is in `config/project.toml`, in three parts: the project (FFI drawings, renamed designations, rack panels, areas), the client's drafting standard (tag grammar, CBD line weights, placeholders) and run preferences. Two optional files beside it, kept per project and not on GitHub: `config/rules.toml` (turn a rule off or change its severity: `[rules."TAG-002"]` `enabled = false`) and `config/waivers.toml` (accepted exceptions, `[[waiver]]` with `id`, `rule`, `subject`, `reason`). Every rule is on with its default severity when they are absent.

The automatic tests and the notebooks are kept locally, not on GitHub.

## Layout
```
src/drawing_qa/
  cli.py, pipeline.py   entry point and stage orchestration
  config.py             settings from config/project.toml
  ingest/               folder discovery and document typing
  pdf/                  page extraction (text layer, AutoCAD SHX annotations, OCR hook)
  dwg/                  text from a sheet's DWG (ODA File Converter -> DXF -> ezdxf), placed on the PDF page
  common/               identifier grammar, placeholder detection
  parsers/              PLC (rack layout, slot sheets, wires) / CBD (routing from drawn lines) / TERM parsers
  model/                enums, source, claims, findings, run records
  rules/                rule engine + TAG, WIRE, PLC, PATH, COMP, PLACEHOLDER, DRW rules
  linker/ graph/ traces/  claims -> engineering model -> networkx graph -> signal traces
  report/               review.json, findings.json, report.md, QA_Register.xlsx, system_map.html
  gui.py                the Drawing QA window
Windows/, Mac/          Set Up, Steve and Drawing QA launchers
steve.py                what Set Up and Steve run
```
