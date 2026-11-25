"""Slot sheet reader: one I/O module per schematic sheet.

Header block (bottom right of the drawing area):
    TYPE: 1734-IB8
    RACK N° LTL SLOT N°13
    POINTS: LTL.S13.DI.00..07

Channel rows: the 2-digit addresses ("00".."07") in the column under the
"TERMINAL /ADDRESS" (or "TERMINAL / CHANNEL") header anchor one row per channel.
Everything is then assigned to the nearest channel row:
  - device tags and same-sheet wires by the grid row their name encodes (labels are
    drawn above the symbol, so the text position would put them a row too high);
  - JBs, module terminals (IN-0, OUT-0) and description lines by their position.
Headers are found by their text, so DI / DO / AI sheets with different column
orders are read the same way.
"""

from __future__ import annotations

import re
from statistics import median

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import EntityType, IssueCode, Predicate
from drawing_qa.model.source import BBox, Drawing, Issue
from drawing_qa.parsers.base import DrawingParser, ParseContext, ParseResult, TemplateReader
from drawing_qa.pdf.extract import PageContent, TextLine

_POINTS = re.compile(r"POINTS\s*:?\s*(?P<text>[^\s:]\S*)", re.I)
_POINTS_LABEL = re.compile(r"^POINTS\s*:?$", re.I)
_POINTS_PARTS = re.compile(
    r"^(?P<rack>[A-Z0-9]+)\.S(?P<slot>\d+)\.?(?P<type>DI|DO|AI|AO)\.(?P<first>\d{2})\.{1,2}(?P<last>\d{2})$"
)
_RACK = re.compile(r"RACK\s*N\S*\s*:?\s*(?P<rack>[A-Z0-9]+)\s+SLOT\s*N\S*?\s*(?P<slot>\d+)", re.I)
_TYPE = re.compile(r"TYPE\s*:\s*(?P<part>\d{4}-[A-Z0-9]+)", re.I)
_TITLE_SLOT = re.compile(r"\bSLOT\s+(\d+)\b", re.I)
_ADDRESS_HEADER = re.compile(r"^(/?\s*ADDRESS|CHANNEL)$", re.I)
_CHANNEL = re.compile(r"^\d{2}$")
_MODULE_TERMINAL = re.compile(r"^(IN|OUT)-\d+$", re.I)
_TERMINAL_NUMBER = re.compile(r"^(?:IN|OUT)-(\d+)$", re.I)


def rows_terminal(rows: list[dict], channel: int) -> str:
    """The module terminal label (OUT-24) of the row now numbered channel."""
    return next((t for row in rows if row["channel"] == channel for t in row["terminals"]), f"terminal {channel}")
_ROWS = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def _cx(bbox: BBox) -> float:
    return (bbox[0] + bbox[2]) / 2


def _cy(bbox: BBox) -> float:
    return (bbox[1] + bbox[3]) / 2


def _is_heading(c: Claim) -> bool:
    """Written as a location designation (=K07.11+BH-LCS1, +BH-LCS1): a heading, not a label on a wire."""
    return (c.source.raw_text or "").strip().startswith(("=", "+"))


