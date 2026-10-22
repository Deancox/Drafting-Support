# Controls Drawing QA Platform — Implementation Plan

Companion to [GAME_PLAN.md](GAME_PLAN.md) (what and why) and [DATA_MODEL.md](DATA_MODEL.md) (entities and graph). This document covers **how it gets built**. Drafted 2026-10-21.

**Status:** built in `src/drawing_qa/`: M0 (scaffolding + parsing), M1 (title block, grid frame, rule engine, TAG / PLACEHOLDER / DRW rules), M3a (PLC parser + PLC / WIRE rules), M3b (linker, engineering graph, signal traces, COMP rules), M4 (CBD routing parser + PATH rules), M5 (QA register, review carry-forward, COMP-003 / 004, coverage, waivers; equipment list accepted as an input) and **M2 (termination parser + TERM rules and termination cross-checks, 2026-10-22)**. 266 tests pass. WIRE-001, WIRE-003 and PATH-004 built too (2026-10-22): every rule in the catalogue is built except PLC-002 (M6) and DEV-001 / 002 (M8). All four inputs are reviewed by default; `--exclude term` leaves the termination drawings out of a run (the sample package's are still on the old standard). Next: M6 (OCR + AI assist), M7 (Word report, revision diff) or M8 (equipment list).

---

## 1. Technology stack
| Concern | Choice | Notes |
|---|---|---|
| Language / runtime | Python ≥ 3.11 (developed on 3.14) | `tomllib` built in |
| PDF text, positions, vector lines | **PyMuPDF** (`fitz`) | Fastest Python PDF library; gives word boxes, rotation, and vector drawing paths |
| Data models / validation | **pydantic v2** | One definition for validation and JSON output |
| Engineering graph | **networkx** (from M3) | MultiDiGraph rebuilt each run (DATA_MODEL §5) |
| OCR (from M6, optional extra) | **PaddleOCR**, with Tesseract as a lighter fallback | Local only. See §3 |
| AI assist (optional, off by default) | Claude API | Description matching and unreadable sheets; every result flagged AI-assisted |
| Persistence | JSON run outputs (M0–M4); **SQLite** from M5 for review decisions carried across runs | JSON keeps early milestones simple; SQLite arrives when fingerprints and reviews need to persist |
| Config | TOML (`config/project.toml`) | Human-editable by the checker; parsed with `tomllib` |
| Tests | **pytest** | Synthetic PDFs generated in fixtures, plus golden files from real sheets |
| Quality | ruff (lint + format), mypy (from M1) | |
| Packaging | `pyproject.toml`, src layout, editable install in `.venv` | `python -m drawing_qa run …` |

Runs fully locally. No network access is needed unless the optional AI assist is enabled.

## 2. Language selection
**Python.**
- It has the strongest PDF, geometry, OCR and data ecosystem (PyMuPDF, PaddleOCR, networkx, pandas).
- It's readable by engineers who aren't full-time developers, which matters for maintaining rules.
- The rule logic is set-and-graph work, not CPU-bound. Drawing packages are thousands of sheets at most.

**Alternatives considered:**
- **C# / .NET:** best if we later read AutoCAD Electrical or EPLAN data natively through their APIs. If native CAD becomes available, a small .NET exporter can write **claims** in the same JSON schema, and the Python platform consumes them unchanged.
- **TypeScript:** weaker PDF geometry and OCR tooling.

> **Phase 0 finding (2026-10-21): the client's drawings have no text layer, but don't need OCR.**
> They're AutoCAD 2025 plots using SHX fonts, so every character is drawn as line geometry. AutoCAD also embeds each text string as an invisible **"AutoCAD SHX Text" annotation** with its bounding box.
> - `pdf/extract.py` reads these annotations as a text source (`ExtractionMethod.SHX_ANNOTATION`), decodes AutoCAD codes (`%%D` → °), and splits each string into words with estimated positions.
> - All 60 sample sheets (18 termination, 3 CBD, 39 schematic) read this way, with no OCR.
> - OCR stays a fallback for scanned or third-party sheets.

## 3. OCR strategy (not implemented in M0)
**Principle: vector first, OCR only where needed.** Most CAD-plotted PDFs have a real text layer, and OCR on clean vector text only adds errors.

1. **Detect.** At ingestion, each page is classified as:
   - `VECTOR`: has a text layer with enough words.
   - `RASTER`: little or no text, but large images.
   - `EMPTY`.

   In M0, RASTER pages are reported as `NEEDS_OCR` issues and skipped.
2. **Render.** RASTER pages are rendered at 300–400 dpi with PyMuPDF. They're split into overlapping tiles, because drawing sheets are too big for OCR models at full resolution.
3. **Recognise.** PaddleOCR is run per tile at 0° and 90°, because drawings have vertical text. Results are merged, duplicates in tile overlaps are dropped, and boxes are mapped back to PDF coordinates.
4. **Constrain.** OCR tokens go through the same identifier grammar. Known confusions are corrected only when the result then matches the grammar (`O↔0`, `I↔1`, `S↔5`, `B↔8`), and each correction is recorded.
5. **Flag.** OCR claims carry `method = OCR` and a lower confidence. Rules can require corroboration before raising Critical findings from OCR-only evidence.
6. **Fallback.** Claude vision for table-like regions (termination strips, card tables) where OCR layout fails. Off by default, and flagged AI-assisted.

**Architecture hook (built in M0):** `pdf/ocr.py` defines an `OcrEngine` protocol. The extraction layer returns the same `PageContent` whether the text came from the PDF text layer or from OCR, so parsers never know the difference.

## 4. PDF parsing strategy
**Layers, lowest first:**

| Layer | Module | Responsibility |
|---|---|---|
| Page extraction | `pdf/extract.py` | Words with bounding boxes from the PDF text layer **and AutoCAD SHX annotations**; vector line segments (loaded lazily, as they're expensive); page classification (vector / raster / empty) |
| Shared services | `common/` | Identifier grammar and canonical keys, placeholder detection, (M1) grid frame, title block reader |
| Template readers | `parsers/<type>/…` | Read one drafting template of one document type. `matches(page)` decides whether a reader applies |
| Document parsers | `parsers/plc.py`, `cable_block.py`, `termination.py` | Choose a template reader per page, always run the generic occurrence reader, and collect claims and issues |
| Registry | `parsers/registry.py` | Document type → parser |

**Per document type:**
- **All types (M0, built):** a generic occurrence reader. Every word is cleaned of surrounding punctuation and run through the identifier grammar. It emits `OCCURS` claims (device, wire, JB, terminal, panel) and `PLACEHOLDER` claims, each with its exact position.
- **Termination drawings (M2):** a table-region reader. Words are grouped into rows by y-position within the terminal strip region. Columns are assigned using template header positions. Each row gives `LANDS_ON`, `REFERS_TO_DEVICE`, `DESTINED_FOR` and `DESTINED_PANEL` claims.
- **PLC drawings (M3):**
  - The rack layout region gives `INSTALLED_IN` and module type and channel count.
  - Channel rows give `HAS_CHANNEL`, `DESCRIBES` and `SAFETY_REF`.
  - The route legend region gives `ROUTE_LEGEND`.
- **Cable block diagrams (M4):** read connectivity from the drawn geometry.
  1. Get line segments from `get_drawings()`.
  2. Snap endpoints within a tolerance and merge collinear segments.
  3. Build a segment graph and join connected parts with union-find.
  4. Treat rectangles as device and JB boxes, and attach labels to the nearest box or segment.
  5. Each connected run between two boxes becomes a `ROUTED` claim, labelled with the cable number and route code found along it.
- **Title block (M1):** a template region reader for drawing no, sheet, rev and title. In M0, the drawing number comes from the file name and the sheet from the page index, and the claim records that it came from the file name.
- **Grid frame (M1):** read border zone labels to build a lookup from x/y to grid cell, stored on the Drawing. Each SourceRef then gets a `grid_cell`.

**Template handling:** template layouts (region boxes, column headers) are data in `config/templates/*.toml`, not code. A new drafting house's layout is mostly a new config file.

## 5. Engineering graph construction (M3)
Implements DATA_MODEL §4–5.
1. **Normalise.** Every claim already carries canonical keys (the grammar runs inside the parsers). Raw spellings stay in `SourceRef.raw_text`.
2. **Create nodes.** Group claims by (entity type, canonical key) to make one node each. Status is CORROBORATED if ≥ 2 parsers mention it, otherwise SINGLE_SOURCE.
3. **Derive implied nodes.** A terminal `SPL-JB1:1` implies JB `SPL-JB1` (status DERIVED). A module key implies its panel.
4. **Create edges.** Map predicates to edge types using the catalogue in DATA_MODEL §4.3. Each edge keeps the IDs of its supporting claims.
5. **Check cardinality.** For each edge type, check its rule (e.g. ≤ 1 channel per terminal). Edges that break it are marked CONFLICTED. Nothing is dropped.
6. **Build the graph.** Load the result into a networkx MultiDiGraph with typed node IDs (`pn:LTL-JB1`). The routing layer is a filtered view of PathNodes and ROUTE edges.

**Module:** `src/drawing_qa/linker/` (claims → nodes and edges) and `src/drawing_qa/graph/` (graph build and queries).

## 6. Signal trace generation (M3–M5)
Implements GAME_PLAN *Signal trace model* and DATA_MODEL §4.6.
1. **Forward walk** from every Device, following the fixed hop sequence Device → Wire → JB Terminal → JB → *routing path* → Panel → Module → Channel. At each step only the allowed edge types are followed.
2. **Backward walk** from every used PLCChannel, using the same sequence in reverse.
3. **Join** forward and backward walks that meet at the same node. Reaching both ends makes the trace COMPLETE. Reaching one end makes it PARTIAL, with `break_after_hop` / `break_before_hop` recorded.
4. **Orphans:** connectivity edges not used by any trace are grouped into connected fragments, and each becomes an ORPHAN trace.
5. **Conflicts:** a CONFLICTED edge forks the walk. The trace is flagged `conflicted` and stores the alternatives on the TraceHop.
6. **Routing:** the JB → Panel step is a path search in the routing layer. The path is stored as `route_path`, and checked against the terminal's `DESTINED_PANEL` / `DESTINED_FOR` claims.
7. **Multi-conductor:** each conductor is one TraceLeg. Legs are grouped into a trace by shared device and channel.

**Module:** `src/drawing_qa/traces/`.

## 7. Rule engine
### 7.1 Categories and rule IDs
Rule IDs follow `<CATEGORY>-<nnn>`. This replaces the earlier IDs in GAME_PLAN; the mapping is in §7.6.

| Category | Prefix | Scope | Data it needs |
|---|---|---|---|
| Tag Validation | `TAG` | occurrence / device | claims, grid frame |
| Wire Validation | `WIRE` | occurrence / wire / link | claims, grid frame, graph |
| PLC Validation | `PLC` | module / channel / link | graph |
| Path Validation | `PATH` | route / JB / routing layer | graph |
| Termination Validation | `TERM` | terminal / JB | claims, graph |
| Device Validation | `DEV` | device | graph |
| Drawing Validation | `DRW` | drawing / cross-ref | drawings, claims |
| Placeholder Validation | `PLACEHOLDER` | occurrence | claims |
| Completeness Validation | `COMP` | trace | traces |

### 7.2 Architecture
```
  config/rules.toml ──▶ RuleConfig (enabled, severity override, params per rule)
                              │
  rules/<category>.py ──@rule──▶ RuleRegistry ──▶ RuleEngine.run(stage, ctx)
                                                     │
                     RuleContext (read-only) ◀───────┤  drawings, claims, graph, traces, config
                                                     │
                     per rule: check prerequisites → evaluate → collect Findings
                                                     │  (an exception becomes RuleRun.error;
                                                     ▼   other rules keep going)
                     FindingBuilder: fingerprint, evidence, templated message
                                                     │
                                                     ▼
                                    findings.json + RuleRun audit in review.json
```

**Components:**
- **`Rule` (abstract base):** each rule has `meta`: `rule_id`, `version`, `title`, `category`, `scope`, `default_severity`, `requires`, `error_class`, `description`. Its `evaluate(ctx) -> Iterable[Finding]` method does the check.
- **Base classes that remove boilerplate:**
  - `OccurrenceRule` gets called per claim of a given predicate or subject type.
  - `EntityRule` gets called per node of a type.
  - `TraceRule` gets called per trace.
  - `SetRule` gets the whole context, for duplicate and cross-set checks.
- **`requires`** declares what a rule needs (`CLAIMS`, `GRID`, `GRAPH`, `TRACES`). The engine runs rules in **stages** as the data becomes available:
  - Stage A after parsing (TAG, PLACEHOLDER, DRW).
  - Stage B after linking (WIRE, PLC, PATH, TERM, DEV).
  - Stage C after tracing (COMP).

  If a prerequisite isn't available yet (e.g. the CBD parser isn't built), the rule is **skipped with a reason**, not failed.
- **`RuleRegistry`:** an `@rule` decorator registers classes. The engine imports `rules/` modules to discover them. Duplicate IDs are an error at start-up.
- **`RuleConfig`:** per-rule `enabled`, `severity`, and `params` from `config/rules.toml`. For example, PLACEHOLDER token lists and HOLD exceptions live there, not in code.
- **`FindingBuilder`:** the only way to create a Finding. It computes the fingerprint (DATA_MODEL §8), attaches evidence (claim IDs and SourceRefs, role A/B), fills the message template with canonical keys, and links the trace and hop.
- **Review carry-forward (M5, built):** after evaluation, every finding gets the checker's latest decision for its fingerprint (`Finding.status`, `Finding.review`); nothing is deleted. FALSE_POSITIVE / WAIVED / FIXED rows are shown greyed. Two findings that would share a fingerprint (two identical placeholders in one grid cell) are numbered `-2`, `-3` in reading order so each occurrence keeps its own decision. **Waivers (built):** `config/waivers.toml` `[[waiver]]` entries (id, rule and subject patterns with `*` / `?`, reason, approved_by, expires) mark matching findings WAIVED with the comment `Waiver <id>: <reason>`. The checker's own decision (anything but OPEN) wins. Expired waivers are listed, not applied; the review stage note names expired waivers and ones matching nothing. Register rows a waiver wrote are not read back as the checker's decisions, so removing a waiver reopens its findings. Waivers are part of the config hash.
- **Determinism:** rules iterate in sorted order and messages are generated from templates. The same input always gives an identical `findings.json`, which golden tests rely on.

### 7.3 Rule catalogue
| ID | Title | Scope | Check |
|---|---|---|---|
| TAG-001 | Device Tag Format | occurrence | Matches `<Prefix>-<FFI><Sheet><Grid>` |
| TAG-002 | Known Prefix / FFI | occurrence | Prefix and FFI are in the configured lists |
| TAG-003 | Sheet Validation | device | Tag's sheet number = sheet of its home occurrence |
| TAG-004 | Grid Validation | device | Tag's grid = physical grid cell, or a valid collision shift (letter advances, `D2 → E2`) |
| TAG-005 | Duplicate Device Tag ✅ | device | A tag drawn as a device on its home sheet and again on another schematic sheet with its label at its own grid reference (a copied, un-renumbered device); suggests the renumbered tag and says if other drawings already use it. Relay coils with contact tables are skipped (TAG-007) |
| TAG-006 | Identifier Spelling Variant | occurrence | Written differently from its canonical key (`SPL-JB-1` → `SPL-JB1`) |
| TAG-007 | Auxiliary Contact Pointer | pointer | A coil's contact-table entry (`43/D4` under `K-TN19F1` on sheet 019) must point to a contact labelled **exactly with the coil tag** (`K-TN19F1`) near grid D4 of sheet 043. A label made from the coil sheet + own grid (`K-TN19D4`) is reported with "Relabel the contact K-TN19F1" |
| WIRE-001 | Wire Number Format ✅ | occurrence | Text starting with a known FFI and a digit that does not fit `<FFI><Sheet><Grid>` (`TN36D`, `TN3D6`). Known FFIs: `[identifiers] ffis` + `ffi_drawings` keys, so words (`THERMISTOR`) are not caught. None on the sample package |
| WIRE-002 | Wire Home Sheet ✅ | wire | A wire number used on any sheet exists on its home sheet (the sheet it encodes), with near-match suggestion. Schematic abbreviations (`D6`, `15D5`) are expanded first |
| WIRE-003 | Duplicate Wire Number ✅ | set | One wire number in the rows of two PLC channels, or on terminals of two PLC modules (slot termination sheets). Commons on one module (`TN20X6` on terminals 17–20), supplies and JB loops are not reported. None on the sample package |
| WIRE-004 | Wire Continuity ✅ | device | A wire number the schematic channel row shows for a device that its JB terminals on the termination drawing do not have. *(The wire at the field device's own terminals is read too, but not checked: which device a field-side terminal belongs to is unreliable where device boxes are stacked)* |
| PLC-001 | Channel Match ✅ | device / wire | A device's JB terminals go to another PLC slot than its schematic channel; or a slot termination sheet lands a wire on a module whose schematic channels do not have it |
| PLC-002 | Description Match | channel | PLC description ↔ device description (exact, then AI-assisted) |
| PLC-003 | Duplicate Channel Allocation ✅ | set | A channel drawn on two sheets; a device on more than one channel of standard modules (dual-channel safety devices on safety modules are expected) |
| PLC-004 | Safety Module Consistency ✅ (part) | module | Safety part number (`…S`) ⇔ sheet titled SAFETY. *Safety references across sources: later* |
| PLC-005 | Module Capacity ✅ | module | POINTS range and channel rows within the part's channel count (module catalogue in `[plc.modules]`) |
| PLC-006 | Module Type Match ✅ (part) | module | Rack layout part = slot sheet part; POINTS type = part type; rack layout slots without a slot sheet. *vs termination slot refs: with M2* |
| PLC-007 | Slot / Sheet Consistency ✅ | module | Title, RACK line and POINTS give the same slot; the rack layout lists exactly the sheet the slot is drawn on |
| PLC-008 | POINTS Notation ✅ | module | POINTS line follows `<rack>.S<slot>.<type>.<first>..<last>` (e.g. `LTL.S13.DI.00..07`) |
| PLC-009 | Panel / Slot Consistency ✅ | module | A JB terminal's destination (`LTL-PLC ... DI SLOT 12`) names a slot the panel does not have, a module in another panel, or a module of another I/O type |
| PATH-001 | Route Exists ✅ | trace | A field device shown on a CBD has a cable route to its PLC panel |
| PATH-002 | JB Consistency ✅ | trace | The schematic channel row and the CBD route take the device through the same JB / field location |
| PATH-003 | Route Endpoint Match ✅ | JB | The terminations send a JB's terminals to a panel, both are on the CBDs, but no CBD cable route joins them |
| PATH-004 | Route Legend Match ✅ | sheet | A schematic sheet's LEGEND box against its channel rows: a JB / location the rows use that the legend does not list, or an empty legend (MINOR); a legend entry found nowhere else in the package (MAJOR, suggesting the rows' location with the same number) |
| PATH-005 | Core Count ✅ | JB | The multicore to the panel has fewer cores than terminals using it (MAJOR); its spare count ≠ cores − used (MINOR, only for a JB on one sheet) |
| TERM-001 | Terminal Allocation ✅ | terminal | A JB terminal not under any device block (placeholder wires are PLACEHOLDER-002) |
| TERM-002 | Duplicate Terminal ✅ | set | A JB terminal number drawn twice with different wires / devices, across sheets. Equipment boxed inside the JB with its own small strip (motor isolators, `STORM PIN LAND LEG`) is numbered on its own |
| TERM-003 | Terminal Reference ✅ | JB / terminal | A slot termination sheet's `REF. TERM. SHT nnnn` names a supplied sheet that does not show that JB; a `<JB>:<Terminal>` reference to a terminal its strip does not have |
| TERM-004 | Terminal Sequence ✅ | JB | Gaps in a JB's terminal numbering across all its sheets (screen `SCR` and `SPARE` rows count as terminals) |
| DEV-001 | Device Presence | device | Appears in every document type the document matrix expects |
| DEV-002 | Device Attribute Consistency | device | Same type, description and signal across sources |
| DEV-003 | Device Terminated ✅ | device | A device on a PLC channel whose JB's termination drawing is supplied, but no terminal on it serves the device (the JB-terminal gap; COMP-001 leaves it to this rule) |
| DRW-001 | Cross-Reference Exists | occurrence | Referenced drawing / sheet / rev exists in the package |
| DRW-002 | Revision Consistency | drawing | Sheets of one drawing share a rev; references cite the current rev |
| DRW-003 | Title Block Complete | drawing | Drawing no, sheet, rev and title present |
| DRW-004 | Folder / Type Match | drawing | Input folder type = title block drawing type |
| DRW-005 | Tag Home Exists | occurrence | A referenced tag exists at the home sheet and grid it encodes |
| DRW-007 | Conflicting Sheet Copies | drawing | Two files with the same drawing no and sheet in the title block but different content (identical copies are skipped with a `DUPLICATE_SHEET` issue instead) |
| DRW-008 | File Name / Title Block Match | drawing | `<drawing>-<sheet>.pdf` file name disagrees with the title block |
| DRW-009 | Separate Document Reference | FFI | Tags / wires whose FFI is a separate package (`EXTERNAL`) are listed as separate-document references (INFO); an FFI with no drawing mapping is reported (MINOR) |
| DRW-010 | Spelling ✅ | word | A word used at most twice in the package that is one letter from a word used 3+ times (STOBE vs STROBE). Word endings (S / D / ED, SUPPLIES / SUPPLIED) are ignored; real words go in `[spelling] known_words` |
| DRW-006 | Superseded Designation | occurrence | A designation listed in `[identifiers.superseded]` is still used; the recommendation is the replacement (e.g. `=K07.11+LTL-AUT1` → `=K07.11+LTL-PLC`) |
| PLACEHOLDER-001 | XXXX Detection | occurrence | `XXX`, `XXXX`, or any run of ≥ 3 X as a token |
| PLACEHOLDER-002 | Identifier Placeholder | occurrence | `TNXXXX`, `PXXXXX`, `CXXXXX`, identifiers with X-run parts (`S-TNXXXX`, `SPL-JBX`, `SPL-JB1:XX`) |
| PLACEHOLDER-003 | REF TERM XXX | occurrence | Phrase match on the text line |
| PLACEHOLDER-004 | HOLD | occurrence | Standalone word, with configured exceptions (e.g. `HOLD OPEN`) |
| PLACEHOLDER-005 | AUT1 | occurrence | Exact token, or a component of a designation (`LTL-AUT1`). Raised together with DRW-006 when the designation has a known replacement, as one finding with the replacement as the recommendation |
| COMP-001 | Incomplete Signal Trace ✅ | trace | A channel trace missing a hop the supplied drawings should show (device tag MAJOR, with the likely orphan device suggested; field wire or JB / field location MINOR). Hops only a not-yet-parsed drawing type can give (JB terminal) are listed as not sourced, never reported |
| COMP-002 | Orphan Field Device ✅ | trace | A device on a CBD and on its home sheet that no PLC channel reaches (devices not on their home sheet are TAG-003) |
| COMP-003 | Conflicted Trace ✅ | trace | Two drawings disagree on a hop and no more specific rule reports it: the CBD cables the device to a different panel from its slot's. JB disagreements stay PATH-002; termination ones come with M2. Every disagreement also marks the trace *conflicted* (`SignalTrace.conflicts`: hop, schematic value, CBD value, reporting rule) |
| COMP-004 | Branching ✅ | device / channel | A device on safety-module channels with different description headings (MAJOR; a dual-channel pair shares a heading; standard modules are PLC-003), suggesting the same-heading neighbour's tag; two field devices in one channel row (MINOR; panel devices and separate-package FFIs ignored) |
| COMP-005 | Field Device Not on CBD ✅ | trace | A field device whose JB / field location is on the supplied CBDs, but which is not; panel devices (`panel_device_prefixes`: K, Q) and near-matches already reported by TAG-003 are skipped |

### 7.4 Where rule code lives
```
src/drawing_qa/rules/
  base.py          Rule, OccurrenceRule, EntityRule, TraceRule, SetRule, RuleMeta
  registry.py      @rule decorator, discovery, duplicate-ID check
  engine.py        stages, prerequisites, isolation, RuleRun audit
  context.py       RuleContext (read-only view of the run)
  findings.py      FindingBuilder, fingerprinting, message templates
  tag.py  wire.py  plc.py  path.py  term.py  dev.py  drw.py  placeholder.py  comp.py
config/rules.toml  per-rule enabled / severity / params
```

### 7.5 Adding a rule
1. Pick the category file and the next ID.
2. Subclass the right base class and fill in `meta`.
3. Write `evaluate`, creating findings only through `FindingBuilder`.
4. Add a pass case and a fail case under `tests/rules/<RULE-ID>/`.
5. Add the default config entry.

### 7.6 Mapping from earlier GAME_PLAN IDs
TAG-001→TAG-001 · TAG-002→TAG-003 + TAG-004 · TAG-003→TAG-004 (collision shift) · TAG-004→TAG-005 · WIR-00n→WIRE-00n · TRC-005→WIRE-004 · PLC-001/003/004/005/006 keep their numbers · PLC-002→PLC-002 · TRC-006→PLC-007 · PLC-007→PLC-004 · JB-001/002/003→PATH-002 · RTE-001→PATH-003 · RTE-003→PATH-004 · CBL-002/004→PATH-005 · TERM-001/002→TERM-002/001 · TERM-003→TERM-003 · CMP-001→DEV-001 · CMP-002→DEV-003 · DEV-001→DEV-002 · CMP-003→COMP-005 · DRW-001/002 keep · DRW-003→PLACEHOLDER-001…005 · DRW-004→DRW-005 · TRC-001→COMP-001 · TRC-002→COMP-002 · TRC-003→COMP-003 · TRC-004→COMP-004 · CBL-001 (duplicate cable no)→PATH-003 · CBL-003→DEV-002 for cable type.

## 8. Report generation
Every run writes three files to the output folder (default `reports/<Area>/`):

| File | Audience | Content |
|---|---|---|
| `review.json` | tools, deeper review, revision diffs | Run metadata (ID, times, tool version, config hash), input manifest (file, type, SHA-256, pages), drawings, all claims with SourceRefs, parse issues, stage status. From M3: nodes, edges, traces. From M5: rule runs |
| `findings.json` | the checker's register, other tools | Run ID, summary counts, and a list of findings: ID, fingerprint, rule, category, severity, confidence, subject, trace, message, recommendation, evidence (drawing / sheet / rev / position / raw text), status |
| `report.md` | the checker, humans | Run summary; input summary per document type; pipeline stage status; (from M3) trace coverage, complete / partial / orphan; findings by category and severity; top findings with evidence; parse issues to fix before trusting results |

- Each JSON file has a `schema_version`. Schemas are pydantic models, so the JSON is validated before it's written.
- **QA register (Excel), agreed with the checker 2026-10-22 (M5).** Written beside `report.md`, which stays. Tabs:
  - *Findings*: one row per finding occurrence. Columns, in this order: Ref, Severity, Rule, Category, Tag / item, PLC channel, Conflict / issue, **PLC schematic**, **Cable block diagram**, **Termination (not checked)** locations in full form (`401103 sh 036 C7`, every place the tag is drawn), Recommendation, then the checker's columns **Decision** (OPEN / CONFIRMED / FALSE_POSITIVE / FIXED / WAIVED / DEFERRED), Checker comment, Reviewed by, Date, and First seen, Fingerprint. FALSE_POSITIVE / WAIVED / FIXED rows are greyed and struck through.
  - *Tag Index*: one row per field device: channel, description, trace status, location on each document type, schematic JB beside CBD route (mismatches highlighted), findings count.
  - *Summary*: counts by category × severity and by decision (formulas).
  - *Key*: how to use it and what each decision means.
  - *No Longer Raised*: findings this run does not raise. Anything the previous run raised shows once; anything with a decision other than FIXED (or OPEN) **stays listed until the checker marks it FIXED** (checker, 2026-10-22). This tab has the same yellow decision columns and is read back too.
  - *Coverage*: traced share by area, panel, slot and JB / location.
  - *Waivers*: every waiver in `config/waivers.toml`, active or expired, with how many findings it matched and where the checker's own decision was kept.

  **Built (2026-10-22):** `report/register.py` writes `QA_Register.xlsx` every run. **Carry-forward** (`review/`): before writing, the run reads the Decision / comment / reviewer / date columns (found by header name) of `QA_Register.xlsx`, stores changed decisions in `qa_review.sqlite` beside it (append-only `reviews` table; also `runs` and `findings_seen` for first-seen dates and "no longer raised"), then writes them back onto findings with the same fingerprint. Decision text is forgiving (`false positive` = FALSE_POSITIVE); an unknown one is a `REGISTER_ROW_SKIPPED` issue and the previous decision stands; clearing a decision reopens the finding. If the register is open in Excel (locked), the run writes `QA_Register-<run id>.xlsx` instead; the next run reads both (newest save wins) and moves the copy to `register_history/`. The store keeps decisions even if the register is deleted. Planned rules for the Key tab live in `rules/catalogue.py`.
  On the sample package: 18 answer-key decisions entered (PATH-002 CONFIRMED ×11, COMP-002 / 005 DEFERRED ×7) were carried to the next two runs unchanged.
- **Later:** a Word area report (M7), and a Rev A vs Rev B diff report using fingerprints.

## 9. Test harness
| Level | What | How |
|---|---|---|
| Unit | Identifier grammar, placeholder detection, token cleaning, config loading, discovery | Plain pytest, table-driven cases |
| Extraction | Page extraction, page classification, parser claims | **Synthetic PDFs built inside fixtures** with PyMuPDF (text placed at known positions, lines drawn). No dependence on client drawings |
| Golden | Real sheets → expected claims | `tests/golden/<case>/input.pdf` + `expected_claims.json`. Re-generated deliberately with a flag (`--update-golden`), never silently |
| Rule | Each rule's pass and fail | `tests/rules/<RULE-ID>/` with a small model fixture (claims JSON) and expected findings |
| End-to-end | Folder in → three outputs | Synthetic package of PLC + CBD + TERM PDFs; asserts the three files exist, validate, and match the golden output |
| Accuracy | Against checker redlines | `tests/accuracy/`: precision and recall per rule versus the answer key, tracked over time (not a pass/fail gate until tuned) |

**Answer key: confirmed issues in the sample package.** These are known-real problems the platform must report. Each becomes an accuracy test case.

| # | Confirmed | Issue | Where | Expected finding |
|---|---|---|---|---|
| AK-001 | 2026-10-21 | Destination panel still uses the old designation `=K07.11+LTL-AUT1`. AUT was renamed PLC everywhere else, so it should be `=K07.11+LTL-PLC`. The CBD 401310-014 already shows `LTL-PLC` for the same "Land Leg Remote I/O Panel" | Termination drawings 401103-201 to 207 (57 occurrences) | PLACEHOLDER-005 / DRW-006 per occurrence, recommendation "replace with `=K07.11+LTL-PLC`"; once traces exist, a PATH conflict on the JB → panel link (TERM says LTL-AUT1, CBD says LTL-PLC) |

| AK-002 | 2026-10-21 (C-01) | `401103-219.pdf` title block says sheet 217; `401103-217.pdf` also says 217 with different content | Terminations | DRW-008 + DRW-007 |
| AK-003 | 2026-10-21 (C-02) | `401103-220.pdf` title block says drawing **401105** sheet 219 | Terminations | DRW-008 |
| AK-004 | 2026-10-21 (C-03) | CBDs reference `H-TN50D8/H8/L8` and `H-TN51D8/H8/L8/N8/R8/W8`; schematic sheets 050/051 have them in column 7 | CBD 401310-014/015 | TAG-003, suggesting the `…7` tag |
| AK-005 | 2026-10-21 (C-04) | `S-TN20D4`, `S-TN20N4`, `S-TN25D4`, `S-TN26D4` referenced; schematics have `…D3` / `…N3` | Terminations 202/205/206, CBDs 014–016 | TAG-003, suggesting the `…3` tag |
| AK-006 | 2026-10-21 (C-06) | `H-TN51F`: grid digit missing | CBD 401310-014 G6 | TAG-001 |
| AK-008 | 2026-10-21 (C-05) | Sheet 043 auxiliary contacts are labelled `K-TN19D4`, `K-TN19F4`, `K-TN19H4`, `K-TN19L4`. They should carry the tags of the relays on sheet 019, exactly `K-TN19F1`, `K-TN19F2`, `K-TN19F3`, `K-TN19F4`, which the sheet 019 contact tables point to at 43/D4, 43/F4, 43/H4, 43/L4 | 401103 sh 043 | TAG-003 (D4, H4, L4 not on sheet 019) and TAG-007 for all four ("Relabel the contact K-TN19F1"…) |
| AK-009 | 2026-10-21 (C-08) | Rack layout 401103-013 lists two sheets for slot 20, `043` and `045` printed over each other; slot 20 is on 043, 045 is slot 22 | 401103 sh 013 | PLC-007 |
| AK-010 | 2026-10-21 (C-09) | POINTS notation: sheet 035 `LTL.S12DI.00..07` (missing dot); sheets 056 / 057 `…AI.00.03` (single dot) | 401103 sh 035, 056, 057 | PLC-008 |
| AK-011 | 2026-10-21 (C-14) | Slot 5 CH02/03 (rail striker west side) has no device tag on sheet 024; the CBD and terminations call it `S-TN24H3` | 401103 sh 024 | COMP-001 (MAJOR, suggests S-TN24H3) |
| AK-012 | 2026-10-21 (C-15) | Sheet 028 (south west broken rail) reuses `S-TN27D2` from sheet 027 (south east); it should be `S-TN28D2`, as the CBD and termination 203 have it | 401103 sh 028 | TAG-005 (new rule), plus COMP-001 and TAG-003 for the knock-on gaps |
| AK-013 | 2026-10-21 (C-11) | `TN16D0` used on sheet 046 (T6); sheet 016 has `TN16D1` | 401103 sh 046 | WIRE-002 |
| AK-014 | 2026-10-21 (C-16) | No JB in the channel row, unlike neighbouring channels: `H-TN50D7` (slot 24 ch 00, sheet 050; H7 / L7 go through LTL-JB2 / LTL-JB1) and `S-TN26N3` (slot 7 ch 04/05, sheet 026, also on no CBD). The CBDs should show the `…7` tags (see AK-004) | 401103 sh 050, 026 | COMP-001 |
| AK-015 | 2026-10-21 (C-19) | Spelling: "SIREN & STOBE" (CBD 401310-015 W0). The same rule also finds "PRSSURE SENSOR" (sheet 057 A0), not yet confirmed but plainly a typo | 401310 sh 015, 401103 sh 057 | DRW-010 (new rule) |
| AK-016 | 2026-10-21 (C-20) | Brakes `S-TN36D4/F4/H4/L4`: schematic 036 = brakes 9–12 via LTL-JB2; CBD 015 uses these tags for the brake 3–6 switches cabled to LTL-JB1 | 401103 sh 036, 401310 sh 015 | PATH-002 |
| AK-017 | 2026-10-21 (C-21) | `S-TN44D4`: schematic 044 via LTL-JB3; CBD 014 inside SH-LCS8 | 401103 sh 044, 401310 sh 014 | PATH-002 |
| AK-018 | 2026-10-21 (C-22) | `U-TN57P1`, `U-TN57S1`: schematic 057 via LTL-JB3; CBD 016 via SH-JB3 | 401103 sh 057, 401310 sh 016 | PATH-002 |
| AK-019 | 2026-10-22 (C-24) | `S-TN44F4`, `S-TN44H4`: schematic 044 via LTL-JB3; CBD 016 via SH-JB11 / SH-JB9 (with AK-017, sheet 044 shows LTL-JB3 where the CBD does not) | 401103 sh 044, 401310 sh 016 | PATH-002 |
| AK-020 | 2026-10-22 (C-26) | `S-TN37D4`, `S-TN37F4`: schematic 037 via LTL-JB13; CBD 016 on SH-JB3 | 401103 sh 037, 401310 sh 016 | PATH-002 |
| AK-021 | 2026-10-22 (C-27) | `S-TN23T4` (south rail, LTL/S4/CH06–07, sheet 023) is copied onto LTL/S5/CH01 "cow catcher contact 2" on sheet 024 (G3); it should be `S-TN24D2`, like contact 1 on CH00 | 401103 sh 024 | COMP-004 (MAJOR), suggesting S-TN24D2 |
| AK-007 | 2026-10-21 (C-07) | Mixed statuses within one revision: 401103 sh 035 CONCEPTUAL, sh 056 PRELIMINARY (rest of rev 0 FOR CONSTRUCTION); 401310 sh 015 AS BUILT (014/016 PRELIMINARY) | Title blocks | DRW-002 |

**Must NOT be reported. These are provisional: assumed by me, not yet confirmed by the checker.**

| # | Correct drafting (assumed) | Handling |
|---|---|---|
| AK-N02 | Sheet 042 contacts labelled with their coil tag (`K-TN18F1`…`F8`), at the grids the sheet 018 contact tables point to | TAG-007 accepts the coil tag near the pointer's grid |
| AK-N03 | Example tags on sheet 008 (GRAPHICS SYMBOLS), e.g. `K-CD011M4` | Legend, symbol key, title and index sheets are left out of the tag and placeholder rules (`title_block.reference_descriptors`) |

| AK-N04 | 2026-10-21 (C-13, confirmed by checker) | `TV20T6`, `TV20U6` on sheet 015 | FFI `TV` belongs to a separate package that is not supplied | `[identifiers.ffi_drawings]` maps each FFI to its drawing; `TV = "EXTERNAL"` makes them a DRW-009 "separate document" entry (INFO), not WIRE-002 |

| AK-N05 | 2026-10-21 (C-16, first item) | `S-TN36R4` (slot 13 CH05) has no JB | It is cabled straight to the panel; the CBD shows no JB | A missing JB is "not yet sourced", not a gap, when the device is on a CBD, until the CBD routing (M4) can confirm a direct cable |

| AK-N06 | 2026-10-21 (C-23) | `S-TN25H4` → SH-JB9 → LTL-PLC on CBD 016 | The cables either side of the narrow SH-JB9 box were read as one | Cables are cut where they cross an enclosure |

| AK-N07 | 2026-10-22 (C-25) | `S-TN36T4`, `S-TN37T4`, `U-TN57E1`, `U-TN57J1` attached to LTL-JB3 on CBD 016 | LTL-JB3 is a narrow box under device symbols with its own dividers; its slices were split off and the tags sit beside the box | Slices under a JB header stay with it; tags beside or touching a box belong to it. All 84 channel devices on a CBD now route to LTL-PLC |

(AK-N01 was withdrawn: C-05 was first marked "correct notation", then confirmed as a real error. It is now AK-008.)

**Candidates from the PLC parser run (2026-10-21), waiting for the checker to confirm or reject.**

| # | Rule | Issue |
|---|---|---|
| C-10 *(on hold: termination drawings not checked)* | WIRE-002 | Termination drawings use `TN20E6/K6`, `TN22…`–`TN28E6/K6` (16 wires); schematic sheets 020–028 have D6/F6 and J6/L6 only (old-standard termination drawings) |
| C-12 *(on hold: termination drawings not checked)* | WIRE-002 | `TN50R5`, `TN51T5` on termination 217 are not on sheets 050 / 051 |

| C-17 *(withdrawn 2026-10-22: a platform bug)* | COMP-002 | `K-TN32C8`, `K-TN32H8` were reported as on no PLC channel. They are in the rows of LTL/S11/CH00 and CH02 on sheet 032 with `K-TN32C4` / `K-TN32H4`; the trace builder only counted the first device of a row as reached. Fixed in M5; the register lists them under No Longer Raised |
| C-18 *(flagged for later, 2026-10-21: may be wrong or not required)* | COMP-005 | Field devices not on the CBDs that show their JB: `A-TN60H2`, `A-TN60H6`, `S-TN24D2`, `S-TN36W4`, and `S-TN25T3` (the CBD shows `A-TN25T3`: different prefix) |

**Candidates from M5 (2026-10-22), waiting for the checker.**

| # | Rule | Issue |
|---|---|---|
| C-27 *(confirmed 2026-10-22: now AK-021)* | COMP-004 (MAJOR) | `S-TN23T4` is the dual-channel south rail device on LTL/S4/CH06–07 (sheet 023) and also tags LTL/S5/CH01 "cow catcher contact 2" on sheet 024 (grid G3). Its neighbour CH00, "contact 1", is `S-TN24D2`: a copied tag? (`S-TN24D2` is also in C-18) |
| C-28 | COMP-004 (MINOR) | LTL/S26/CH00 and CH01 (skew load cells, sheet 056) each have two U- devices in the row: `U-TN56E1` + `U-TN56E3`, `U-TN56J1` + `U-TN56J3`. Probably load cell + amplifier / isolator; confirm, and mark FALSE_POSITIVE in the register if so |




Also seen but not reported: sheet 026 has the address `04` drawn twice on top of itself (a duplicated text object).

**Choosing the inputs reviewed (2026-10-22).** All four inputs (PLC, CBD, TERM, EQUIP) are reviewed by default. `--exclude TYPE` (repeatable or comma-separated; `plc`, `cbd`, `term`, `equip` or a folder name such as `Terminations`) leaves one out of a run; `--include TYPE` puts back one that `[ingest] unchecked_types` in the config leaves out. A type left out is still read and listed (`review.json`, the register's location columns marked "(left out)"), but produces no findings, is not used by other checks or traces (its hops are "not sourced", never gaps), and rules that need it are skipped with the reason. A missing folder for a left-out type is not a warning. Excluding everything still writes the outputs.

**Termination drawings on the sample package (checker decision 2026-10-21).** They are not up to date, so the checker runs this package with `--exclude term`; the answer-key tests do the same, except AK-001 to AK-003 (found only on the termination drawings), which run with all inputs.

**Known limits in M2 (termination parser):**
- Built against the sample package's old-standard termination sheets: JB termination diagrams (200–207) and PLC slot termination diagrams (211–218). Re-check when the new-standard sheets arrive.
- JB strips: rows are a terminal-number box with a wire-number box just right of it; a row's device is the block header box above it; its destination is the panel label whose bracket (thin vertical line, ≤ 0.5 pt) spans it (`DI SLOT 13` gives the module). LCS strips are read like JB strips; a JB / LCS box further right than the strip column is a field enclosure (its terminals are the device's).
- Slot sheets: the module is named by slot only and resolved to the rack's module when one rack has that slot; the strip stops at the next text in its column (`24V DC SUPPLIES`).
- With all inputs, the sample package gives 601 findings (most are placeholders and the old-standard wire numbers on the termination drawings: C-10 / C-12 territory) and 99 complete channel traces; with `--exclude term`, the same 179 as before M2.

**Candidates from the M2 run (2026-10-22, termination drawings on the old standard: confirm only once the new ones arrive).**

| # | Rule | Issue |
|---|---|---|
| C-29 | PLC-001 | `S-TN38D4` is on LTL/S15/CH00 on the schematics; its terminals LTL-LCS1:47–48 go to DI slot 16 |
| C-30 | PLC-009 | LTL-LCS1:23–26 go to **DO** slot 16, which is a DI module; LTL-JB1:41–42 go to **DI** slot 24, a DO module |
| C-31 | COMP-003 | Schematic JB ≠ termination JB with the CBD saying neither: `H-TN51R7`, `S-TN39F4` (LTL-LCS2 vs LCS1), `S-TN26H4` (SH-JB8 vs SH-JB7), `S-TN36W4` (LTL-JB3 vs JB1), `S-TN37D4/F4` (LTL-JB13 vs JB3) |
| C-32 | TERM-003 | Slot sheets cite the wrong termination sheet: LTL-JB1 "SHT 202" (it is on 203/204), LTL-JB4 "SHT 201" and "SHT 207" (on 202); `RC-JB1` (sheet 214) looks like a typo for RCL-JB1 |
| C-33 | TERM-002 | SH-JB7:1–4 drawn on both 206 (S-TN26D4) and 207 (S-TN26H4) |
| C-34 | PATH-005 | LTL-JB3's 6C multicore has 7 terminals using it |

**Candidates from PATH-004 (2026-10-22, schematics: can be confirmed now).**

| # | Rule | Issue |
|---|---|---|
| C-35 | PATH-004 (MAJOR) | Sheet 051's legend lists `=K07.11+LTL-SH8`; the channel rows use `SH-LCS8` |
| C-36 | PATH-004 | Empty LEGEND on sheets 021 (rows use LTL-LCS1) and 045 (LTL-JB1, LTL-JB2) |
| C-37 | PATH-004 | Sheet 022's legend lists LTL-LCS1/2/4/5/6 but not LTL-JB1, LTL-JB2, which its channel rows use |

**Known limits in M4 (CBD routing):**
- Cables are read as 0.96 pt runs and enclosure outlines as 0.24 pt dash-dot lines (the client's CAD standard); another drafting house may use other widths (make them config).
- Cables whose ends meet no enclosure, device or labelled box are dropped (4 of 95 on the sample package: a placeholder-tagged storm pin box and three around SH-JB10 on 016).
- Devices are tied to an enclosure when their tag is inside it or right beside it; a tag far from its symbol is not.

**Known limits in M3a (PLC parser):**
- Channel rows are found from the 2-digit addresses under the "/ADDRESS" or "CHANNEL" header. Only addresses inside the POINTS range are channels; safety modules' test outputs (`10`, `11` beside T0/T1) are kept as separate rows but not reported.
- Devices and same-sheet wires are placed in a channel row by the grid row their name encodes; JBs and descriptions by position.
- A short wire number is accepted when it sits next to its cell or on the same row / column (long runs). Relay terminal labels `T1`/`T2` on sheets 018/019 pass this test by coincidence and are read as wires; harmless, since nothing references them.
- PLC-002 (description match) waits for AI-assisted matching (M6).

**Known limits in M1:**
- TAG-004 uses the label position, not the device symbol. Labels sit up to 2 rows above (and 1 column beside) their symbol, so that offset is accepted. A true symbol-position check comes with the PLC parser (M3).
- Schematic wire numbers are abbreviated (`D6` = TN36D6 on sheet 036, `15D5` = TN15D5). Expanding them needs the PLC template reader (M3), so the WIRE rules wait until then.
- Sheet numbers in tags can be 3 digits (`R-TH157V0`, `K-CD011M4`). The grammar accepts `\d{2,3}`.

**Linker note from AK-001:** the linker resolves a superseded designation to its replacement, so traces still reach `LTL-PLC` and aren't broken by a stale name. The original claim keeps what the drawing says, and the finding still fires.

Run with `.venv/Scripts/python -m pytest`. Real client drawings are kept out of the repo by default (`tests/golden/` holds only what the client allows).

## 10. Folder ingestion process
**Input layout:**
```
input/<Area>/
├─ PLC/            PLC drawings (any depth below; *.pdf)
├─ CableBlocks/    cable block diagrams
├─ Terminations/   termination drawings
└─ EquipmentList/  electrical equipment list (optional; .xlsx / .xlsm / .xls / .csv / .pdf)
```
The equipment list folder (aliases `Equipment`, `EquipList`, `EEL`) is optional: no `MISSING_FOLDER` without it. Its files are fingerprinted and listed in the manifest, but not read yet; the reader, checks against it, and the full equipment list output are M8.
**Steps (`ingest/discovery.py`, built in M0):**
1. **Validate the root.** The folder exists and at least one of the three subfolders is present. A missing subfolder is a warning, because a partial package is still useful.
2. **Map folders to types**, case-insensitively, with configurable aliases:
   - `PLC` → PLC
   - `CableBlocks` / `CableBlock` / `CBD` → CBD
   - `Terminations` / `Termination` / `Terms` → TERM
3. **Discover files** recursively. `*.pdf` files are accepted. Other files are listed as `IGNORED_FILE` issues. Hidden and temporary files (`~$…`, `.…`) are skipped.
4. **Fingerprint** each file with SHA-256 for the manifest. Identical files in two places are a `DUPLICATE_FILE` issue.
5. **Open** each PDF. Encrypted or corrupt files are an `UNREADABLE_FILE` issue, and the run continues.
6. **Split into sheets.** Each page becomes one Drawing (drawing number from the title block from M1, from the file name in M0).
7. **Classify** each page as vector / raster / empty. Raster pages get `NEEDS_OCR` and are skipped until OCR exists.
8. **Dispatch** each sheet to the parser registered for its document type.
9. **Write outputs** to `--output` (default `reports/<Area>/`).

**Command:**
```
python -m drawing_qa run --input input/Main --output reports/Main
python -m drawing_qa run --input input/Main --exclude term      # leave the termination drawings out
```

---

## Milestones
| # | Milestone | Scope | Done when |
|---|---|---|---|
| **M0** | **Scaffolding + parsing architecture** ✅ built | Package layout, config, CLI, ingestion, page extraction with OCR hook, parser framework with three document parsers running the generic occurrence reader, JSON / Markdown writers, tests | `pytest` green; the pipeline runs on the real termination PDFs and writes the three outputs |
| **M1** | **Title block, grid frame, Stage A rules** ✅ built | Label-anchored title block reader, grid frame reader, duplicate / conflicting sheet detection, rule engine (registry, stages, isolation, fingerprints, rules.toml), TAG-001/002/003/004/006, PLACEHOLDER-001…005, DRW-001…008 | Real findings on the sample package; AK-001 caught (57/57) |
| **M3a** | **PLC parser** ✅ built | Rack layout reader, slot sheet reader (module header, channel rows with device / JB / wire / description / module terminal), abbreviated wire expansion; PLC-003…008, WIRE-002; PLC I/O table in report.md | All 27 slots and 209 channels read; rack-layout and POINTS findings on the sample package |
| **M3b** | **Linker + graph + first traces** ✅ built | Claims → nodes and edges (superseded names linked to their replacement), networkx graph, one trace per channel (device → wire → JB terminal → JB / field location → panel → slot → channel), orphans from CBD devices, inferred devices for second contacts; COMP-001 / 002 / 005; trace register in report.md, model + traces in review.json | 208 channel traces + 25 orphans on the sample package; 128 of 136 field traces complete as far as the parsed drawings go |
| **M2** | **Termination parser** ✅ built 2026-10-22 | Strip table reader, termination claims, TERM rules, PLC-001 / PLC-009 / WIRE-004 cross-checks | Waits for termination drawings on the new standard |
| **M4** | **CBD routing parser** ✅ built | Enclosures from dash-dot outlines (plus wall pairs, split stacked boxes, solid boxes with a designation), devices in / beside each enclosure, cables from 0.96 pt runs (rejoined across label boxes) with spec and number, ROUTE / DEVICE_IN edges, CBD route per trace (device → JB / LCS → panel), PATH-001 / PATH-002; CBD route column in the trace register | 95 cables read on 3 CBDs; 84 of 84 channel devices on a CBD routed to LTL-PLC; S-TN36R4 confirmed direct to panel from the drawing |
| **M5** | **Completeness + persistence** ✅ built | Excel QA register (§8) ✅, SQLite review store ✅, review carry-forward from the register ✅, equipment list accepted as an input (listed only) ✅, COMP-003 / 004 ✅, trace coverage by area / panel / slot / JB (report.md, review.json, register Coverage tab) ✅, waivers (`config/waivers.toml`) ✅ | Re-running shows review decisions carried forward ✅ |
| M6 | OCR + AI assist | PaddleOCR path for raster pages, optional Claude assist | Raster sheets produce flagged claims |
| M7 | Reporting + revisions | Word report, Rev A vs Rev B diff | Report usable as-is by the checker |
| M8 | **Electrical equipment list** — final phase (flagged 2026-10-22) | Reader for the client's equipment list; DEV rules against it (tag on the list but not drawn, drawn but not on the list, description / type mismatch); **output a full electrical equipment list** built from the drawings and the supplied list | Needs a sample of the client's list to fix the format |
