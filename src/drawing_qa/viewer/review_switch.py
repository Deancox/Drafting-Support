"""Switches [review] enabled on in a project config file, changing only that one line.

The register only has the decision columns, and a run only reads decisions back, while review is on. The
viewer offers this switch (after the checker confirms); everything else in the file, comments included, stays.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

from drawing_qa.config import Settings

_TABLE = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]")
_ENABLED = re.compile(r"^(\s*)enabled\s*=\s*[^#\n]*?(\s*#.*)?$")


def _switched(text: str) -> str:
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if (m := _TABLE.match(line)) and m[1] == "review"), None)
    if start is None:
        return text + ("" if text.endswith("\n") or not text else "\n") + "\n[review]\nenabled = true\n"
    end = next((i for i in range(start + 1, len(lines)) if _TABLE.match(lines[i])), len(lines))
    for i in range(start + 1, end):
        m = _ENABLED.match(lines[i].rstrip("\r\n"))
        if m:
            newline = lines[i][len(lines[i].rstrip("\r\n")):]
            lines[i] = f"{m[1]}enabled = true{m[2] or ''}{newline}"
            return "".join(lines)
    newline = lines[start][len(lines[start].rstrip("\r\n")):] or "\n"
    lines.insert(start + 1, f"enabled = true{newline}")
    return "".join(lines)


def enable_review(config_path: Path) -> None:
    """Set [review] enabled = true. Raises ValueError, leaving the file as it was, if the result would not load."""
    config_path = Path(config_path)
    raw = config_path.read_bytes()
    text = raw.decode("utf-8")
    try:
        if tomllib.loads(text).get("review", {}).get("enabled") is True:
            return
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{config_path.name} can't be read ({exc}), so review was not switched on") from exc
    temp = config_path.with_name(f"{config_path.stem}.switching.toml")
    temp.write_bytes(_switched(text).encode("utf-8"))
    try:
        if Settings.load(temp).review.enabled is not True:
            raise ValueError(f"couldn't switch review on in {config_path.name}; set [review] enabled = true by hand")
    except ValueError:
        temp.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temp.unlink(missing_ok=True)
        raise ValueError(f"switching review on would break {config_path.name}: {exc}") from exc
    os.replace(temp, config_path)
