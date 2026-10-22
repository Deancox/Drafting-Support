"""SQLite review store (DATA_MODEL.md sections 4.7 and 9): one file per output folder.

Holds what has to outlive a run: which fingerprints each run raised (for "first seen" and
"no longer raised"), and every decision the checker has entered in the QA register. Reviews
are appended, never updated; the latest row per fingerprint is the current decision.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from drawing_qa.model.enums import Decision
from drawing_qa.model.findings import Finding, FindingReview
from drawing_qa.model.run import RunInfo

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    input_root TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS findings_seen (
    fingerprint TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    rule_id TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    PRIMARY KEY (fingerprint, run_id)
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL,
    decision TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    reviewer TEXT NOT NULL DEFAULT '',
    decided_on TEXT NOT NULL DEFAULT '',
    recorded_in_run TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_fp ON reviews (fingerprint, id);
"""


@dataclass(frozen=True)
class RegisterEntry:
    """One row of the checker's columns, as read from the QA register."""

    fingerprint: str
    decision: Decision
    comment: str = ""
    reviewer: str = ""
    decided_on: str = ""


@dataclass(frozen=True)
class SeenFinding:
    fingerprint: str
    rule_id: str
    subject_key: str
    severity: str
    message: str


class ReviewStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.executescript(_SCHEMA)
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> ReviewStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- reviews ---------------------------------------------------------------------

    def latest(self, fingerprint: str) -> FindingReview | None:
        row = self.db.execute(
            "SELECT decision, comment, reviewer, decided_on, recorded_in_run FROM reviews "
            "WHERE fingerprint = ? ORDER BY id DESC LIMIT 1",
            (fingerprint,),
        ).fetchone()
        if row is None:
            return None
        return FindingReview(decision=Decision(row[0]), comment=row[1], reviewer=row[2], decided_on=row[3], recorded_in_run=row[4])

    def record(self, entries: list[RegisterEntry], run_id: str) -> int:
        """Store register rows that differ from the current decision. Returns how many changed.

        A blank OPEN row is only stored when it clears an earlier decision.
        """
        changed = 0
        now = datetime.now(UTC).isoformat(timespec="seconds")
        for e in entries:
            current = self.latest(e.fingerprint)
            if current is None and e.decision is Decision.OPEN and not e.comment:
                continue
            if current is not None and (current.decision, current.comment, current.reviewer, current.decided_on) == (
                e.decision, e.comment, e.reviewer, e.decided_on
            ):
                continue
            self.db.execute(
                "INSERT INTO reviews (fingerprint, decision, comment, reviewer, decided_on, recorded_in_run, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (e.fingerprint, e.decision.value, e.comment, e.reviewer, e.decided_on, run_id, now),
            )
            changed += 1
        self.db.commit()
        return changed

    # --- runs ------------------------------------------------------------------------

    def previous_run(self, before: str) -> str | None:
        row = self.db.execute(
            "SELECT run_id FROM runs WHERE run_id <> ? ORDER BY started_at DESC LIMIT 1", (before,)
        ).fetchone()
        return row[0] if row else None

    def record_run(self, info: RunInfo, findings: list[Finding]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?, ?)",
            (info.run_id, info.started_at.isoformat(), info.tool_version, info.config_hash, info.input_root),
        )
        self.db.executemany(
            "INSERT OR REPLACE INTO findings_seen VALUES (?, ?, ?, ?, ?, ?)",
            [(f.fingerprint, info.run_id, f.rule_id, f.subject_key, f.severity.value, f.message) for f in findings],
        )
        self.db.commit()

    def decided(self) -> dict[str, Decision]:
        """Fingerprint -> its current (latest) decision, for every fingerprint ever reviewed."""
        rows = self.db.execute(
            "SELECT fingerprint, decision FROM reviews r WHERE id = (SELECT MAX(id) FROM reviews WHERE fingerprint = r.fingerprint)"
        )
        return {fp: Decision(d) for fp, d in rows}

    def last_seen(self, fingerprint: str) -> tuple[SeenFinding, str] | None:
        """The finding as the last run that raised it recorded it, with that run's date."""
        row = self.db.execute(
            "SELECT s.fingerprint, s.rule_id, s.subject_key, s.severity, s.message, r.started_at FROM findings_seen s "
            "JOIN runs r USING (run_id) WHERE s.fingerprint = ? ORDER BY r.started_at DESC LIMIT 1",
            (fingerprint,),
        ).fetchone()
        return (SeenFinding(*row[:5]), row[5][:10]) if row else None

    def first_seen(self) -> dict[str, str]:
        """Fingerprint -> date (YYYY-MM-DD) of the first run that raised it."""
        rows = self.db.execute(
            "SELECT s.fingerprint, MIN(r.started_at) FROM findings_seen s JOIN runs r USING (run_id) GROUP BY s.fingerprint"
        )
        return {fp: started[:10] for fp, started in rows}

    def seen_in(self, run_id: str) -> list[SeenFinding]:
        rows = self.db.execute(
            "SELECT fingerprint, rule_id, subject_key, severity, message FROM findings_seen WHERE run_id = ? ORDER BY fingerprint",
            (run_id,),
        )
        return [SeenFinding(*r) for r in rows]
