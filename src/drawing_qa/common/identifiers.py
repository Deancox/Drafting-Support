"""Package identifier grammar and canonical keys.

Formats (GAME_PLAN.md "Engineering conventions"):
    Device tag   <Prefix>-<FFI><Sheet><Grid>   S-TN23N2
    Wire number  <FFI><Sheet><Grid>            TN23N2
    Junction box <AREA>-JB<n>                  SPL-JB1
    Terminal     <JunctionBox>:<Terminal>      SPL-JB1:1
    Panel        <AREA>-<PanelType>            LTL-PLC   (to confirm)
    Location     <AREA>-<Code><n>              LTL-LCS3, LTL-AUT1 (known areas only)

Locations may carry an IEC 81346 reference designation prefix on the drawings,
e.g. "=K07.11+LTL-JB1" (function =K07.11, location +LTL-JB1). The function
aspect is kept as a part; the key is the location alone.

This module is the only place canonical keys are produced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from drawing_qa.config import IdentifierConfig
from drawing_qa.model.enums import EntityType

_EDGE_PUNCTUATION = ".,;:()[]{}<>\"'`"
_DESIGNATION = re.compile(r"^(?:=(?P<function>[A-Z0-9.]+))?\+(?P<location>[A-Z0-9][A-Z0-9:_ -]*)$")


def clean_token(text: str) -> str:
    """Upper-case a word and strip punctuation that drafting leaves around identifiers."""
    return text.strip().strip(_EDGE_PUNCTUATION).upper()


@dataclass(frozen=True)
class ParsedIdentifier:
    kind: EntityType
    key: str
    raw: str
    parts: dict[str, str | bool] = field(default_factory=dict)
    spelled: str = ""  # the identifier as written, cleaned, without any designation prefix

    @property
    def is_variant(self) -> bool:
        """True when the drawing spelled the identifier differently from its canonical key."""
        return (self.spelled or clean_token(self.raw)) != self.key


class IdentifierGrammar:
    def __init__(self, config: IdentifierConfig) -> None:
        self._config = config
        self._ffis = {f.upper() for f in config.ffis}
        self._prefixes = {p.upper() for p in config.prefixes}
        self._areas = {a.upper() for a in config.areas}
        self._superseded = {k.upper(): v.upper() for k, v in config.superseded.items()}
        area = r"(?P<area>[A-Z][A-Z0-9]{1,4})"  # LTL, SPL, EER1
        jb = rf"{area}[-_ ]?JB[-_ ]?(?P<num>\d+)"
        loc = rf"(?P<ffi>{config.ffi})(?P<sheet>{config.sheet})(?P<grid>{config.grid})"
        panel_types = "|".join(re.escape(t.upper()) for t in config.panel_types) or "PLC"
        self._terminal = re.compile(rf"^{jb}:(?P<term>[A-Z0-9]+)$")
        self._jb = re.compile(rf"^{jb}$")
        self._panel = re.compile(rf"^{area}-(?P<ptype>{panel_types})(?P<num>\d*)$")
        self._location = re.compile(rf"^{area}-(?P<code>[A-Z]{{2,5}})(?P<num>\d*)$")
        self._device = re.compile(rf"^(?P<prefix>{config.prefix})-{loc}$")
        self._wire = re.compile(rf"^{loc}$")
        # Looks like a device tag but does not fit the format (e.g. H-TN51F, missing grid digit).
        # Kept as a DEVICE claim with format_valid=False so TAG-001 can report it.
        self._device_malformed = re.compile(rf"^(?P<prefix>{config.prefix})-(?P<ffi>{config.ffi})\d[A-Z0-9]*$")
        # A known FFI followed by a digit that does not fit <FFI><Sheet><Grid> (TN36D, TN3D6): WIRE-001.
        # Only for FFIs the package uses, so part numbers and words (THERMISTOR) are not caught.
        self._wire_ffis = self._ffis | {f.upper() for f in config.ffi_drawings}
        self._wire_malformed = re.compile(rf"^(?P<ffi>{config.ffi})\d[A-Z0-9]*$")

    def parse(self, raw: str) -> ParsedIdentifier | None:
        """Parse one word. Returns None when it is not a package identifier."""
        token = clean_token(raw)
        if not token:
            return None
        function = None
        if d := _DESIGNATION.match(token):
            function, token = d["function"], d["location"]
        parsed = self._parse_token(token, raw)
        if parsed is None:
            return None
        parts = dict(parsed.parts)
        if function:
            parts["function"] = function
        if replacement := self._superseded.get(parsed.key):
            parts["superseded_by"] = replacement
        return ParsedIdentifier(parsed.kind, parsed.key, raw, parts, spelled=token)

    def _parse_token(self, token: str, raw: str) -> ParsedIdentifier | None:
        if m := self._terminal.match(token):
            jb_key = self._jb_key(m)
            return ParsedIdentifier(
                EntityType.TERMINAL,
                f"{jb_key}:{m['term']}",
                raw,
                {"jb": jb_key, "terminal": m["term"], "known_area": self._known_area(m["area"])},
            )
        if m := self._jb.match(token):
            return ParsedIdentifier(
                EntityType.JUNCTION_BOX,
                self._jb_key(m),
                raw,
                {"area": m["area"], "number": str(int(m["num"])), "known_area": self._known_area(m["area"])},
            )
        if m := self._panel.match(token):
            return ParsedIdentifier(
                EntityType.PANEL,
                token,
                raw,
                {
                    "area": m["area"],
                    "panel_type": m["ptype"],
                    "number": m["num"],
                    "known_area": self._known_area(m["area"]),
                },
            )
        # Generic locations only for configured areas: the pattern is too loose otherwise.
        if (m := self._location.match(token)) and m["code"] == "JB":
            # "SPL-JB" with no number: a junction box reference that does not fit the format.
            return ParsedIdentifier(
                EntityType.JUNCTION_BOX,
                token,
                raw,
                {"area": m["area"], "known_area": self._known_area(m["area"]), "format_valid": False},
            )
        if (m := self._location.match(token)) and m["area"] in self._areas:
            return ParsedIdentifier(
                EntityType.LOCATION,
                token,
                raw,
                {"area": m["area"], "code": m["code"], "number": m["num"]},
            )
        if m := self._device.match(token):
            return ParsedIdentifier(
                EntityType.DEVICE,
                token,
                raw,
                {
                    "prefix": m["prefix"],
                    "ffi": m["ffi"],
                    "sheet": m["sheet"],
                    "grid": m["grid"],
                    "known_prefix": not self._prefixes or m["prefix"] in self._prefixes,
                    "known_ffi": not self._ffis or m["ffi"] in self._ffis,
                    "format_valid": True,
                },
            )
        if m := self._wire.match(token):
            # Bare <FFI><Sheet><Grid> tokens are common in other text (part numbers),
            # so when an FFI list is configured, only known FFIs count as wires.
            if self._ffis and m["ffi"] not in self._ffis:
                return None
            return ParsedIdentifier(
                EntityType.WIRE,
                token,
                raw,
                {"ffi": m["ffi"], "sheet": m["sheet"], "grid": m["grid"]},
            )
        if (m := self._device_malformed.match(token)) and (not self._ffis or m["ffi"] in self._ffis):
            return ParsedIdentifier(
                EntityType.DEVICE,
                token,
                raw,
                {"prefix": m["prefix"], "ffi": m["ffi"], "format_valid": False},
            )
        if (m := self._wire_malformed.match(token)) and m["ffi"] in self._wire_ffis:
            return ParsedIdentifier(EntityType.WIRE, token, raw, {"ffi": m["ffi"], "format_valid": False})
        return None

    def _jb_key(self, m: re.Match[str]) -> str:
        return f"{m['area']}-JB{int(m['num'])}"

    def _known_area(self, area: str) -> bool:
        return not self._areas or area in self._areas
