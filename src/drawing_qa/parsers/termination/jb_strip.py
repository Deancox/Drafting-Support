"""JB termination diagram reader (sheets like 401103-200 to 207).

Layout, left to right:
  destination labels       "=K07.11+LTL-PLC / LAND LEG REMOTE I/O PANEL / DI SLOT 16 / (REF TERM. XXX)",
                           each with a thin bracket spanning the rows that go there
  multicore to the panel   "50C+E 1.5mm2 PVC / CXXXXX / 10 SPARES"
  JB terminal strip        under the JB's designation ("=K07.11+LTL-JB1"): device blocks, each a header
                           box (the device tag, or text for power blocks) over its rows "5 | TN15D5"
  field side               each device's cable, its terminals "1 TN15D5" and its tag

Claims per JB terminal row: TERMINAL OCCURS; WIRE LANDS_ON TERMINAL; TERMINAL REFERS_TO_DEVICE;
TERMINAL DESTINED_PANEL (+ DESTINED_FOR the PLC module from "DI SLOT 16", with the slot sheet
from "(REF TERM. 218)"). Per JB: HAS_SPEC for the multicore to the panel (cores, spares, terminals,
cable number). Per field device: WIRE CONNECTS DEVICE (the wire numbers at the device's own
terminals) and DEVICE DESCRIBES (the function written beside it: "LONG TRAVEL WEST").
"""

from __future__ import annotations

import re

from drawing_qa.common.identifiers import IdentifierGrammar, clean_token
from drawing_qa.model.enums import EntityType, ExtractionMethod, Predicate
from drawing_qa.parsers.base import ParseContext, ParseResult, TemplateReader
from drawing_qa.parsers.termination.common import (
    CABLE_SPEC_RE,
    SLOT_RE,
    SPARES_RE,
    Row,
    block_bbox,
    brackets,
    cable_labels,
    designation,
    label_block,
    mid_x,
    mid_y,
    span,
    terminal_rows,
)
from drawing_qa.pdf.extract import PageContent

_STRIP_REACH = 45.0  # how far left / right of the JB designation its strip's numbers may sit (pt)
_STRIP_COLUMN = 90.0  # strips sit in one column; a JB / LCS further right is a field enclosure (its device terminals)
# "(REF TERM. 218)" / "(REF. TERM. SHT 0218)" under a destination label: the slot termination sheet.
_REF_TERM = re.compile(r"REF\.?\s*TERM\.?\s*(?:SHT\.?\s*)?0*(?P<sheet>\d+)")
_WORDY = re.compile(r"[A-Z]{2,}")


def rack_for_panel(panel: str, settings) -> str | None:
    """LTL-PLC -> LTL (the rack in that panel): config [project.rack_panels], else <rack>-PLC."""
    for rack, p in settings.plc.rack_panels.items():
        if p.upper() == panel.upper():
            return rack
    area, _, kind = panel.partition("-")
    return area if kind.upper().startswith("PLC") else None


