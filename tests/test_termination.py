"""M2: termination drawing readers (JB strips, PLC slot sheets), linking, traces and the termination rules,
on synthetic sheets laid out like the client's (positions taken from 401103-203 and 211)."""

from __future__ import annotations

from drawing_qa.model.engineering import HopStatus, TraceStatus
from drawing_qa.model.enums import DocType, Predicate, Severity, StageStatus
from drawing_qa.pipeline import run_pipeline
from tests.conftest import build_pdf, sheet_frame
import pytest

from tests.test_plc import channel_row, slot_sheet
from tests.test_plc import cfg as plc_cfg  # noqa: F401  (fixture)
from tests.test_rules import add_sheet, by_rule


@pytest.fixture
def cfg(plc_cfg):  # noqa: F811
    plc_cfg.identifiers.superseded = {"LTL-AUT1": "LTL-PLC"}  # as config/project.toml: the drawings still say LTL-AUT1
    return plc_cfg

BRACKET = 0.36
ROW = 7.0


def jb_sheet(root, sheet="203", *, jb="=K07.11+LTL-JB1", blocks=(), destinations=(), spec=("50C+E 1.5mm2 PVC", "CXXXXX", "10 SPARES"),
             extra=()):
    """blocks: [(header text, [(terminal, wire), ...])]; destinations: [(panel, "DI SLOT 13", first terminal, last terminal)]."""
    items = [((294, 40, 333, 52), jb), ((69, 42, 126, 56), "=K07.11+LTL-AUT1"), ((169, 43, 189, 53), "FIELD")]
    lines = []
    y = 65.0
    row_y: dict[str, float] = {}
    for header, rows in blocks:
        items.append(((285, y, 313, y + 11), header))
        y += 10
        for terminal, wire in rows:
            items.append(((280, y, 288, y + 9), terminal))
            items.append(((293, y, 313, y + 9), wire))
            row_y[terminal] = y
            y += ROW
        y += 8
    for panel, slot_text, first, last in destinations:
        y0, y1 = row_y[first], row_y[last] + 9
        mid = (y0 + y1) / 2
        items += [((65, mid - 12, 108, mid - 2), f"=K07.11+{panel}"), ((44, mid - 4, 109, mid + 5), "LAND LEG REMOTE I/O PANEL"),
                  ((82, mid + 4, 109, mid + 14), slot_text)]
        lines.append((112, y0, 112, y1, BRACKET))
    if spec:
        items += [((173, 56, 217, 68), spec[0]), ((184, 64, 207, 73), spec[1]), ((179, 70, 211, 82), spec[2])]
    frame = sheet_frame("401103", sheet, descriptor="TERMINATION DIAGRAM")
    build_pdf(root / "Terminations" / f"401103-{sheet}.pdf", [[]], lines=[lines], shx=[frame + items + list(extra)])


def slot_term_sheet(root, sheet="211", *, slot=13, rows=(), target="=K07.11+LTL-JB1", ref="0203", first=1, last=2):
    """A PLC slot termination sheet: 'SAFETY / INPUTS SLOT n' strip and one EXTERNAL WIRING group."""
    items = [((183, 45, 207, 56), "SAFETY"), ((178, 51, 213, 63), f"INPUTS SLOT {slot}")]
    ys = {}
    for i, (terminal, wire) in enumerate(rows):
        y = 59 + ROW * i
        items += [((176, y, 183, y + 9), terminal), ((189, y, 211, y + 9), wire)]
        ys[terminal] = y
    y0, y1 = ys[str(first)], ys[str(last)] + 9
    for i, t in enumerate(range(first, last + 1)):
        items.append(((272, ys[str(t)], 278, ys[str(t)] + 8), str(i + 1)))
    items += [((287, y0, 336, y0 + 9), "EXTERNAL WIRING TO"), ((287, y0 + 5, 327, y0 + 16), target),
              ((287, y0 + 7, 338, y0 + 24), f"(REF. TERM. SHT {ref})")]
    frame = sheet_frame("401103", sheet, title="LAND LEG REMOTE I/O PANEL", descriptor=f"PLC SLOT - {slot} TERMINATION DIAGRAM")
    build_pdf(root / "Terminations" / f"401103-{sheet}.pdf", [[]], lines=[[(284, y0, 284, y1, BRACKET)]], shx=[frame + items])


def schematic(tmp_path, jb="LTL-JB1", wire="D6", device="S-TN36D4", slot=13):
    items, frame = slot_sheet(sheet=f"{slot + 23:03d}", slot=slot, rows=[channel_row(113, "00", "IN-0", device, "B4", wire, jb, ["BRAKE 9"])])
    add_sheet(tmp_path, "PLC", f"{slot + 23:03d}", items, **frame)


