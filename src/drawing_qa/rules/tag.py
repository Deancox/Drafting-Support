"""Tag Validation (TAG). Device tags are <Prefix>-<FFI><Sheet><Grid>, e.g. S-TN23N2."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import DocType, EntityType, Predicate, Severity
from drawing_qa.model.findings import Finding
from drawing_qa.rules.base import Category, Requirement, Rule, RuleMeta
from drawing_qa.rules.common import col_offset, row_offset
from drawing_qa.rules.context import RuleContext
from drawing_qa.rules.findings import FindingBuilder
from drawing_qa.rules.registry import rule

_NEEDS_GRID = frozenset({Requirement.CLAIMS, Requirement.DRAWINGS, Requirement.GRID})


def device_claims(ctx: RuleContext) -> dict[str, list[Claim]]:
    """Valid device tag occurrences grouped by tag."""
    out: dict[str, list[Claim]] = defaultdict(list)
    for c in ctx.claims_of(Predicate.OCCURS, EntityType.DEVICE):
        if c.value.get("format_valid", True):
            out[c.subject_key].append(c)
    return out


def home_sheets(ctx: RuleContext, ffi: str, sheet: int):
    """Sheets in this package where a tag or wire with this FFI and sheet number is drawn.
    Empty when the sheet is not supplied, or the FFI belongs to a separate package."""
    status, _, sheets = ctx.home(ffi, sheet)
    return sheets if status == "package" else []


@rule
class DeviceTagFormat(Rule):
    meta = RuleMeta(
        rule_id="TAG-001",
        title="Device Tag Format",
        category=Category.TAG,
        default_severity=Severity.MAJOR,
        description="Tag-like text that does not fit <Prefix>-<FFI><Sheet><Grid> (devices) or <AREA>-JB<n> (junction boxes).",
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for c in ctx.claims:
            if c.predicate is not Predicate.OCCURS or c.value.get("format_valid", True):
                continue
            expected = "<AREA>-JB<n>" if c.subject_type is EntityType.JUNCTION_BOX else "<Prefix>-<FFI><Sheet><Grid>, e.g. S-TN23N2"
            yield make(
                subject_type=c.subject_type.value,
                subject_key=c.subject_key,
                message=f"'{c.source.raw_text}' at {make.where(c.source)} does not fit the tag format {expected}",
                evidence=[c],
                recommendation="Correct the tag to the package format",
            )


@rule
class KnownPrefixFfi(Rule):
    meta = RuleMeta(
        rule_id="TAG-002",
        title="Known Prefix / FFI",
        category=Category.TAG,
        default_severity=Severity.MINOR,
        description="Device prefix or first field identifier not in the configured lists (no output while the lists are empty).",
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for tag, claims in sorted(device_claims(ctx).items()):
            first = claims[0]
            bad = [p for p in ("prefix", "ffi") if first.value.get(f"known_{p}") is False]
            if bad:
                yield make(
                    subject_type="DEVICE",
                    subject_key=tag,
                    message=f"{tag}: unknown {' and '.join(bad)} ({', '.join(first.value[p] for p in bad)})",
                    evidence=claims[:1],
                )


@rule
class SheetValidation(Rule):
    meta = RuleMeta(
        rule_id="TAG-003",
        title="Sheet Validation",
        category=Category.TAG,
        default_severity=Severity.MAJOR,
        description="A device tag is referenced, its home sheet is in the package, but the tag is not on that sheet.",
        requires=_NEEDS_GRID,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        all_devices = device_claims(ctx)
        for tag, claims in sorted(all_devices.items()):
            sheet = int(claims[0].value["sheet"])
            homes = home_sheets(ctx, claims[0].value["ffi"], sheet)
            if not homes:
                continue  # home sheet not supplied: DRW-005
            home_ids = {d.id for d in homes}
            if any(c.source.drawing_id in home_ids for c in claims):
                continue
            if home_ids & ctx.decoded_ids:
                continue  # home sheet decoded from its strokes (--ocr): the tag may be a word that did not decode
            near = _near_matches(tag, claims[0], all_devices, home_ids)
            refs = sorted({make.where(c.source).split(" grid")[0] for c in claims})
            home_label = homes[0].label
            hint = f"; sheet {homes[0].sheet} has {', '.join(near)}" if near else ""
            yield make(
                subject_type="DEVICE",
                subject_key=tag,
                message=f"{tag} is referenced on {', '.join(refs)} but is not drawn on its home sheet {home_label}{hint}",
                evidence=claims,
                recommendation=f"Check the reference; did you mean {near[0]}?" if near else "Add the device to its home sheet or correct the reference",
            )


def _near_matches(tag: str, claim: Claim, devices: dict[str, list[Claim]], home_ids: set[str]) -> list[str]:
    """Tags on the home sheet with the same prefix, FFI and sheet whose grid is close."""
    v = claim.value
    grid = v["grid"]
    found = []
    for other, cs in devices.items():
        if other == tag or not any(c.source.drawing_id in home_ids for c in cs):
            continue
        ov = cs[0].value
        if (ov["prefix"], ov["ffi"], ov["sheet"]) != (v["prefix"], v["ffi"], v["sheet"]):
            continue
        dr, dc = row_offset(grid, ov["grid"]), col_offset(grid, ov["grid"])
        if dr is not None and dc is not None and abs(dr) <= 2 and abs(dc) <= 1:
            found.append((abs(dr) + abs(dc), other))
    return [t for _, t in sorted(found)]


@rule
class GridValidation(Rule):
    meta = RuleMeta(
        rule_id="TAG-004",
        title="Grid Validation",
        category=Category.TAG,
        default_severity=Severity.MINOR,
        description=(
            "On its home sheet, the tag label is not near the grid cell its tag encodes. Labels are drawn "
            "above their symbol, so up to 2 rows above and 1 column either side is accepted. Uses the label "
            "position until symbol positions are read (M3)."
        ),
        requires=_NEEDS_GRID,
    )

    max_rows_above = 2
    max_rows_below = 1  # also covers the collision shift (D2 -> E2)
    max_cols = 1

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for tag, claims in sorted(device_claims(ctx).items()):
            grid = claims[0].value["grid"]
            homes = {d.id for d in home_sheets(ctx, claims[0].value["ffi"], int(claims[0].value["sheet"]))}
            on_home = [c for c in claims if c.source.drawing_id in homes and c.source.grid_cell]
            if not on_home or any(self._close(grid, c.source.grid_cell) for c in on_home):
                continue
            c = on_home[0]
            yield make(
                subject_type="DEVICE",
                subject_key=tag,
                message=f"{tag} encodes grid {grid} but its label is at {make.where(c.source)}",
                evidence=on_home,
                recommendation=f"Re-number the device for its drawn position or move it to {grid}",
            )

    def _close(self, grid: str, cell: str) -> bool:
        dr, dc = row_offset(cell, grid), col_offset(cell, grid)
        if dr is None or dc is None:
            return False
        return -self.max_rows_below <= dr <= self.max_rows_above and abs(dc) <= self.max_cols


@rule
class DuplicateDeviceTag(Rule):
    meta = RuleMeta(
        rule_id="TAG-005",
        title="Duplicate Device Tag",
        category=Category.TAG,
        default_severity=Severity.MAJOR,
        description=(
            "A device tag drawn as a device on its home sheet and again, as a device, on another schematic sheet: "
            "its label sits where its own grid reference says, as a copied device would, rather than elsewhere as a "
            "reference. Typically a copied sheet that was not renumbered (S-TN27D2 on sheets 027 and 028)."
        ),
        requires=_NEEDS_GRID,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        devices = device_claims(ctx)
        # Relay coils with a contact table appear on other sheets as their contacts (TAG-007 checks those).
        coils = {c.value["coil"] for c in ctx.claims_of(Predicate.CONTACT_REF) if c.value.get("coil")}
        for tag, claims in sorted(devices.items()):
            if tag in coils:
                continue
            v = claims[0].value
            homes = {d.id for d in home_sheets(ctx, v["ffi"], int(v["sheet"]))}
            if not homes or not any(c.source.drawing_id in homes for c in claims):
                continue  # not on its home sheet: TAG-003
            if homes & ctx.decoded_ids:
                continue  # home sheet decoded (--ocr): a relay's contact table there may not have decoded
            copies = [
                c
                for c in claims
                if c.source.drawing_id not in homes
                and ctx.sheet_of(c).doc_type is DocType.PLC
                and c.source.grid_cell
                and GridValidation()._close(v["grid"], c.source.grid_cell)
            ]
            for sheet_id in sorted({c.source.drawing_id for c in copies}):
                sheet = ctx.drawing[sheet_id]
                here = [c for c in copies if c.source.drawing_id == sheet_id]
                renumbered = f"{v['prefix']}-{v['ffi']}{sheet.sheet_number:02d}{v['grid']}" if sheet.sheet_number else None
                used = renumbered in devices if renumbered else False
                hint = f"; on {sheet.label} it would be {renumbered}" + (", which other drawings already use" if used else "") if renumbered else ""
                home_label = ctx.drawing[next(c.source.drawing_id for c in claims if c.source.drawing_id in homes)].label
                yield make(
                    subject_type="DEVICE",
                    subject_key=tag,
                    message=f"{tag} is drawn as a device on its home sheet {home_label} and again at {make.where(here[0].source)}{hint}",
                    evidence=[c for c in claims if c.source.drawing_id in homes][:1],
                    evidence_b=here,
                    discriminator=sheet.label,
                    recommendation=f"Renumber the device on {sheet.label}" + (f" to {renumbered}" if renumbered else "") + " (copied without renumbering?)",
                )


@rule
class AuxiliaryContactPointer(Rule):
    meta = RuleMeta(
        rule_id="TAG-007",
        title="Auxiliary Contact Pointer",
        category=Category.TAG,
        default_severity=Severity.MAJOR,
        description=(
            "A relay coil's contact table entry ('43/D4' under K-TN19F1 on sheet 019) must point to an auxiliary "
            "contact labelled with the coil tag (K-TN19F1) near grid D4 of sheet 043. A contact labelled with "
            "the coil sheet plus its own grid (K-TN19D4) is wrong (checker, C-05)."
        ),
        requires=_NEEDS_GRID,
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        devices = device_claims(ctx)
        for p in ctx.claims_of(Predicate.CONTACT_REF):
            coil, aux, sheet, grid = p.value["coil"], p.value["aux_tag"], p.value["target_sheet"], p.value["target_grid"]
            pointer = f"{sheet}/{grid}"
            if coil is None:
                if p.source.drawing_id in ctx.decoded_ids:
                    continue  # decoded sheet: the coil tag above may not have decoded
                yield make(
                    subject_type="DEVICE",
                    subject_key=pointer,
                    message=f"Contact pointer '{pointer}' at {make.where(p.source)} has no coil tag above it",
                    evidence=[p],
                    severity=Severity.MINOR,
                    recommendation="Check the contact table header",
                )
                continue
            coil_ffi = devices[coil][0].value["ffi"] if devices.get(coil) else ""
            targets = home_sheets(ctx, coil_ffi, sheet)  # the pointer's sheet is in the coil's drawing
            if not targets:
                continue  # target sheet not in the package
            target_ids = {d.id for d in targets}
            # The contact must carry its coil's tag (K-TN18F1 on sheet 042), at the grid the pointer gives.
            on_target = [c for c in devices.get(coil, []) if c.source.drawing_id in target_ids]
            if any(_label_near(grid, c.source.grid_cell) for c in on_target):
                continue
            mislabelled = [
                c for c in devices.get(aux, []) if c.source.drawing_id in target_ids and _label_near(grid, c.source.grid_cell)
            ]
            where = make.where(p.source)
            if mislabelled:
                # e.g. sheet 043 labels the contact K-TN19D4 (coil sheet + own grid) instead of K-TN19F1 (C-05).
                message = f"{coil} contact table at {where} points to {pointer}, but the contact there is labelled {aux} at {make.where(mislabelled[0].source)} instead of {coil}"
                recommendation, severity, evidence_b = f"Relabel the contact {coil}", None, mislabelled
            elif on_target:
                message = f"{coil} contact table at {where} points to {pointer}, but {coil} is labelled at {make.where(on_target[0].source)}"
                recommendation, severity, evidence_b = "Correct the pointer or move the contact", Severity.MINOR, on_target
            elif target_ids & ctx.decoded_ids:
                continue  # decoded target sheet: the contact's label may not have decoded
            else:
                message = f"{coil} contact table at {where} points to {pointer}, but {coil} is not on {targets[0].label}"
                recommendation = f"Draw the auxiliary contact {coil} at {targets[0].label} grid {grid}, or correct the pointer"
                severity, evidence_b = None, []
            yield make(
                subject_type="DEVICE",
                subject_key=coil,
                message=message,
                evidence=[p],
                evidence_b=evidence_b,
                discriminator=pointer,
                recommendation=recommendation,
                severity=severity,
            )


def _label_near(grid: str, cell: str | None) -> bool:
    return bool(cell) and GridValidation()._close(grid, cell)


@rule
class SpellingVariant(Rule):
    meta = RuleMeta(
        rule_id="TAG-006",
        title="Identifier Spelling Variant",
        category=Category.TAG,
        default_severity=Severity.MINOR,
        description="An identifier written differently from its canonical form (e.g. SPL-JB-1 for SPL-JB1).",
    )

    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        for c in ctx.claims_of(Predicate.OCCURS):
            if c.value.get("is_variant"):
                yield make(
                    subject_type=c.subject_type.value,
                    subject_key=c.subject_key,
                    message=f"'{c.source.raw_text}' at {make.where(c.source)} should be written {c.subject_key}",
                    evidence=[c],
                    recommendation=f"Write as {c.subject_key}",
                )
