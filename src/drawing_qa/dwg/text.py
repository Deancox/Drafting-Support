"""Read a sheet's text from its DXF and place it on the PDF page.

Only what plots is read: text on layers that are off, frozen or set not to plot, and invisible attributes,
are left out, because they are not on the PDF. An attribute definition left loose in model space plots its tag. Text inside blocks (title block, symbols) is read through
the block, with attribute values (tags are often attributes). Positions are in drawing units (mm).

The DWG is lined up with the PDF by its line work: the extents of the long lines give the scale and offset,
and then every horizontal and vertical line of the DWG must land on a line of the PDF. A DWG that does not
line up (another revision, another layout plotted, a rotated plot) is not used.
"""

from __future__ import annotations

import bisect
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from drawing_qa.model.enums import ExtractionMethod
from drawing_qa.model.source import BBox
from drawing_qa.pdf.extract import Segment, Word, decode_autocad_text, split_annotation

# Block numbers for DWG text start here so they never collide with text-layer or SHX-note blocks.
_DWG_BLOCK_BASE = 200_000
_MAX_DEPTH = 8  # nested blocks


@dataclass(frozen=True)
class DwgText:
    text: str
    corners: tuple[tuple[float, float], ...]  # the text's box in drawing units, rotated with the text
    height: float
    vertical: bool  # runs up or down the sheet


@dataclass
class DwgSheet:
    texts: list[DwgText]
    lines: list[tuple[float, float, float, float]]  # plotted LINE / polyline segments, drawing units
    saved: datetime | None = None  # when the DWG was last saved (dwg_saved: from the DWG, not the DXF)
    name: str = ""  # the DWG's file name, for messages


@dataclass(frozen=True)
class Placement:
    """Drawing units -> PDF points: x' = ox + s*x, y' = oy - s*y (PDF y runs down)."""

    s: float
    ox: float
    oy: float

    def point(self, x: float, y: float) -> tuple[float, float]:
        return self.ox + self.s * x, self.oy - self.s * y


@dataclass
class Placed:
    words: list[Word] = field(default_factory=list)
    texts: int = 0
    unplaced: list[str] = field(default_factory=list)  # texts with no ink under them on the PDF
    reason: str = ""  # why the DWG was not used; "" when it was


# ---------------------------------------------------------------------------------------------- reading


def read_dxf(path: Path, char_width: float = 0.72) -> DwgSheet:
    """Every plotted piece of text, and the plotted line work, from model space."""
    import ezdxf  # optional dependency: only needed when DWGs are read

    doc = ezdxf.readfile(path)
    hidden = {layer.dxf.name.upper() for layer in doc.layers
              if layer.is_off() or layer.is_frozen() or not layer.dxf.get("plot", 1)}
    texts: list[DwgText] = []
    lines: list[tuple[float, float, float, float]] = []

    def walk(entities, parent_layer: str, depth: int) -> None:
        for e in entities:
            layer = e.dxf.get("layer", "0").upper()
            if layer == "0" and parent_layer:
                layer = parent_layer  # entities on layer 0 in a block take the insert's layer
            if layer in hidden or e.dxf.get("invisible", 0):
                continue
            kind = e.dxftype()
            if kind == "INSERT":
                for a in e.attribs:
                    if not a.dxf.get("flags", 0) & 1 and a.dxf.get("layer", "0").upper() not in hidden:
                        texts.extend(_single_line(a, char_width))
                if depth < _MAX_DEPTH:
                    try:
                        walk(e.virtual_entities(), layer, depth + 1)
                    except Exception:  # an odd block: its text is left out rather than failing the sheet
                        continue
            elif kind in ("TEXT", "ATTRIB"):
                texts.extend(_single_line(e, char_width))
            elif kind == "ATTDEF" and depth == 0 and not e.dxf.get("flags", 0) & 1:
                # Left loose in model space (an exploded block), an attribute definition plots its tag:
                # the channel addresses 24..31 on 401104-151. Inside a block it is a template and does not.
                texts.extend(_single_line(e, char_width, e.dxf.get("tag", "")))
            elif kind == "MTEXT":
                texts.extend(_mtext(e, char_width))
            elif kind == "LINE":
                lines.append((e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y))
            elif kind == "LWPOLYLINE":
                pts = [p[:2] for p in e.get_points()]
                if e.closed and pts:
                    pts.append(pts[0])
                lines.extend((a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:]))

    walk(doc.modelspace(), "", 0)
    return DwgSheet(texts=texts, lines=lines)


