"""Which drawing each First Field Identifier (FFI) belongs to, for this package.

A tag like S-TN36D4 encodes its home sheet (36) but not its drawing number, so the platform
needs "TN tags are drawn on 401103". Every schematic sheet shows its FFI in the "WIRE NUMBER /
FIRST FIELD IDENTIFIER" box, so the mapping is learned from the package itself:

  - an FFI shown on a supplied drawing's schematic sheets maps to that drawing, whatever the
    config says (a config written for another package must not switch checks off here);
  - an FFI no supplied sheet shows keeps the config's value: EXTERNAL (a separate package),
    or a drawing number;
  - a config drawing number that is not in this package, for an FFI the package's tags use,
    is a loud warning, because those tags cannot be checked.

Every disagreement or gap becomes an FFI_MAPPING issue and a run warning.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from drawing_qa.config import Settings
from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import DocType, EntityType, ExtractionMethod, IssueCode, Predicate
from drawing_qa.model.source import Drawing, Issue

EXTERNAL = "EXTERNAL"


def resolve_ffis(settings: Settings, drawings: list[Drawing], claims: list[Claim]) -> tuple[Settings, dict, list[Issue]]:
    """Return settings with the effective ffi_drawings ([project.ffi_drawings]), the mapping with where each
    entry came from (for the reports), and the issues to raise."""
    left_out = set(settings.ingest.unchecked_types)
    learned: dict[str, Counter] = defaultdict(Counter)
    for d in drawings:
        if (d.doc_type is DocType.PLC and d.ffi and d.duplicate_of is None and d.doc_type not in left_out
                and d.title_block_method is ExtractionMethod.TEMPLATE):
            learned[d.ffi.upper()][d.drawing_no] += 1
    present = {d.drawing_no for d in drawings if d.title_block_method is ExtractionMethod.TEMPLATE}
    config = {k.upper(): v for k, v in settings.identifiers.ffi_drawings.items()}
    used: Counter = Counter(
        str(c.value["ffi"]).upper() for c in claims
        if c.predicate is Predicate.OCCURS and c.subject_type in (EntityType.DEVICE, EntityType.WIRE) and "ffi" in c.value
    )

    effective: dict[str, str] = {}
    mapping: dict[str, dict[str, str]] = {}
    issues: list[Issue] = []

    def warn(message: str) -> None:
        issues.append(Issue(code=IssueCode.FFI_MAPPING, message=message))

    for ffi, drawings_seen in sorted(learned.items()):
        drawing, sheets = drawings_seen.most_common(1)[0]
        if len(drawings_seen) > 1:
            others = ", ".join(f"{d} ({n} sheets)" for d, n in drawings_seen.most_common()[1:])
            warn(f"FFI {ffi} is shown on the schematics of more than one drawing: {drawing} ({sheets} sheets), {others}. "
                 f"{ffi} tags are checked against {drawing}")
        configured = config.get(ffi)
        effective[ffi] = drawing
        source = "sheets"
        if configured is not None and configured.upper() == drawing.upper():
            source = "sheets, config"
        elif configured is not None:
            source = "sheets, overrides the config"
            warn(f"The config maps {ffi} to {configured}, but drawing {drawing}'s schematic sheets show FFI {ffi}: "
                 f"{ffi} tags are checked against {drawing}. Set {ffi} = \"{drawing}\" in [project.ffi_drawings] for this package")
        mapping[ffi] = {"drawing": drawing, "source": source}

    for ffi, configured in sorted(config.items()):
        if ffi in effective:
            continue
        effective[ffi] = configured
        mapping[ffi] = {"drawing": configured, "source": "config"}
        if configured.upper() == EXTERNAL or configured in present or not used.get(ffi):
            continue
        mapping[ffi]["source"] = "config, drawing not supplied"
        warn(f"The config maps {ffi} to drawing {configured}, which is not in this package, so {used[ffi]} {ffi} tag/wire "
             f"occurrence(s) are not checked. If {ffi} belongs to this package, set {ffi} to its drawing number; "
             f"if it is a separate package, set {ffi} = \"{EXTERNAL}\"")

    resolved = settings.model_copy(deep=True)
    if effective:
        resolved.identifiers.ffi_drawings = effective
    return resolved, mapping, issues


def describe(mapping: dict[str, dict[str, str]]) -> str:
    """'TN -> 401103 (sheets); TV -> EXTERNAL (config)'."""
    return "; ".join(f"{ffi} -> {m['drawing']} ({m['source']})" for ffi, m in sorted(mapping.items())) or "none"
