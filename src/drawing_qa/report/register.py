"""QA_Register.xlsx: the checker's findings register (IMPLEMENTATION_PLAN.md section 8, agreed 2026-10-22).

Tabs: Findings (one row per finding occurrence, with the checker's Decision / comment columns),
Tag Index (every field device and where it is drawn on each document type), No Longer Raised,
Summary (formulas), Key (how to use it, decisions, and every rule in the catalogue).

Only the yellow checker columns are read back (review/register_reader.py); everything else is
rewritten each run.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from drawing_qa.common.descriptions import without_prefix
from drawing_qa.model.enums import Decision, DocType, Predicate
from drawing_qa.model.run import RunResult
from drawing_qa.model.source import Drawing, SourceRef
from drawing_qa.rules.base import Category
from drawing_qa.rules.catalogue import PLANNED
from drawing_qa.rules.registry import all_rules

log = logging.getLogger(__name__)

REGISTER_NAME = "QA_Register.xlsx"
FINDINGS_SHEET = "Findings"
GONE_SHEET = "No Longer Raised"
HEADER_ROW = 4
# Column headers the reader looks for (by name, so the checker may hide or resize columns).
COL_DECISION, COL_COMMENT, COL_REVIEWER, COL_DATE, COL_FINGERPRINT = (
    "Decision", "Checker comment", "Reviewed by", "Date", "Fingerprint",
)

_DOCS = (DocType.PLC, DocType.CBD, DocType.TERM)
_FONT = "Arial"
_THIN = Side(style="thin", color="BFBFBF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_HEAD_FILL = PatternFill("solid", fgColor="1F3864")
_GROUP_FILLS = {"PLC": "DDEBF7", "CBD": "E2EFDA", "TERM": "EDEDED", "REVIEW": "FFF2CC"}
_INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
_SEV_FILLS = {"CRITICAL": "C00000", "MAJOR": "F4B183", "MINOR": "FFE699", "INFO": "D9E1F2"}
_SEVERITY_NOTE = {
    "COMP-001": "MAJOR (no device tag) / MINOR (no wire or JB)",
    **{f"PLACEHOLDER-00{n}": "MAJOR on issued sheets / MINOR on preliminary" for n in range(1, 6)},
}
_CATEGORY_ORDER = [c.name for c in Category]


def _left_out(result: RunResult, doc_type: str) -> str:
    return "\n(left out)" if doc_type in result.left_out else ""


def _location(drawing: Drawing, source: SourceRef) -> str:
    """'401103 sh 036 C7'."""
    return f"{drawing.drawing_no} sh {drawing.sheet}" + (f" {source.grid_cell}" if source.grid_cell else "")


def _header(ws, row: int, columns: list[tuple[str, float, str | None]]) -> None:
    for i, (title, width, group) in enumerate(columns, start=1):
        cell = ws.cell(row=row, column=i, value=title)
        cell.font = Font(name=_FONT, bold=True, color="000000" if group else "FFFFFF")
        cell.fill = PatternFill("solid", fgColor=_GROUP_FILLS[group]) if group else _HEAD_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        cell.border = _BORDER
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[row].height = 32


def _cell(ws, row: int, col: int, value, **font):
    cell = ws.cell(row=row, column=col, value=value)
    cell.font = Font(name=_FONT, size=9, **font)
    cell.alignment = Alignment(wrap_text=True, vertical="top")
    cell.border = _BORDER
    return cell


def _title(ws, text: str, subtitle: str = "") -> None:
    ws["A1"] = text
    ws["A1"].font = Font(name=_FONT, bold=True, size=14)
    if subtitle:
        ws["A2"] = subtitle
        ws["A2"].font = Font(name=_FONT, italic=True, size=9, color="595959")


class _Index:
    """Where every identifier is drawn, per document type."""

    def __init__(self, result: RunResult) -> None:
        self.drawings = {d.id: d for d in result.drawings}
        self.occurs: dict[str, dict[DocType, list[str]]] = defaultdict(lambda: defaultdict(list))
        for c in result.claims:
            if c.predicate is Predicate.OCCURS:
                self.add(self.occurs[c.subject_key], c.source)
        self.trace_by_channel = {t.channel: t for t in result.traces if t.channel}
        self.channel_by_device: dict[str, str] = {}
        for t in result.traces:
            if t.device and t.channel:
                self.channel_by_device.setdefault(t.device, t.channel)

    def add(self, per_doc: dict[DocType, list[str]], source: SourceRef) -> None:
        d = self.drawings[source.drawing_id]
        text = _location(d, source)
        if text not in per_doc[d.doc_type]:
            per_doc[d.doc_type].append(text)


def _findings_sheet(wb: Workbook, result: RunResult, idx: _Index) -> tuple[str, int]:
    ws = wb.active
    ws.title = FINDINGS_SHEET
    _title(
        ws,
        "QA Findings Register",
        f"Run {result.info.run_id} · {len(result.findings)} findings · "
        "edit only the yellow columns; everything else is rewritten each run",
    )
    columns = [
        ("Ref", 8, None), ("Severity", 9, None), ("Rule", 14, None), ("Category", 14, None),
        ("Tag / item", 15, None), ("PLC channel", 13, None), ("Conflict / issue", 55, None),
        ("PLC schematic" + _left_out(result, "PLC"), 20, "PLC"), ("Cable block diagram" + _left_out(result, "CBD"), 20, "CBD"),
        ("Termination" + _left_out(result, "TERM"), 20, "TERM"),
        ("Recommendation", 32, None),
        (COL_DECISION, 15, "REVIEW"), (COL_COMMENT, 34, "REVIEW"), (COL_REVIEWER, 11, "REVIEW"), (COL_DATE, 11, "REVIEW"),
        ("First seen", 10, None), (COL_FINGERPRINT, 13, None),
    ]
    _header(ws, HEADER_ROW, columns)
    first = HEADER_ROW + 1
    for r, f in enumerate(result.findings, start=first):
        is_channel = f.subject_type == "PLC_CHANNEL"
        trace = idx.trace_by_channel.get(f.subject_key) if is_channel else None
        tag = (trace.device if trace else None) or ("(untagged)" if is_channel else f.subject_key)
        channel = f.subject_key if is_channel else idx.channel_by_device.get(tag, "") if f.subject_type == "DEVICE" else ""

        per_doc: dict[DocType, list[str]] = defaultdict(list)
        for e in f.evidence:
            if e.source:
                idx.add(per_doc, e.source)
        if f.subject_type in ("DEVICE", "PLC_CHANNEL", "WIRE"):
            for doc in _DOCS:
                for text in idx.occurs.get(tag, {}).get(doc, []):
                    if text not in per_doc[doc]:
                        per_doc[doc].append(text)

        review = f.review
        values = [
            f.id, f.severity.value, f.rule_id, f.category.replace(" Validation", ""), tag, channel, f.message,
            *("\n".join(per_doc[d]) or "-" for d in _DOCS),
            f.recommendation or "",
            f.status.value, review.comment if review else "", review.reviewer if review else "", review.decided_on if review else "",
            f.first_seen or "", f.fingerprint,
        ]
        for c, v in enumerate(values, start=1):
            cell = _cell(ws, r, c, v)
            if 12 <= c <= 15:
                cell.fill = _INPUT_FILL
    last = max(first, HEADER_ROW + len(result.findings))
    ws.freeze_panes = ws.cell(row=first, column=6)
    ws.auto_filter.ref = f"A{HEADER_ROW}:{get_column_letter(len(columns))}{last}"

    dv = DataValidation(type="list", formula1='"' + ",".join(d.value for d in Decision) + '"', allow_blank=True)
    dv.errorTitle, dv.error = "Decision", "Pick a decision from the list"
    ws.add_data_validation(dv)
    dv.add(f"L{first}:L{last}")
    for sev, colour in _SEV_FILLS.items():
        ws.conditional_formatting.add(
            f"B{first}:B{last}",
            CellIsRule(operator="equal", formula=[f'"{sev}"'], fill=PatternFill("solid", fgColor=colour),
                       font=Font(name=_FONT, size=9, bold=True, color="FFFFFF" if sev == "CRITICAL" else "000000")),
        )
    closed = ",".join(f'$L{first}="{d}"' for d in ("FALSE_POSITIVE", "WAIVED", "FIXED"))
    ws.conditional_formatting.add(f"A{first}:K{last}", FormulaRule(formula=[f"OR({closed})"], font=Font(color="A6A6A6", strike=True)))
    for decision, colour in (("CONFIRMED", "C6EFCE"), ("DEFERRED", "D9D9D9")):
        ws.conditional_formatting.add(
            f"L{first}:L{last}", CellIsRule(operator="equal", formula=[f'"{decision}"'], fill=PatternFill("solid", fgColor=colour))
        )
    return "E", last


def _tag_index_sheet(wb: Workbook, result: RunResult, idx: _Index, tag_col: str, last: int) -> None:
    ws = wb.create_sheet("Tag Index")
    _title(ws, "Tag Index", "Every field device and where it is drawn on the three document types")
    columns = [
        ("Tag", 13, None), ("PLC channel", 13, None), ("Description", 40, None), ("Trace status", 11, None),
        ("PLC schematic\nlocation", 19, "PLC"), ("Schematic JB", 13, "PLC"),
        ("CBD location", 19, "CBD"), ("CBD route via", 15, "CBD"),
        ("Termination\nlocation", 19, "TERM"), ("Findings\n(count)", 10, None),
    ]
    _header(ws, 3, columns)
    rows = []
    for t in result.traces:
        if not t.device or t.status.value in ("SPARE", "INTERNAL"):
            continue
        occurs = idx.occurs.get(t.device, {})
        jb = next((h.values for h in t.hops if h.hop == "JB"), [])
        heading = without_prefix(t.description, result.model.description_prefix).replace(" / ", " - ")
        via = ", ".join(t.cbd_via) or ("direct" if t.cbd_route else "-")
        rows.append([
            t.device, t.channel or "(none: orphan)", heading, t.status.value,
            "\n".join(occurs.get(DocType.PLC, [])) or "-", ", ".join(jb) or "-",
            "\n".join(occurs.get(DocType.CBD, [])) or "-", via,
            "\n".join(occurs.get(DocType.TERM, [])) or "-",
        ])
    rows.sort(key=lambda row: (row[0], row[1]))
    for r, values in enumerate(rows, start=4):
        for c, v in enumerate(values, start=1):
            _cell(ws, r, c, v)
        cell = _cell(ws, r, 10, f"=COUNTIF({FINDINGS_SHEET}!${tag_col}${HEADER_ROW + 1}:${tag_col}${last},A{r})")
        cell.alignment = Alignment(horizontal="center", vertical="top")
    end = max(4, 3 + len(rows))
    ws.freeze_panes = "B4"
    ws.auto_filter.ref = f"A3:J{end}"
    mismatch = 'AND($F4<>"-",$H4<>"-",$H4<>"direct",$F4<>$H4)'
    for col in ("F", "H"):
        ws.conditional_formatting.add(f"{col}4:{col}{end}", FormulaRule(formula=[mismatch], fill=PatternFill("solid", fgColor="F8CBAD")))


def _gone_sheet(wb: Workbook, result: RunResult) -> None:
    ws = wb.create_sheet(GONE_SHEET)
    review = result.review
    gone = review.no_longer_raised if review else []
    _title(ws, "No Longer Raised", "Findings this run does not raise (fixed, or the evidence changed). Ones with a decision stay "
           "here until you mark them FIXED; the rest show once, on the run after they stop")
    columns = [("Rule", 14, None), ("Severity", 9, None), ("Tag / item", 15, None), ("Finding (as last raised)", 62, None),
               ("Last raised", 11, None), (COL_DECISION, 15, "REVIEW"), (COL_COMMENT, 34, "REVIEW"), (COL_REVIEWER, 11, "REVIEW"),
               (COL_DATE, 11, "REVIEW"), (COL_FINGERPRINT, 13, None)]
    _header(ws, HEADER_ROW, columns)
    first = HEADER_ROW + 1
    for r, g in enumerate(gone, start=first):
        rv = g.review
        values = [g.rule_id, g.severity, g.subject_key, g.message, g.last_raised, g.decision.value,
                  rv.comment if rv else "", rv.reviewer if rv else "", rv.decided_on if rv else "", g.fingerprint]
        for c, v in enumerate(values, start=1):
            cell = _cell(ws, r, c, v)
            if 6 <= c <= 9:
                cell.fill = _INPUT_FILL
    if not gone:
        _cell(ws, first, 1, "None.")
    last = max(first, HEADER_ROW + len(gone))
    dv = DataValidation(type="list", formula1='"' + ",".join(d.value for d in Decision) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"F{first}:F{last}")
    ws.freeze_panes = ws.cell(row=first, column=4)


def _summary_sheet(wb: Workbook, result: RunResult, last: int) -> None:
    ws = wb.create_sheet("Summary")
    _title(ws, "Summary", "Counts update as decisions are entered on the Findings tab")
    severities = ["CRITICAL", "MAJOR", "MINOR", "INFO"]
    decisions = [d.value for d in Decision]
    _header(ws, 3, [("Category", 16, None), *((s.title(), 10, None) for s in severities), ("Total", 9, None),
                    *((d.replace("_", " ").title(), 11, "REVIEW") for d in decisions)])
    cats = [Category[n].value.replace(" Validation", "") for n in _CATEGORY_ORDER]

    def rng(col: str) -> str:
        return f"{FINDINGS_SHEET}!${col}${HEADER_ROW + 1}:${col}${last}"

    for r, cat in enumerate(cats, start=4):
        _cell(ws, r, 1, cat)
        for c, sev in enumerate(severities, start=2):
            _cell(ws, r, c, f'=COUNTIFS({rng("D")},$A{r},{rng("B")},"{sev}")')
        _cell(ws, r, 6, f"=SUM(B{r}:E{r})")
        for c, dec in enumerate(decisions, start=7):
            _cell(ws, r, c, f'=COUNTIFS({rng("D")},$A{r},{rng("L")},"{dec}")')
    total = 4 + len(cats)
    _cell(ws, total, 1, "Total", bold=True)
    for c in range(2, 7 + len(decisions)):
        col = get_column_letter(c)
        _cell(ws, total, c, f"=SUM({col}4:{col}{total - 1})", bold=True)


def _coverage_sheet(wb: Workbook, result: RunResult) -> None:
    ws = wb.create_sheet("Coverage")
    overall = next((r for r in result.coverage if r.group == "Overall"), None)
    _title(ws, "Trace Coverage",
           (f"{overall.traced_pct:.0%} of {overall.traces} field traces traced end to end as far as the parsed drawings go"
            if overall else "No traces") + " · spare and internal channels left out"
           + (" · JB terminals not sourced: termination drawings left out of the review" if "TERM" in result.left_out else ""))
    _header(ws, 3, [("Group", 14, None), ("Area / panel / slot / JB", 22, None), ("Traces", 9, None), ("Traced", 9, None),
                    ("Traced %", 9, None), ("Gap", 8, None), ("Orphan", 8, None), ("Conflicted", 10, None)])
    for r, row in enumerate(result.coverage, start=4):
        values = [row.group, row.key, row.traces, row.complete + row.complete_so_far, None, row.gap, row.orphan, row.conflicted]
        for c, v in enumerate(values, start=1):
            _cell(ws, r, c, v)
        pct = _cell(ws, r, 5, f"=IF(C{r}=0,0,D{r}/C{r})")
        pct.number_format = "0%"
    end = max(4, 3 + len(result.coverage))
    ws.freeze_panes = "C4"
    ws.auto_filter.ref = f"A3:H{end}"
    ws.conditional_formatting.add(
        f"E4:E{end}", CellIsRule(operator="lessThan", formula=["0.8"], fill=PatternFill("solid", fgColor="F8CBAD"))
    )


def _waiver_sheet(wb: Workbook, result: RunResult) -> None:
    ws = wb.create_sheet("Waivers")
    uses = result.review.waivers if result.review else []
    _title(ws, "Waivers", "Known, accepted exceptions from config/waivers.toml. Edit that file to add, change or remove one; "
           "your own decision on a finding takes precedence")
    _header(ws, 3, [("Waiver", 9, None), ("Rule", 14, None), ("Subject pattern", 16, None), ("Reason", 50, None),
                    ("Approved by", 13, None), ("Expires", 11, None), ("Status", 10, None), ("Findings\nmatched", 10, None),
                    ("Checker decision\nkept instead", 13, None)])
    for r, u in enumerate(uses, start=4):
        values = [u.id, u.rule, u.subject, u.reason, u.approved_by, u.expires.isoformat() if u.expires else "",
                  u.status, u.matched, u.overridden]
        for c, v in enumerate(values, start=1):
            _cell(ws, r, c, v, color="808080" if u.status == "EXPIRED" else "000000")
    if not uses:
        _cell(ws, 4, 1, "None. Add [[waiver]] entries to config/waivers.toml.")


def _key_sheet(wb: Workbook, result: RunResult) -> None:
    ws = wb.create_sheet("Key")
    lines = [
        ("How to use", None),
        ("Edit", "Only the yellow columns on Findings (Decision, Checker comment, Reviewed by, Date). Everything else is rewritten every run."),
        ("Carry-forward", "Save this file where it is. The next run reads your decisions, keeps them in qa_review.sqlite next to it, "
                          "and writes them back onto the same findings (matched by Fingerprint), on later runs and revisions, while the problem is unchanged."),
        ("Locations", "Drawing, sheet and grid cell on each document type, e.g. '401103 sh 036 C7'. '-' = not shown on that document type."),
        ("Left out", "An input type left out of the review (python -m drawing_qa run ... --exclude term) is still read and its locations "
                     "listed, marked '(left out)' in its column header, but nothing is checked against it."),
        ("No Longer Raised", "Findings this run does not raise. Check the fix and mark it FIXED; findings with any other decision stay listed until you do."),
        ("Waivers", "Known exceptions in config/waivers.toml (rule + subject pattern, reason, approver, expiry) mark matching findings WAIVED "
                    "with the comment 'Waiver <id>: ...'. Change such a row's Decision to override it; remove the waiver to reopen them."),
        ("", None),
        ("Decision", None),
        ("OPEN", "Not reviewed yet (default)."),
        ("CONFIRMED", "A real problem; stays on the register until the drawing is fixed."),
        ("FALSE_POSITIVE", "Not a problem. Greyed out on later runs, with your comment, until the evidence changes."),
        ("FIXED", "Fixed in the drawings; the next run should no longer raise it."),
        ("WAIVED", "A known exception, accepted (give the reason)."),
        ("DEFERRED", "Decide later (e.g. needs the termination drawings)."),
        ("", None),
        ("Example", "Decision = FALSE_POSITIVE, Checker comment = 'Direct cable to panel, no JB needed', Reviewed by = 'DC', Date = 2026-10-22"),
    ]
    decisions = {d.value for d in Decision}
    for r, (a, b) in enumerate(lines, start=1):
        ca, cb = ws.cell(row=r, column=1, value=a), ws.cell(row=r, column=2, value=b)
        ca.font = Font(name=_FONT, bold=b is None or a in decisions, size=11 if b is None else 9)
        cb.font = Font(name=_FONT, size=9)
        cb.alignment = Alignment(wrap_text=True, vertical="top")
        if b:
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)

    built = {cls.meta.rule_id: cls.meta for cls in all_rules()}
    planned = {p.rule_id: p for p in PLANNED if p.rule_id not in built}
    ran = {r.rule_id: r for r in result.rule_runs}

    def order(rule_id: str) -> tuple[int, int]:
        prefix, _, number = rule_id.rpartition("-")
        return _CATEGORY_ORDER.index(prefix), int(number)

    ids = sorted({*built, *planned}, key=order)
    start = len(lines) + 2
    ws.cell(row=start, column=1, value="Rules").font = Font(name=_FONT, bold=True, size=11)
    note = ws.cell(row=start + 1, column=1, value=f"{len(ids)} rules: {len(built)} built, {len(planned)} planned. "
                   "Severity is the default; config/rules.toml can change it or turn a rule off.")
    note.font = Font(name=_FONT, italic=True, size=9, color="595959")
    ws.merge_cells(start_row=start + 1, start_column=1, end_row=start + 1, end_column=6)
    _header(ws, start + 2, [("Rule", 18, None), ("Title", 30, None), ("Category", 13, None), ("Severity", 22, None),
                            ("What it checks", 75, None), ("This run", 30, None)])
    for r, rule_id in enumerate(ids, start=start + 3):
        meta, plan = built.get(rule_id), planned.get(rule_id)
        if rule_id in ran:
            run = ran[rule_id]
            status = f"Ran: {run.findings} finding(s)" if run.status.value == "DONE" else f"{run.status.value.title()}: {run.note}"
        elif plan and plan.waits_for:
            status = f"Planned: needs {plan.waits_for}" if "(" in plan.waits_for else f"Planned: in build ({plan.waits_for})"
        else:
            status = "Planned"
        category = Category[rule_id.rpartition("-")[0]].value.replace(" Validation", "")
        values = [
            rule_id, meta.title if meta else plan.title, category,
            _SEVERITY_NOTE.get(rule_id, meta.default_severity.value if meta else "-"),
            meta.description if meta else plan.check, status,
        ]
        for c, v in enumerate(values, start=1):
            _cell(ws, r, c, v, color="000000" if meta else "808080")


def build_register(result: RunResult) -> Workbook:
    idx = _Index(result)
    wb = Workbook()
    tag_col, last = _findings_sheet(wb, result, idx)
    _tag_index_sheet(wb, result, idx, tag_col, last)
    _gone_sheet(wb, result)
    _summary_sheet(wb, result, last)
    _coverage_sheet(wb, result)
    _waiver_sheet(wb, result)
    _key_sheet(wb, result)
    for ws in wb.worksheets:
        ws.sheet_view.zoomScale = 90
    return wb


def write_register(result: RunResult, out_dir: Path) -> Path:
    """Write QA_Register.xlsx. If it is open in Excel (locked), write a run-named copy instead."""
    wb = build_register(result)
    path = out_dir / REGISTER_NAME
    try:
        wb.save(path)
    except PermissionError:
        path = out_dir / f"QA_Register-{result.info.run_id}.xlsx"
        log.warning("%s is open (locked); wrote %s instead. Close it before the next run.", REGISTER_NAME, path.name)
        wb.save(path)
        return path
    # Copies written while the register was locked have been read by this run; file them away so
    # they are not read again over newer decisions.
    for copy in out_dir.glob("QA_Register-*.xlsx"):
        history = out_dir / "register_history"
        history.mkdir(exist_ok=True)
        copy.replace(history / copy.name)
    return path
