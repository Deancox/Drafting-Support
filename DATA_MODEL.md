# Controls Drawing QA Platform — Data Model

Companion to [GAME_PLAN.md](GAME_PLAN.md). Drafted 2026-10-21. Design only, no code.

## 1. Design principles
1. **Evidence is separate from belief.** Parsers write immutable **claims** ("TERM 401103-203 sh2 says wire TN39N6 lands on SPL-JB1:1"). The linker turns claims into the **resolved model**. A resolved node or edge always links back to the claims behind it, and it never exists without at least one claim or a named derivation.
2. **Conflicts are data, not errors.** Two claims that disagree both stay in the model. A conflict is detected as a broken cardinality rule (e.g. one terminal assigned to two channels), not by overwriting one claim with the other.
3. **Runs are immutable snapshots.** Every model row belongs to one run of one package. Re-running never mutates an old run, so Rev A vs Rev B is a diff between two runs.
4. **Identity is a canonical key, stable across runs.** Rows get surrogate IDs. Cross-run identity (for revision diffs and carrying checker decisions forward) uses the canonical key from the package grammar.
5. **Relational store is the system of record; the graph is a derived index.** Tables hold the truth and support review, export and audit. The in-memory graph is rebuilt from them per run for traversal and trace building.
6. **One graph, two layers.** The **routing layer** (PathNodes joined by Routes, e.g. `SPL-JB1 → LTL-PLC`) and the **connectivity layer** (Device → Wire → Terminal → PLC Channel) live in one graph. The routing layer is a filtered view of it.

## 2. Layers
```
 ┌────────────────────────────────────────────────────────────────────────┐
 │ SCOPE        Project → Area → Package → Run                            │
 ├────────────────────────────────────────────────────────────────────────┤
 │ SOURCE       Drawing (sheet + rev) · SourceRef (where on the sheet)    │
 ├────────────────────────────────────────────────────────────────────────┤
 │ EVIDENCE     Claim (immutable, one fact, one SourceRef, one parser)    │
 ├────────────────────────────────────────────────────────────────────────┤
 │ RESOLVED     Nodes: Device, Wire, PathNode (JunctionBox, Panel, Field  │
 │ MODEL        endpoint), Terminal, PLCModule, PLCChannel                │
 │ (graph)      Edges: typed relationships incl. Route; each ↔ Claims     │
 ├────────────────────────────────────────────────────────────────────────┤
 │ ANALYSIS     SignalTrace → TraceLeg → TraceHop · QARule · RuleRun ·    │
 │              Finding ↔ FindingEvidence                                 │
 ├────────────────────────────────────────────────────────────────────────┤
 │ REVIEW       FindingReview (checker decision) · Waiver                 │
 └────────────────────────────────────────────────────────────────────────┘
```

## 3. Identity and canonical keys
Each row has a surrogate `id` (ULID: sortable and unique without a central counter). Each resolved entity also has a `canonical_key` that's unique within (run, entity type). In the graph, node IDs are typed as `<type>:<canonical_key>` so a device and its field endpoint can share a tag without colliding.

| Entity | Canonical key | Example | Graph node ID |
|---|---|---|---|
| Device | `<Prefix>-<FFI><Sheet><Grid>` | `S-TN39N4` | `dev:S-TN39N4` |
| Wire | `<FFI><Sheet><Grid>` | `TN39N6` | `wire:TN39N6` |
| PathNode — JunctionBox | `<AREA>-JB<n>` | `SPL-JB1` | `pn:SPL-JB1` |
| PathNode — Panel | `<AREA>-PLC` *(to confirm)* | `LTL-PLC` | `pn:LTL-PLC` |
| PathNode — Field endpoint | device tag | `S-TN39N4` | `pn:S-TN39N4` |
| Terminal | `<PathNode>:<Terminal>` | `SPL-JB1:1` | `term:SPL-JB1:1` |
| PLCModule | `<Panel>/R<rack>/S<slot>` | `LTL-PLC/R1/S16` | `mod:LTL-PLC/R1/S16` |
| PLCChannel | `<Module>/CH<nn>` | `LTL-PLC/R1/S16/CH05` | `ch:LTL-PLC/R1/S16/CH05` |
| Route | cable no, else route code, else `<from>><to>` | `C12345` / `SPL-JB1>LTL-PLC` | edge, not node |
| SignalTrace | device key, else channel key, else first hop | `S-TN39N4` | — |
| Finding | fingerprint (see §8) | `PLC-001:…:9f3a` | — |

