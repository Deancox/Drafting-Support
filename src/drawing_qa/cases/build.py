"""Turns one QA tool finding into a case the QA prompt can judge.

A case holds the finding, the text the tool read near each evidence spot, and
the warnings the tool raised about the drawing files behind those spots. It
names the crop images but does not make them (crops.py does that). Everything
here is a pure function over findings.json and review.json.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

DRAWING_WARNING_RULES = ("DRW-007", "DRW-008")


def _usable_box(value: object) -> list[float] | None:
    """The box as four finite numbers, or None when it is missing or not that."""
    if not isinstance(value, list) or len(value) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        return None
    box = [float(v) for v in value]
    return box if all(math.isfinite(v) for v in box) else None


@dataclass
class ReviewIndex:
    drawings: dict[str, dict]
    claims_by_drawing: dict[str, list[dict]]
    rule_titles: dict[str, str]
    tool_version: str
    drawing_warnings: dict[str, list[str]]

    @classmethod
    def from_review(cls, review: dict, findings: list[dict]) -> ReviewIndex:
        drawings = {d["id"]: d for d in review.get("drawings", [])}

        claims_by_drawing: dict[str, list[dict]] = {}
        for claim in review.get("claims", []):
            source = claim.get("source")
            # A claim with no usable box has no place on the drawing, so it is left out.
            if source is not None and _usable_box(source.get("bbox")) is not None:
                claims_by_drawing.setdefault(source["drawing_id"], []).append(claim)

        rule_titles = {rr["rule_id"]: rr["title"] for rr in review.get("rule_runs", [])}

        # A DRW finding names its drawing by file name or by "<drawing no> sh <sheet>".
        drawing_warnings: dict[str, list[str]] = {}
        for drawing_id, d in drawings.items():
            names = {Path(d["file_path"]).name, f"{d['drawing_no']} sh {d['sheet']}"}
            messages = [
                f["message"]
                for f in findings
                if f["rule_id"] in DRAWING_WARNING_RULES and f["subject_key"] in names
            ]
            if messages:
                drawing_warnings[drawing_id] = messages

        return cls(
            drawings=drawings,
            claims_by_drawing=claims_by_drawing,
            rule_titles=rule_titles,
            tool_version=review.get("run", {}).get("tool_version", ""),
            drawing_warnings=drawing_warnings,
        )


def drawing_label(index: ReviewIndex, drawing_id: str) -> str:
    """'900001 sh 056' for a drawing in the review, otherwise the drawing id."""
    drawing = index.drawings.get(drawing_id)
    if drawing is None:
        return drawing_id
    return f"{drawing['drawing_no']} sh {drawing['sheet']}"


def message_hash(finding: dict) -> str:
    """Changes when the message or any evidence text or grid cell changes.

    A reviewed row uses this to tell that the tool now says something different.
    The finding id is left out, so the same finding keeps its hash across runs.
    """
    spots = []
    for item in finding.get("evidence") or []:
        source = item.get("source")
        if source is not None:
            spots.append(
                [item["role"], source["drawing_id"], source.get("grid_cell"), source.get("raw_text")]
            )
    text = json.dumps([finding["message"], spots], ensure_ascii=False)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def nearby_text(
    index: ReviewIndex,
    drawing_id: str,
    bbox: list[float],
    margin: float = 120.0,
    limit: int = 60,
) -> list[dict]:
    """Text the tool read on this drawing near an evidence box, top to bottom.

    bbox is [x0, y0, x1, y1] in PDF points, origin top left. A claim counts when
    its own box touches the evidence box grown by margin on every side. The tool
    often makes several claims from one piece of text, so an exact repeat of a
    text and grid already listed is dropped before the limit is applied.
    """
    x0, y0 = bbox[0] - margin, bbox[1] - margin
    x1, y1 = bbox[2] + margin, bbox[3] + margin
    hits = []
    for claim in index.claims_by_drawing.get(drawing_id, []):
        source = claim["source"]
        cx0, cy0, cx1, cy1 = source["bbox"]
        if cx0 <= x1 and cx1 >= x0 and cy0 <= y1 and cy1 >= y0:
            hits.append(source)
    hits.sort(key=lambda s: (s["bbox"][1], s["bbox"][0]))
    near: list[dict] = []
    seen: set[tuple] = set()
    for s in hits:
        key = (s["raw_text"], s.get("grid_cell"))
        if key not in seen:
            seen.add(key)
            near.append({"text": s["raw_text"], "grid": s.get("grid_cell")})
    return near[:limit]


def _spot(source: dict, index: ReviewIndex) -> dict:
    """Where one piece of evidence sits in the package, as the case shows it."""
    drawing = index.drawings.get(source["drawing_id"])
    return {
        "drawing": drawing_label(index, source["drawing_id"]),
        "drawing_id": source["drawing_id"],
        "source_pdf": None if drawing is None else drawing["file_path"],
        "page_index": None if drawing is None else drawing["page_index"],
        "grid_cell": source.get("grid_cell"),
        "bbox": _usable_box(source.get("bbox")),
        "raw_text": source.get("raw_text"),
    }


def build_case(finding: dict, index: ReviewIndex, package: str, run_id: str) -> dict:
    """One case: the finding, its A/B evidence, and what the tool read near each side."""
    # CONTEXT items are not a side, so only A and B are kept; an item with no source
    # has nowhere to point at and is skipped, as message_hash skips it.
    items = [
        item
        for item in finding.get("evidence") or []
        if item.get("role") in ("A", "B") and item.get("source")
    ]
    evidence = [{"role": item["role"], **_spot(item["source"], index)} for item in items]

    # A side is built from the first evidence item with that role.
    sides = {}
    for role in ("A", "B"):
        first = next((item for item in items if item["role"] == role), None)
        if first is None:
            continue
        source = first["source"]
        box = _usable_box(source.get("bbox"))
        sides[role] = {
            **_spot(source, index),
            "crop": f"{finding['fingerprint']}-{role}.png",
            "crop_problem": None,
            # No box means no spot to look around; the packer records why there is no crop.
            "nearby": [] if box is None else nearby_text(index, source["drawing_id"], box),
            "drawing_warnings": list(index.drawing_warnings.get(source["drawing_id"], [])),
        }

    review = finding.get("review") or {}
    if review.get("waiver"):
        tool_reviewer = "waivers.toml"
    else:
        tool_reviewer = review.get("reviewer") or ""

    return {
        "package": package,
        "run_id": run_id,
        "fingerprint": finding["fingerprint"],
        "ref": finding["id"],
        "rule_id": finding["rule_id"],
        "rule_title": index.rule_titles.get(finding["rule_id"], ""),
        "severity": finding["severity"],
        "category": finding["category"],
        "subject_type": finding["subject_type"],
        "subject_key": finding["subject_key"],
        "message": finding["message"],
        "recommendation": finding.get("recommendation"),
        "decoded": finding.get("decoded", False),
        "tool_status": finding["status"],
        "tool_reviewer": tool_reviewer,
        "message_hash": message_hash(finding),
        "evidence": evidence,
        "sides": sides,
    }
