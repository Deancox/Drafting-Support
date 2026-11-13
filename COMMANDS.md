# Drawing QA: command line

Run these from the `Drafting Support` folder (PowerShell or Command Prompt). Every command starts with the project's own Python, `.venv\Scripts\python`.

## First time: set up

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Survey a package (quick, checks nothing)

How many sheets can be read from their SHX text, need decoding, are scans or are blank.

```
.venv\Scripts\python -m drawing_qa survey --input input/Main
```

## Run the review

Load the package into `input/Main` (subfolders `PLC/`, `CableBlocks/`, `Terminations/`, `EquipmentList/`; any may be missing), then:

```
.venv\Scripts\python -m drawing_qa run --input input/Main
```

Outputs go to `reports/Main/` (each run overwrites them):

| File | What it is |
|---|---|
| `report.md` | Summary: findings by rule, sheets read / not read, traces, parse issues |
| `QA_Register.xlsx` | The register to print and mark up (close it in Excel before re-running) |
| `system_map.html` | Interactive map: panel → PLC module → JB → field device; opens offline in any browser |
| `findings.json` | Every finding with its evidence |
| `review.json` | Everything the run read and built: sheets, claims, model, traces |

## Run options

| Option | What it does | Example |
|---|---|---|
| `--output <folder>` | Write the outputs somewhere else, e.g. to keep a run | `--output reports/Main-2026-11-02` |
| `--exclude <type>` | Leave an input type out of the review: `plc`, `cbd`, `term` or `equip` (or a folder name). Repeat or comma-separate. Still listed, not checked | `--exclude term` |
| `--include <type>` | Put back a type that `[ingest] unchecked_types` in the config leaves out | `--include term` |
| `--ocr` | Also read sheets plotted without SHX text by decoding their letter strokes | `--ocr` |
| `--config <file>` | Use another config file (default `config/project.toml`) | `--config config/other.toml` |
| `-v`, `--verbose` | Show more detail while running | `-v` |

Combined, for example:

```
.venv\Scripts\python -m drawing_qa run --input input/Main --exclude term --output reports/Main-2026-11-02
```

## Tests and help

```
.venv\Scripts\python -m pytest
.venv\Scripts\python -m drawing_qa run --help
.venv\Scripts\python -m drawing_qa survey --help
.venv\Scripts\python -m drawing_qa --version
```

The same commands, one cell each, are in `commands.ipynb`.
