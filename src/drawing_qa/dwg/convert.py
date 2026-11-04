"""DWG -> DXF with the ODA File Converter (free, opendesign.com), run once for all the DWGs a run needs."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

_EXE = "ODAFileConverter.exe" if os.name == "nt" else "ODAFileConverter"


def find_converter(configured: str = "") -> Path | None:
    """The converter: the configured path, the ODA_FILE_CONVERTER variable, on PATH, or a usual install folder."""
    candidates = [configured, os.environ.get("ODA_FILE_CONVERTER", ""), shutil.which(_EXE) or ""]
    for root in (Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "ODA", Path.home() / "Tools"):
        if root.is_dir():
            candidates += [str(p) for p in sorted(root.glob(f"*/{_EXE}"), reverse=True)]  # newest version first
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


def convert(dwgs: list[Path], converter: Path, out_dir: Path, timeout: float = 600) -> dict[Path, Path]:
    """DWG -> DXF for each file; returns {dwg: dxf} for those converted. The DWGs are copied first, so the
    originals are only ever read."""
    if not dwgs:
        return {}
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dwg_in_") as tmp:
        names: dict[str, Path] = {}
        for i, dwg in enumerate(dwgs):
            name = f"{i:04d}_{dwg.stem}.dwg"  # two folders may hold the same file name
            shutil.copy2(dwg, Path(tmp) / name)
            names[Path(name).stem.lower()] = dwg
        # input folder, output folder, output version, output type, recurse, audit, filter
        cmd = [str(converter), tmp, str(out_dir), "ACAD2018", "DXF", "0", "1", "*.DWG"]
        try:
            subprocess.run(cmd, check=False, timeout=timeout, capture_output=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("ODA File Converter failed: %s", exc)
            return {}
    found = {p.stem.lower(): p for p in out_dir.glob("*.dxf")}
    return {dwg: found[stem] for stem, dwg in names.items() if stem in found}
