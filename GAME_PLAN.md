# Controls Drawing QA Platform — Game Plan

## Objective
Automatically QA a controls/E&I drawing package for one functional area (e.g. `Main`, `Conveyor 1`). Given a folder of **PLC drawings, cable block diagrams (CBDs) and termination drawings**, the platform:
1. **Extracts** every drawing into structured, source-tagged claims.
2. **Rebuilds the system** the drawings describe (devices, cables, JBs, terminals, PLC channels) as one graph, and from it **builds a signal trace for every signal**: device → wire → JB terminal → JB → destination panel → PLC slot → PLC channel.
3. **Classifies every trace** as complete, partial or orphan, and checks traces and objects for gaps and disagreements between sources: missing references, inconsistent routing, JB inconsistencies, PLC assignment errors, missing signal traces, placeholder text, cross-reference errors, device inconsistencies.
4. **Reports** findings with evidence from both sides, severity and confidence, in a register the checker closes out.

Later sources (IO lists, instrument indexes, vendor data) plug in as extra parsers that write claims to the same model. The model and rules stay the same.

## What changed from the schedule-led version
> ⚠️ **Changed (2026-10-21):** the first version of this plan used the I/O list, cable schedule and instrument index as the **baseline** and checked drawings against them.

| Area | Before (schedule-led) | Now (drawing-led) |
|---|---|---|
| Inputs | Schedules + drawings, schedules as master | PLC, CBD and termination drawings only; schedules are a **future** source |
| Source of truth | Schedules | **None.** The three drawing types check each other. Disagreements are reported with both sides' evidence, and the checker decides which drawing is wrong |
| Data unit | One Excel table per source | **Claims**: one fact per row, each tagged with drawing/sheet/rev/x-y and extraction confidence. Conflicting claims are kept, never overwritten |
| Model | Text labels next to each other link objects (no line tracing) | Text labels for termination and PLC sheets, **plus vector line tracing for CBD routing** |
| New capability | — | **Trace engine** that follows device → cable → JB → terminal → PLC channel paths; reports broken traces and routing that differs between sources |
| Rules | Mostly schedule ↔ drawing | Re-pointed to drawing ↔ drawing; added routing, JB, trace and device-consistency rules (see catalogue) |
| Phase order | Schedule extraction first | Drawing register + occurrence index first, then termination, PLC and CBD readers. CBD line tracing gets an early feasibility spike |

Everything else carries over: code decides and AI helps, every finding quotes its evidence, the checker reviews extracted data before it's trusted, and accuracy is measured against an answer key.

> ⚠️ **Changed (2026-10-21, later):** package conventions added (see *Engineering conventions*). Tags are **location-coded** (`S-TN23N2`), not ISA-5.1 loop tags, so the drawings themselves show where each device should be drawn. This added the TAG, WIR, JB-003, TERM-003 and DRW-004 rules (marked 📐), and grid-frame detection in Phase 1.

> ⚠️ **Changed (2026-10-21, latest):** **signal traces are now the primary business object** (see *Signal trace model*). The trace builder is its own stage, not just one rule among many. The **trace register** is the main output, and every finding links to a trace. The trace work moved from Phase 5 to an early end-to-end slice (Phase 2). Trace changes are marked 🔗.

> ⚠️ **Changed (2026-10-21, parsers):** each document type now has its **own independent parser** (PLC, CBD, termination), and a **linker** joins their claims into the common engineering model (see *Document parsers*). This added racks, modules and routes to the model, and route, module and safety-reference rules. Parser changes are marked 🧩.

> ⚠️ **Changed (2026-10-21, data model):** the full data model and internal graph are now in **[DATA_MODEL.md](DATA_MODEL.md)**. Core entities: Drawing, Device, Wire, JunctionBox, Terminal, PathNode, PLCModule, PLCChannel, SignalTrace, Finding, QARule. Supporting entities: Run, SourceRef, Claim, Edge, TraceLeg/TraceHop, FindingEvidence, FindingReview. Storage is SQLite as the system of record, with the graph rebuilt in memory each run.

> ⚠️ **Changed (2026-10-21, implementation):** **[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)** now holds the build plan, the rule engine, milestones M0–M7, and the **new rule IDs** by category (TAG, WIRE, PLC, PATH, TERM, DEV, DRW, PLACEHOLDER, COMP). The rule catalogue below is superseded; the old → new ID mapping is in §7.6. Run outputs are now `review.json`, `findings.json` and `report.md`. M0 is built in `src/drawing_qa/`.

> ⚠️ **Changed (2026-10-22, focus):** M6 (OCR + AI assist), M7 (Word report, revision diff) and M8 (electrical equipment list) are **parked**: kept in the plan, not being built. The focus is refining what exists: accuracy against the checker's answer key, the open candidates, and the drawing readers.