class JbStripReader(TemplateReader):
    name = "JB termination diagram"

    def matches(self, page: PageContent, drawing) -> bool:
        title = f"{drawing.title or ''} {drawing.descriptor or ''}".upper()
        return "TERMINATION" in title and "SLOT" not in title

    def _headers(self, page: PageContent, grammar: IdentifierGrammar):
        for line in page.lines:
            parsed = designation(grammar, line)
            if (parsed is not None and parsed.kind in (EntityType.JUNCTION_BOX, EntityType.LOCATION)
                    and not parsed.parts.get("superseded_by")  # LTL-AUT1: a panel's old name, a destination label
                    and line.text.lstrip().startswith(("=", "+"))):
                yield line, parsed

    def read(self, page, drawing, ctx: ParseContext, parser, prior) -> ParseResult:
        result = ParseResult()
        grammar = ctx.grammar
        rows = terminal_rows(page, grammar)
        headers = sorted(self._headers(page, grammar), key=lambda h: h[0].bbox[1])

        def claim(**kw):
            return ctx.claim(parser=parser.doc_type, parser_version=parser.version, drawing=drawing,
                             method=ExtractionMethod.TEMPLATE, **kw)

        used: set[int] = set()
        strips: list[tuple] = []  # (jb key, header line, rows)
        for i, (line, jb) in enumerate(headers):
            below = [h for h in headers[i + 1:] if abs(mid_x(h[0].bbox) - mid_x(line.bbox)) < 120]
            y_end = below[0][0].bbox[1] if below else page.height
            mine = [
                r for r in rows
                if id(r) not in used and line.bbox[3] < r.y < y_end
                and line.bbox[0] - _STRIP_REACH <= r.number.bbox[0] <= line.bbox[2] + _STRIP_REACH
            ]
            if not mine:
                continue
            used.update(id(r) for r in mine)
            strips.append((jb, line, sorted(mine, key=lambda r: r.y)))
        if strips:
            column = min(mid_x(line.bbox) for _, line, _ in strips)
            strips = [s for s in strips if mid_x(s[1].bbox) - column <= _STRIP_COLUMN]

        destinations = self._destinations(page, ctx, strips)
        strip_words = {id(r.number) for _, _, rs in strips for r in rs} | {id(r.wire) for _, _, rs in strips for r in rs}
        for jb, header, strip in strips:
            jb_key = jb.key
            blocks = self._blocks(page, grammar, header, strip, strip_words)
            panel_terminals: dict[str, list[str]] = {}
            for r in strip:
                key = f"{jb_key}:{r.terminal}"
                block = blocks.get(id(r))
                dest = next((d for d in destinations if d["y0"] - 4 <= r.y <= d["y1"] + 4 and d["x1"] < r.number.bbox[0]), None)
                value = {"jb": jb_key, "jb_kind": jb.kind.value, "terminal": r.terminal, "wire_text": r.wire.text}
                if block is not None:
                    value["block"] = block[1]
                    # A box headed by text, not a device tag ("STORM PIN LAND LEG"): equipment inside the JB
                    # with its own small strip, numbered on its own.
                    value["device_block"] = block[0] is not None
                result.claims.append(claim(bbox=r.bbox, raw_text=f"{r.terminal} {r.wire.text}", subject_type=EntityType.TERMINAL,
                                           subject_key=key, predicate=Predicate.OCCURS, value=value))
                if r.wire_id is not None:
                    result.claims.append(claim(bbox=r.wire.bbox, raw_text=r.wire.text, subject_type=EntityType.WIRE,
                                               subject_key=r.wire_id.key, predicate=Predicate.LANDS_ON,
                                               object_type=EntityType.TERMINAL, object_key=key, value={"jb": jb_key, "terminal": r.terminal}))
                if block is not None and block[0] is not None:
                    result.claims.append(claim(bbox=block[2], raw_text=block[1], subject_type=EntityType.TERMINAL, subject_key=key,
                                               predicate=Predicate.REFERS_TO_DEVICE, object_type=EntityType.DEVICE, object_key=block[0]))
                if dest is not None:
                    panel_terminals.setdefault(dest["panel"], []).append(r.terminal)
                    dvalue = {k: dest[k] for k in ("panel_written", "io", "slot", "label", "ref_sheet") if dest.get(k) is not None}
                    if r.wire_id is not None:
                        dvalue["wire"] = r.wire_id.key  # this row's wire: a terminal number drawn twice has two
                    result.claims.append(claim(bbox=dest["bbox"], raw_text=dest["label"], subject_type=EntityType.TERMINAL, subject_key=key,
                                               predicate=Predicate.DESTINED_PANEL, object_type=EntityType.PANEL,
                                               object_key=dest["panel"], value=dvalue))
                    if dest.get("module"):
                        result.claims.append(claim(bbox=dest["bbox"], raw_text=dest["label"], subject_type=EntityType.TERMINAL,
                                                   subject_key=key, predicate=Predicate.DESTINED_FOR,
                                                   object_type=EntityType.PLC_MODULE, object_key=dest["module"], value=dvalue))
            for panel, terminals in panel_terminals.items():
                spec = self._multicore(page, header, strip, destinations)
                if spec is not None:
                    result.claims.append(claim(bbox=spec["bbox"], raw_text=spec["text"], subject_type=EntityType.JUNCTION_BOX,
                                               subject_key=jb_key, predicate=Predicate.HAS_SPEC,
                                               value={**{k: v for k, v in spec.items() if k != "bbox"}, "to": panel, "terminals": terminals}))
        result.claims.extend(self._field_side(page, ctx, claim, strips, strip_words))
        if strips:
            result.claims.extend(self._cables(page, ctx, claim, strips))
        return result

    # --- every cable drawn on the sheet ---------------------------------------------------

    def _cables(self, page, ctx, claim, strips):
        """CABLE HAS_SPEC for each cable label stack on the sheet, with the enclosures and panels the sheet
        names (its strips' JBs / LCSs and the destination panels), for the cable block diagram cross-checks."""
        places = set()
        for line in page.lines:
            parsed = designation(ctx.grammar, line)
            if parsed is not None and line.text.lstrip().startswith(("=", "+")):
                places.add(str(parsed.parts.get("superseded_by") or parsed.key))
        places.update(jb.key for jb, _, _ in strips)
        strip_x0 = min(r.number.bbox[0] for _, _, rs in strips for r in rs)
        out = []
        for lab in cable_labels(page):
            value = {k: lab[k] for k in ("cable_no", "text", "cores", "spares", "spare_unit")}
            value.update({"side": "panel" if lab["bbox"][2] < strip_x0 else "field", "places": sorted(places)})
            out.append(claim(bbox=lab["bbox"], raw_text=lab["raw_text"], subject_type=EntityType.CABLE,
                             subject_key=lab["cable_no"], predicate=Predicate.HAS_SPEC, value=value))
        return out

    # --- destinations (left labels with brackets) ---------------------------------------

    def _destinations(self, page, ctx, strips) -> list[dict]:
        if not strips:
            return []
        strip_x = min(r.number.bbox[0] for _, _, rs in strips for r in rs)
        out = []
        lines = page.lines
        for line in lines:
            parsed = designation(ctx.grammar, line)
            if parsed is None or line.bbox[2] > strip_x - 60:
                continue
            replacement = ctx.grammar.parse(str(parsed.parts["superseded_by"])) if parsed.parts.get("superseded_by") else None
            if EntityType.PANEL not in (parsed.kind, replacement.kind if replacement else None):
                continue  # LTL-AUT1 is a location, but stands for the panel LTL-PLC
            block = label_block(lines, line)
            text = " ".join(b.text for b in block).upper()
            m = SLOT_RE.search(text)
            if m is None:
                continue  # the sheet's own heading ("=K07.11+LTL-AUT1   FIELD"), not a destination
            bb = block_bbox(block)
            near = [s for s in brackets(page, bb[2], bb[2] + 25) if span(s)[0] - 10 <= mid_y(bb) <= span(s)[1] + 10]
            if near:
                y0, y1 = span(min(near, key=lambda s: abs(mid_y(bb) - (span(s)[0] + span(s)[1]) / 2)))
            else:
                y0, y1 = bb[1], bb[3]
            panel = str(parsed.parts.get("superseded_by") or parsed.key)
            rack = rack_for_panel(panel, ctx.settings)
            slot = int(m["slot"])
            ref = _REF_TERM.search(text)
            out.append({
                "panel": parsed.key, "panel_written": parsed.key, "resolved_panel": panel, "io": m["io"], "slot": slot,
                "module": f"{rack}/S{slot}" if rack else None, "label": " / ".join(b.text for b in block),
                "ref_sheet": ref["sheet"] if ref else None,
                "bbox": bb, "x1": bb[2], "y0": y0, "y1": y1,
            })
        return out

    # --- device blocks ---------------------------------------------------------------------

    def _blocks(self, page, grammar: IdentifierGrammar, header, strip: list[Row], strip_words: set[int]) -> dict[int, tuple]:
        """Row -> (device key or None, header text, bbox) of the block header above it."""
        x0 = min(r.number.bbox[0] for r in strip) - 10
        x1 = max(r.wire.bbox[2] for r in strip) + 10
        candidates = [
            ln for ln in page.lines
            if header.bbox[3] <= ln.bbox[1] and x0 <= mid_x(ln.bbox) <= x1
            and not any(id(w) in strip_words for w in ln.words) and not ln.text.strip().isdigit()
        ]
        out = {}
        for r in strip:
            above = [ln for ln in candidates if ln.bbox[3] <= r.number.bbox[1] + 4 and r.y - ln.bbox[3] < 400]
            if not above:
                continue
            nearest = max(above, key=lambda ln: ln.bbox[3])
            # A header written on two lines ("RAIL CLAMP LAND LEG" / "NORTH"): take the line(s) just above too.
            group = [ln for ln in above if nearest.bbox[1] - ln.bbox[3] < 4 and ln is not nearest] + [nearest]
            group.sort(key=lambda ln: ln.bbox[1])
            text = " ".join(ln.text for ln in group)
            device = None
            for w in nearest.words:
                if (p := grammar.parse(clean_token(w.text))) is not None and p.kind is EntityType.DEVICE:
                    device = p.key
            out[id(r)] = (device, text, block_bbox(group))
        return out

    # --- multicore to the panel -----------------------------------------------------------

    def _multicore(self, page, header, strip: list[Row], destinations: list[dict]) -> dict | None:
        first = strip[0]
        left = max((d["x1"] for d in destinations), default=0.0)
        specs = [
            ln for ln in page.lines
            if CABLE_SPEC_RE.match(ln.text.strip().upper()) and left < ln.bbox[0] < first.number.bbox[0]
            and header.bbox[1] - 25 <= ln.bbox[1] <= first.y
        ]
        if not specs:
            return None
        spec = min(specs, key=lambda ln: abs(ln.bbox[1] - first.y))
        cores = int(CABLE_SPEC_RE.match(spec.text.strip().upper())["cores"])
        spares, cable_no = None, None
        for ln in page.lines:
            if 0 < ln.bbox[1] - spec.bbox[1] <= 25 and abs(mid_x(ln.bbox) - mid_x(spec.bbox)) < 30:
                text = ln.text.strip().upper()
                if m := SPARES_RE.match(text):
                    spares = int(m["n"])
                elif cable_no is None and len(text) <= 8 and " " not in text:
                    cable_no = text
        return {"text": spec.text, "cores": cores, "spares": spares, "cable_no": cable_no, "bbox": spec.bbox}

    # --- field side --------------------------------------------------------------------------

    def _field_side(self, page, ctx, claim, strips, strip_words):
        """Wire numbers at each field device's own terminals, for WIRE-004."""
        if not strips:
            return []
        strip_x1 = max(r.wire.bbox[2] for _, _, rs in strips for r in rs)
        rows = [r for r in terminal_rows(page, ctx.grammar) if r.number.bbox[0] > strip_x1 + 60 and id(r.number) not in strip_words]
        strip_x0 = min(r.number.bbox[0] for _, _, rs in strips for r in rs)
        all_tags = []
        for line in page.lines:
            for w in line.words:
                p = ctx.grammar.parse(clean_token(w.text))
                if p is not None and p.kind is EntityType.DEVICE and w.bbox[0] > strip_x0 - 20:
                    all_tags.append((w, p.key))
        tags = [(w, key) for w, key in all_tags if w.bbox[0] > strip_x1 + 60]
        # Devices boxed inside the JB / LCS beside its strip are described too (sheet 201: LTL-LCS1's switches).
        out = self._descriptions(page, ctx, claim, all_tags)
        for r in rows:
            if r.wire_id is None:
                continue
            near = [(w, key) for w, key in tags if -15 <= r.y - mid_y(w.bbox) <= 40 and -10 <= w.bbox[0] - r.number.bbox[0] <= 110]
            if not near:
                continue
            w, key = min(near, key=lambda t: abs(r.y - mid_y(t[0].bbox)) + abs(t[0].bbox[0] - r.number.bbox[0]) / 4)
            out.append(claim(bbox=r.bbox, raw_text=f"{r.terminal} {r.wire.text}", subject_type=EntityType.WIRE,
                             subject_key=r.wire_id.key, predicate=Predicate.CONNECTS, object_type=EntityType.DEVICE,
                             object_key=key, value={"terminal": r.terminal, "side": "device"}))
        return out

    def _descriptions(self, page, ctx, claim, tags):
        """The function written to the right of a field device's tag ('LONG TRAVEL' / 'MODE INDICATION LAMP'):
        the nearest wordy line starting just right of the tag, and the lines stacked under it."""
        lines = page.lines
        tag_words = {id(w) for w, _ in tags}

        def wordy(ln) -> bool:
            text = ln.text.strip().upper()
            if any(id(w) in tag_words for w in ln.words) or len(_WORDY.findall(text)) == 0 or len(text) < 4:
                return False
            return all(ctx.grammar.parse(w.text) is None for w in ln.words)

        out, seen = [], set()
        for w, key in tags:
            near = [
                ln for ln in lines
                if 4 <= ln.bbox[0] - w.bbox[2] <= 45 and -3 <= ln.bbox[1] - w.bbox[1] <= 12 and wordy(ln)
            ]
            if not near:
                continue
            first = min(near, key=lambda ln: (ln.bbox[0] - w.bbox[2]) + abs(ln.bbox[1] - w.bbox[1]))
            block = [b for b in label_block(lines, first, max_gap=10, max_lines=3) if wordy(b)]
            text = " / ".join(b.text.strip() for b in block)
            if (key, text) in seen:
                continue
            seen.add((key, text))
            out.append(claim(bbox=block_bbox(block), raw_text=text, subject_type=EntityType.DEVICE, subject_key=key,
                             predicate=Predicate.DESCRIBES, value={"text": text, "side": "field"}))
        return out
