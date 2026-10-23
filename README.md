# Controls Drawing QA Platform

Automated QA of controls drawing packages: PLC drawings, cable block diagrams and termination drawings.

| Document | What it covers |
|---|---|
| [GAME_PLAN.md](GAME_PLAN.md) | Goals, conventions, signal trace model, parsers |
| [DATA_MODEL.md](DATA_MODEL.md) | Entities, claims, engineering graph |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Stack, parsing, rule engine, milestones |

**Status:** milestones M0, M1, M2 (termination parser), M3a (PLC parser), M3b (linker, graph, signal traces), M4 (CBD routing) and M5 (QA register, review carry-forward, coverage, waivers). The register is printed and marked up by hand by default; recording decisions in it is switched on with `[review] enabled = true`.
- Every sheet's title block and grid frame are read; identifiers, placeholders and words (for spelling) are extracted with their grid cells.
- Schematic sheets are read as a PLC system: rack layout, modules, channels (device, JB, wire, description per channel), abbreviated wire numbers.
- Cable block diagrams are read from the drawn lines: enclosures (JB / LCS / panel), the devices in them, and every cable between them with its spec and number.
- Everything is linked into one engineering graph; a **signal trace** is built for every PLC channel, with the device's **cable route** on the CBD (device → JB / LCS → panel) compared against the schematic.
- 34 rules run (TAG, WIRE, PLC, PATH, COMP, PLACEHOLDER, DRW). Per-rule settings are in `config/rules.toml`.

Termination drawings are read as JB terminal strips (terminal, wire, device, destination panel and slot, multicore) and PLC slot terminal strips, and cross-checked against the schematics and CBDs.

## Setup
```bash
python -m venv .venv
```
```bash
.venv/Scripts/python -m pip install -e ".[dev]"
```

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
The outputs are written to `reports/Main/` (change this with `--output`):
- `review.json`: files, sheets, every extracted claim with its position, and parse issues
- `findings.json`: QA findings, each with rule, severity, evidence (sheet + grid) and recommendation
- `report.md`: a summary for the checker

Project conventions (areas, tag patterns, placeholder tokens) are in `config/project.toml`.

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
  report/               review.json, findings.json, report.md
tests/                  pytest suite with synthetic PDF fixtures
```