> ⚠️ **Changed (2026-10-22, M2 and input selection):** the **termination parser** is built: JB terminal strips and PLC slot terminal strips are read, linked into the traces (JB terminal hop), and checked by TERM-001…004, WIRE-004, PLC-001 / 009, PATH-003 / 005 and DEV-003. **All four inputs are reviewed by default**; `--exclude term` (or `plc`, `cbd`, `equip`) leaves one out of a run, for drawings that are not up to date. See IMPLEMENTATION_PLAN.md §9 notes.

> ⚠️ **Changed (2026-10-22, inputs and review register):** the **electrical equipment list** is a fourth input (`input/<Area>/EquipmentList/`, Excel / CSV / PDF). For now it is only listed in the run manifest; reading it, checking the drawings against it, and **producing a full equipment list as an output** are the **final phase** (M8). Checker decisions will be recorded in an **Excel QA register** alongside `report.md` (one row per finding occurrence; tag, issue, and full locations `401103 sh 036 C7` on the PLC, CBD and termination drawings; Decision / comment columns carried forward by fingerprint). Preview: `reports/Main/QA_Register_PREVIEW.xlsx`.

## Constraints and principles
- **Any input can be left out of a run** with `--exclude` (e.g. `--exclude term` while the termination drawings are not on the new standard, checker decision 2026-10-21); all four are reviewed by default (2026-10-22).
- **Read-only. The platform never changes a drawing** (confirmed with the checker 2026-10-21). Its job is a detailed report of issues, each with a sheet and grid reference, so a person can find it, confirm it, and fix the drawing themselves. PDFs are opened read-only and never saved; the input files' SHA-256 fingerprints are recorded in every `review.json`.
- **Inputs are PDF** for now. If native CAD is available (AutoCAD Electrical project DB, EPLAN, DWG with attributes), use it instead: it replaces most of extraction and makes routing exact.
- **No master document.** The platform doesn't guess which drawing is right. It reports the disagreement.
- **Code makes the decisions; AI is a helper.** Rules are code with tests. Claude is used only to read sheets the template readers can't parse and to match descriptions that mean the same thing in different words. Anything AI-derived is marked as needing review.
- **Every claim keeps its source**: drawing, sheet, revision and position on the sheet. Every finding quotes its evidence.
- **The checker sees the extracted data before it's used.** Stage 1 Excel outputs are reviewed before checks are trusted.
- **Accuracy is measured, not assumed.** Results are compared against a past package the checker has already reviewed.

## Architecture
```
 input/<Area>/*.pdf
        │
 ① INGEST & CLASSIFY     title block → drawing no / sheet / rev / type (PLC | CBD | TERM)
        │                vector-vs-scan check, drawing register, sheet grid frame
        │
        ├─────────────────────────┬─────────────────────────┐
        ▼                         ▼                         ▼
 ② PLC PARSER 🧩          ② CBD PARSER 🧩          ② TERM PARSER 🧩
   rack layouts, modules,    devices, JBs,             wires, wire numbers,
   slots, channels,          connectivity, routes,     JB terminals, device refs,
   descriptions, safety      cable specs               PLC destinations, slot refs
   refs, route legends
        │                         │                         │
        └─────────────┬───────────┴─────────────────────────┘
                      │  each emits its own tables + claims; none reads another's output
                      ▼
 ③ NORMALISE (shared)    identifier grammar (S-TN23N2, TN23N2, SPL-JB1, SPL-JB1:1),
        │                x/y → grid cell, placeholder detection, area/interface lists
        ▼
 ④ LINKER 🧩             joins claims from all three parsers on canonical keys
        │                (device, wire, JB terminal, JB, panel, rack/slot/channel,
        │                cable, route); description match only as a flagged fallback
        ▼
 ⑤ COMMON ENGINEERING    graph nodes: Device, Wire, JB Terminal, JB, Panel, Rack,
   MODEL                 Module, Slot, Channel, Cable, Route, Drawing/Sheet
        │                every edge carries the claims (and parsers) behind it;
        │                conflicting claims are both kept
        ▼
 ⑥ TRACE BUILDER 🔗      forward from every device, backward from every PLC channel,
        │                joined where they meet → Signal Trace objects
        │                status: Complete | Partial | Orphan   (+ Conflicted flag)
        ▼
 ⑦ ANALYSIS              ⑦a Trace rules: missing hop, conflicting hop, branching
        │                ⑦b Object rules: tags, grid, placeholders, cross-refs
        │                ⑦c AI assist: description meaning match, unreadable sheets
        ▼
 ⑧ TRACE REGISTER &      one row per trace: hops, status, break point, findings;
   FINDINGS              findings register linked by Trace ID; checker decisions
                         feed the answer key and regression tests
```

## Document parsers 🧩
Each document type has its own parser, and a parser reads only its own drawing type. That keeps the sources independent. When two parsers agree, it's genuine corroboration. When they disagree, it's a real finding, not a side effect of one parser copying another. All three use the same shared services and write claims in the same schema. The linker then joins them into the common engineering model.

