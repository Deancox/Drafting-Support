# Drawing QA — User Guide

Drawing QA checks a controls drawing package (PLC schematics, cable block diagrams, termination drawings) and
lists the problems it finds for you to check. **It only reads the drawings. It never changes them.**

This guide covers how to install it, run a review, read the results, and adjust it for a project.

---

## 1. Quick start (about 5 minutes)

1. **Install it once.** Open the `Windows` folder (or `Mac` on a Mac) and double-click **Set Up**. Wait for the box
   that says set up is complete.
2. **Add the drawings.** Copy the package PDFs into the subfolders of `input/Main`:

   ```
   input/Main/
     PLC/            PLC schematic sheets (PDF, plus the DWG beside it if you have one)
     CableBlocks/    cable block diagrams (PDF)
     Terminations/   termination drawings: JB strips and PLC slot sheets (PDF)
     EquipmentList/  the equipment list (Excel or PDF), optional
   ```

   You can leave any folder empty, and the review checks whatever is there. Keep the issued file names
   (e.g. `401103-036.pdf`).
3. **Run it.** Double-click **Steve**. When it finishes, a box shows how many findings there are, and
   `reports/Main` opens.
4. **Review the results.** Open `QA_Register.xlsx`, print it or work through it on screen, and check each
   finding against the drawings.

That covers most reviews. The rest of this guide covers the other options.

---

## 2. Installing

### The easy way

Double-click **Set Up** in `Windows/` (`Set Up.cmd`) or `Mac/` (`Set Up.command`). It:

- installs Python 3.12 if the computer has no Python 3.11 or newer (with winget on Windows, Homebrew on a Mac),
- creates a private Python environment in a `.venv` folder in the project folder,
- downloads and installs what the tool needs.

Run it again after the tool is updated. If nothing has changed, it says so and installs nothing.

**If something goes wrong:**

| Message | What to do |
|---|---|
| "Python has been installed. Run Set Up once more to finish." | Double-click Set Up again. |
| "winget is not available" | Install Python 3.12 from python.org (tick *Add python.exe to PATH*), then run Set Up again. |
| "Installing the requirements failed" | Check your internet connection. A proxy or firewall may be blocking pypi.org. |
| Mac: "cannot be opened" | Right-click the `.command` file, choose **Open**, then **Open** again (first time only). |
| Mac: the Drawing QA window won't open | Run `brew install python-tk@3.12`, then run Set Up again. Steve still works without it. |

### By hand (command line)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt      # Windows
.venv/bin/python -m pip install -r requirements.txt          # Mac / Linux
```

The command examples in this guide use the Windows path `.venv/Scripts/python`. On a Mac, use `.venv/bin/python`.

---

## 3. Three ways to run a review

### A. Steve (one click, fixed folders)

**Steve** always reviews `input/Main` and saves the results to `reports/Main`, using `config/project.toml`. Use it
for the routine review of the current package.

If the tool isn't set up yet, or `input/Main` has no PDFs, the box tells you what to do. If `QA_Register.xlsx` is
open in Excel, Steve warns you, because it can't overwrite a file that is open. Close the file and run Steve again.

### B. The Drawing QA window (choose everything yourself)

Double-click **Drawing QA**. In the window you can:

- **Folders:** browse for the input package folder, the output folder, and the project settings file.
- **Drawings to check:** untick PLC, CBD, Terminations or Equipment list to leave that type out. A left-out type
  is still listed, but nothing is checked against it. For example, untick Terminations when the termination
  drawings are out of date.
- **Extras:**
  - *Read sheets with no SHX text (OCR)*: see section 6. It's slower.
  - *Detailed log*: shows more messages, for tracking down a problem.
- **Survey:** a quick count of which PDFs can be read. Nothing is checked.
- **Run review:** watch the progress, then use the **Open …** buttons to open the register, system map, report
  or output folder.

The window remembers your last choices. You can also start it with `python -m drawing_qa.gui`.

### C. Command line

```bash
# Review a package (results go to reports/<folder name>)
.venv/Scripts/python -m drawing_qa run --input input/Main

# Choose the output folder
.venv/Scripts/python -m drawing_qa run --input input/Main --output reports/Main_RevB

# Leave out one or more drawing types
.venv/Scripts/python -m drawing_qa run --input input/Main --exclude term
.venv/Scripts/python -m drawing_qa run --input input/Main --exclude term,equip

# Decode sheets that were plotted without SHX text
.venv/Scripts/python -m drawing_qa run --input input/Main --ocr

