"""Review carry-forward (M5): the checker's decisions from the QA register, kept across runs.

Per run, before the outputs are written:
  1. read the Decision columns (Findings and No Longer Raised tabs) of the QA register(s) in the
     output folder;
  2. store decisions that changed in qa_review.sqlite (appended, never overwritten); rows a waiver
     wrote are not taken as the checker's own;
  3. record which fingerprints this run raised;
  4. put the decision on every finding: the checker's own (anything but OPEN) first, then an active
     waiver from waivers.toml, else OPEN; with the first-seen date;
  5. list what this run no longer raises: everything the previous run raised, plus anything with a
     decision other than FIXED, until the checker marks it FIXED.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

from drawing_qa.config import Settings
from drawing_qa.model.enums import Decision, IssueCode, StageStatus
from drawing_qa.model.run import GoneFinding, ReviewSummary, RunResult, StageResult
from drawing_qa.model.source import Issue
from drawing_qa.report.register import REGISTER_NAME
from drawing_qa.review.register_reader import RegisterError, read_register
from drawing_qa.review.store import ReviewStore
from drawing_qa.review.waivers import is_waiver_generated, waive

STORE_NAME = "qa_review.sqlite"


def _registers(out_dir: Path) -> list[Path]:
    """QA_Register.xlsx plus any run-named copies written while it was locked, oldest save first,
    so the most recently saved decisions win."""
    found = [p for p in [out_dir / REGISTER_NAME, *out_dir.glob("QA_Register-*.xlsx")] if p.is_file()]
    return sorted(found, key=lambda p: p.stat().st_mtime)


def carry_forward(result: RunResult, out_dir: Path, settings: Settings | None = None, today: date | None = None) -> ReviewSummary:
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = result.info.run_id
    summary = ReviewSummary(store_path=STORE_NAME)
    waived, summary.waivers = waive(result.findings, settings.waivers if settings else [], run_id, today or date.today())
    with ReviewStore(out_dir / STORE_NAME) as store:
        read = []
        for path in _registers(out_dir):
            try:
                entries, problems = read_register(path)
            except RegisterError as exc:
                result.issues.append(Issue(code=IssueCode.REGISTER_UNREADABLE, message=str(exc), file_path=path.name))
                continue
            for p in problems:
                result.issues.append(Issue(code=IssueCode.REGISTER_ROW_SKIPPED, message=p, file_path=path.name))
            own = [e for e in entries if not is_waiver_generated(e.decision, e.comment)]
            summary.decisions_recorded += store.record(own, run_id)
            read.append(path.name)
        summary.register_read = ", ".join(read) or None

        previous = store.previous_run(run_id)
        before = {s.fingerprint for s in store.seen_in(previous)} if previous else set()
        store.record_run(result.info, result.findings)
        first_seen = store.first_seen()

        uses = {u.id: u for u in summary.waivers}
        for f in result.findings:
            own = store.latest(f.fingerprint)
            if own is not None and own.decision is not Decision.OPEN:
                f.review = own
                if f.fingerprint in waived:
                    uses[waived[f.fingerprint].waiver].overridden += 1
            else:
                f.review = waived.get(f.fingerprint) or own
            f.status = f.review.decision if f.review else Decision.OPEN
            f.first_seen = first_seen.get(f.fingerprint)

        now = {f.fingerprint for f in result.findings}
        decided = store.decided()
        still_listed = {fp for fp, d in decided.items() if d not in (Decision.OPEN, Decision.FIXED)}
        for fp in (before | still_listed) - now:
            seen = store.last_seen(fp)
            if seen is None:
                continue
            s, last_raised = seen
            review = store.latest(fp)
            summary.no_longer_raised.append(
                GoneFinding(fingerprint=fp, rule_id=s.rule_id, subject_key=s.subject_key, severity=s.severity, message=s.message,
                            last_raised=last_raised, decision=review.decision if review else Decision.OPEN, review=review)
            )
        summary.no_longer_raised.sort(key=lambda g: (g.rule_id, g.subject_key, g.fingerprint))
        summary.previous_run_id = previous
        summary.new_findings = len(now - before)
        summary.by_decision = dict(sorted(Counter(f.status.value for f in result.findings).items()))

    reviewed = sum(n for d, n in summary.by_decision.items() if d != Decision.OPEN.value)
    note = (f"{summary.decisions_recorded} decision(s) read from the QA register; {reviewed} of {len(result.findings)} findings "
            f"carry a decision; {summary.new_findings} new" + (f" since run {previous}" if previous else " (first run)")
            + f"; {len(summary.no_longer_raised)} no longer raised")
    if summary.waivers:
        active = [u for u in summary.waivers if u.status == "ACTIVE"]
        note += f"; {len(active)} waiver(s) active, matching {sum(u.matched for u in active)} finding(s)"
        expired = [u.id for u in summary.waivers if u.status == "EXPIRED"]
        unused = [u.id for u in active if not u.matched]
        if expired:
            note += f"; expired: {', '.join(expired)}"
        if unused:
            note += f"; matching nothing: {', '.join(unused)}"
    result.stages.append(StageResult(name="review", status=StageStatus.DONE, note=note))
    result.review = summary
    return summary
