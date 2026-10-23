"""Drawing Validation (DRW): title blocks, sheet identity, cross-references, superseded names."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import PurePosixPath

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import EntityType, Predicate, Severity
from drawing_qa.model.findings import Finding
from drawing_qa.rules.base import Category, Requirement, Rule, RuleMeta
from drawing_qa.rules.context import RuleContext
from drawing_qa.rules.findings import FindingBuilder
from drawing_qa.rules.registry import rule
from drawing_qa.rules.tag import device_claims

_DRAWINGS_ONLY = frozenset({Requirement.DRAWINGS})
_FILE_NAME = re.compile(r"^(?P<drawing>[A-Z0-9]+)-(?P<sheet>\d+[A-Z]?)$", re.I)


@rule
class CrossReferenceExists(Rule):
    meta = RuleMeta(
        rule_id="DRW-001",
        title="Cross-Reference Exists",
        category=Category.DRW,
        default_severity=Severity.MINOR,
        description=(
            "Sheet references such as '(SHT 35)' or '(CONT. SHT 204)' that point to sheets not in the package: one finding "
            "per referenced drawing, listing its missing sheets and where they are referenced. Sheets in the package whose "
            "text cannot be read (NO_SHX_TEXT) are not missing."
        ),
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        from drawing_qa.rules.term import ranges

        missing: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
        unreadable_numbers = {n for _, n in ctx.unreadable_sheets}
        for c in ctx.claims_of(Predicate.CROSS_REF):
            key = (c.value["drawing_no"], int(c.value["sheet"]))
            # Same drawing number first; otherwise any sheet with that number (e.g. "REF. SCHEM. SHT 015"
            # on a sheet whose own drawing number is wrong is reported by DRW-008, not here).
            if key in ctx.sheets_by_number or key[1] in ctx.sheet_numbers:
                continue
            if key in ctx.unreadable_sheets or key[1] in unreadable_numbers:
                continue
            missing[key[0]][key[1]].append(c)
        for drawing_no, sheets in sorted(missing.items()):
            claims = [c for cs in sheets.values() for c in cs]
            from_sheets = sorted({ctx.sheet_of(c).label for c in claims})
            listed = ranges(sorted(sheets))
            yield make(
                subject_type="DRAWING",
                subject_key=drawing_no,
                message=(f"{len(claims)} reference(s) point to {len(sheets)} sheet(s) of {drawing_no} that are not in the package: "
                         f"sheet(s) {listed}; referenced from {', '.join(from_sheets[:8])}{' …' if len(from_sheets) > 8 else ''}"),
                evidence=claims,
                discriminator=listed,
                recommendation=f"Add {drawing_no} sheet(s) {listed} to the package, or correct the references",
            )


@rule
class StatusConsistency(Rule):
    meta = RuleMeta(
        rule_id="DRW-002",
        title="Revision / Status Consistency",
        category=Category.DRW,
        default_severity=Severity.MINOR,
        description="Sheets of one drawing number at the same revision show different drawing statuses.",
        requires=_DRAWINGS_ONLY,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        groups = defaultdict(list)
        for d in ctx.active_drawings:
            if d.rev and d.status:
                groups[(d.drawing_no, d.rev)].append(d)
        for (drawing_no, rev), sheets in sorted(groups.items()):
            by_status = defaultdict(list)
            for d in sheets:
                by_status[d.status].append(d)
            if len(by_status) < 2:
                continue
            majority = max(by_status, key=lambda s: len(by_status[s]))
            for status, odd in sorted(by_status.items()):
                if status == majority:
                    continue
                for d in odd:
                    yield make(
                        subject_type="DRAWING",
                        subject_key=d.label,
                        message=f"{d.label} rev {rev} is '{status}' while {len(by_status[majority])} other sheet(s) of {drawing_no} rev {rev} are '{majority}'",
                        discriminator=f"{rev}/{status}",
                        recommendation="Confirm the status; a revision letter should not carry two statuses",
                    )


@rule
class TitleBlockComplete(Rule):
    meta = RuleMeta(
        rule_id="DRW-003",
        title="Title Block Complete",
        category=Category.DRW,
        default_severity=Severity.MAJOR,
        description="Drawing number, sheet, revision, title or status could not be found in the title block.",
        requires=_DRAWINGS_ONLY,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for d in ctx.active_drawings:  # sheets with no readable text are excluded: NO_SHX_TEXT reports them
            if not d.missing_title_fields or d.id in ctx.decoded_ids:  # decoded: identity is from the file name
                continue
            yield make(
                subject_type="DRAWING",
                subject_key=d.label,
                message=f"{d.label} ({d.file_path}): title block is missing {', '.join(d.missing_title_fields)}",
                discriminator=",".join(d.missing_title_fields),
                recommendation="Complete the title block",
                severity=Severity.MINOR if d.missing_title_fields == ["title"] else None,
            )


@rule
class FolderTypeMatch(Rule):
    meta = RuleMeta(
        rule_id="DRW-004",
        title="Folder / Type Match",
        category=Category.DRW,
        default_severity=Severity.MAJOR,
        description="The title block describes a different drawing type from the input folder it was found in.",
        requires=_DRAWINGS_ONLY,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for d in ctx.active_drawings:
            if d.title_doc_type is None or d.title_doc_type is d.doc_type:
                continue
            yield make(
                subject_type="DRAWING",
                subject_key=d.label,
                message=f"{d.label} is a '{d.descriptor}' ({d.title_doc_type.value}) but is in the {d.doc_type.value} folder",
                recommendation=f"Move {PurePosixPath(d.file_path).name} to the {d.title_doc_type.value} folder",
            )


@rule
class HomeSheetSupplied(Rule):
    meta = RuleMeta(
        rule_id="DRW-005",
        title="Tag Home Sheet Supplied",
        category=Category.DRW,
        default_severity=Severity.INFO,
        description="Device tags point to a home sheet that is not in the package, so they cannot be validated.",
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        missing: dict[tuple[str | None, int], list[str]] = defaultdict(list)
        for tag, claims in device_claims(ctx).items():
            sheet = int(claims[0].value["sheet"])
            status, drawing, sheets = ctx.home(claims[0].value["ffi"], sheet)
            if status == "package" and not sheets and (drawing, sheet) not in ctx.unreadable_sheets:
                missing[(drawing, sheet)].append(tag)
        for (drawing, sheet), tags in sorted(missing.items(), key=lambda kv: (kv[0][0] or "", kv[0][1])):
            tags.sort()
            name = f"{drawing} sheet {sheet:03d}" if drawing else f"Sheet {sheet:03d}"
            yield make(
                subject_type="DRAWING",
                subject_key=name,
                message=f"{name} is not in the package; {len(tags)} tag(s) cannot be checked: {', '.join(tags[:8])}{' …' if len(tags) > 8 else ''}",
                discriminator=str(sheet),
                recommendation=f"Add {name} to the package",
            )


@rule
class ExternalReference(Rule):
    meta = RuleMeta(
        rule_id="DRW-009",
        title="Separate Document Reference",
        category=Category.DRW,
        default_severity=Severity.INFO,
        description=(
            "Tags and wires whose FFI belongs to a separate package (config [identifiers.ffi_drawings] = EXTERNAL) are "
            "listed as references to that document and not checked. An FFI with no mapping is reported so it can be added."
        ),
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        if not ctx.settings.identifiers.ffi_drawings:
            return
        groups: dict[tuple[str, str], list[Claim]] = defaultdict(list)
        for c in ctx.claims_of(Predicate.OCCURS):
            if c.subject_type not in (EntityType.DEVICE, EntityType.WIRE) or "ffi" not in c.value or "sheet" not in c.value:
                continue
            status, _, _ = ctx.home(c.value["ffi"], int(c.value["sheet"]))
            if status in ("external", "unmapped"):
                groups[(status, c.value["ffi"])].append(c)
        for (status, ffi), claims in sorted(groups.items()):
            keys = sorted({c.subject_key for c in claims})
            where = sorted({ctx.sheet_of(c).label for c in claims})
            listed = ", ".join(keys[:10]) + (" …" if len(keys) > 10 else "")
            if status == "external":
                yield make(
                    subject_type="FFI",
                    subject_key=ffi,
                    message=f"Separate document: {listed} (FFI {ffi}) referenced on {', '.join(where)} belong to a separate package that is not supplied; not checked",
                    evidence=claims,
                    discriminator=status,
                    recommendation=f"Check these references against the {ffi} package",
                )
            else:
                yield make(
                    subject_type="FFI",
                    subject_key=ffi,
                    message=f"FFI {ffi} is not linked to a drawing number: {listed} on {', '.join(where)} cannot be checked",
                    evidence=claims,
                    discriminator=status,
                    severity=Severity.MINOR,
                    recommendation=f"Add {ffi} to [identifiers.ffi_drawings] in config/project.toml (drawing number, or EXTERNAL)",
                )


@rule
class SupersededDesignation(Rule):
    meta = RuleMeta(
        rule_id="DRW-006",
        title="Superseded Designation",
        category=Category.DRW,
        default_severity=Severity.MAJOR,
        description="A designation that has been renamed (config [identifiers.superseded]) is still used.",
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for c in ctx.claims_of(Predicate.OCCURS):
            new = c.value.get("superseded_by")
            if not new:
                continue
            function = c.value.get("function")
            written = c.source.raw_text
            fixed = f"={function}+{new}" if function else new
            yield make(
                subject_type=c.subject_type.value,
                subject_key=c.subject_key,
                message=f"'{written}' at {make.where(c.source)} uses the old designation {c.subject_key}; it is now {new}",
                evidence=[c],
                recommendation=f"Replace with {fixed}",
            )


@rule
class ConflictingSheetCopies(Rule):
    meta = RuleMeta(
        rule_id="DRW-007",
        title="Conflicting Sheet Copies",
        category=Category.DRW,
        default_severity=Severity.MAJOR,
        description="Two files carry the same drawing number and sheet in their title blocks but their content differs.",
        requires=_DRAWINGS_ONLY,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        done: set[frozenset[str]] = set()
        for d in ctx.active_drawings:
            for other_id in d.conflicts_with:
                if other_id not in ctx.drawing:
                    continue  # the other copy is in a document type that is not checked
                pair = frozenset({d.id, other_id})
                if pair in done:
                    continue
                done.add(pair)
                other = ctx.drawing[other_id]
                yield make(
                    subject_type="DRAWING",
                    subject_key=d.label,
                    message=f"{d.file_path} and {other.file_path} both say {d.label} in the title block but differ (rev {d.rev} / {other.rev})",
                    discriminator="|".join(sorted([d.file_path, other.file_path])),
                    recommendation="Keep one current sheet; correct the sheet number on the other",
                )


@rule
class FileNameMatchesTitleBlock(Rule):
    meta = RuleMeta(
        rule_id="DRW-008",
        title="File Name / Title Block Match",
        category=Category.DRW,
        default_severity=Severity.MAJOR,
        description="The file is named <drawing>-<sheet>.pdf but its title block says a different drawing or sheet.",
        requires=_DRAWINGS_ONLY,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for d in ctx.drawings:
            m = _FILE_NAME.match(PurePosixPath(d.file_path).stem)
            if not m or d.missing_title_fields and "sheet" in d.missing_title_fields:
                continue
            same_drawing = m["drawing"].upper() == d.drawing_no.upper()
            same_sheet = m["sheet"].lstrip("0") == d.sheet.lstrip("0")
            if same_drawing and same_sheet:
                continue
            yield make(
                subject_type="DRAWING",
                subject_key=PurePosixPath(d.file_path).name,
                message=f"{PurePosixPath(d.file_path).name}: title block says drawing {d.drawing_no} sheet {d.sheet}",
                discriminator=f"{d.drawing_no}/{d.sheet}",
                recommendation="Correct the title block or the file name",
            )


def _one_letter_apart(a: str, b: str) -> bool:
    """One letter changed, added or dropped."""
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1 :] == short for i in range(len(long_)))


def _inflection(a: str, b: str) -> bool:
    """Plural / past-tense pairs are not misspellings (LAMP / LAMPS, CLOSE / CLOSED, SUPPLIES / SUPPLIED)."""
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    if long_ in (short + "S", short + "D", short + "ED"):
        return True
    return len(a) == len(b) and a[:-1] == b[:-1] and {a[-1], b[-1]} <= {"S", "D", "R", "N"}


@rule
class Spelling(Rule):
    meta = RuleMeta(
        rule_id="DRW-010",
        title="Spelling",
        category=Category.DRW,
        default_severity=Severity.MINOR,
        description=(
            "A word used rarely in the package that is one letter away from a word used often in it, e.g. "
            "'STOBE' once next to 'STROBE' eight times. Correct words that trip it go in [spelling] known_words."
        ),
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        cfg = ctx.settings.spelling
        known = {w.upper() for w in cfg.known_words}
        by_word: dict[str, list[Claim]] = defaultdict(list)
        for c in ctx.claims_of(Predicate.WORD):
            by_word[c.subject_key].append(c)
        common = {w: len(cs) for w, cs in by_word.items() if len(cs) >= cfg.min_common}
        for word, claims in sorted(by_word.items()):
            if len(claims) > cfg.max_rare or word in known or word in common:
                continue
            matches = sorted(
                (w for w in common if w[0] == word[0] and _one_letter_apart(word, w) and not _inflection(word, w)),
                key=lambda w: -common[w],
            )
            if not matches:
                continue
            best = matches[0]
            for c in claims:
                yield make(
                    subject_type="TEXT",
                    subject_key=word,
                    message=f"'{word}' at {make.where(c.source)} looks misspelt: '{best}' is used {common[best]} times in the package",
                    evidence=[c],
                    recommendation=f"Check the spelling; did you mean {best}?",
                )
