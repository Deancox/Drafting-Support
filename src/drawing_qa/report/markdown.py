"""report.md: the human-readable summary for the checker."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from drawing_qa.common.descriptions import without_prefix
from drawing_qa.model.enums import Decision, IssueCode, Severity, StageStatus
from drawing_qa.model.findings import Finding
from drawing_qa.model.run import RunResult
from drawing_qa.report.summary import RunSummary
from drawing_qa.rules.context import quiet_rules

# Issues that mean part of the package was not checked; listed in full.
_COVERAGE_ISSUES = {
    IssueCode.MISSING_FOLDER,
    IssueCode.UNREADABLE_FILE,
    IssueCode.NEEDS_OCR,
    IssueCode.PARSER_ERROR,
    IssueCode.EMPTY_FOLDER,
    IssueCode.NO_SHX_TEXT,
    IssueCode.FFI_MAPPING,
    IssueCode.PANEL_MAPPING,
    IssueCode.NO_GRID,
}
_SEVERITIES = [Severity.CRITICAL, Severity.MAJOR, Severity.MINOR, Severity.INFO]
_MAX_LISTED = 25  # findings listed per rule before switching to a grouped summary


def _kinds(d: dict[str, int]) -> str:
    return ", ".join(f"{k.lower()} {v}" for k, v in d.items()) or "—"


DECODED_MARK = "(decoded from line work)"


def _msg(f: Finding) -> str:
    """The finding's message, marked when it rests on text decoded from letter strokes (--ocr)."""
    return f"{f.message} {DECODED_MARK}" if f.decoded else f.message


def _sheet(f: Finding, labels: dict[str, str]) -> str:
    src = next((e.source for e in f.evidence if e.source), None)
    return labels.get(src.drawing_id, "?") if src else f.subject_key


_TRACE_MEANING = {
    "COMPLETE": "every hop found",
    "PARTIAL": "reaches a PLC channel; at least one hop missing or not yet sourced",
    "ORPHAN": "field device on a cable block diagram that reaches no PLC channel",
    "SPARE": "spare channel",
    "INTERNAL": "channel with no field device or JB (e.g. supply-healthy monitoring)",
}