Rack defaults to `R1` until rack layouts show more than one rack (question 23 in the game plan).

The key is only ever produced by the shared identifier grammar. Raw spellings (`SPL JB1`, `SPL-JB-1`) are kept on the claim as `raw_text`, never in the key.

## 4. Entities

### 4.1 Scope
| Entity | Key fields | Notes |
|---|---|---|
| **Project** | id, code, name, standards profile | Holds the config: tag grammar, area list, placeholder list, severities |
| **Area** | id, project_id, code (`LTL`, `RCL`, `SPL`, `Main`…) | Functional area under QA |
| **Package** | id, area_id, name, received_at | One set of input PDFs |
| **Run** | id, package_id, started_at, finished_at, status, code_version, config_hash, input_manifest (file name + SHA-256) | Immutable once finished. Everything below is scoped by `run_id` |

### 4.2 Source
| Entity | Key fields | Notes |
|---|---|---|
| **Drawing** | id, run_id, file_sha256, page_index, drawing_no, sheet, rev, title, doc_type (PLC / CBD / TERM / OTHER), area, is_vector, grid_frame (column/row labels and boundaries) | One row per sheet per revision. `grid_frame` maps x/y → grid cell |
| **SourceRef** | id, drawing_id, bbox (x0, y0, x1, y1 in PDF points), grid_cell, raw_text | An exact place on a sheet. Every claim and every piece of finding evidence points to one |

### 4.3 Evidence
| Entity | Key fields | Notes |
|---|---|---|
| **Claim** | id, run_id, parser (PLC / CBD / TERM / future), parser_version, method (TEMPLATE / VECTOR / AI_VISION / AI_MATCH), confidence (0–1), subject_type, subject_key, predicate, object_type, object_key, value (JSON), source_ref_id | **Immutable.** One fact from one place. `subject_key`/`object_key` are canonical; spelling stays in `SourceRef.raw_text` |

Predicate catalogue (claims → edges):

| Predicate | Subject → Object | Parsers | Becomes edge |
|---|---|---|---|
| `DRAWN_AT` | Device/Wire → SourceRef | all | home or reference occurrence (attribute) |
| `CONNECTS` | Device → Wire | TERM, (PLC sheet if home) | `DEVICE_WIRE` |
| `LANDS_ON` | Wire → Terminal | TERM | `WIRE_TERMINAL` |
| `REFERS_TO_DEVICE` | Terminal → Device | TERM | supports `DEVICE_WIRE` / trace |
| `DESTINED_FOR` | Terminal → PLCChannel or PLCModule | TERM | `TERMINAL_CHANNEL` |
| `DESTINED_PANEL` | Terminal → PathNode(Panel) | TERM | supports `ROUTE` |
| `ROUTED` | PathNode → PathNode (+ cable, route code) | CBD | `ROUTE` |
| `HAS_SPEC` | Route(cable) → spec value | CBD | Route attributes |
| `INSTALLED_IN` | PLCModule → PathNode(Panel) (+ rack, slot) | PLC | `MODULE_PANEL` |
| `HAS_CHANNEL` | PLCModule → PLCChannel | PLC | `CHANNEL_MODULE` |
| `DESCRIBES` | PLCChannel → text (+ device tag if shown) | PLC | Channel attribute; `CHANNEL_DEVICE` if a tag is shown |
| `SAFETY_REF` | PLCChannel → reference | PLC | Channel attribute |
| `ROUTE_LEGEND` | route code → meaning | PLC | Route attribute (cross-check vs CBD) |
| `CROSS_REF` | SourceRef → drawing/sheet | all | checked against Drawing |
| `PLACEHOLDER` | SourceRef → token | all | goes straight to a finding |

### 4.4 Resolved model — nodes
Every node table shares: `id, run_id, canonical_key, status, first_claim_id`. `status` is one of CORROBORATED (≥2 parsers), SINGLE_SOURCE, CONFLICTED, DERIVED (inferred by the linker, e.g. a JB implied by a terminal key), PLACEHOLDER.

