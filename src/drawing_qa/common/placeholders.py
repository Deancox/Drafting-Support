"""Placeholder detection (incomplete drafting markers).

Detection happens during parsing so every placeholder becomes a claim with its
exact position. The PLACEHOLDER rules (M1) turn those claims into findings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from drawing_qa.common.identifiers import clean_token
from drawing_qa.config import PlaceholderConfig

# Identifier-shaped tokens whose variable part is a run of X: S-TNXXXX, TNXXXX, PXXXXX.
_X_RUN = re.compile(r"^(?:[A-Z]{1,4}-)?[A-Z]{0,2}X{3,}$")
# Junction box or terminal with an X placeholder: SPL-JBX, SPL-JB1:XX.
_JB_X = re.compile(r"^[A-Z]{2,4}-JBX+$")
_TERMINAL_X = re.compile(r"^[A-Z0-9-]+:X+$")
# Separators inside compound identifiers such as "=K07.11+LTL-AUT1".
_COMPONENT_SPLIT = re.compile(r"[=+:\-./()]+")
_NON_WORD = re.compile(r"[^A-Z0-9 ]+")


def normalise_phrase(text: str) -> str:
    """Upper-case, punctuation to spaces, single spaces: '(REF TERM. XXX)' -> 'REF TERM XXX'."""
    return " ".join(_NON_WORD.sub(" ", text.upper()).split())


@dataclass(frozen=True)
class PlaceholderHit:
    pattern: str  # which rule matched: TOKEN, COMPONENT, X_RUN, IDENTIFIER_X, PHRASE
    text: str  # the matched text, upper-cased


class PlaceholderDetector:
    def __init__(self, config: PlaceholderConfig) -> None:
        self._tokens = {t.upper() for t in config.tokens}
        self._phrases = [normalise_phrase(p) for p in config.phrases]
        self._hold_exceptions = [normalise_phrase(h) for h in config.hold_exceptions]

    def check_token(self, raw: str, line_text: str = "") -> PlaceholderHit | None:
        token = clean_token(raw)
        if not token:
            return None
        if token == "HOLD" and any(exc in normalise_phrase(line_text) for exc in self._hold_exceptions):
            return None
        if token in self._tokens:
            return PlaceholderHit("TOKEN", token)
        if _X_RUN.match(token):
            return PlaceholderHit("X_RUN", token)
        if _JB_X.match(token) or _TERMINAL_X.match(token):
            return PlaceholderHit("IDENTIFIER_X", token)
        # A listed token inside a compound identifier, e.g. AUT1 in "=K07.11+LTL-AUT1".
        for part in _COMPONENT_SPLIT.split(token):
            if part in self._tokens and part != "HOLD":
                return PlaceholderHit("COMPONENT", part)
        return None

    def check_line(self, line_text: str) -> list[PlaceholderHit]:
        """Multi-word phrases such as 'REF TERM XXX', ignoring punctuation."""
        normalised = normalise_phrase(line_text)
        return [PlaceholderHit("PHRASE", p) for p in self._phrases if p in normalised]