### Parser contract
- **Input:** sheets of one drawing type, taken from the drawing register.
- **Output:** its own review tables (Excel) plus claims in the shared schema. Every claim records drawing/sheet/rev, x-y, grid cell and confidence.
- **Independence:** a parser never reads another parser's output and never decides a cross-document question.
- **Gaps:** anything it can't parse goes to `extraction_issues`.
- **Templates:** one reader per drafting template, with AI vision as a flagged fallback.

**Shared services:** PDF text, coordinates and vector lines · title block and drawing register · sheet grid frame · identifier grammar and variant resolver · placeholder detector · claim writer.

### What each parser extracts
**PLC parser** (PLC drawings)
| Content | Output table | Model objects / claims |
|---|---|---|
| Rack layouts | `plc_racks.xlsx` | Panel → Rack → Slot |
| Modules | `plc_racks.xlsx` | Slot holds Module: part no, type (DI/DO/AI/AO), channel count |
| Slots, channels | `plc_points.xlsx` | Slot → Channel |
| Descriptions | `plc_points.xlsx` | Channel description, and device tag if shown |
| Safety references | `plc_points.xlsx` | Channel is safety-rated; what the reference points to |
| Route legends | `plc_routes.xlsx` | Route code → what it means (e.g. via which JB / panel) |

**CBD parser** (cable block diagrams)
| Content | Output table | Model objects / claims |
|---|---|---|
| Devices | `cbd_devices.xlsx` | Device occurrence on the CBD |
| Junction boxes | `cbd_devices.xlsx` | JB occurrence |
| Connectivity | `cbd_connections.xlsx` | Device → JB → Panel links, read from the drawn lines |
| Route information | `cbd_connections.xlsx` | Route code on each link |
| Cable specifications | `cable_specs.xlsx` | Cable No → type, cores, size |

**Termination parser** (termination drawings)
| Content | Output table | Model objects / claims |
|---|---|---|
| Wires, wire numbers | `terminations.xlsx` | Wire landed on a terminal |
| JB terminals | `terminations.xlsx` | `JB:terminal` |
| Device references | `terminations.xlsx` | Wire / terminal → device |
| PLC destinations | `terminations.xlsx` | Terminal → panel |
| Slot references | `terminations.xlsx` | Terminal → slot (and channel, if shown) |

### Which parser supplies each trace hop
| Hop | PLC | CBD | TERM | Can be cross-checked? |
|---|---|---|---|---|
| 1 Device | ○ description (tag if shown) | ● | ● device refs | Yes |
| 2 Wire | — | — | ● | **TERM only.** Format, grid and duplicate checks; plus its home sheet, if that drawing is in the package |
| 3 JB terminal | — | — | ● | **TERM only.** Internal checks only |
| 4 JB | — | ● | ● | Yes |
| 5 Destination panel | ● rack layout | ● | ● PLC destination | Yes, three ways |
| 6 PLC slot | ● | — | ● slot refs | Yes |
| 7 PLC channel | ● | — | ○ if slot refs show the channel | Partly |

● = primary source · ○ = partial source

Two things follow from this:
- **Hops 2 and 3 come only from termination drawings**, so the termination parser has the highest accuracy bar. An error there can't be caught by another drawing.
- **Linking a device to its PLC channel** depends on whether PLC drawings show the device tag on each channel. If they only show descriptions, the link goes through the termination drawing's slot reference, plus a description match marked AI-assisted.

### Linker
1. Load the claims from all three parsers.
2. Resolve every identifier to a canonical key using the shared grammar.
3. Merge objects with the same key into one node, and record which parsers mentioned it.
4. Join the links:
   - TERM gives device → wire → JB terminal → panel / slot.
   - CBD gives device → JB → panel.
   - PLC gives panel → rack → slot → channel.
   - Route codes tie PLC route legends to CBD route information.
5. Where parsers disagree, keep both claims on the link. That makes the trace Conflicted.
6. Objects one parser can't key, such as a PLC channel with only a description, are matched by description and marked AI-assisted / needs review.

## Signal trace model 🔗
The signal trace is the **primary business object**. Everything the platform extracts exists to build traces, and every finding links to one.

### The trace
| # | Hop | Example | Format | Main evidence |
|---|---|---|---|---|
| 1 | Device | `S-TN39N4` | `<Prefix>-<FFI><Sheet><Grid>` | Home occurrence on its sheet |
| 2 | Wire | `TN39N6` | `<FFI><Sheet><Grid>` | Wire number connected to the device on its home sheet; same wire number on the termination drawing |
| 3 | JB terminal | `SPL-JB1:1` | `<JB>:<Terminal>` | Termination drawing |
| 4 | Junction box | `SPL-JB1` | `<AREA>-JB<n>` | Implied by the terminal key; CBD |
| 5 | Destination panel | `LTL-PLC` | `<AREA>-PLC` *(to confirm)* | CBD (JB → panel cable); termination drawing |
| 6 | PLC slot | `DI Slot 16` | `<Type> Slot <n>` *(to confirm)* | PLC drawing |
| 7 | PLC channel | `Channel 05` | two digits *(to confirm)* | PLC drawing |