| Entity | Own fields | Notes |
|---|---|---|
| **Device** | prefix, ffi, sheet, grid, home_source_ref_id, description, device_type | Home = occurrence at the sheet and grid its tag encodes; TAG-002/003 compare against it |
| **Wire** | ffi, sheet, grid, home_source_ref_id, conductor_role (signal / common / shield, if known) | Same location logic as Device |
| **PathNode** | kind (JUNCTION_BOX / PANEL / FIELD_ENDPOINT / MARSHALLING), area, description | **Routing-layer node.** `LTL-PLC`, `LTL-JB1`, `LTL-JB2`, `RCL-JB1` are PathNodes. A field device on the CBD gets a FIELD_ENDPOINT PathNode linked 1:1 to its Device, so every route runs PathNode → PathNode |
| **JunctionBox** | path_node_id (1:1), area_code, jb_number, terminal_count | Extension of PathNode where kind = JUNCTION_BOX |
| **Terminal** | path_node_id, terminal_no, strip (attribute), is_spare | Belongs to a JB (or later a panel). Key is `JB:terminal` |
| **PLCModule** | path_node_id (panel), rack, slot, module_type (DI / DO / AI / AO / …), part_no, channel_count | From rack layouts |
| **PLCChannel** | module_id, channel_no, description, signal_type, is_safety, safety_ref, is_spare | From PLC drawings; may be confirmed by TERM slot references |

PathNode is the routing location; JunctionBox (and a future Panel extension) holds the type-specific columns. This class-table inheritance keeps the routing graph uniform while each type keeps typed columns.

### 4.5 Resolved model — edges
A single **Edge** table, so every relationship (including routes) has an ID, attributes, evidence and a status, and findings can point at it.

| Field | Notes |
|---|---|
| id, run_id | |
| edge_type | from the catalogue below |
| src_node_id, dst_node_id | stored direction is **field → control** (device side to PLC side) whatever the signal direction; traversal works both ways |
| attrs (JSON) | type-specific: cable_no, route_code, cores, size, core_no, device terminal, rack/slot… |
| status | CORROBORATED / SINGLE_SOURCE / CONFLICTED / DERIVED |
| parsers | which parsers support it (e.g. `{CBD, TERM}`) |

**EdgeClaim** (edge_id, claim_id, role SUPPORTS / DERIVED_FROM) links each edge to its evidence.

Edge type catalogue, with the cardinality rules that detect conflicts:

| Edge type | From → To | Layer | Cardinality rule | Broken rule means |
|---|---|---|---|---|
| `DEVICE_WIRE` | Device → Wire | connectivity | a wire starts at ≤ 1 device | Wire claimed by two devices |
| `WIRE_TERMINAL` | Wire → Terminal | connectivity | a field wire lands on ≤ 1 JB terminal | Same wire on two terminals |
| `TERMINAL_IN` | Terminal → PathNode | containment | exactly 1 (derived from the key) | — |
| `TERMINAL_CHANNEL` | Terminal → PLCChannel | connectivity | ≤ 1 channel per terminal; ≤ 1 signal terminal per channel | PLC assignment conflict / branching |
| `CHANNEL_DEVICE` | PLCChannel → Device | connectivity (PLC claim) | ≤ 1 device per channel | Two devices on one channel |
| `CHANNEL_MODULE` | PLCChannel → PLCModule | containment | exactly 1 | — |
| `MODULE_PANEL` | PLCModule → PathNode(Panel) | containment | exactly 1; ≤ 1 module per (panel, rack, slot) | Two modules in one slot |
| `DEVICE_ENDPOINT` | Device ↔ PathNode(FIELD_ENDPOINT) | containment | 1 : 1 | — |
| `ROUTE` | PathNode → PathNode | **routing** | cable no unique; a cable has exactly one from and one to | CBD and TERM disagree on a cable's ends |

A broken cardinality rule doesn't stop the build. Both edges stay, both get status CONFLICTED, and the trace builder explores both alternatives.

### 4.6 Analysis
| Entity | Key fields | Notes |
|---|---|---|
| **SignalTrace** | id, run_id, trace_key, anchor_type (DEVICE / CHANNEL / NONE), anchor_node_id, completeness (COMPLETE / PARTIAL / ORPHAN / SPARE), conflicted (bool), break_after_hop, break_before_hop, route_path (ordered Route edge IDs), findings_count | **Primary business object.** `route_path` is the physical path through the routing layer. It may pass through more than one JB (`RCL-JB1 → LTL-JB2 → LTL-PLC`) even though the logical trace shows one JB → panel hop |
| **TraceLeg** | id, trace_id, conductor_index, conductor_role | One per conductor. A single-wire DI has one leg; an analog +/− pair has two. This works whichever way trace question 15 is answered |
| **TraceHop** | id, leg_id, seq (1–7), hop_type (DEVICE / WIRE / JB_TERMINAL / JB / PANEL / SLOT / CHANNEL), node_id (null if missing), edge_in_id, status (FOUND / MISSING / CONFLICTED / SINGLE_SOURCE), alternatives (node IDs, when conflicted) | The value at each hop and how well it's supported |
| **QARule** | rule_id (`PLC-001`), version, title, description, error_class (the 8 classes), scope (OBJECT / LINK / TRACE / DRAWING), compares (e.g. `PLC↔TERM`), default_severity, parameters (JSON, e.g. placeholder list), enabled, owner | Rule metadata lives here. The rule logic is tested code registered under the same `rule_id` + `version` |
| **RuleRun** | run_id, rule_id, rule_version, started_at, duration_ms, findings_count, error | Audit of what ran, which version, and whether it failed |
| **Finding** | id, run_id, fingerprint, rule_id, rule_version, severity, confidence (DETERMINISTIC / AI_ASSISTED), error_class, subject_type, subject_id, trace_id, hop_seq, message, recommendation, status | Message is generated from a template with the canonical keys, so it's the same every run |
| **FindingEvidence** | finding_id, role (A / B / CONTEXT), claim_id or source_ref_id, note | Every finding has at least one. "Evidence A vs B" in the register comes from here |

