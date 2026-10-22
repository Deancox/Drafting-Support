"""PLC slot termination diagram reader (sheets like 401103-211 to 220, "PLC SLOT - 1,2,3,4").

Each slot is a terminal strip under a header box ("SAFETY / INPUTS SLOT 1"): rows "1 | TN20C6" are
the module's terminals and the wires landed on them. Groups of rows go out through brackets
labelled "EXTERNAL WIRING TO / =K07.11+LTL-JB4 / (REF. TERM. SHT 0202)", with the multicore core
number beside each row.

Claims: WIRE LANDS_ON PLC_MODULE (value: terminal) and PLC_MODULE DESTINED_FOR the JB / location
(value: terminal, wire, core, referenced sheet). The module key is "S<slot>"; the linker resolves
it to the rack's module when only one rack has that slot.
"""

from __future__ import annotations

import re

from drawing_qa.model.enums import EntityType, ExtractionMethod, Predicate
from drawing_qa.parsers.base import ParseContext, ParseResult, TemplateReader
from drawing_qa.parsers.termination.common import (
    Row,
    block_bbox,
    brackets,
    designation,
    label_block,
    mid_x,
    mid_y,
    span,
    terminal_rows,
)

_SLOT_HEADER = re.compile(r"\b(?:INPUTS?|OUTPUTS?|RACK\s+\w+)\s+SLOT\s+0*(?P<slot>\d+)\b")
_EXTERNAL = "EXTERNAL WIRING TO"
_SHEET_REF = re.compile(r"SHT\.?\s*0*(?P<sheet>\d+)")


class SlotStripReader(TemplateReader):
    name = "PLC slot termination diagram"

    def matches(self, page, drawing) -> bool:
        return any(_SLOT_HEADER.search(ln.text.upper()) for ln in page.lines) and any(_EXTERNAL in ln.text.upper() for ln in page.lines)

    def read(self, page, drawing, ctx: ParseContext, parser, prior) -> ParseResult:
        result = ParseResult()

        def claim(**kw):
            return ctx.claim(parser=parser.doc_type, parser_version=parser.version, drawing=drawing,
                             method=ExtractionMethod.TEMPLATE, **kw)

        headers = sorted(
            ((ln, int(m["slot"])) for ln in page.lines if (m := _SLOT_HEADER.search(ln.text.upper()))),
            key=lambda h: h[0].bbox[1],
        )
        rows = terminal_rows(page, ctx.grammar)
        groups = self._external_groups(page, ctx)
        for i, (header, slot) in enumerate(headers):
            below = [h for h in headers[i + 1:] if abs(mid_x(h[0].bbox) - mid_x(header.bbox)) < 80]
            y_end = below[0][0].bbox[1] if below else page.height
            strip = self._strip(page, header, rows, y_end)
            module = f"S{slot}"
            for r in strip:
                if r.wire_id is None:
                    continue
                result.claims.append(claim(bbox=r.bbox, raw_text=f"{r.terminal} {r.wire.text}", subject_type=EntityType.WIRE,
                                           subject_key=r.wire_id.key, predicate=Predicate.LANDS_ON,
                                           object_type=EntityType.PLC_MODULE, object_key=module,
                                           value={"slot": slot, "terminal": r.terminal}))
                group = next((g for g in groups if g["y0"] - 4 <= r.y <= g["y1"] + 4 and g["x0"] > r.wire.bbox[2]), None)
                if group is None:
                    continue
                core = next((c for c in group["cores"] if abs(c[1] - r.y) <= 4), None)
                result.claims.append(claim(bbox=group["bbox"], raw_text=group["label"], subject_type=EntityType.PLC_MODULE,
                                           subject_key=module, predicate=Predicate.DESTINED_FOR,
                                           object_type=group["kind"], object_key=group["key"],
                                           value={"slot": slot, "terminal": r.terminal, "wire": r.wire_id.key,
                                                  "core": core[0] if core else None, "ref_sheet": group["ref_sheet"]}))
        return result

    def _strip(self, page, header, rows: list[Row], y_end: float) -> list[Row]:
        """The slot's rows, down to the next text in the column (e.g. a "24V DC SUPPLIES" block)."""
        column = sorted((r for r in rows if header.bbox[3] - 3 < r.y < y_end and abs(mid_x(r.bbox) - mid_x(header.bbox)) < 60),
                        key=lambda r: r.y)
        row_words = {id(r.number) for r in column} | {id(r.wire) for r in column}
        stops = [
            ln.bbox[1] for ln in page.lines
            if header.bbox[3] < ln.bbox[1] < y_end and abs(mid_x(ln.bbox) - mid_x(header.bbox)) < 40
            and not any(id(w) in row_words for w in ln.words) and not ln.text.strip().isdigit()
        ]
        stop = min(stops, default=y_end)
        return [r for r in column if r.y < stop]

    def _external_groups(self, page, ctx) -> list[dict]:
        """'EXTERNAL WIRING TO / =K07.11+LTL-JB4 / (REF. TERM. SHT 0202)' with its bracket and core numbers."""
        out = []
        lines = page.lines
        for line in lines:
            if _EXTERNAL not in line.text.upper():
                continue
            block = label_block(lines, line, max_lines=3)
            target = next((p for b in block if (p := designation(ctx.grammar, b)) is not None), None)
            if target is None:  # wired straight to a device ("EXTERNAL WIRING TO S-TN35R4")
                target = next((p for b in block[1:] for w in b.words
                               if (p := ctx.grammar.parse(w.text)) is not None and p.kind is EntityType.DEVICE), None)
            if target is None:
                continue
            bb = block_bbox(block)
            near = [s for s in brackets(page, bb[0] - 18, bb[0]) if span(s)[0] - 6 <= mid_y(bb) <= span(s)[1] + 6]
            if not near:
                continue
            bracket = max(near, key=lambda s: s.x0)
            y0, y1 = span(bracket)
            cores = [
                (w.text, mid_y(w.bbox)) for w in page.words
                if w.text.isdigit() and bracket.x0 - 22 <= w.bbox[0] <= bracket.x0 and y0 - 4 <= mid_y(w.bbox) <= y1 + 4
            ]
            ref = next((m["sheet"] for b in block if (m := _SHEET_REF.search(b.text.upper()))), None)
            out.append({
                "key": str(target.parts.get("superseded_by") or target.key), "kind": target.kind,
                "label": " ".join(b.text for b in block), "bbox": bb, "x0": bracket.x0 - 25, "y0": y0, "y1": y1,
                "cores": cores, "ref_sheet": ref,
            })
        return out


__all__ = ["SlotStripReader", "Row"]