Each link between hops stores the claims that support it, their sources (drawing/sheet/rev/x-y), and whether the sources agree. Cables are stored on the links they carry: the field cable on device → JB, the multicore on JB → panel. They are not separate hops.

### Trace status
Two separate questions are asked of every trace:

**1. How complete is it?**
- **Complete**: all seven hops are found and linked end to end.
- **Partial**: starts from one end (a device or a PLC channel) but breaks before reaching the other. The trace records how far it got from each side and which link is missing.
- **Orphan**: connected to neither end. It's a fragment in the middle, for example a JB terminal with a wire landed but no device and no PLC channel, or a wire number that goes nowhere.

**2. Do the sources agree?**
- **Conflicted**: two drawings disagree about a link. For example, the PLC drawing puts `S-TN39N4` on DI 16 / 05 but the termination drawing takes `SPL-JB1:1` to DI 16 / 06.
- A trace can be both Complete and Conflicted.

Terminals and channels marked SPARE are not orphans. They're listed as spares.

### How traces are built
1. Start at every device's home occurrence and walk forward, hop by hop.
2. Start at every used PLC channel and walk backward.
3. Join the walks where they meet. A trace that reaches both ends is Complete.
4. Any remaining claims that aren't in a trace become Orphan fragments.
5. If one device reaches two channels, or two devices reach one channel, that's a branching finding.

**Trace ID** is the device tag where there is one (`S-TN39N4`). Otherwise it's the PLC address (`LTL-PLC/DI16/05`), or failing that the first hop found.

### Outputs
- `traces.xlsx`: the trace register. One row per trace with Trace ID, status, Conflicted flag, the value at each hop, the break point, and a findings count.
- `trace_hops.xlsx`: one row per link, with its sources and whether they agree.
- **Coverage**: % complete / partial / orphan per area, JB, panel and PLC slot.

## Engineering conventions (package standard)
Received 2026-10-21. These replace the earlier ISA-5.1 assumption.

### Identifier grammar
| Object | Format | Example | Parts |
|---|---|---|---|
| Device tag | `<Prefix>-<FFI><Sheet><Grid>` | `S-TN23N2` | S = device prefix · TN = first field identifier (FFI) · 23 = sheet · N2 = grid |
| Wire number | `<FFI><Sheet><Grid>` | `TN23N2` | TN = FFI · 23 = sheet · N2 = grid |
| Junction box | `<AREA>-JB<number>` | `LTL-JB1`, `RCL-JB2`, `SPL-JB1` | area code + JB number |
| Terminal | `<JunctionBox>:<Terminal>` | `SPL-JB1:1` | JB key + terminal number |
| Panel 🔗 *(from trace example, to confirm)* | `<AREA>-PLC` | `LTL-PLC` | area code + panel type |
| PLC address 🔗 *(to confirm)* | `<Type> Slot <n>` / `Channel <nn>` | `DI Slot 16`, `Channel 05` | card type + slot number; channel number |

### What this means for the model
1. **Every device and wire has a home location.** The tag says which sheet and grid cell it's drawn on. The occurrence there is the *home*. Every other occurrence (termination drawing, CBD, another PLC sheet) is a *reference* to it. So a reference to `S-TN23N2` can be checked against sheet 23 even with no drawn cross-reference.
2. **Readers must know each sheet's grid frame.** Detect the border zone labels per template so any x/y position maps to a grid cell like `N2`. Every occurrence gets a grid cell.
3. **Grid validation allows the collision shift.** A tag's grid should equal the device's physical grid. If it doesn't, the tag is still valid when that grid ID is already taken by another device and the tag is the next available grid (letter advances, digit stays: `TN15D2` → `TN15E2`). Anything else is a finding. The same check applies to wire numbers.
4. **Terminals are keyed by JB.** Terminal identity is `JB:terminal`, so terminal numbers must be unique within a JB. Strip is an attribute, not part of the key.
5. **JBs are keyed by area.** The area code must come from a known list (`config/`: LTL, RCL, SPL so far). A bare "JB1" on a drawing is resolved using the drawing's area and flagged as Minor.
6. **Drafting variants resolve to one key and are reported.** Covers case, spaces and missing or extra hyphens (`S TN23N2`, `SPL JB1`, `SPL-JB-1`).

### FFI → drawing number (confirmed 2026-10-21)
- A tag's or wire's sheet number only means something within its drawing. The **FFI decides which drawing**: `TN` → 401103, so `S-TN23N2` is drawn on 401103 sheet 023.
- An FFI can belong to a **separate package** we don't have, e.g. `TV` (`TV20T6` on sheet 015). Its references are listed in the report as a separate document, not checked.
- The mapping is in `config/project.toml` `[project.ffi_drawings]`. `TV` and `TH` are separate packages. An FFI not listed there is reported so it can be added.

