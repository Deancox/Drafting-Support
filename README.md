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
```bash
python -m venv .venv
```
```bash
.venv/Scripts/python -m pip install -r requirements.txt
```
`requirements.txt` installs the tool with its dependencies (listed in `pyproject.toml`), plus pytest and ipykernel.

**`commands.ipynb`** has the main commands one cell each: install, survey, run (with `--exclude` / `--ocr` variations), open the outputs, run the tests, help. Pick the `.venv` as its kernel.

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

Sheets plotted without SHX text notes (`PDFSHX = 0`, reported as `NO_SHX_TEXT`) have nothing to read. Re-plotting them with `PDFSHX = 1` is the fix; when that is not possible, add `--ocr`:
```bash
.venv/Scripts/python -m drawing_qa run --input input/Main --ocr
```
To see how many sheets a set has of each kind before running anything (quick: nothing is checked):
```bash
.venv/Scripts/python -m drawing_qa survey --input input/Main
```
It counts the sheets that are SHX readable, that need decoding (`--ocr`), that are scans and that are blank, by folder, and lists the files to re-plot or decode with the AutoCAD version that plotted them.

`--ocr` learns each letter's strokes from the package's readable sheets and decodes the unreadable ones by exact match (no OCR program or service is involved). Only words whose every letter matched are used, nothing is reported as missing from a decoded sheet, and findings resting on decoded text are marked "(decoded from line work)". A package with no readable sheets to learn from stays unread. `report.md` lists what was decoded under "Sheets decoded from line work".
The outputs are written to `reports/Main/` (change this with `--output`):
- `review.json`: files, sheets, every extracted claim with its position, and parse issues
- `findings.json`: QA findings, each with rule, severity, evidence (sheet + grid) and recommendation
- `report.md`: a summary for the checker, including which sheets could not be fully read
- `QA_Register.xlsx`: the findings register to print and mark up
- `system_map.html`: the package as an interactive map (panel → PLC module → JB → field device); opens in any browser, offline

Configuration is in `config/project.toml`, in three parts: the project (FFI drawings, renamed designations, rack panels, areas), the client's drafting standard (tag grammar, CBD line weights, placeholders) and run preferences. Per-rule settings are in `config/rules.toml`, accepted exceptions in `config/waivers.toml`.

## Test
```bash
.venv/Scripts/python -m pytest
```
The tests generate their own PDFs, including AutoCAD-style SHX text annotations, so they don't need client drawings.

## Layout
```
src/drawing_qa/
  cli.py, pipeline.py   entry point and stage orchestration
  config.py             settings from config/project.toml
  ingest/               folder discovery and document typing
  pdf/                  page extraction (text layer, AutoCAD SHX annotations, OCR hook)
  common/               identifier grammar, placeholder detection
  parsers/              PLC (rack layout, slot sheets, wires) / CBD (routing from drawn lines) / TERM parsers
  model/                enums, source, claims, findings, run records
  rules/                rule engine + TAG, WIRE, PLC, PATH, COMP, PLACEHOLDER, DRW rules
  linker/ graph/ traces/  claims -> engineering model -> networkx graph -> signal traces
  report/               review.json, findings.json, report.md, QA_Register.xlsx, system_map.html
tests/                  pytest suite with synthetic PDF fixtures
```