# Quick check of which PDFs are readable (nothing is checked)
.venv/Scripts/python -m drawing_qa survey --input input/Main
```

| Option | Meaning |
|---|---|
| `--input FOLDER` | The package folder (required). |
| `--output FOLDER` | Where results are saved. Default: `reports/<input folder name>`. |
| `--config FILE` | A different project settings file. Default: `config/project.toml`. |
| `--exclude TYPE` | Leave out `plc`, `cbd`, `term` or `equip` (or a folder name such as `Terminations`). Repeat the option or separate types with commas. |
| `--include TYPE` | Put back a type that the config's `[ingest] unchecked_types` leaves out. |
| `--ocr` | Decode the letter strokes of sheets with no SHX text (section 6). |
| `-v`, `--verbose` | Show the detailed log. |

`steve.py` also passes any command through to the tool: `python steve.py run --input input/LL_Main` is the same
as `python -m drawing_qa run --input input/LL_Main`.

`commands.ipynb` has these commands one per cell, if you prefer working in a notebook. Choose `.venv` as the
kernel. `Drawing QA - Start Here.ipynb` is a step-by-step notebook for first-time users.

---

## 4. Before a full run: Survey

Run **Survey** first on a new package. It takes seconds and tells you, by folder, how many sheets are:

- **SHX readable**: fine, these will be checked.
- **Need decoding**: plotted without SHX text. Re-plot them, add their DWG, or use `--ocr` (section 6).
- **Scans**: raster images. These can't be read reliably.
- **Blank**.

It also lists the files to re-plot or decode, with the AutoCAD version that plotted each one.

---

## 5. Reading the results

Each run writes these files to the output folder (`reports/Main` by default):

| File | Use it for |
|---|---|
| **`QA_Register.xlsx`** | The main deliverable: one row per finding, to print and mark up. |
| `report.md` | A summary: sheets read, sheets that couldn't be fully read, signal traces, findings by rule, decoded sheets. |
| `system_map.html` | An interactive map of the package (panel → PLC module → JB → field device, plus termination drawings). Opens in any browser and works offline. |
| `findings.json`, `review.json` | Everything the run produced, as data for other tools. |

The console (or the box) also prints a one-line summary and warnings, such as sheets with no readable text, FFI
or panel mapping warnings, and which types were reviewed or left out.

### The QA register

The **Findings** tab has one row per finding:

| Column | Meaning |
|---|---|
| Ref | Row reference for marking up. |
| Severity | **MAJOR**: a wiring or identity error. **MINOR**: incomplete or inconsistent drafting. **INFO**: a note, e.g. a reference to a separate package. |
| Rule / Category | Which check raised it (see section 9). |
| Tag / item | The tag, wire, cable, channel or text concerned. |
| PLC channel | The PLC channel involved, if any. |
| Conflict / issue | What is wrong. |
| PLC schematic / Cable block diagram / Termination | Where to look on each document type, as drawing, sheet and grid cell, e.g. `401103 sh 036 C7`. `-` means not shown on that type. `(left out)` in a header means that type was excluded. |
| Recommendation | The suggested fix. |

The other tabs:

- **Summary**: counts by severity and rule.
- **Tag Index**: every tag and where it appears.
- **Coverage**: how much of each PLC channel's signal trace was found on the drawings.
- **Unreadable Text**: words on decoded sheets with a letter or two missing (e.g. `MO?ULE`). They're for you to
  check by eye, and no rule uses them.
- **Key**: what the columns, severities and decisions mean, and a list of every rule.

**Findings marked "(decoded from line work)"** rest on text the tool decoded with `--ocr`. Check those against
the drawing before acting on them.

### Grid references

Locations use the drawing's own grid frame. Tag labels are usually drawn just above their symbol, so look one or
two rows above and around the cell given.

---

## 6. Sheets with no readable text

AutoCAD plots SHX fonts as line work. The tool reads the text from the hidden SHX text notes that AutoCAD adds
when `PDFSHX = 1`. A sheet plotted with `PDFSHX = 0` has no notes, so it's reported as **`NO_SHX_TEXT`** and
nothing on it can be checked.

Fix it with one of these options, best first:

1. **Re-plot** the sheet from AutoCAD with `PDFSHX = 1`.
2. **Supply the DWG.** Put it beside the PDF with the same name (`PLC/401104-052.pdf` + `PLC/401104-052.dwg`). The
   text is then read exactly from the DWG. This needs the free
   [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter), which the tool finds
   automatically on PATH, in `Program Files\ODA` or in `~/Tools`, or you can set its path under `[dwg]` in
   `config/project.toml`.
   - The DWG is used only if its line work lines up with the PDF. If it doesn't, the sheet is reported
     `DWG_NOT_USED`.
   - If the DWG was saved after the PDF was plotted, it's still used but reported `DWG_NEWER`.
3. **Use `--ocr`** (or tick *OCR* in the window). The tool learns each letter's shape from the package's readable
   sheets and decodes the unreadable ones by exact match. No outside OCR program or service is used.
   - Only words where every letter matched are used.
   - Nothing is reported *missing* from a decoded sheet. Instead, TAG-011 notes tags it couldn't confirm there.
   - A package with no readable sheets to learn from can't be decoded.
   - Set `[ocr] confirm_decoded = true` to hold back findings whose only evidence is on decoded sheets.

---

## 7. Recording decisions in the register (optional)

By default, the register is for printing and marking up by hand. To record decisions in Excel and carry them
forward from run to run, set this in `config/project.toml`:

```toml
[review]
enabled = true
```

The register then gains yellow **Decision**, **Checker comment**, **Reviewed by** and **Date** columns. Edit only
those columns, because everything else is rewritten on every run. Save the file **in the output folder where it
is**. On the next run the tool reads your decisions, stores them in `qa_review.sqlite` next to the register, and
puts them back on the same findings (matched by the *Fingerprint* column), including on later revisions, for as
long as the problem is unchanged.

| Decision | Meaning |
|---|---|
| OPEN | Not reviewed yet (default). |
| CONFIRMED | A real problem. It stays on the register until the drawing is fixed. |
| FALSE_POSITIVE | Not a problem. Greyed out on later runs, with your comment, until the evidence changes. |
| FIXED | Fixed in the drawings. The next run should no longer raise it. |
| WAIVED | A known, accepted exception. Give the reason. |
| DEFERRED | Decide later, e.g. when the termination drawings arrive. |

The **No Longer Raised** tab lists findings this run didn't raise. Check that each one is really fixed and mark it
FIXED. Findings with any other decision stay listed until you do.

Close the register in Excel before running again.

---

## 8. Setting the tool up for a project

All settings are plain text files in `config/`, read at the start of every run.

### `config/project.toml`

It has three parts:

**1. Project (check this when you start a new job)**

| Setting | What it's for |
|---|---|
| `[project] code`, `name` | Labels only. |
| `areas` | Area codes that never appear in a `+` location designation. Most areas are learned from the drawings. |
| `description_prefix` | Text that starts every channel description. Usually detected automatically. |
| `[project.ffi_drawings]` | Which drawing each First Field Identifier belongs to, e.g. `TN = "401103"`. Use `"EXTERNAL"` for a separate package you don't have. Its references are then listed (DRW-009) but not checked. |
| `[project.superseded]` | Renamed designations, e.g. `"LTL-AUT1" = "LTL-PLC"`. Any use of the old name is reported (DRW-006). |
| `[project.rack_panels]` | Which panel each PLC rack sits in, when it isn't `<rack>-PLC`. |

The run warns you when one of these entries is needed. For example: *"Rack … is assumed to be in panel …, which no
drawing shows"*.

**2. Client drafting standard (change only for a new client or drafting house)**

| Section | What it's for |
|---|---|
| `[identifiers]` | The tag and wire number grammar (`S-TN23N2` = prefix, FFI, sheet, grid), lists of known prefixes and FFIs, panel device prefixes, `type_prefixes` (e.g. solenoids must be `Y`), and `io_direction` (e.g. `S` = input, `H` = output). |
| `[pdf]` | When a page counts as raster, and the SHX annotation title. |
| `[cbd]` | CBD line weights (cables 0.96 pt, outlines 0.24 pt by default). **Adjust these if the CBD cables aren't being found.** |
| `[placeholders]` | Tokens and phrases that mean unfinished drafting (`XXXX`, `HOLD`, `REF TERM XXX` …). |
| `[spelling] known_words` | Correct words that the spelling check would otherwise flag. |
| `[ingest.folders]` | Which subfolder names map to which drawing type (case-insensitive). |

**3. Run preferences**

| Section | What it's for |
|---|---|
| `[ingest] unchecked_types` | Types left out of every run, e.g. `["TERM"]`. Use `--include` to put one back for a run. |
| `[review] enabled` | Turns on decision recording (section 7). |
| `[dwg]` | Use DWGs beside PDFs, and the ODA File Converter path. |
| `[ocr]` | `enabled` (same as `--ocr`) and `confirm_decoded`. |

### `config/rules.toml`: turning rules off or changing severity

Every rule runs at its default severity unless you list it here:

```toml
[rules."PLACEHOLDER-004"]   # report HOLD as MAJOR everywhere
severity = "MAJOR"