BRAKE = [("S-TN36D4", [("5", "TN15D5"), ("6", "TN36D6")])]
TO_SLOT_13 = [("LTL-AUT1", "DI SLOT 13", "5", "6")]


def trace(result, key="LTL/S13/CH00"):
    return next(t for t in result.traces if t.key == key)


def hop(t, name):
    return next(h for h in t.hops if h.hop == name)


def test_jb_strip_reader_claims(tmp_path, cfg):
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13)
    result = run_pipeline(tmp_path, cfg)
    claims = {(c.predicate, c.subject_key, c.object_key) for c in result.claims if c.parser is DocType.TERM}
    assert (Predicate.LANDS_ON, "TN36D6", "LTL-JB1:6") in claims
    assert (Predicate.REFERS_TO_DEVICE, "LTL-JB1:5", "S-TN36D4") in claims
    assert (Predicate.DESTINED_PANEL, "LTL-JB1:5", "LTL-AUT1") in claims
    assert (Predicate.DESTINED_FOR, "LTL-JB1:6", "LTL/S13") in claims
    spec = next(c for c in result.claims if c.predicate is Predicate.HAS_SPEC)
    assert (spec.value["cores"], spec.value["spares"], spec.value["terminals"]) == (50, 10, ["5", "6"])
    # The old panel name is linked to its replacement.
    assert {e.dst for e in result.model.edges if e.type == "TERMINAL_PANEL"} == {"PANEL:LTL-PLC"}


def test_trace_is_complete_through_the_jb_terminals(tmp_path, cfg):
    schematic(tmp_path)
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13)
    t = trace(run_pipeline(tmp_path, cfg))
    assert hop(t, "JB_TERMINAL").status is HopStatus.FOUND and hop(t, "JB_TERMINAL").values == ["LTL-JB1:5", "LTL-JB1:6"]
    assert t.status is TraceStatus.COMPLETE and not t.conflicts


def test_excluding_terminations_leaves_the_hop_not_sourced_and_skips_term_rules(tmp_path, cfg):
    schematic(tmp_path)
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13)
    cfg.ingest.unchecked_types = [DocType.TERM]
    result = run_pipeline(tmp_path, cfg)
    assert hop(trace(result), "JB_TERMINAL").status is HopStatus.NOT_SOURCED
    runs = {r.rule_id: r for r in result.rule_runs}
    assert runs["TERM-004"].status is StageStatus.SKIPPED and "TERMINATIONS" in runs["TERM-004"].note


def test_comp_003_terminations_land_the_device_in_another_jb(tmp_path, cfg):
    schematic(tmp_path, jb="LTL-JB2")
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13)
    result = run_pipeline(tmp_path, cfg)
    assert [(c.hop, c.other, c.other_source) for c in trace(result).conflicts] == [("JB", ["LTL-JB1"], "TERM")]
    findings = by_rule(result, "COMP-003")
    assert len(findings) == 1 and "termination drawings land it in LTL-JB1" in findings[0].message


def test_plc_001_and_009_destination_slot(tmp_path, cfg):
    """The terminal strip sends S-TN36D4 to DI slot 14; the schematic has it on slot 13, and there is no slot 14."""
    schematic(tmp_path)
    jb_sheet(tmp_path, blocks=BRAKE, destinations=[("LTL-AUT1", "DI SLOT 14", "5", "6")])
    result = run_pipeline(tmp_path, cfg)
    plc1 = by_rule(result, "PLC-001")
    assert len(plc1) == 1 and "go to LTL/S14" in plc1[0].message
    plc9 = by_rule(result, "PLC-009")
    assert len(plc9) == 1 and "no LTL/S14 on the schematics" in plc9[0].message


def test_plc_009_io_type(tmp_path, cfg):
    schematic(tmp_path)
    jb_sheet(tmp_path, blocks=BRAKE, destinations=[("LTL-AUT1", "DO SLOT 13", "5", "6")])
    findings = by_rule(run_pipeline(tmp_path, cfg), "PLC-009")
    assert len(findings) == 1 and "DI module" in findings[0].message


def test_term_rules_on_one_strip(tmp_path, cfg):
    blocks = [
        ("S-TN36D4", [("1", "TN15D5"), ("2", "TN36D6"), ("3", "SCR")]),  # a screen row is a terminal, not a gap
        ("S-TN36F4", [("2", "TN36F6"), ("6", "TN15D5")]),  # 2 again; 4 and 5 not drawn
        ("STORM PIN LAND LEG", [("1", "TNXXXX"), ("2", "TNXXXX")]),  # equipment with its own strip: not duplicates
    ]
    jb_sheet(tmp_path, blocks=blocks, destinations=[("LTL-AUT1", "DI SLOT 13", "1", "6")])
    result = run_pipeline(tmp_path, cfg)
    assert [f.subject_key for f in by_rule(result, "TERM-002")] == ["LTL-JB1:2"]
    gaps = by_rule(result, "TERM-004")
    assert len(gaps) == 1 and "terminals 4-5 are not drawn" in gaps[0].message


