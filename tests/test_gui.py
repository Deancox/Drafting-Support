"""The Drawing QA window builds the same command as the command line."""

from __future__ import annotations

import sys

import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture
def app(tmp_path, monkeypatch):
    from drawing_qa import gui

    monkeypatch.setattr(gui, "PREFS", tmp_path / "prefs.json")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    a = gui.App(root)
    yield a
    root.destroy()


def test_run_command_has_the_folders_options_and_types(app, tmp_path):
    (tmp_path / "Pkg" / "PLC").mkdir(parents=True)
    app.input.set(str(tmp_path / "Pkg"))
    assert app.output.get().endswith("Pkg")  # the output follows the input until one is chosen
    app.include["term"].set(False)
    app.ocr.set(True)
    cmd = app.command()
    assert cmd[:4] == [sys.executable, "-m", "drawing_qa", "run"]
    assert cmd[cmd.index("--input") + 1] == str(tmp_path / "Pkg")
    assert cmd[cmd.index("--output") + 1].endswith("Pkg")
    pairs = {cmd[i + 1]: cmd[i] for i, c in enumerate(cmd) if c in ("--include", "--exclude")}
    assert pairs == {"plc": "--include", "cbd": "--include", "term": "--exclude", "equip": "--include"}
    assert "--ocr" in cmd


def test_survey_command_and_a_chosen_output_stays(app, tmp_path):
    app.output_auto = False
    app.output.set(str(tmp_path / "Out"))
    app.input.set(str(tmp_path / "Other"))
    assert app.output.get() == str(tmp_path / "Out")
    cmd = app.command(survey=True)
    assert "survey" in cmd and "--output" not in cmd and "--ocr" not in cmd