[rules."DRW-010"]           # turn off the spelling check
enabled = false
```

Severities: `CRITICAL`, `MAJOR`, `MINOR`, `INFO`. Placeholder rules default to MAJOR on issued drawings
(FOR CONSTRUCTION, AS BUILT) and MINOR on PRELIMINARY ones. Setting a severity here fixes it for all drawings.

### `config/waivers.toml`: accepted exceptions

A waiver marks matching findings WAIVED, with its reason as the comment:

```toml
[[waiver]]
id = "W-001"
rule = "COMP-002"            # wildcards allowed: "COMP-*"
subject = "K-TN32*"          # matches the register's "Tag / item" column; * and ? allowed
reason = "Interface to the RCL package; wired to the PLC there"
approved_by = "DC"
expires = 2027-06-30         # after this date it's listed but no longer applied
```

A decision you enter yourself in the register takes priority over a waiver. For a one-off exception, set that
finding's Decision to WAIVED in the register instead of adding a waiver.

---

## 9. What gets checked

The tool reads every sheet's title block and grid. It reads the PLC rack layout, modules and channels from the
schematics, the cables and enclosures from the CBD line work, and the JB and PLC slot terminal strips from the
termination drawings. Then it links them and builds a **signal trace** for every PLC channel
(device → JB / LCS → panel → slot), and compares the documents against each other.

66 rules run in 10 families. The **Key** tab of the register lists each one with what it checks.

| Family | What it looks for | Rules |
|---|---|---|
| **TAG** | Tag format, known prefixes and FFIs, tag on its home sheet and near its grid cell, duplicates, relay contact pointers, mistyped FFIs, prefix matches device type | TAG-001 … 011 |
| **WIRE** | Wire number format, on its home sheet, near its grid cell, duplicates, continuity schematic ↔ terminations | WIRE-001 … 005 |
| **PLC** | Channel and description match with terminations, duplicate channels, safety modules on SAFETY sheets, module capacity and type, slot and sheet consistency, POINTS notation, slot references in notes | PLC-001 … 010 |
| **PATH** | Each CBD device has a cable route to its panel, the same JB on schematic and CBD, routes match terminations, legend matches channel rows, multicore core and spare counts | PATH-001 … 005 |
| **CABLE** | Cable numbers on CBD vs terminations: present on both, same spec, same ends, no duplicates, same spare counts | CABLE-001 … 006 |
| **TERM** | Terminal strips: every terminal wired, no duplicate terminals, valid sheet references, no gaps in numbering, wires match the PLC slot sheet, connector pins match the schematic | TERM-001 … 007 |
| **DEV** | Field devices appear on terminations, I/O direction matches the module (switch on a DI, lamp on a DO) | DEV-003, 004 |
| **COMP** | Incomplete or conflicting signal traces, orphan CBD devices, field devices missing from the CBD, branching safety devices | COMP-001 … 005 |
| **DRW** | Cross-referenced sheets supplied, revision and status consistent, title block complete, folder matches drawing type, duplicate or mismatched sheet files, superseded designations, separate-package references, spelling | DRW-001 … 010 |
| **PLACEHOLDER** | `XXXX`, `TNXXXX` / `CXXXXX`-style identifiers, `REF TERM XXX`, `HOLD`, `AUT1` | PLACEHOLDER-001 … 005 |

Rules that need a type you left out (e.g. TERM rules with `--exclude term`) are skipped, not failed. DEV-001 and
DEV-002 (equipment list checks) are planned but not built yet.

---

## 10. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| "The tool is not set up on this computer yet" | Run **Set Up**. |
| Steve says `input/Main` has no PDFs | Put the drawings in the subfolders (section 1). |
| Warning: register open in Excel | Close `QA_Register.xlsx` and run again. |
| Many sheets reported `NO_SHX_TEXT` | They were plotted with `PDFSHX = 0`. See section 6. |
| CBD cables or routes not found (lots of PATH or COMP findings) | The drafting house uses different line weights. Adjust `[cbd]` in `project.toml`. |
| FFI or panel mapping warnings in the output | Add the entry to `[project.ffi_drawings]` or `[project.rack_panels]`. |
| A correct word keeps being flagged by DRW-010 | Add it to `[spelling] known_words`. |
| TAG-002 never reports anything | That's expected until `prefixes` / `ffis` are listed in `[identifiers]`. |
| Need more detail on a problem | Tick *Detailed log* or add `-v`. |

---

## Data and privacy

Client drawings in `input/` and results in `reports/` stay on your computer and are excluded from git. The tool
never uploads anything, and `--ocr` uses no outside service.
