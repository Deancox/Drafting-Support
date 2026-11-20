"""Saves the checker's decisions to the register, holding them while Excel has it open.

Every decision is first held in QA_Register.pending.json beside the register, with what the row's yellow cells
held when the decision was made ("expected"). Saving re-reads the register, then for each held decision:

- the row still holds "expected": the decision is written;
- the row already holds the decision: nothing to do;
- the row holds something else (edited in Excel, or a run happened): a Conflict, nothing written, still held
  until the checker picks which to keep (resolve);
- the fingerprint is no longer on the register: dropped, and reported as gone.

So a decision is never overwritten without the checker seeing it, and held decisions survive a restart.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from drawing_qa.viewer.register_writer import Entry, RegisterLocked, read_decisions, write_decisions

PENDING_NAME = "QA_Register.pending.json"


@dataclass(frozen=True)
class Conflict:
    fingerprint: str
    mine: Entry
    theirs: Entry


@dataclass(frozen=True)
class SaveResult:
    saved: int = 0
    waiting: int = 0
    conflicts: list[Conflict] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)


@dataclass
class _Held:
    new: Entry
    expected: Entry


class DecisionSaver:
    def __init__(self, register: Path) -> None:
        self.register = Path(register)
        self.pending_path = self.register.with_name(PENDING_NAME)
        self.problem: str | None = None  # a line to show when the pending file could not be read
        self._register_view = read_decisions(self.register)
        self._held: dict[str, _Held] = self._load_pending()

    # --- what the window shows ------------------------------------------------------------------

    @property
    def decisions(self) -> dict[str, Entry]:
        """The register's decisions, with the held ones shown as decided."""
        view = dict(self._register_view)
        view.update({fp: h.new for fp, h in self._held.items() if fp in view})
        return view

    @property
    def waiting(self) -> int:
        return len(self._held)

    # --- deciding and saving --------------------------------------------------------------------

    def decide(self, changes: dict[str, Entry]) -> SaveResult:
        for fp, new in changes.items():
            held = self._held.get(fp)
            if held is not None:
                held.new = new  # keep what the row held when it was first decided
            else:
                self._held[fp] = _Held(new, self._register_view.get(fp, Entry("OPEN", "", "", "")))
        self._save_pending()
        return self.flush()

    def flush(self) -> SaveResult:
        if not self._held:
            return SaveResult()
        current = read_decisions(self.register)
        self._register_view = current
        to_write: dict[str, Entry] = {}
        conflicts: list[Conflict] = []
        gone: list[str] = []
        for fp, held in list(self._held.items()):
            now = current.get(fp)
            if now is None:
                gone.append(fp)
                del self._held[fp]
            elif now == held.new:
                del self._held[fp]
            elif now == held.expected:
                to_write[fp] = held.new
            else:
                conflicts.append(Conflict(fp, held.new, now))
        saved = 0
        if to_write:
            try:
                write_decisions(self.register, to_write)
            except RegisterLocked:
                pass
            else:
                saved = len(to_write)
                for fp, entry in to_write.items():
                    del self._held[fp]
                    self._register_view[fp] = entry
        self._save_pending()
        return SaveResult(saved=saved, waiting=len(self._held), conflicts=conflicts, gone=gone)

    def resolve(self, fingerprint: str, keep: Literal["mine", "theirs"]) -> SaveResult:
        held = self._held.get(fingerprint)
        if held is not None:
            if keep == "mine":
                held.expected = read_decisions(self.register).get(fingerprint, held.expected)
            else:
                del self._held[fingerprint]
            self._save_pending()
        return self.flush()

    # --- the pending file -----------------------------------------------------------------------

    def _load_pending(self) -> dict[str, _Held]:
        if not self.pending_path.is_file():
            return {}
        try:
            data = json.loads(self.pending_path.read_text(encoding="utf-8"))
            return {h["fingerprint"]: _Held(Entry(**h["new"]), Entry(**h["expected"])) for h in data["held"]}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            aside = self.pending_path.with_name(f"QA_Register.pending.unreadable-{datetime.now():%Y%m%d-%H%M%S}.json")
            try:
                os.replace(self.pending_path, aside)
            except OSError:
                aside = self.pending_path
            self.problem = f"{PENDING_NAME} could not be read ({exc}); it was kept as {aside.name}"
            return {}

    def _save_pending(self) -> None:
        if not self._held:
            self.pending_path.unlink(missing_ok=True)
            return
        data = {
            "register": self.register.name,
            "held": [{"fingerprint": fp, "new": asdict(h.new), "expected": asdict(h.expected)} for fp, h in self._held.items()],
        }
        temp = self.pending_path.with_suffix(".saving")
        temp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(temp, self.pending_path)