class SlotSheetReader(TemplateReader):
    name = "plc-slot-sheet"

    def matches(self, page: PageContent, drawing: Drawing) -> bool:
        return any(_RACK.search(ln.text) or _POINTS.search(ln.text) for ln in page.lines)

    def read(
        self, page: PageContent, drawing: Drawing, ctx: ParseContext, parser: DrawingParser, prior: list[Claim]
    ) -> ParseResult:
        result = ParseResult()
        common = {"parser": parser.doc_type, "parser_version": parser.version, "drawing": drawing}
        body = [ln for ln in page.lines if _cy(ln.bbox) < page.height * 0.8]

        module, module_claim = self._module(page, body, drawing, ctx, common)
        if module_claim is None:
            return result
        result.claims.append(module_claim)

        header = next((ln for ln in body if _ADDRESS_HEADER.match(ln.text.strip())), None)
        if header is None:
            return result
        hx0, hx1 = header.bbox[0] - 25, header.bbox[2] + 25
        anchors = sorted(
            (ln for ln in body if _CHANNEL.match(ln.text.strip()) and hx0 <= _cx(ln.bbox) <= hx1 and ln.bbox[1] > header.bbox[3]),
            key=lambda ln: _cy(ln.bbox),
        )
        # The same address drawn twice on top of itself (sheet 026 has two "04") is one row.
        anchors = [a for i, a in enumerate(anchors) if not any(
            b.text.strip() == a.text.strip() and abs(b.bbox[1] - a.bbox[1]) < 3 and abs(b.bbox[0] - a.bbox[0]) < 3 for b in anchors[:i]
        )]
        anchors = self._address_column(anchors)
        if not anchors:
            return result
        bands = self._bands([_cy(a.bbox) for a in anchors])

        def band_of(y: float) -> int | None:
            return next((i for i, (lo, hi) in enumerate(bands) if lo <= y < hi), None)

        rows: list[dict] = [
            {"channel": int(a.text.strip()), "anchor": a, "band": band, "terminals": [], "description": [], "items": []}
            for a, band in zip(anchors, bands)
        ]
        row_left = max(0.0, header.bbox[0] - page.width / 2)  # an empty row: about the device side of the sheet

        for ln in body:
            text = ln.text.strip()
            if _MODULE_TERMINAL.match(text) and header.bbox[0] - 45 <= _cx(ln.bbox) <= header.bbox[2] + 45:
                if (i := band_of(_cy(ln.bbox))) is not None:
                    rows[i]["terminals"].append(text.upper())

        if issue := self._numbers_from_terminals(rows, drawing):
            result.issues.append(issue)

        desc_header = next((ln for ln in body if ln.text.strip().upper() == "DESCRIPTION"), None)
        if desc_header is not None:
            dx0, dx1 = desc_header.bbox[0] - 60, desc_header.bbox[2] + 20
            for ln in body:
                if ln is desc_header or not (dx0 <= ln.bbox[0] <= dx1):
                    continue
                if (i := band_of(_cy(ln.bbox))) is not None:
                    rows[i]["description"].append(ln)

        this_sheet = drawing.sheet_number

        def device_y(c: Claim) -> float:
            """A device tag's row: the one its grid names (labels sit above their symbol)."""
            if int(c.value.get("sheet", -1)) == this_sheet and drawing.grid is not None:
                return self._row_centre(drawing, c.value["grid"][0], _cy(c.source.bbox))
            return _cy(c.source.bbox)

        devices = [c for c in prior if c.source.drawing_id == drawing.id and c.predicate is Predicate.OCCURS
                   and c.subject_type is EntityType.DEVICE and c.value.get("format_valid")]
        for c in prior:
            if c.source.drawing_id != drawing.id or c.predicate is not Predicate.OCCURS:
                continue
            y = _cy(c.source.bbox)
            if c.subject_type in (EntityType.JUNCTION_BOX, EntityType.LOCATION) and _is_heading(c):
                # A location heading (=K07.11+BH-LCS1) is drawn above the device it labels, often halfway
                # between two channel numbers (401104-066): it goes to the row of the device under it.
                if under := self._device_under(c, devices):
                    y = device_y(under)
            if c.subject_type in (EntityType.DEVICE, EntityType.WIRE):
                if c.subject_type is EntityType.DEVICE and not c.value.get("format_valid"):
                    continue
                encoded_sheet = int(c.value.get("sheet", -1))
                if encoded_sheet == this_sheet and drawing.grid is not None:
                    y = self._row_centre(drawing, c.value["grid"][0], y)
                elif c.subject_type is EntityType.WIRE:
                    continue  # a wire from another sheet (e.g. 15D5 supply) belongs to no single channel
            elif c.subject_type not in (EntityType.JUNCTION_BOX, EntityType.LOCATION):
                continue  # JBs and field locations such as local control stations (LTL-LCS1)
            if (i := band_of(y)) is not None:
                rows[i]["items"].append(c)

        # Only addresses in the module's I/O range are channels. Safety input modules also show
        # their test outputs (T0, T1 at "10", "11") in the address column; those rows keep their
        # band, so their wires are not pulled into a neighbouring channel, but are not emitted.
        v = module_claim.value
        first = v["points_first"] if v["points_first"] is not None else 0
        last = v["points_last"] if v["points_last"] is not None else (v["channels"] or 99) - 1
        for row in rows:
            if first <= row["channel"] <= last:
                row["left"] = min((c.source.bbox[0] for c in row["items"]), default=row_left)
                result.claims.extend(self._channel_claims(row, module, ctx, common))
        return result

    # --- module header ---------------------------------------------------------

    def _module(self, page, body, drawing, ctx, common):
        points = rack = part = None
        points_line = rack_line = None
        for ln in page.lines:
            if (m := _POINTS.search(ln.text)) and points is None:
                points, points_line = m["text"].upper(), ln
            elif _POINTS_LABEL.match(ln.text.strip()) and points is None and (value := self._beside(page, ln)):
                points, points_line = value.text.strip().upper(), ln  # label and value as two texts (decoded sheets)
            if (m := _RACK.search(ln.text)) and rack is None:
                rack, rack_line = m, ln
            if (m := _TYPE.search(ln.text)) and part is None:
                part = m["part"].upper()
        pm = _POINTS_PARTS.match(points) if points else None
        title_slot = _TITLE_SLOT.search(drawing.title or "")
        rack_name = (rack["rack"] if rack else pm["rack"] if pm else None) or ""
        slot = rack["slot"] if rack else pm["slot"] if pm else title_slot.group(1) if title_slot else None
        if not rack_name or slot is None:
            return None, None
        spec = ctx.settings.plc.modules.get(part) if part else None
        module = f"{rack_name.upper()}/S{int(slot)}"
        anchor = points_line or rack_line
        claim = ctx.claim(
            **common,
            method=anchor.words[0].method,
            bbox=anchor.bbox,
            raw_text=anchor.text,
            subject_type=EntityType.PLC_MODULE,
            subject_key=module,
            predicate=Predicate.INSTALLED_IN,
            value={
                "source": "slot_sheet",
                "rack": rack_name.upper(),
                "slot": int(slot),
                "part": part,
                "module_type": spec.type if spec else (pm["type"] if pm else None),
                "channels": spec.channels if spec else None,
                "safety": spec.safety if spec else None,
                "points": points,
                "points_valid": bool(points and re.match(ctx.settings.plc.points_format, points)),
                "points_type": pm["type"] if pm else None,
                "points_slot": int(pm["slot"]) if pm else None,
                "points_first": int(pm["first"]) if pm else None,
                "points_last": int(pm["last"]) if pm else None,
                "rack_line_slot": int(rack["slot"]) if rack else None,
                "title_slot": int(title_slot.group(1)) if title_slot else None,
                "title": drawing.title,
            },
        )
        return module, claim

    @staticmethod
    def _device_under(heading: Claim, devices: list[Claim], reach: float = 30.0, sideways: float = 60.0) -> Claim | None:
        """The device tag written directly under a heading: within `reach` pt below it, about the same column."""
        hx0, _, hx1, hy1 = heading.source.bbox
        below = [d for d in devices if 0 <= d.source.bbox[1] - hy1 <= reach and abs(_cx(d.source.bbox) - (hx0 + hx1) / 2) <= sideways]
        return min(below, key=lambda d: d.source.bbox[1] - hy1, default=None)

    @staticmethod
    def _beside(page: PageContent, label: TextLine) -> TextLine | None:
        """The text just right of a label, on its line."""
        height = label.bbox[3] - label.bbox[1]
        right = [ln for ln in page.lines if ln is not label and abs(_cy(ln.bbox) - _cy(label.bbox)) < height / 2
                 and 0 <= ln.bbox[0] - label.bbox[2] < 4 * height]
        return min(right, key=lambda ln: ln.bbox[0], default=None)

    # --- channel rows -------------------------------------------------------------

    @staticmethod
    def _address_column(anchors: list[TextLine]) -> list[TextLine]:
        """The addresses, without the terminal numbers some sheets write in their own column under
        the same header (sheet 176: terminals 10..12 beside channels 00..03; sheet 147: terminals
        27..35 beside channels 24..31). The address column is the one drawn in the larger text."""
        columns: list[list[TextLine]] = []
        for a in sorted(anchors, key=lambda ln: _cx(ln.bbox)):
            if columns and _cx(a.bbox) - _cx(columns[-1][-1].bbox) <= 12:
                columns[-1].append(a)
            else:
                columns.append([a])
        if len(columns) < 2:
            return anchors
        best = max(columns, key=lambda col: (median(a.bbox[3] - a.bbox[1] for a in col), len(col)))
        return sorted(best, key=lambda ln: _cy(ln.bbox))

    @staticmethod
    def _numbers_from_terminals(rows: list[dict], drawing: Drawing) -> Issue | None:
        """Check the address column against the module terminal labels (OUT-24, IN-8) in each row.

        When most labelled rows disagree with their address, the column read is not the addresses: on 401104-151
        read from its DWG, the only two-digit numbers there were the PLC terminal numbers (27, 28 ... under OUT-24,
        OUT-25 ...). The labels then give the channel numbers, a row with no label is dropped, and the sheet gets an
        issue. One row disagreeing is a drawing question, so the addresses are kept.
        """
        labelled = []
        for i, row in enumerate(rows):
            numbers = {int(m[1]) for t in row["terminals"] if (m := _TERMINAL_NUMBER.match(t))}
            if len(numbers) == 1:
                labelled.append((i, numbers.pop()))
        disagree = [(i, n) for i, n in labelled if n != rows[i]["channel"]]
        if not labelled or len(disagree) * 2 <= len(labelled):
            return None
        i0, n0 = disagree[0]
        read = rows[i0]["channel"]
        keep = {i for i, _ in labelled}
        for i, n in labelled:
            rows[i]["channel"] = n
        rows[:] = [row for i, row in enumerate(rows) if i in keep]
        return Issue(
            code=IssueCode.ADDRESS_MISMATCH,
            message=(f"{drawing.label}: the numbers read in the TERMINAL / ADDRESS column disagree with the module "
                     f"terminals on {len(disagree)} of {len(labelled)} rows (e.g. {read:02d} beside "
                     f"{rows_terminal(rows, n0)}), so the channel numbers were taken from the terminals"),
            file_path=drawing.file_path,
            drawing_id=drawing.id,
        )

    @staticmethod
    def _bands(ys: list[float]) -> list[tuple[float, float]]:
        gap = median([b - a for a, b in zip(ys, ys[1:])]) if len(ys) > 1 else 70.0
        bounds = [ys[0] - gap / 2] + [(a + b) / 2 for a, b in zip(ys, ys[1:])] + [ys[-1] + gap / 2]
        # A row reaches at most 0.6 of a row spacing from its address, so where an address is missing
        # (not drawn, or not decoded) that row's items go to no channel rather than to a neighbour.
        return [(max(lo, y - 0.6 * gap), min(hi, y + 0.6 * gap)) for y, lo, hi in zip(ys, bounds, bounds[1:])]

    @staticmethod
    def _row_centre(drawing: Drawing, row: str, default: float) -> float:
        return next((y for label, y in drawing.grid.rows if label == row), default)

    def _channel_claims(self, row: dict, module: str, ctx: ParseContext, common: dict) -> list[Claim]:
        anchor: TextLine = row["anchor"]
        channel_key = f"{module}/CH{row['channel']:02d}"
        description = " / ".join(ln.text.strip() for ln in sorted(row["description"], key=lambda ln: ln.bbox[1]))
        items: list[Claim] = row["items"]
        claims = [
            ctx.claim(
                **common,
                method=anchor.words[0].method,
                bbox=anchor.bbox,
                raw_text=anchor.text,
                subject_type=EntityType.PLC_CHANNEL,
                subject_key=channel_key,
                predicate=Predicate.HAS_CHANNEL,
                object_type=EntityType.PLC_MODULE,
                object_key=module,
                value={
                    "channel": row["channel"],
                    "module_terminals": row["terminals"],
                    "description": description,
                    "spare": "SPARE" in description.upper().split(" / ") or description.upper().startswith("SPARE"),
                    "devices": sorted({c.subject_key for c in items if c.subject_type is EntityType.DEVICE}),
                    "jbs": sorted({c.subject_key for c in items if c.subject_type is EntityType.JUNCTION_BOX}),
                    "locations": sorted({c.subject_key for c in items if c.subject_type is EntityType.LOCATION}),
                    "wires": sorted({c.subject_key for c in items if c.subject_type is EntityType.WIRE}),
                    # The whole row, from its leftmost item to the address, over the row's band: what a finding
                    # about something missing from the row outlines.
                    "row_bbox": [row["left"], row["band"][0], anchor.bbox[2], row["band"][1]],
                },
            )
        ]
        seen = set()
        for c in items:
            if (c.subject_type, c.subject_key) in seen:
                continue
            seen.add((c.subject_type, c.subject_key))
            claims.append(
                ctx.claim(
                    **common,
                    method=c.method,
                    bbox=c.source.bbox,
                    raw_text=c.source.raw_text,
                    subject_type=EntityType.PLC_CHANNEL,
                    subject_key=channel_key,
                    predicate=Predicate.WIRED_TO,
                    object_type=c.subject_type,
                    object_key=c.subject_key,
                )
            )
        return claims
