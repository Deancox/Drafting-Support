"""Connector pin labels beside wire numbers, on schematics and termination sheets.

A field device wired by plug or connector has its pins written beside each wire ("E1 / PIN D" on
schematic 401103-056, "TN56E1 / PIN A" on termination sheet 401103-202). Each "PIN x" label is
paired with the wire number nearest to it on its own line of text (within half a text height or so
up or down and a short way left or right), giving WIRE AT_PIN claims (value: pin) for TERM-007.
"""

from __future__ import annotations

import re

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import EntityType, ExtractionMethod, Predicate
from drawing_qa.model.source import Drawing
from drawing_qa.pdf.extract import PageContent

PIN_RE = re.compile(r"^PIN\s*(?P<pin>[A-Z0-9]{1,3})$")
_MAX_DY = 9.0  # pin label to its wire number, centre to centre (a row is ~14pt)
_MAX_DX = 90.0


def _mid(b):
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def read_pins(page: PageContent, drawing: Drawing, ctx, parser, wires: list[Claim]) -> list[Claim]:
    labels = [w for w in wires if w.subject_type is EntityType.WIRE and w.predicate is Predicate.OCCURS and w.source.bbox]
    out = []
    for line in page.lines:
        m = PIN_RE.match(line.text.strip().upper())
        if not m:
            continue
        px, py = _mid(line.bbox)
        near = []
        for w in labels:
            wx, wy = _mid(w.source.bbox)
            if abs(wy - py) <= _MAX_DY and abs(wx - px) <= _MAX_DX:
                near.append((abs(wy - py) + abs(wx - px) / 10, w))
        if not near:
            continue
        near.sort(key=lambda t: t[0])
        if len(near) > 1 and near[1][1].subject_key != near[0][1].subject_key and near[1][0] - near[0][0] < 1.5:
            continue  # two wires equally close: no telling which the pin belongs to
        wire = near[0][1]
        out.append(ctx.claim(parser=parser.doc_type, parser_version=parser.version, drawing=drawing,
                             method=ExtractionMethod.TEMPLATE, bbox=line.bbox, raw_text=f"{wire.source.raw_text} {line.text}",
                             subject_type=EntityType.WIRE, subject_key=wire.subject_key, predicate=Predicate.AT_PIN,
                             value={"pin": m["pin"]}))
    return out
