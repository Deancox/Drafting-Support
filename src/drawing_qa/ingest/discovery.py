"""Discover input PDFs (IMPLEMENTATION_PLAN.md section 10).

Expected layout:
    <input root>/
        PLC/            PLC drawings
        CableBlocks/    cable block diagrams
        Terminations/   termination drawings
        EquipmentList/  electrical equipment list (optional; Excel, CSV or PDF)
Folder names are matched case-insensitively against config [ingest.folders].
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from drawing_qa.config import Settings
from drawing_qa.model.enums import DocType, IssueCode
from drawing_qa.model.source import InputFile, Issue

_REQUIRED_TYPES = (DocType.PLC, DocType.CBD, DocType.TERM)
# File types accepted per document type; drawings are PDF only.
_EXTENSIONS = {DocType.EQUIP: {".xlsx", ".xlsm", ".xls", ".csv", ".pdf"}}
_PDF_ONLY = {".pdf"}


@dataclass
class DiscoveredFile:
    path: Path
    record: InputFile


@dataclass
class DiscoveryResult:
    files: list[DiscoveredFile] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_hidden(path: Path) -> bool:
    return path.name.startswith((".", "~$"))


def discover(root: Path, settings: Settings) -> DiscoveryResult:
    if not root.is_dir():
        raise FileNotFoundError(f"Input folder not found: {root}")

    result = DiscoveryResult()
    folder_map = {name.lower(): doc_type for name, doc_type in settings.ingest.folders.items()}
    seen_types: set[DocType] = set()
    seen_hashes: dict[str, str] = {}

    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if _is_hidden(entry):
            continue
        rel_entry = entry.relative_to(root).as_posix()
        if entry.is_file():
            result.issues.append(
                Issue(
                    code=IssueCode.IGNORED_FILE,
                    message="File is outside the PLC / CableBlocks / Terminations / EquipmentList folders",
                    file_path=rel_entry,
                )
            )
            continue
        doc_type = folder_map.get(entry.name.lower())
        if doc_type is None:
            result.issues.append(
                Issue(
                    code=IssueCode.UNKNOWN_FOLDER,
                    message=f"Folder '{entry.name}' is not a known document type folder; its files are ignored",
                    file_path=rel_entry,
                )
            )
            continue
        seen_types.add(doc_type)
        accepted = _EXTENSIONS.get(doc_type, _PDF_ONLY)
        file_count = 0
        for path in sorted(entry.rglob("*"), key=lambda p: p.as_posix().lower()):
            if not path.is_file() or _is_hidden(path):
                continue
            rel = path.relative_to(root).as_posix()
            if path.suffix.lower() == ".dwg" and accepted is _PDF_ONLY:
                if not any(path.with_suffix(ext).is_file() for ext in (".pdf", ".PDF")):
                    result.issues.append(Issue(code=IssueCode.IGNORED_FILE, file_path=rel,
                                               message="DWG with no PDF of the same name: a DWG is only read for the text of its PDF"))
                continue  # read with its PDF when the PDF has no SHX text (drawing_qa.dwg)
            if path.suffix.lower() not in accepted:
                what = "Not a PDF" if accepted is _PDF_ONLY else f"Not one of {', '.join(sorted(accepted))}"
                result.issues.append(Issue(code=IssueCode.IGNORED_FILE, message=what, file_path=rel))
                continue
            digest = _sha256(path)
            if digest in seen_hashes:
                result.issues.append(
                    Issue(
                        code=IssueCode.DUPLICATE_FILE,
                        message=f"Identical to {seen_hashes[digest]}; skipped",
                        file_path=rel,
                    )
                )
                continue
            seen_hashes[digest] = rel
            file_count += 1
            result.files.append(
                DiscoveredFile(
                    path=path,
                    record=InputFile(
                        path=rel,
                        doc_type=doc_type,
                        sha256=digest,
                        size_bytes=path.stat().st_size,
                    ),
                )
            )
        if file_count == 0:
            what = "No PDFs found" if accepted is _PDF_ONLY else "No equipment list files found"
            result.issues.append(Issue(code=IssueCode.EMPTY_FOLDER, message=what, file_path=rel_entry))

    for doc_type in _REQUIRED_TYPES:
        if doc_type not in seen_types and doc_type not in settings.ingest.unchecked_types:
            result.issues.append(
                Issue(
                    code=IssueCode.MISSING_FOLDER,
                    message=f"No folder for {doc_type.value} drawings; checks needing them will be skipped",
                )
            )
    return result
