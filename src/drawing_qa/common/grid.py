"""Grid frame reader: border zone labels -> GridFrame.

The package convention: row letters A-Z (I and O skipped) down the left and right
edges, column numbers across the top. Tag grid references are <row><column>, so
N2 is row N, column 2.

Labels are searched for in a band along the left and top edges. Within the band,
the labels that line up (same x for rows, same y for columns) and increase in
order are kept, which drops stray letters and digits from title blocks and tables.
"""

from __future__ import annotations

import re
from collections import defaultdict

from drawing_qa.config import GridConfig
from drawing_qa.model.source import GridFrame
from drawing_qa.pdf.extract import PageContent

_ROW = re.compile(r"^[A-Z]$")
_COL = re.compile(r"^\d{1,2}$")
_ALIGN = 6.0  # points; labels on one edge share x (rows) or y (columns) within this
_ROW_ORDER = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def read_grid_frame(page: PageContent, config: GridConfig) -> GridFrame | None:
    """Return the sheet's grid, or None when no frame is found."""
    left = page.width * config.margin
    top = page.height * config.margin * 2  # some sheets are plotted with the frame offset downwards

    rows: dict[int, list[tuple[float, str]]] = defaultdict(list)
    cols: dict[int, list[tuple[float, str]]] = defaultdict(list)
    for line in page.lines:
        text = line.text.strip()
        x0, y0, x1, y1 = line.bbox
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if _ROW.match(text) and x1 <= left:
            rows[round(cx / _ALIGN)].append((cy, text))
        elif _COL.match(text) and y1 <= top:
            cols[round(cy / _ALIGN)].append((cx, text))

    best_rows = _fill_gaps(_best_line(rows, key=lambda label: label), list(_ROW_ORDER))
    best_cols = _fill_gaps(_best_line(cols, key=int), [str(i) for i in range(100)])
    if len(best_rows) < config.min_labels or len(best_cols) < config.min_labels:
        return None
    return GridFrame(rows=[(lbl, pos) for pos, lbl in best_rows], cols=[(lbl, pos) for pos, lbl in best_cols])


def _best_line(groups: dict[int, list[tuple[float, str]]], key) -> list[tuple[float, str]]:
    """Of the aligned groups (merging neighbouring buckets), the longest increasing run."""
    best: list[tuple[float, str]] = []
    for bucket in groups:
        items = sorted(groups.get(bucket - 1, []) + groups[bucket] + groups.get(bucket + 1, []))
        run = _increasing(items, key)
        if len(run) > len(best):
            best = run
    return best


def _fill_gaps(run: list[tuple[float, str]], order: list[str]) -> list[tuple[float, str]]:
    """Put back labels missing between two read ones (unreadable, or not decoded) when the gap between
    them is the usual spacing times the number missing, so their cells do not go to a neighbour."""
    if len(run) < 3 or any(label not in order for _, label in run):
        return run
    steps = sorted((b[0] - a[0]) / (order.index(b[1]) - order.index(a[1])) for a, b in zip(run, run[1:]))
    spacing = steps[len(steps) // 2]
    out = [run[0]]
    for (pa, la), (pb, lb) in zip(run, run[1:]):
        n = order.index(lb) - order.index(la)
        if n > 1 and abs((pb - pa) / n - spacing) <= 0.15 * spacing:
            out += [(pa + (pb - pa) * k / n, order[order.index(la) + k]) for k in range(1, n)]
        out.append((pb, lb))
    return out


def _increasing(items: list[tuple[float, str]], key) -> list[tuple[float, str]]:
    kept: list[tuple[float, str]] = []
    for pos, label in items:
        if not kept or key(label) > key(kept[-1][1]):
            kept.append((pos, label))
    return kept