def test_term_001_terminal_outside_any_device_block(tmp_path, cfg):
    extra = [((280, 58, 288, 67), "9"), ((293, 58, 313, 67), "TN36W6")]  # above the first block header
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13, extra=extra)
    findings = by_rule(run_pipeline(tmp_path, cfg), "TERM-001")
    assert [f.subject_key for f in findings] == ["LTL-JB1:9"]


def test_wire_004_schematic_wire_not_on_the_jb_terminals(tmp_path, cfg):
    schematic(tmp_path, wire="D5")  # TN36D5 on the schematic; the terminals have TN36D6
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13)
    findings = by_rule(run_pipeline(tmp_path, cfg), "WIRE-004")
    assert len(findings) == 1 and "shows TN36D5" in findings[0].message and "TN36D6" in findings[0].message


def test_path_005_core_count(tmp_path, cfg):
    blocks = [("S-TN36D4", [("1", "TN15D5"), ("2", "TN36D6"), ("3", "TN15D5"), ("4", "TN36F6")])]
    jb_sheet(tmp_path, blocks=blocks, destinations=[("LTL-AUT1", "DI SLOT 13", "1", "4")], spec=("2C+E 1.5mm2 PVC", "CXXXXX", "0 SPARES"))
    findings = by_rule(run_pipeline(tmp_path, cfg), "PATH-005")
    assert len(findings) == 1 and findings[0].severity is Severity.MAJOR and "2 cores but 4 terminals" in findings[0].message


def test_path_005_spare_count(tmp_path, cfg):
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13, spec=("6C+E 1.5mm2 PVC", "CXXXXX", "2 SPARES"))
    findings = by_rule(run_pipeline(tmp_path, cfg), "PATH-005")
    assert len(findings) == 1 and findings[0].severity is Severity.MINOR and "so 4 spare" in findings[0].message


def test_dev_003_device_missing_from_its_jbs_strip(tmp_path, cfg):
    schematic(tmp_path, device="S-TN36D5")  # in the same channel row, but not on the strip
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13)
    result = run_pipeline(tmp_path, cfg)
    assert hop(trace(result), "JB_TERMINAL").status is HopStatus.MISSING
    findings = by_rule(result, "DEV-003")
    assert [f.subject_key for f in findings] == ["S-TN36D5"]
    assert by_rule(result, "COMP-001") == []  # a missing JB terminal is DEV-003's, not COMP-001's


def test_slot_sheet_reader_and_cross_checks(tmp_path, cfg):
    schematic(tmp_path)  # TN36D6 on LTL/S13/CH00
    jb_sheet(tmp_path, blocks=BRAKE, destinations=TO_SLOT_13)
    slot_term_sheet(tmp_path, slot=13, rows=[("1", "TN36D6"), ("2", "TN15D5")], ref="0205")  # LTL-JB1 is on 203
    jb_sheet(tmp_path, "205", jb="=K07.11+LTL-JB2", blocks=[("S-TN37D4", [("1", "TN15D5"), ("2", "TN37D6")])], spec=None)
    result = run_pipeline(tmp_path, cfg)
    lands = [e for e in result.model.edges if e.type == "WIRE_LANDS_ON" and e.dst.startswith("PLC_MODULE:")]
    assert {(e.src, e.dst, e.attrs["terminal"]) for e in lands} >= {("WIRE:TN36D6", "PLC_MODULE:LTL/S13", "1")}
    dest = [e for e in result.model.edges if e.type == "DESTINED_FOR" and e.src == "PLC_MODULE:LTL/S13"]
    assert {e.dst for e in dest} == {"JUNCTION_BOX:LTL-JB1"} and {e.attrs["core"] for e in dest} == {"1", "2"}
    assert by_rule(result, "PLC-001") == []  # TN36D6 is on slot 13 on both
    refs = by_rule(result, "TERM-003")
    assert len(refs) == 1 and "SHT 205" in refs[0].message and "LTL-JB1 is on 203" in refs[0].message


def test_slot_sheet_wire_on_another_module_is_plc_001(tmp_path, cfg):
    schematic(tmp_path)
    slot_term_sheet(tmp_path, slot=12, rows=[("1", "TN36D6"), ("2", "TN15D5")])
    findings = by_rule(run_pipeline(tmp_path, cfg), "PLC-001")
    assert len(findings) == 1 and "the schematics have it on LTL/S13" in findings[0].message