### 4.7 Review
| Entity | Key fields | Notes |
|---|---|---|
| **FindingReview** | id, fingerprint, decision (OPEN / CONFIRMED / FALSE_POSITIVE / FIXED / WAIVED / DEFERRED), reviewer, decided_on (as entered), comment, recorded_in_run, recorded_at | Keyed by **fingerprint**, not finding ID, so a decision carries forward to later runs and revisions. Built in M5 as the append-only `reviews` table of `qa_review.sqlite`; the checker enters decisions in `QA_Register.xlsx` |
| **Waiver** | id, rule_id (pattern), scope (subject key pattern), reason, approved_by, expires | Known exceptions, e.g. interfaces to other areas that look like orphans. Built in M5 as `config/waivers.toml` (version-controlled with the config, not in SQLite); the checker's own decision on a finding takes precedence |

Checker decisions marked CONFIRMED and FALSE_POSITIVE also become the answer key for rule regression tests.

## 5. The engineering graph (internal representation)
- **Structure:** one directed multigraph per run (`networkx.MultiDiGraph`). Node ID = typed canonical key (`pn:LTL-JB1`). Edge key = Edge.id, so parallel edges (conflicting claims, or two cables on the same route) stay separate. Node and edge attributes carry `entity`, `status`, `parsers` and the row ID back into the tables.
- **Routing layer:** the subgraph of PathNodes and `ROUTE` edges. For the examples:
  ```
  pn:RCL-JB1 ──ROUTE(C…)──▶ pn:LTL-JB2 ──ROUTE(C…)──▶ pn:LTL-PLC
                pn:LTL-JB1 ──ROUTE(C…)──────────────▶ pn:LTL-PLC
  pn:S-TN39N4 (field endpoint) ──ROUTE(field cable)──▶ pn:SPL-JB1 ──ROUTE──▶ pn:LTL-PLC
  ```
  It answers routing questions (is there a path from `SPL-JB1` to `LTL-PLC`? through which JBs? which cables?) independently of individual signals.
- **Connectivity layer:** Device, Wire, Terminal, PLCModule and PLCChannel nodes plus their edges. Terminals hang off their PathNode (`TERMINAL_IN`) and modules off their panel (`MODULE_PANEL`), so the two layers meet at the PathNodes.
- **Trace walking is typed, not shortest-path.** The builder follows a fixed hop sequence: Device → `DEVICE_WIRE` → Wire → `WIRE_TERMINAL` → Terminal → `TERMINAL_IN` → JB → *routing path* → Panel → `MODULE_PANEL` → Module → `CHANNEL_MODULE` → Channel. The JB → Panel step is a path search in the routing layer, cross-checked against the terminal's `TERMINAL_CHANNEL` destination. A wrong node type stops the walk and marks the hop MISSING. A CONFLICTED edge forks the walk, and the trace is flagged conflicted with both alternatives recorded.
- **Rebuilt, not persisted:** the graph is built from the Edge and node tables at the start of analysis and thrown away afterwards. Traces and findings are written back to the tables. Nothing lives only in the graph.

## 6. Example: one trace through the model
`S-TN39N4 → TN39N6 → SPL-JB1:1 → SPL-JB1 → LTL-PLC → DI Slot 16 → Channel 05`