def _box(ax: float, ay: float, u0: float, v0: float, w: float, h: float, rot: float):
    """Corners of a w x h box whose lower-left is (u0, v0) from the anchor along the text direction."""
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    return tuple((ax + u * c - v * s, ay + u * s + v * c) for u, v in ((u0, v0), (u0 + w, v0), (u0 + w, v0 + h), (u0, v0 + h)))


def _vertical(rot: float) -> bool:
    return 45 < rot % 180 < 135


def _single_line(e, char_width: float, text: str | None = None) -> list[DwgText]:
    """TEXT / ATTRIB (or a loose ATTDEF, with its tag as the text): one line, placed by its alignment."""
    text = decode_autocad_text(e.dxf.get("text", "") if text is None else text).strip()
    h = float(e.dxf.get("height", 0) or 0)
    if not text or h <= 0:
        return []
    rot = float(e.dxf.get("rotation", 0) or 0)
    w = len(text) * char_width * h * float(e.dxf.get("width", 1) or 1)
    halign, valign = int(e.dxf.get("halign", 0)), int(e.dxf.get("valign", 0))
    ins = e.dxf.insert
    anchor = ins if (halign, valign) == (0, 0) else e.dxf.get("align_point", ins)
    if halign in (3, 5):  # aligned / fit: the text runs from the insert to the align point
        anchor, u0 = ins, 0.0
        w = math.dist((ins.x, ins.y), (e.dxf.get("align_point", ins).x, e.dxf.get("align_point", ins).y)) or w
    else:
        u0 = -{0: 0.0, 1: w / 2, 2: w, 4: w / 2}.get(halign, 0.0)
    v0 = -h / 2 if halign == 4 or valign == 2 else -h if valign == 3 else 0.0
    return [DwgText(text, _box(anchor.x, anchor.y, u0, v0, w, h, rot), h, _vertical(rot))]


def _mtext(e, char_width: float) -> list[DwgText]:
    """MTEXT: one DwgText per line, wrapped at the MTEXT's width the way AutoCAD wraps it (by estimate)."""
    h = float(e.dxf.get("char_height", 0) or 0)
    if h <= 0:
        return []
    try:
        raw = e.plain_text(split=True)
    except Exception:
        raw = [e.dxf.get("text", "")]
    limit = float(e.dxf.get("width", 0) or 0)
    lines: list[str] = []
    for line in raw:
        line = decode_autocad_text(line).strip()
        if limit > 0:
            lines.extend(_wrap(line, limit, h * char_width))
        elif line:
            lines.append(line)
    if not lines:
        return []
    rot = e.get_rotation() if hasattr(e, "get_rotation") else float(e.dxf.get("rotation", 0) or 0)
    pitch = 5 / 3 * h * float(e.dxf.get("line_spacing_factor", 1) or 1)
    total = h + (len(lines) - 1) * pitch
    point = int(e.dxf.get("attachment_point", 1))
    col, row = (point - 1) % 3, (point - 1) // 3
    top = {0: 0.0, 1: total / 2, 2: total}[row]  # the block's top above the anchor
    ins = e.dxf.insert
    out = []
    for i, line in enumerate(lines):
        w = len(line) * char_width * h
        u0 = -{0: 0.0, 1: w / 2, 2: w}[col]
        v0 = top - h - i * pitch
        out.append(DwgText(line, _box(ins.x, ins.y, u0, v0, w, h, rot), h, _vertical(rot)))
    return out


