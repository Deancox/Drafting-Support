"""Survey: how much of a set of PDFs can be read, before any checking.

Every page of every PDF under a folder (any layout) is sorted into:
  readable  - has SHX text notes (or a text layer): read as normal;
  decode    - drawn, but plotted without SHX text notes (NO_SHX_TEXT): needs `run --ocr`;
  scanned   - an image with no text: would need real OCR, which the tool does not do;
  blank     - nothing on the page.
Nothing is parsed or checked, so it is quick.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from drawing_qa.config import Settings
from drawing_qa.model.enums import DocType, PageKind
from drawing_qa.parsers.titleblock import drawing_for_page
from drawing_qa.pdf.extract import extract_page, open_pdf

SHORT = {"readable": "SHX readable", "decode": "need decoding", "scanned": "scanned", "blank": "blank",
         "unreadable file": "would not open"}
LABELS = {
    "readable": "SHX readable",
    "decode": "Need decoding (--ocr)",
    "scanned": "Scanned (need OCR)",
    "blank": "Blank pages",
    "unreadable file": "Files that would not open",
}


@dataclass
class SheetStatus:
    path: str  # relative to the surveyed folder, forward slashes
    page: int  # 1-based
    status: str  # a key of LABELS
    creator: str = ""  # the software that plotted the PDF


def survey(root: Path, settings: Settings) -> list[SheetStatus]:
    from drawing_qa.pipeline import _unreadable_text

    folders = {name.lower(): t for name, t in settings.ingest.folders.items()}
    rows: list[SheetStatus] = []
    for path in sorted(root.rglob("*.pdf"), key=lambda p: p.as_posix().lower()):
        rel = path.relative_to(root).as_posix()
        doc_type = folders.get(rel.split("/")[0].lower(), DocType.PLC) if "/" in rel else DocType.PLC
        try:
            with open_pdf(path) as doc:
                creator = (doc.metadata or {}).get("creator") or ""
                for page in doc:
                    content = extract_page(page, settings.pdf.min_words_for_vector, None,
                                           settings.pdf.shx_annotation_titles, settings.pdf.min_drawing_bytes)
                    if content.kind is PageKind.EMPTY:
                        status = "blank"
                    elif content.kind is PageKind.RASTER:
                        status = "scanned"
                    else:
                        drawing = drawing_for_page(rel, doc_type, content, doc.page_count, settings)
                        status = "decode" if _unreadable_text(content, drawing) else "readable"
                    rows.append(SheetStatus(rel, page.number + 1, status, creator))
        except Exception:  # a broken file is listed, not fatal
            rows.append(SheetStatus(rel, 1, "unreadable file"))
    return rows


def render(rows: list[SheetStatus], root: Path) -> str:
    files = {r.path for r in rows}
    out = [f"Survey of {root}: {len(files)} PDF file(s), {len(rows)} sheet(s)"]
    for status, label in LABELS.items():
        mine = [r for r in rows if r.status == status]
        if mine or status in ("readable", "decode"):
            out.append(f"  {label + ':':<27}{len(mine)} sheet(s) in {len({r.path for r in mine})} file(s)")

    by_folder: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_folder[r.path.split("/")[0] if "/" in r.path else "."][r.status] += 1
    if len(by_folder) > 1:
        out.append("By folder:")
        for folder, counts in sorted(by_folder.items()):
            parts = ", ".join(f"{n} {SHORT[s]}" for s, n in sorted(counts.items()))
            out.append(f"  {folder:<14}{sum(counts.values())} sheet(s): {parts}")

    decode = [r for r in rows if r.status == "decode"]
    if decode:
        out.append("Plotted by (sheets needing decoding):")
        for creator, n in Counter(r.creator or "unknown software" for r in decode).most_common():
            out.append(f"  {n:>4}  {creator}")
        out.append("Sheets needing decoding (re-plot with PDFSHX = 1, or run with --ocr):")
        pages = Counter(r.path for r in rows)
        listed: dict[str, list[str]] = defaultdict(list)
        for r in decode:
            folder, _, name = r.path.rpartition("/")
            stem = name.rsplit(".", 1)[0]
            listed[folder or "."].append(stem if pages[r.path] == 1 else f"{stem} (page {r.page})")
        for folder, names in listed.items():
            out.append(f"  {folder}: {', '.join(names)}")
    scanned = [r for r in rows if r.status == "scanned"]
    if scanned:
        out.append("Scanned sheets (images: this tool cannot read them):")
        out += [f"  {r.path} (page {r.page})" for r in scanned]
    broken = [r for r in rows if r.status == "unreadable file"]
    if broken:
        out.append("Files that would not open:")
        out += [f"  {r.path}" for r in broken]
    return "\n".join(out)
