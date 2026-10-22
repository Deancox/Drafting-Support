"""Channel descriptions: the project prefix they share, and the heading that names the signal.

Schematic channel descriptions are written in lines, e.g.
    "07.11 SHIPLOADER / LONG TRAVEL BRAKE / RELEASED SWITCH - LTL BRAKE 9 / (1 = RELEASED)".
The first line is often the same project / area text on every channel. It says nothing about the
signal, so it is detected (not hard-coded) and left out of headings and report text.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

_SEP = " / "
MIN_SHARE = 0.5  # a first line counts as the prefix when at least half of the descriptions start with it
MIN_HEADINGS = 3  # ... and it is followed by at least this many different headings (it names a project, not a signal)
MIN_DESCRIPTIONS = 5


def detect_prefix(descriptions: Iterable[str]) -> str:
    """The first description line shared by at least half of the (multi-line) descriptions and followed by
    several different headings, or "". Two contacts of one device ("SKEW EAST / CONTACT 1", "SKEW EAST /
    CONTACT 2") share a first line too, but that line is their heading, not a project prefix."""
    split = [[ln.strip().upper() for ln in d.split(_SEP) if ln.strip()] for d in descriptions if d and _SEP in d]
    split = [lines for lines in split if len(lines) >= 2]
    if len(split) < MIN_DESCRIPTIONS:
        return ""
    line, count = Counter(lines[0] for lines in split).most_common(1)[0]
    following = {lines[1] for lines in split if lines[0] == line}
    return line if count >= MIN_SHARE * len(split) and len(following) >= MIN_HEADINGS else ""


def heading(description: str, prefix: str = "") -> str:
    """The first line after the project prefix, upper case, e.g. 'LONG TRAVEL BRAKE'."""
    lines = [ln.strip() for ln in description.split(_SEP) if ln.strip()]
    if prefix and lines and lines[0].upper() == prefix.upper():
        lines = lines[1:]
    return lines[0].upper() if lines else ""


def without_prefix(description: str, prefix: str = "") -> str:
    """The description with the project prefix line removed, for display."""
    if prefix and description.upper().startswith(prefix.upper() + _SEP):
        return description[len(prefix) + len(_SEP):]
    return description