def _wrap(line: str, limit: float, advance: float) -> list[str]:
    out, current = [], ""
    for word in line.split():
        trial = f"{current} {word}".strip()
        if current and len(trial) * advance > limit * 1.02:
            out.append(current)
            current = word
        else:
            current = trial
    return out + ([current] if current else [])


# ---------------------------------------------------------------------------------------------- placing


def _extent(values):
    return (min(values), max(values)) if values else (0.0, 0.0)


def fit(sheet: DwgSheet, segments: list[Segment], min_match: float = 0.9) -> tuple[Placement | None, str]:
    """Line the DWG up with the PDF: scale and offset from the extents of the long lines, then check that
    the DWG's horizontal and vertical lines land on lines of the PDF."""
    if not sheet.lines or not segments:
        return None, "no line work to line up"
    xs = [v for l in sheet.lines for v in (l[0], l[2])]
    ys = [v for l in sheet.lines for v in (l[1], l[3])]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    long_d = [l for l in sheet.lines if math.dist(l[:2], l[2:]) > 0.05 * span]
    page_span = max(max(max(s.x0, s.x1) for s in segments), max(max(s.y0, s.y1) for s in segments))
    long_p = [s for s in segments if s.length > 0.05 * page_span]
    if not long_d or not long_p:
        return None, "no long lines to line up"
    dx, dy = _extent([v for l in long_d for v in (l[0], l[2])]), _extent([v for l in long_d for v in (l[1], l[3])])
    px, py = _extent([v for s in long_p for v in (s.x0, s.x1)]), _extent([v for s in long_p for v in (s.y0, s.y1)])
    if dx[1] - dx[0] <= 0 or dy[1] - dy[0] <= 0:
        return None, "no long lines to line up"
    sx, sy = (px[1] - px[0]) / (dx[1] - dx[0]), (py[1] - py[0]) / (dy[1] - dy[0])
    if abs(sx - sy) > 0.005 * max(sx, sy):
        return None, f"its line work does not line up with the PDF's (scale {sx:.4f} across, {sy:.4f} down)"
    s = (sx + sy) / 2
    place = Placement(s=s, ox=px[0] - s * dx[0], oy=py[1] + s * dy[0])
    matched, checked = _matched_lines(sheet.lines, segments, place)
    if not checked or matched < min_match * checked:
        return None, f"only {matched} of {checked} of its straight lines are on the PDF"
    return place, ""


def _matched_lines(lines, segments: list[Segment], place: Placement) -> tuple[int, int]:
    tol = 0.6
    horiz = sorted(((s.y0 + s.y1) / 2, min(s.x0, s.x1), max(s.x0, s.x1)) for s in segments if s.horizontal and s.length > 1)
    vert = sorted(((s.x0 + s.x1) / 2, min(s.y0, s.y1), max(s.y0, s.y1)) for s in segments if s.vertical and s.length > 1)
    hk, vk = [h[0] for h in horiz], [v[0] for v in vert]
    matched = checked = 0
    for x0, y0, x1, y1 in lines:
        (ax, ay), (bx, by) = place.point(x0, y0), place.point(x1, y1)
        if abs(ay - by) < 0.1 and abs(bx - ax) > 5 * place.s:
            index, keys, at, lo, hi = horiz, hk, ay, min(ax, bx), max(ax, bx)
        elif abs(ax - bx) < 0.1 and abs(by - ay) > 5 * place.s:
            index, keys, at, lo, hi = vert, vk, ax, min(ay, by), max(ay, by)
        else:
            continue
        checked += 1
        i = bisect.bisect_left(keys, at - tol)
        while i < len(index) and index[i][0] <= at + tol:
            if index[i][1] <= hi + 1 and index[i][2] >= lo - 1 and (abs(index[i][1] - lo) < 1.5 or abs(index[i][2] - hi) < 1.5
                                                                   or index[i][1] <= lo + 1 and index[i][2] >= hi - 1):
                matched += 1
                break
            i += 1
    return matched, checked


