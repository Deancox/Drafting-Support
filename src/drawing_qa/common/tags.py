"""Comparing device tags."""

from __future__ import annotations

_ROWS = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def near_tags(a: str, b: str, rows: int = 2, cols: int = 2) -> bool:
    """Same prefix, FFI and sheet, grid within a couple of rows and columns: likely the same device
    referenced with a wrong grid (S-TN40D4 on the schematic, S-TN40D2 on the CBD)."""
    if "-" not in a or "-" not in b:
        return False
    pa, ra = a.split("-", 1)
    pb, rb = b.split("-", 1)
    if pa != pb or ra[:-2] != rb[:-2] or len(ra) < 3 or len(rb) < 3:
        return False
    ga, gb = ra[-2:], rb[-2:]
    if ga[0] not in _ROWS or gb[0] not in _ROWS or not (ga[1].isdigit() and gb[1].isdigit()):
        return False
    return abs(_ROWS.index(ga[0]) - _ROWS.index(gb[0])) <= rows and abs(int(ga[1]) - int(gb[1])) <= cols