| Hop | Node | Incoming edge | Claims behind it |
|---|---|---|---|
| 1 | `dev:S-TN39N4` | — | CBD device occurrence; TERM device ref; home occurrence on sheet 39 at N4 |
| 2 | `wire:TN39N6` | `DEVICE_WIRE` | TERM (wire with device ref); home sheet 39 at N6 if in the package |
| 3 | `term:SPL-JB1:1` | `WIRE_TERMINAL` | TERM |
| 4 | `pn:SPL-JB1` | `TERMINAL_IN` (DERIVED from key) | key; CBD JB occurrence |
| 5 | `pn:LTL-PLC` | routing path `SPL-JB1 → LTL-PLC` | CBD `ROUTED`; TERM `DESTINED_PANEL`; PLC rack layout |
| 6 | `mod:LTL-PLC/R1/S16` | `MODULE_PANEL` | PLC rack layout (DI module); TERM slot ref "DI Slot 16" |
| 7 | `ch:LTL-PLC/R1/S16/CH05` | `CHANNEL_MODULE` + `TERMINAL_CHANNEL` | PLC channel row; TERM destination (if channel shown) |

If every hop is FOUND, the trace is COMPLETE. If the PLC drawing also says channel 05 describes a different device, `CHANNEL_DEVICE` breaks its cardinality rule, the trace is flagged conflicted, and TRC-003 raises a finding with the PLC claim as evidence A and the TERM claim as evidence B.

## 7. Integrity rules (enforced on every run)
1. Canonical key unique per (run, entity type).
2. Every resolved node and edge has ≥ 1 claim, or status DERIVED with the derivation recorded in EdgeClaim.
3. Every claim has exactly one SourceRef. Every SourceRef has a Drawing.
4. Every finding has ≥ 1 FindingEvidence, a rule_id and a rule_version.
5. Claims, finished runs and their findings are never updated. Corrections produce a new run.
6. Cardinality breaks become CONFLICTED status plus a finding, never a load failure.
7. Enum values (doc_type, parser, status, completeness, severity, decision) come from one shared definition used by both the store and the code.

## 8. Finding fingerprints
`fingerprint = hash(rule_id + sorted canonical keys of the subject and evidence objects + discriminator)`, where the discriminator is a rule-specific value such as the conflicting channel numbers.

- It excludes run IDs, row IDs, coordinates and rev, so the same problem has the same fingerprint in Rev A and Rev B.
- **Revision comparison:** fingerprints in A but not B are *resolved*, in B but not A are *new*, in both are *still open*.
- **Review carry-forward:** a FALSE_POSITIVE decision on a fingerprint suppresses it in later runs (shown, greyed, with the reviewer's comment) until the evidence changes.

## 9. Storage and technology
| Concern | Choice | Why |
|---|---|---|
| System of record | **SQLite**, one file per project | Single portable file, transactional review updates, no server. Schema stays portable to Postgres if the platform phase needs multi-user |
| Analysis | pandas / DuckDB reading the same tables | Fast joins for rules and coverage |
| Graph | networkx MultiDiGraph rebuilt per run | Typed traversal, path search in the routing layer, cheap at drawing-package scale (thousands to tens of thousands of nodes) |
| Schema in code | pydantic models + shared enums mirroring the tables | One definition for validation, storage and Excel export |
| Checker outputs | Excel views (`traces.xlsx`, `trace_hops.xlsx`, findings register) generated from the tables | Excel is a view, never the system of record |
| Migrations | versioned schema migrations, schema version stored on each Run | Old runs stay readable after model changes |

## 10. Extending the model
- **New source** (IO list, instrument index, vendor data): add a parser enum value and its predicates. The claims flow into existing nodes and edges, and new rules compare the new parser with the others.
- **New hop type** (e.g. marshalling terminals or interposing relays, trace question 13): add a PathNode kind or node type and one step in the trace sequence. Existing traces gain a hop; nothing else changes.
- **Panel terminals:** same Terminal entity, keyed `<Panel>:<Terminal>`, under a PANEL or MARSHALLING PathNode.

## 11. Open points that affect the model
- **PathNode for field devices:** I've given each field device on the CBD a FIELD_ENDPOINT PathNode so all routes run node-to-node. The alternative is letting routes start at a Device directly. That's simpler, but the routing layer would then have two node types. I recommend the FIELD_ENDPOINT approach.
- **Racks:** the module key includes `R<rack>`, defaulting to `R1` until rack layouts are confirmed (game plan question 23).
- **Multi-conductor signals:** supported via TraceLeg either way. The answer to trace question 15 only changes how legs are grouped into traces.
- **Route identity:** keyed by cable number where one exists. If route codes (game plan question 18) turn out to be the primary identifier on CBDs, the Route key switches to the route code, with cable number kept as an attribute.
