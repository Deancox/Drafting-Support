"""Where a finding's red box goes: on the text that is wrong, or on the channel row when something is missing.

A finding's A (and B) evidence is what its case crops outline in red. A PLC channel is anchored on its address
("01"), which only says which channel it is; these helpers give the rules the things in the row instead (the
device tag, a JB, a wire number written in that channel row), the row itself, and where an entity is drawn on
one kind of drawing (a device on the cable block diagram).
"""

from __future__ import annotations

from drawing_qa.model.claims import Claim
from drawing_qa.model.engineering import SignalTrace
from drawing_qa.model.enums import DocType, EntityType, Predicate
from drawing_qa.rules.context import RuleContext


def channel_claim(ctx: RuleContext, trace: SignalTrace) -> Claim | None:
    """The trace's channel claim (anchored on the address)."""
    return ctx.claim_by_id.get(trace.channel_claim_id or "")


def row_box(claim: Claim | None) -> Claim | None:
    """The channel claim with its box widened to the whole channel row, for a finding about something missing
    from the row. The text and grid cell stay the address's, so the finding's fingerprint does too."""
    if claim is None:
        return None
    box = claim.value.get("row_bbox") if isinstance(claim.value, dict) else None
    if not box or len(box) != 4:
        return claim
    return claim.model_copy(update={"source": claim.source.model_copy(update={"bbox": tuple(float(v) for v in box)})})


def channel_row(ctx: RuleContext, trace: SignalTrace) -> Claim | None:
    """The trace's channel row, as a box."""
    return row_box(channel_claim(ctx, trace))


def in_row(ctx: RuleContext, channel: str, keys: list[str] | str, drawing_id: str | None = None) -> list[Claim]:
    """Where each key (a device tag, JB, field location or wire) is written in the channel's row, in key order.
    A key not written in the row is left out."""
    keys = [keys] if isinstance(keys, str) else keys
    found: dict[str, Claim] = {}
    for c in ctx.claims_of(Predicate.WIRED_TO):
        if c.subject_key == channel and c.object_key in keys and (drawing_id is None or c.source.drawing_id == drawing_id):
            found.setdefault(c.object_key, c)
    return [found[k] for k in keys if k in found]


def trace_row(ctx: RuleContext, trace: SignalTrace, keys: list[str] | str) -> list[Claim]:
    """Where each key is written in the trace's channel row (on the channel's own sheet)."""
    claim = channel_claim(ctx, trace)
    if claim is None or not trace.channel:
        return []
    return in_row(ctx, trace.channel, keys, claim.source.drawing_id)


def drawn_on(ctx: RuleContext, kind: EntityType | tuple[EntityType, ...], key: str, doc: DocType) -> list[Claim]:
    """Where an entity is written on drawings of one type (a device on the cable block diagrams)."""
    kinds = kind if isinstance(kind, tuple) else (kind,)
    return [c for k in kinds for c in ctx.occurrences_by_key.get((k, key), []) if ctx.sheet_of(c).doc_type is doc]


PLACES = (EntityType.JUNCTION_BOX, EntityType.LOCATION)


def terminal_claims(ctx: RuleContext, terminals: list[str], limit: int = 2) -> list[Claim]:
    """Where JB terminals (LTL-JB2:14) are drawn on the termination drawings, at most `limit` of them."""
    out: list[Claim] = []
    for key in terminals:
        node = ctx.model.nodes.get(f"TERMINAL:{key}")
        if node is not None:
            out += ctx.evidence(node.claim_ids)[:1]
        if len(out) >= limit:
            break
    return out[:limit]