### Relay auxiliary contacts (confirmed 2026-10-21)
- A relay coil's contact table lists where its contacts are used, as `<sheet>/<grid>` entries. For example, coil `K-TN19F1` on sheet 019 has `43/D4`.
- The auxiliary contact at that place must carry **exactly the coil's tag**: `K-TN19F1` near D4 on sheet 043, as sheet 042 does with `K-TN18F1`…`F8`.
- A contact labelled with the coil's sheet plus its own grid (`K-TN19D4`) is **wrong**. It points to a device that doesn't exist (AK-008).

### Placeholders
These always produce a finding (DRW-003):

`XXXX` · `XXX` · `TNXXXX` · `PXXXXX` · `CXXXXX` · `REF TERM XXX` · `HOLD` · `AUT1`

Matching is on whole tokens. It also catches any identifier whose sheet, grid or number part is a run of X (e.g. `S-TNXXXX`, `SPL-JBX`, `SPL-JB1:XX`). `HOLD` matches only as a standalone word, so `HOLDING` doesn't match. Descriptive uses such as "HOLD OPEN" are an open question below.

## Folder layout
```
Drafting Support/
├─ input/<Area>/        PDFs dropped here (PLC, CBD, TERM drawings; schedules later)
├─ extracted/<Area>/    Stage 1 outputs (Excel), reviewed by the checker
├─ reports/<Area>/      findings register + summary report
├─ config/              tag rules, document matrix, interface list, severities
└─ src/
   ├─ common/           PDF text/lines, title block, grid frame, identifier grammar, placeholders, claim schema
   ├─ parsers/plc/      PLC drawing parser
   ├─ parsers/cbd/      cable block diagram parser
   ├─ parsers/term/     termination drawing parser
   ├─ model/            entities, enums, SQLite schema, graph builder (see DATA_MODEL.md)
   ├─ linker/           joins claims into the common engineering model
   ├─ traces/           trace builder and classifier
   ├─ rules/            object and trace rules
   └─ tests/
```

## Stage 1 — Extract (PDF → claims)
| Source | Output | Key columns |
|---|---|---|
| All drawings (title blocks) | `drawing_register.xlsx` | Drawing No, Sheet, Rev, Title, Drawing type |
| All drawings (grid frame) | `sheet_grids.xlsx` | Drawing, Sheet, Grid labels, Cell boundaries |
| All drawings (all text) | `occurrences.xlsx` | Identifier, Kind, Parsed parts (Prefix/FFI/Sheet/Grid or Area/JB/Terminal), Drawing, Sheet, Rev, X/Y, Grid cell, Home or reference |
| PLC parser 🧩 | `plc_racks.xlsx` | Panel, Rack, Slot, Module part no, Module type, Channel count |
| PLC parser 🧩 | `plc_points.xlsx` | Panel, Rack, Slot, Channel, Device tag (if shown), Description, Safety reference, Route code |
| PLC parser 🧩 | `plc_routes.xlsx` | Route code, Legend text, Via JB / panel |
| CBD parser 🧩 | `cbd_devices.xlsx` | Tag, Kind (device / JB), Drawing, Sheet, Grid cell |
| CBD parser 🧩 | `cbd_connections.xlsx` | From, To, Cable No, Route code |
| CBD parser 🧩 | `cable_specs.xlsx` | Cable No, Type, Cores, Size |
| Termination parser 🧩 | `terminations.xlsx` | JB, Terminal, Wire No, Device ref, Cable, Core, PLC destination panel, Slot ref, Channel (if shown) |
| Everything above | `claims.xlsx` | Claim ID, Subject, Relation, Object, Source drawing/sheet/rev, X/Y, Confidence (Deterministic / AI) |
| *Future:* I/O list, instrument index, vendor data | `io_list.xlsx`, `instrument_index.xlsx`, … | Extra claims for the same model |

Every extraction checks itself before it's used: row counts per page, every tag matched against the tag format, wrapped description lines joined, and an `extraction_issues` sheet for anything it couldn't parse.

## Stage 2 — Model, trace and check
- **Matching identifiers**: every identifier is parsed with the package grammar (see *Engineering conventions*). Drafting variants resolve to one key.
- **Home vs reference**: each device and wire tag is linked to its home occurrence (the sheet and grid the tag encodes). All other occurrences become references to it.
- **Document matrix**: which object type should appear in which drawing type (e.g. a field device should be on the CBD, a termination drawing and a PLC sheet).
- **System graph**: claims merged into nodes and edges. Where sources disagree, both claims stay on the edge.
- **Trace builder** 🔗: builds and classifies every signal trace (see *Signal trace model*). Outputs `traces.xlsx`, `trace_hops.xlsx` and coverage.