def _traces(result: RunResult, w) -> None:
    traces = result.traces
    counts = Counter(t.status.value for t in traces)
    gaps = [t for t in traces if t.status.value == "PARTIAL" and t.gaps]
    unsourced = sorted({h for t in traces for h in t.unsourced})
    w("## Signal traces")
    w("")
    w("One trace per PLC channel: device → wire → JB terminal → JB / field location → panel → slot → channel.")
    w("")
    w("| Status | Count | Meaning |")
    w("|---|---|---|")
    for status in ("COMPLETE", "PARTIAL", "ORPHAN", "SPARE", "INTERNAL"):
        w(f"| {status.title()} | {counts.get(status, 0)} | {_TRACE_MEANING[status]} |")
    w("")
    partial = counts.get("PARTIAL", 0)
    routed = sum(1 for t in traces if t.cbd_route)
    w(f"**{routed}** traces have a cable route on the cable block diagrams from the device to its panel (PATH rules compare it with the schematic).")
    w("")
    w(f"Of the {partial} partial traces, **{partial - len(gaps)} are complete as far as the supplied, parsed drawings go**"
      + (f" (only {', '.join(h.replace('_', ' ').lower() for h in unsourced)} not sourced: termination drawings left out of the review, "
         "or none supplied for that JB)" if unsourced else "")
      + f"; {len(gaps)} have a gap in the drawings (COMP-001).")
    w("")

    def hop(t, name):
        h = next((h for h in t.hops if h.hop == name), None)
        if h is None:
            return ""
        if h.status.value == "NOT_SOURCED":
            return "·"
        if h.status.value == "MISSING":
            return "**missing**"
        text = ", ".join(h.values)
        return f"{text} *(inferred)*" if h.status.value == "INFERRED" else text

    conflicted = [t for t in traces if t.conflicted]
    if conflicted:
        w(f"**{len(conflicted)}** traces are **conflicted**: two drawings disagree on a hop "
          f"({', '.join(sorted({(c.hop if c.hop == 'JB' else c.hop.replace('_', ' ').lower()) + ' (' + c.reported_by + ')' for t in conflicted for c in t.conflicts}))}).")
        w("")
    _coverage(result, w)

    w("<details><summary>Trace register (" + str(len(traces)) + " traces)</summary>")
    w("")
    w("| Trace | Status | Device | Wire | JB terminal | JB / location | Panel | Slot | CBD route | Description |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    for t in traces:
        if t.cbd_route:
            on_cbd = " → ".join(t.cbd_route)
        else:
            on_cbd = "" if t.on_cbd is None else ("on CBD, no route" if t.on_cbd else "not on CBD")
        desc = without_prefix(t.description, result.model.description_prefix).replace("|", "/")[:60]
        w(f"| {t.key} | {t.status.value.title()} | {hop(t, 'DEVICE') or t.device or ''} | {hop(t, 'WIRE')} | {hop(t, 'JB_TERMINAL')} | {hop(t, 'JB')} | "
          f"{hop(t, 'PANEL')} | {hop(t, 'SLOT')} | {on_cbd} | {desc} |")
    w("")
    w("· = not sourced (JB terminals come from the termination drawings: left out, or none supplied for that JB). "
      "*(inferred)* = device taken from the "
      "channel with the same description on the same module (dual-channel safety device, or one device for several channels).")
    w("")
    w("</details>")
    w("")


def _coverage(result: RunResult, w) -> None:
    from drawing_qa.traces.coverage import by_group

    groups = by_group(result.coverage)
    if not groups:
        return
    overall = groups["Overall"][0]
    w("### Coverage")
    w("")
    w(f"**{overall.traced_pct:.0%}** of {overall.traces} field traces are traced end to end as far as the supplied, parsed "
      "drawings go (spare and internal channels left out). *Traced* = complete, or complete except hops only a "
      "not-yet-parsed drawing type gives, with no hop the drawings disagree on.")
    w("")
    header = "| {} | Traces | Traced | Traced % | Gap | Orphan | Conflicted |"
    for group in ("Area", "Panel", "Slot", "JB / location"):
        rows = groups.get(group, [])
        if not rows:
            continue
        fold = len(rows) > 12
        if fold:
            w(f"<details><summary>By {group if group.startswith('JB') else group.lower()} ({len(rows)})</summary>")
            w("")
        else:
            w(f"**By {group.lower()}**")
            w("")
        w(header.format(group))
        w("|---|---|---|---|---|---|---|")
        for r in rows:
            w(f"| {r.key} | {r.traces} | {r.complete + r.complete_so_far} | {r.traced_pct:.0%} | {r.gap or ''} | "
              f"{r.orphan or ''} | {r.conflicted or ''} |")
        w("")
        if fold:
            w("</details>")
            w("")


_CLOSED = {Decision.FALSE_POSITIVE, Decision.WAIVED, Decision.FIXED}


def _struck(f: Finding, text: str) -> str:
    return f"~~{text}~~" if f.status in _CLOSED else text


def _decision(f: Finding) -> str:
    if f.review is None:
        return ""
    text = f.status.value.replace("_", " ").title()
    return f"{text}: {f.review.comment.replace('|', '/')}" if f.review.comment else text


def _review(result: RunResult, w) -> None:
    review = result.review
    if review is None:
        return
    reviewed = {k: v for k, v in review.by_decision.items() if k != Decision.OPEN.value}
    w("**Review:** " + (", ".join(f"{k.replace('_', ' ').title()} {v}" for k, v in reviewed.items()) or "no decisions yet")
      + f" · Open {review.by_decision.get(Decision.OPEN.value, 0)}"
      + f" · **{review.new_findings} new**" + (f" since run `{review.previous_run_id}`" if review.previous_run_id else " (first run)")
      + f" · {len(review.no_longer_raised)} no longer raised. Decisions are entered in `QA_Register.xlsx` and carried forward by fingerprint.")
    w("")
    if review.waivers:
        w("**Waivers** (`config/waivers.toml`): " + "; ".join(
            f"{u.id} {u.rule} `{u.subject}` " + ("expired" if u.status == "EXPIRED" else f"{u.matched} finding(s)")
            + (f" ({u.overridden} with the checker's own decision)" if u.overridden else "")
            for u in review.waivers))
        w("")
    if review.no_longer_raised:
        w(f"<details><summary>No longer raised ({len(review.no_longer_raised)})</summary>")
        w("")
        w("| Rule | Item | Finding (as last raised) | Last raised | Last decision |")
        w("|---|---|---|---|---|")
        for g in review.no_longer_raised:
            w(f"| {g.rule_id} | {g.subject_key} | {g.message.replace('|', '/')} | {g.last_raised} | {g.decision.value.replace('_', ' ').title()} |")
        w("")
        w("</details>")
        w("")


def _plc_io(result: RunResult, labels: dict[str, str]) -> list[tuple]:
    from drawing_qa.model.enums import Predicate

    channels: dict[str, list] = defaultdict(list)
    for c in result.claims:
        if c.predicate is Predicate.HAS_CHANNEL:
            channels[c.object_key].append(c.value)
    rows = []
    for c in result.claims:
        if c.predicate is not Predicate.INSTALLED_IN or c.value.get("source") != "slot_sheet":
            continue
        chs = channels.get(c.subject_key, [])
        with_device = sum(bool(v["devices"]) for v in chs)
        spare = sum(bool(v["spare"]) and not v["devices"] for v in chs)
        rows.append(
            (
                c.subject_key,
                labels.get(c.source.drawing_id, "?"),
                c.value.get("part") or "?",
                c.value.get("module_type") or "?",
                len(chs),
                with_device,
                spare,
                len(chs) - with_device - spare,
            )
        )
    return sorted(rows, key=lambda r: (r[0].split("/S")[0], int(r[0].split("/S")[1])))


def render_report(result: RunResult, summary: RunSummary) -> str:
    info = result.info
    out: list[str] = []
    w = out.append
    labels = {d.id: d.label for d in result.drawings}

    w("# Drawing QA Report")
    w("")
    w(f"- **Run:** `{info.run_id}`")
    w(f"- **Input:** `{info.input_root}`")
    w(f"- **Started:** {info.started_at:%Y-%m-%d %H:%M:%S} UTC")
    w(f"- **Tool version:** {info.tool_version} · **Config:** `{info.config_hash or 'defaults'}`")
    w("")

    # --- Summary -----------------------------------------------------------
    by_sev = Counter(f.severity for f in result.findings)
    w("## Summary")
    w("")
    w(f"**{len(result.findings)} finding(s)**: " + " · ".join(f"{s.value.title()} {by_sev.get(s, 0)}" for s in _SEVERITIES))
    w("")
    _review(result, w)
    not_built = [s.name for s in result.stages if s.status is StageStatus.NOT_IMPLEMENTED]
    if not_built:
        w(f"> Not yet built: {', '.join(not_built)}. Checks that need linked connectivity or signal traces "
          "(WIRE, PLC, PATH, TERM, DEV, COMP) are not run yet, so a clean category here does not mean a clean package.")
        w("")

    # --- Findings by rule ---------------------------------------------------
    w("## Findings by rule")
    w("")
    w("| Rule | Title | Category | Findings | Severity |")
    w("|---|---|---|---|---|")
    findings_by_rule: dict[str, list[Finding]] = defaultdict(list)
    for f in result.findings:
        findings_by_rule[f.rule_id].append(f)
    for rr in result.rule_runs:
        fs = findings_by_rule.get(rr.rule_id, [])
        sev = Counter(f.severity.value.title() for f in fs)
        sev_text = ", ".join(f"{k} {v}" for k, v in sev.items()) if fs else ("—" if rr.status is StageStatus.DONE else rr.status.value)
        w(f"| {rr.rule_id} | {rr.title} | {rr.category} | {len(fs)} | {sev_text} |")
    w("")

    # --- Details, most severe rules first ------------------------------------
    w("## Findings")
    w("")
    order = sorted(findings_by_rule, key=lambda rid: (min(_SEVERITIES.index(f.severity) for f in findings_by_rule[rid]), rid))
    titles = {rr.rule_id: rr.title for rr in result.rule_runs}
    for rid in order:
        fs = findings_by_rule[rid]
        w(f"### {rid} {titles.get(rid, '')} ({len(fs)})")
        w("")
        repetitive = len({f.subject_key for f in fs}) <= len(fs) / 2  # e.g. 233 x CXXXXX
        if len(fs) <= _MAX_LISTED or not repetitive:
            if result.review is not None:
                w("| ID | Severity | Finding | Recommendation | Decision |")
                w("|---|---|---|---|---|")
                for f in fs:
                    w(f"| {f.id} | {f.severity.value.title()} | {_struck(f, _msg(f))} | {f.recommendation or ''} | {_decision(f)} |")
            else:
                w("| ID | Severity | Finding | Recommendation |")
                w("|---|---|---|---|")
                for f in fs:
                    w(f"| {f.id} | {f.severity.value.title()} | {_msg(f)} | {f.recommendation or ''} |")
        else:
            # Many similar findings (typically placeholders): group by text and sheet.
            groups: dict[str, Counter[str]] = defaultdict(Counter)
            for f in fs:
                groups[f.subject_key][_sheet(f, labels)] += 1
            w("| Text | Count | Sheets |")
            w("|---|---|---|")
            for key, sheets in sorted(groups.items(), key=lambda kv: -sum(kv[1].values())):
                listed = ", ".join(f"{s} ({n})" for s, n in sorted(sheets.items()))
                w(f"| `{key}` | {sum(sheets.values())} | {listed} |")
            w("")
            w(f"Every occurrence, with its grid reference, is in `findings.json`. Recommendation: {fs[0].recommendation or '—'}")
        w("")

    # --- Signal traces ------------------------------------------------------------
    if result.traces:
        _traces(result, w)

    # --- PLC I/O read from the schematics -------------------------------------
    io_rows = _plc_io(result, labels)
    if io_rows:
        w("## PLC I/O (from the schematics)")
        w("")
        w("| Module | Sheet | Part | Type | Channels | With device | Spare | Other |")
        w("|---|---|---|---|---|---|---|---|")
        for row in io_rows:
            w("| " + " | ".join(str(x) for x in row) + " |")
        w("")
        w("*Other*: channel rows with a description but no device tag in the row. On this package these are the second "
          "contact of dual-channel safety devices, supply-healthy monitoring (sheet 046), and devices labelled once for "
          "several channels (collision lasers on sheet 045). Not a finding on its own.")
        w("")

    # --- Pipeline and input -------------------------------------------------
    w("## Pipeline stages")
    w("")
    w("| Stage | Status | Note |")
    w("|---|---|---|")
    for s in result.stages:
        w(f"| {s.name} | {s.status.value} | {s.note} |")
    w("")

    w("## Input")
    w("")
    w("| Type | Files | Sheets | Sheet kinds | Identifiers found (unique) | Placeholders |")
    w("|---|---|---|---|---|---|")
    for dt, s in summary.by_doc_type.items():
        idents = ", ".join(
            f"{k.lower()} {s.identifiers_by_kind[k]} ({s.unique_identifiers_by_kind.get(k, 0)})" for k in s.identifiers_by_kind
        ) or "—"
        w(f"| {dt} | {s.files} | {s.sheets} | {_kinds(s.sheets_by_kind)} | {idents} | {s.placeholders} |")
    w("")

    w("## Parse issues")
    w("")
    if not result.issues:
        w("None.")
    else:
        w("| Code | Count |")
        w("|---|---|")
        for code, n in summary.issues_by_code.items():
            w(f"| {code} | {n} |")
        coverage = [i for i in result.issues if i.code in _COVERAGE_ISSUES or i.code is IssueCode.DUPLICATE_SHEET]
        if coverage:
            w("")
            for i in coverage[:50]:
                where = i.drawing_id or i.file_path or "package"
                w(f"- `{i.code.value}` {where}: {i.message}")
            if len(coverage) > 50:
                w(f"- … {len(coverage) - 50} more in review.json")
    w("")
    _decoded(result, w)
    return "\n".join(out)


def _decoded(result: RunResult, w) -> None:
    """Sheets read with --ocr by decoding their letter strokes, and those that could not be."""
    decoded = [d for d in result.drawings if d.decoded is not None]
    left = [i for i in result.issues if i.code is IssueCode.SHX_NOT_DECODED]
    if not decoded and not left:
        return
    w("## Sheets decoded from line work")
    w("")
    w("Plotted without SHX text notes, these sheets were read with `--ocr` by matching each letter's strokes against "
      "letters learned from the readable sheets; a letter at a text size those sheets do not use is matched scaled to "
      "its line's height. Only words whose every letter matched are used; nothing is "
      f"reported as missing from them, and findings resting on them are marked {DECODED_MARK}. Re-plotting with "
      "PDFSHX = 1 is still the better fix.")
    w("")
    if decoded:
        w("| Sheet | Letters recognised / letter shapes | Words used | Words dropped |")
        w("|---|---|---|---|")
        for d in decoded:
            scaled = f" ({d.decoded.scaled} by scale)" if d.decoded.scaled else ""
            w(f"| {d.label} | {d.decoded.letters} / {d.decoded.shapes}{scaled} | {d.decoded.words_kept} | {d.decoded.words_dropped} |")
        w("")
    if quiet := quiet_rules(result.drawings):
        w(f"A cable block diagram or termination sheet was decoded, so these rules are quiet for the whole package "
          f"(what they report missing could be on a decoded sheet): {', '.join(quiet)}.")
        w("")
    if left:
        labels = {d.id: d.label for d in result.drawings}
        w("Not decoded:")
        w("")
        for i in left:
            w(f"- {labels.get(i.drawing_id or '', i.file_path)}: {i.message}")
        w("")


def write_report(result: RunResult, summary: RunSummary, out_dir: Path) -> Path:
    path = out_dir / "report.md"
    path.write_text(render_report(result, summary), encoding="utf-8")
    return path