def place(sheet: DwgSheet, segments: list[Segment], min_match: float = 0.9) -> Placed:
    """The DWG's text as words on the PDF page, or the reason it could not be placed."""
    where, reason = fit(sheet, segments, min_match)
    if where is None:
        return Placed(reason=reason)
    ink = _InkIndex(segments)
    out = Placed(texts=len(sheet.texts))
    for i, t in enumerate(sheet.texts):
        pts = [where.point(x, y) for x, y in t.corners]
        bbox: BBox = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
        if not ink.any_in(bbox, t.height * where.s * 1.5):
            out.unplaced.append(t.text)
        pad = 0.2 * t.height * where.s
        bbox = (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)
        out.words.extend(split_annotation(t.text, bbox, _DWG_BLOCK_BASE + i, ExtractionMethod.DWG_TEXT, vertical=t.vertical))
    return out


class _InkIndex:
    """Short PDF strokes by cell: the letters a text's box should cover."""

    def __init__(self, segments: list[Segment], cell: float = 20.0) -> None:
        self.cell = cell
        self.cells: dict[tuple[int, int], list[Segment]] = {}
        for s in segments:
            if s.length < 30:
                mx, my = (s.x0 + s.x1) / 2, (s.y0 + s.y1) / 2
                self.cells.setdefault((int(mx // cell), int(my // cell)), []).append(s)

    def any_in(self, bbox: BBox, max_len: float) -> bool:
        c = self.cell
        for gx in range(int(bbox[0] // c), int(bbox[2] // c) + 1):
            for gy in range(int(bbox[1] // c), int(bbox[3] // c) + 1):
                for s in self.cells.get((gx, gy), ()):
                    mx, my = (s.x0 + s.x1) / 2, (s.y0 + s.y1) / 2
                    if s.length <= max_len and bbox[0] <= mx <= bbox[2] and bbox[1] <= my <= bbox[3]:
                        return True
        return False


_PDF_DATE = re.compile(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?(?:([Zz+-])(\d{2})?'?(\d{2})?)?")


def pdf_created(metadata: dict | None) -> datetime | None:
    """When the PDF was plotted, from its CreationDate. AutoCAD writes local time with no zone: taken as
    this computer's zone."""
    m = _PDF_DATE.match((metadata or {}).get("creationDate") or "")
    if not m:
        return None
    parts = [int(p) if p else 0 for p in m.groups()[:6]]
    sign, hh, mm = m.group(7), m.group(8), m.group(9)
    try:
        when = datetime(*parts)
    except ValueError:
        return None
    if sign in ("Z", "z"):
        return when.replace(tzinfo=timezone.utc)
    if sign in ("+", "-"):
        offset = timedelta(hours=int(hh or 0), minutes=int(mm or 0))
        return when.replace(tzinfo=timezone(offset if sign == "+" else -offset))
    return when.astimezone()  # naive: local time


_SAVED = re.compile(r'<prop_set fmt_id="\{f29f85e0-4ff9-1068-ab91-08002b27b3d9\}">(.*?)</prop_set>', re.S)
_PROP = re.compile(r'<prop id="(\d+)"><datetime>([0-9T:-]+)</datetime>')


def dwg_saved(dwg: Path) -> datetime | None:
    """When the DWG was last saved (UTC), from the summary information AutoCAD keeps uncompressed in the file
    (property 13, or 10 as AutoCAD writes it; 12 is when it was created). Read from the DWG itself, because
    converting it to DXF stamps the DXF with the conversion time. None when not found."""
    try:
        raw = dwg.read_bytes()
    except OSError:
        return None
    # UTF-16 text may start at an odd byte
    m = _SAVED.search(raw.decode("utf-16le", errors="ignore")) or _SAVED.search(raw[1:].decode("utf-16le", errors="ignore"))
    if not m:
        return None
    props = dict(_PROP.findall(m.group(1)))
    value = props.get("13") or props.get("10")
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc) if value else None
    except ValueError:
        return None