### Rule catalogue
> Superseded by IMPLEMENTATION_PLAN.md §7.3 (category-based IDs). Kept for history.

| ID | Rule | Compares | Error class |
|---|---|---|---|
| CMP-001 | Device appears in every drawing type it should (document matrix) | PLC ↔ CBD ↔ TERM | Missing references |
| CMP-002 | Cable is on the CBD and in the terminations at both ends | CBD ↔ TERM | Missing references |
| CMP-003 | Orphans (JB, PLC point, device, cable appearing in only one source) | all | Missing references |
| RTE-001 🆕 | Cable From/To/Via on the CBD matches the routing rebuilt from terminations | CBD ↔ TERM | Inconsistent routing |
| RTE-002 🆕 | Multicore pairs through a JB connect the same field cable to the same home-run core | CBD ↔ TERM | Inconsistent routing |
| RTE-003 🧩 | A route code in a PLC route legend exists in the CBD route information and matches the trace's JB → panel path | PLC ↔ CBD | Inconsistent routing |
| JB-001 🆕 | JB identity matches across sources (name, strips, cable entries) | CBD ↔ TERM | JB inconsistencies |
| JB-002 🆕 | Every cable entering a JB on the CBD is terminated in that JB | CBD ↔ TERM | JB inconsistencies |
| JB-003 📐 | JB name matches `<AREA>-JB<n>`, area code is in the area list, and the same JB isn't written differently | occurrences | JB inconsistencies |
| PLC-001 | Channel → tag matches | PLC ↔ TERM *(was io_list ↔ plc_points)* | PLC assignment errors |
| PLC-002 | Description matches (exact match, then AI meaning match) | PLC ↔ TERM ↔ CBD *(was io_list ↔ plc_points)* | Device inconsistencies |
| PLC-003 | No duplicate channel allocation; rack/slot consistent | PLC ↔ TERM *(was io_list ↔ plc_points)* | PLC assignment errors |
| PLC-004 | Safety marker consistent on safety I/O | PLC ↔ TERM *(was io_list ↔ plc_points)* | PLC assignment errors |
| PLC-005 | Channels used don't exceed the module's channel count from the rack layout | PLC | PLC assignment errors |
| PLC-006 🧩 | Module type matches the slot reference and signal (e.g. a DI slot on the termination drawing is a DI module in the rack layout) | PLC ↔ TERM | PLC assignment errors |
| PLC-007 🧩 | Safety reference resolves to an existing drawing / sheet | PLC ↔ drawing_register | Cross-reference errors |
| TRC-001 🔗 | Partial trace; the finding names the missing link | traces | Missing signal traces |
| TRC-002 🔗 | Orphan fragment (no device and no PLC channel) | traces | Missing signal traces |
| TRC-003 🔗 | Conflicted link: sources disagree | trace_hops | PLC assignment / routing / JB, depending on the link |
| TRC-004 🔗 | Branching: one device on two channels, or two devices on one channel | traces | PLC assignment errors |
| TRC-005 🔗 | Wire number at the device matches the wire landed on the JB terminal | trace_hops | Missing signal traces |
| TRC-006 🔗 | The destination panel is the panel that holds that PLC slot | trace_hops | PLC assignment errors |
| CBL-001 | Duplicate cable numbers | occurrences | Device inconsistencies |
| CBL-002 | Core count vs terminated cores | CBD ↔ TERM *(was cable_schedule ↔ terminations)* | Inconsistent routing |
| CBL-003 | Cable type/size consistent | CBD ↔ TERM *(was From/To vs cable_schedule; From/To is now RTE-001)* | Device inconsistencies |
| CBL-004 | Spare cores documented | TERM | Missing references |
| TERM-001 | Duplicate or missing terminal numbers | TERM | Missing references |
| TERM-002 | Every terminal has a device/cable allocation | TERM | Missing references |
| TERM-003 📐 | Terminal references use `<JB>:<Terminal>`, the JB exists, and the terminal exists on that JB's termination drawing | all ↔ TERM | Cross-reference errors |
| DEV-001 🆕 | Same tag has the same type, signal and range in every source | all | Device inconsistencies |
| TAG-001 📐 | Device tag matches `<Prefix>-<FFI><Sheet><Grid>`, with a known prefix and FFI *(was ISA-5.1)* | occurrences | Device inconsistencies |
| TAG-002 📐 | Tag's sheet and grid match where the device is actually drawn (home occurrence) | occurrences ↔ sheet_grids | Device inconsistencies |
| TAG-003 📐 | Where the grid doesn't match, it's a valid collision shift (grid taken, tag is next available) | occurrences ↔ sheet_grids | Device inconsistencies |
| TAG-004 📐 | Each device tag has exactly one home occurrence (none = missing device, two or more = duplicate) | occurrences | Device inconsistencies |
| WIR-001 📐 | Wire number matches `<FFI><Sheet><Grid>` | occurrences | Device inconsistencies |
| WIR-002 📐 | Wire number's sheet and grid match where the wire starts, allowing the collision shift | occurrences ↔ sheet_grids | Device inconsistencies |
| WIR-003 📐 | Duplicate wire numbers | occurrences | Device inconsistencies |
| DRW-001 | Cross-references point to existing drawings/sheets/revs | occurrences ↔ drawing_register | Cross-reference errors |
| DRW-002 | Revision consistency | drawing_register | Cross-reference errors |
| DRW-003 📐 | Placeholder text: `XXXX`, `XXX`, `TNXXXX`, `PXXXXX`, `CXXXXX`, `REF TERM XXX`, `HOLD`, `AUT1`, and identifiers with X-run parts | occurrences | Placeholder text |
| DRW-004 📐 | A tag referenced on any drawing exists at the home sheet and grid its tag encodes | occurrences ↔ drawing_register | Cross-reference errors |

