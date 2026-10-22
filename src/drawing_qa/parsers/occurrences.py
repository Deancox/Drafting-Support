"""Generic occurrence reader, run on every sheet of every document type.

Emits an OCCURS claim for each package identifier found anywhere on the sheet,
and a PLACEHOLDER claim for each incomplete-drafting marker.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from drawing_qa.common.identifiers import clean_token
from drawing_qa.model.enums import EntityType, Predicate
from drawing_qa.model.source import Drawing
from drawing_qa.parsers.base import ParseContext, ParseResult
from drawing_qa.pdf.extract import PageContent

if TYPE_CHECKING:
    from drawing_qa.parsers.base import DrawingParser

# Sheet cross-references: "(SHT 35)", "(CONT. SHT 204)", "SHEET 12". Not "SHEET 11 OF 20"
# (sheet numbering), and not on lines that are worked examples in a legend.
_CROSS_REF = re.compile(r"\b(?:SHT|SHEET)\.?\s*(\d{1,4})\b(?!\s+OF\b)", re.I)
_NOT_A_REFERENCE = re.compile(r"\bEXAMPLE\b", re.I)
# Relay contact table entry under a coil: "<sheet>/<grid>", e.g. "43/D4".
_CONTACT_POINTER = re.compile(r"^(?P<sheet>\d{1,3})/(?P<grid>[A-Z]\d)$")
_COIL_SEARCH_HEIGHT = 60.0  # points above a pointer to look for its coil's tag (table header)
_WORD = re.compile(r"^[A-Z]+$")


class OccurrenceReader:
    name = "occurrences"

    def read(self, page: PageContent, drawing: Drawing, ctx: ParseContext, parser: DrawingParser) -> ParseResult:
        result = ParseResult()
        common = {"parser": parser.doc_type, "parser_version": parser.version, "drawing": drawing}
        min_word = ctx.settings.spelling.min_length
        for line in page.lines:
            refs =[] if _NOT_A_REFERENCE.search(line.text) else _CROSS_REF.finditer(line.text)
            for m in refs:
                result.claims.append(
                    ctx.claim(
                        **common,
                        method=line.words[0].method,
                        bbox=line.bbox,
                        raw_text=line.text,
                        subject_type=EntityType.DRAWING,
                        subject_key=f"{drawing.drawing_no}/{int(m.group(1))}",
                        predicate=Predicate.CROSS_REF,
                        value={"drawing_no": drawing.drawing_no, "sheet": int(m.group(1))},
                    )
                )
            phrase_hits = ctx.placeholders.check_line(line.text)
            # Words already reported as part of a phrase are not reported again on their own.
            phrase_words = {w for hit in phrase_hits for w in hit.text.split()}
            for hit in phrase_hits:
                result.claims.append(
                    ctx.claim(
                        **common,
                        method=line.words[0].method,
                        bbox=line.bbox,
                        raw_text=line.text,
                        subject_type=EntityType.TEXT,
                        subject_key=hit.text,
                        predicate=Predicate.PLACEHOLDER,
                        value={"pattern": hit.pattern, "line": line.text},
                    )
                )
            for word in line.words:
                ident = ctx.grammar.parse(word.text)
                hit = ctx.placeholders.check_token(word.text, line.text)
                if hit is not None and hit.text not in phrase_words:
                    value = {"pattern": hit.pattern, "line": line.text}
                    if ident is not None and "superseded_by" in ident.parts:
                        # e.g. AUT1 in "=K07.11+LTL-AUT1": the fix is the current designation.
                        value["replacement"] = ident.parts["superseded_by"]
                    result.claims.append(
                        ctx.claim(
                            **common,
                            method=word.method,
                            bbox=word.bbox,
                            raw_text=word.text,
                            subject_type=EntityType.TEXT,
                            subject_key=hit.text,
                            predicate=Predicate.PLACEHOLDER,
                            value=value,
                        )
                    )
                # A word can be both: "=K07.11+LTL-AUT1" is a location and contains AUT1.
                if ident is None:
                    text = clean_token(word.text)
                    if hit is None and len(text) >= min_word and _WORD.match(text):
                        result.claims.append(
                            ctx.claim(
                                **common,
                                method=word.method,
                                bbox=word.bbox,
                                raw_text=word.text,
                                subject_type=EntityType.TEXT,
                                subject_key=text,
                                predicate=Predicate.WORD,
                            )
                        )
                    continue
                result.claims.append(
                    ctx.claim(
                        **common,
                        method=word.method,
                        bbox=word.bbox,
                        raw_text=word.text,
                        subject_type=ident.kind,
                        subject_key=ident.key,
                        predicate=Predicate.OCCURS,
                        value={**ident.parts, "is_variant": ident.is_variant},
                    )
                )
        result.claims.extend(self._contact_pointers(page, drawing, ctx, common, result.claims))
        return result

    def _contact_pointers(self, page, drawing, ctx, common, claims):
        """Relay contact tables: each "43/D4" under a coil tag points to that coil's auxiliary
        contact on sheet 43 at grid D4, which is labelled <prefix>-<FFI><coil sheet>D4."""
        coils = [c for c in claims if c.subject_type is EntityType.DEVICE and c.value.get("format_valid")]
        for line in page.lines:
            m = _CONTACT_POINTER.match(line.text.strip())
            if not m:
                continue
            x0, y0, x1, _ = line.bbox
            above = [
                c
                for c in coils
                if 0 < y0 - c.source.bbox[3] <= _COIL_SEARCH_HEIGHT
                and c.source.bbox[0] < x1 + 15
                and c.source.bbox[2] > x0 - 15
            ]
            coil = min(above, key=lambda c: y0 - c.source.bbox[3]) if above else None
            aux_tag = None
            if coil is not None:
                v = coil.value
                aux_tag = f"{v['prefix']}-{v['ffi']}{v['sheet']}{m['grid']}"
            yield ctx.claim(
                **common,
                method=line.words[0].method,
                bbox=line.bbox,
                raw_text=line.text,
                subject_type=EntityType.DEVICE,
                subject_key=coil.subject_key if coil else "",
                predicate=Predicate.CONTACT_REF,
                value={
                    "target_sheet": int(m["sheet"]),
                    "target_grid": m["grid"],
                    "coil": coil.subject_key if coil else None,
                    "aux_tag": aux_tag,
                },
            )
