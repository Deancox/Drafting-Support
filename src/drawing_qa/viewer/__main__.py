"""python -m drawing_qa.viewer [--output FOLDER] [--config FILE]: open the Case Review window."""

from __future__ import annotations

import argparse
from pathlib import Path

from drawing_qa.gui import DEFAULT_CONFIG


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m drawing_qa.viewer", description="Go through a run's review cases.")
    parser.add_argument("--output", type=Path, help="the output folder with QA_Register.xlsx and findings.json")
    parser.add_argument("--config", type=Path, help="the project config (default: config/project.toml)")
    args = parser.parse_args(argv)
    config = args.config or (DEFAULT_CONFIG if DEFAULT_CONFIG.is_file() else None)

    from drawing_qa.viewer.window import main as run_window

    run_window(args.output, config)


if __name__ == "__main__":
    main()