🆕 = new in the drawing-led version. 📐 = comes from the package conventions. 🔗 = trace rule; findings attach to the trace. 🧩 = needs data from the dedicated parsers. *(was …)* = re-pointed from a schedule comparison. Schedule-based checks come back as extra comparisons when the I/O list and cable schedule readers are added.

### Finding record
Finding ID · Trace ID 🔗 · Link/hop 🔗 · Rule ID · Severity (Critical/Major/Minor) · Confidence (Deterministic / AI-assisted) · Object · Evidence A (source + claim) · Evidence B (source + claim) · Drawing/Sheet/Rev · Recommendation · Status · Owner · Comments

## Phases
| # | Phase | Deliverable | Done when |
|---|---|---|---|
| 0 | **Setup and feasibility** | Sample area package; vector/scan check; template survey per drawing type; **CBD line-tracing spike** on 2–3 sheets; grid frame layout per template; QA checklist; conventions questions answered; one checker-reviewed package as the answer key; native CAD availability confirmed | We know the PDFs have extractable text and lines, and how many templates we need readers for |
| 1 | **Register + occurrence index + Level 1 checks** | `drawing_register`, `sheet_grids`, `occurrences` (with grid cells); DRW, TAG, WIR, JB-003, CBL-001 rules; first findings register | Grid cells agree with a manual check on sample sheets; real placeholder, grid and cross-ref findings on the sample package; false alarms reviewed |
| 2 | **Trace slice** 🔗 | 5–10 signals through one JB traced end to end, using minimal PLC, TERM and CBD parsers and a first version of the linker; trace model, builder, `traces.xlsx`, `trace_hops.xlsx` | Built traces match the checker's hand traces for those signals, including at least one deliberately broken one |
| 3 | **Termination parser** 🧩 | Full `terminations.xlsx`; linker joins TERM claims; TERM, TERM-003, CBL-004 rules. Goes first because it supplies the most hops and is the only source for wires and JB terminals | Spot-check finds no dropped or mangled rows |
| 4 | **PLC parser** 🧩 | `plc_racks`, `plc_points`, `plc_routes` (rack layouts, modules, safety refs, route legends); linker joins PLC claims; PLC-001 to 007 | All PLC findings in the answer key caught |
| 5 | **CBD parser** 🧩 | `cbd_devices`, `cbd_connections` (routing from vector lines), `cable_specs`; linker joins CBD claims; CMP-002, CBL, JB, RTE-003 rules | All cable/JB/route findings in the answer key caught |
| 6 | **Traces at area scale** 🔗 | All traces for the area; TRC, RTE, DEV, CMP rules; coverage report | Coverage report produced; trace findings checked against the answer key |
| 7 | **AI layer** | Claude reads sheets the readers can't parse; description meaning matching | AI findings marked for review; acceptance rate tracked |
| 8 | **Reporting + revisions** | Word/Excel area report with the trace register up front; Rev A vs Rev B trace comparison | Report usable as-is by the checker |
| 9 | **More sources** | I/O list, instrument index, vendor data readers → extra claims; schedule ↔ drawing rules | New sources add findings without changes to model or rules |
| 10 | **Platform (optional)** | SharePoint input/output, Power BI over the trace register and findings | Team runs it without me |

## Technology
- **Python**: `PyMuPDF` / `pdfplumber` (text, positions and vector line segments from the PDF), `pydantic` (claim and entity schema), `networkx` (engineering graph, rebuilt per run), `SQLite` (system of record, one file per project) with `DuckDB`/pandas for analysis, `pandas` + `openpyxl` (Excel review outputs), `pytest` (rule tests).
- **Claude API**: extraction from sheet images where the readers fail; description meaning matching.
- **Later**: SharePoint / Dataverse / Power BI if a Microsoft 365 rollout is wanted.

