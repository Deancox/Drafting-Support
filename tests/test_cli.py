"""Command-line options."""

from __future__ import annotations

from drawing_qa import cli
from drawing_qa.model.findings import Finding
from drawing_qa.model.source import Drawing


def test_ocr_flag_parsed():
    assert cli._build_parser().parse_args(["run", "--input", "x", "--ocr"]).ocr is True
    assert cli._build_parser().parse_args(["run", "--input", "x"]).ocr is False


def test_ocr_flag_enables_decoding(tmp_path, monkeypatch):
    seen = {}

    def fake_run(input_root, settings):
        seen["enabled"] = settings.ocr.enabled
        raise SystemExit(0)

    monkeypatch.setattr(cli, "run_pipeline", fake_run)
    for flag, expected in ((["--ocr"], True), ([], False)):
        try:
            cli.main(["run", "--input", str(tmp_path), *flag])
        except SystemExit:
            pass
        assert seen["enabled"] is expected


def test_decode_fields_default_off():
    assert Drawing.model_fields["decoded"].default is None
    assert Finding.model_fields["decoded"].default is False