## Risks
| Risk | Mitigation |
|---|---|
| PDFs are scanned, not vector | Check in Phase 0; fall back to Claude reading the sheet images, with lower confidence |
| CBD lines can't be traced reliably (broken segments, crossings, off-sheet arrows) 🆕 | Phase 0 spike; fall back to labels next to each other plus off-sheet reference arrows; native CAD if available |
| No master source, so a finding can't say which drawing is wrong 🆕 | Report both sides with evidence; checker decides; add schedules in Phase 8 as a tie-breaker |
| Sheet templates vary between projects or vendors | One reader per template; Claude as fallback |
| Too many false alarms reduce trust | Answer-key testing; tune before rollout; mark AI findings for review |
| Interfaces with other areas look like orphans | Interface list in `config/` |
| Drafting variants of identifiers | Parser based on the package grammar; variants resolved and reported as Minor |
| Grid frame can't be read, or differs between templates 📐 | Grid layout stored per template in `config/`; flag sheets where the frame isn't found |
| Grid text sits on a cell boundary, giving false TAG-002 hits 📐 | Use the device symbol's position, not the label's; allow a tolerance band at boundaries |

## Open questions
1. **Is native CAD available** (AutoCAD Electrical project DB, EPLAN, DWG with attributes)? It would be the biggest single win.
2. **One drafting house and title block**, or several templates per drawing type?
3. **What are the files already here?** `Terms/401103-2xx` look like termination drawings. Are `401310-014…016` CBDs or PLC sheets, and what is `SchematicSet.pdf`? Are they all the same area?
4. **Is there a checker-redlined package** to use as the answer key?

### Conventions questions
5. **FFI:** what are the valid FFIs and what does each mean (drawing type, area, discipline)? Is `TN` tied to one drawing series?
6. **Sheet number:** how does `23` map to a drawing number (e.g. `401103-223`, or sheet 23 of a multi-sheet drawing)? Is it always two digits?
7. **Grid:** what do the letter and digit each mark (column or row)? Are any letters skipped (I, O)? When the letter runs out during a collision shift, what comes next?
8. **Prefixes:** what is the list of valid device prefixes (e.g. what does `S` cover)?
9. **Missing conventions:** what are the formats for cable numbers, PLC addresses (rack/slot/channel), and panel or marshalling terminals? The `CXXXXX` and `PXXXXX` placeholders suggest cable and PLC formats exist.
10. **HOLD / AUT1:** can `HOLD` appear legitimately in descriptions ("HOLD OPEN")? Is `AUT1` a template default, and are there siblings (`AUT2`)?
11. **Wire vs device:** can a wire number be the same as a device tag's suffix (wire `TN23N2` next to device `S-TN23N2`)?

### Trace questions 🔗
12. **Definitions:** do *Partial* (reaches one end) and *Orphan* (reaches neither end) match how you'd use the terms?
13. **Panel side:** between the JB multicore and the PLC channel, are there panel terminals, marshalling or interposing relays? Should they be hops in the trace?
14. **Cables:** is it right to store cables on the links (field cable on device → JB, multicore on JB → panel) rather than as hops?
15. **Multi-wire signals:** analog +/−, shields, a DI with a common. Should that be one trace per signal with several conductors (my recommendation), or one trace per conductor?
16. **Panels and PLC addresses:** is the panel format `<AREA>-PLC`, and are there other panel types (MCC, RIO)? Are there racks as well as slots? Which card types besides DI (DO, AI, AO)?
17. **Wire location:** is a device's wire always on the device's own sheet (`S-TN39N4` → `TN39N6`, both sheet 39)?

### Parser questions 🧩
18. **Routes:** what is a route code? What does a PLC route legend entry look like, and is the same code used in the CBD route information?
19. **PLC channels:** do PLC drawings show the device tag on each channel, or only the description? This decides how strongly PLC links to the other two parsers.
20. **Device home sheets:** which drawing type holds sheet 39 for `S-TN39N4`? Is it a PLC drawing, or a schematic set outside these three types (e.g. `SchematicSet.pdf`)?
21. **Slot references:** do termination drawings give rack and channel as well as slot?
22. **Safety references:** what do they point to (a safety drawing, a SIF number, a cause & effect)?
23. **Rack layouts:** can a panel have more than one rack? Is slot numbering per rack?

## Immediate next steps
1. **You:** answer the open questions above, and put one area's PLC, CBD and termination drawings into `input/Main/` (the PDFs at the top level and in `Terms/` can move there if they're the same area).
2. **You:** share the QA checklist, the checker's redlines if available, and answers to the conventions questions (FFI list, sheet mapping, grid axes, prefixes, cable/PLC formats).
3. **You:** pick 5–10 signals through one JB (e.g. `SPL-JB1`) and write down their full traces. These are the answer key for the Phase 2 trace slice.
4. **Me:** Phase 0: vector/scan check, template survey, CBD line-tracing spike.
5. **Me:** Phase 1: drawing register + occurrence index → first placeholder and cross-reference findings.
6. **Me:** Phase 2: trace slice on your hand-traced signals.
