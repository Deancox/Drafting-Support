import pytest

from drawing_qa.common.placeholders import PlaceholderDetector
from drawing_qa.config import PlaceholderConfig


@pytest.fixture
def detector() -> PlaceholderDetector:
    return PlaceholderDetector(PlaceholderConfig(hold_exceptions=["HOLD OPEN"]))


@pytest.mark.parametrize("token", ["XXXX", "XXX", "TNXXXX", "PXXXXX", "CXXXXX", "HOLD", "AUT1", "xxxx,"])
def test_listed_tokens(detector, token):
    hit = detector.check_token(token)
    assert hit is not None
    assert hit.pattern == "TOKEN"


@pytest.mark.parametrize("token", ["S-TNXXXX", "XXXXXX", "TNXXXXX"])
def test_x_runs(detector, token):
    assert detector.check_token(token).pattern == "X_RUN"


@pytest.mark.parametrize("token", ["SPL-JBX", "SPL-JB1:XX"])
def test_identifier_placeholders(detector, token):
    assert detector.check_token(token).pattern == "IDENTIFIER_X"


@pytest.mark.parametrize("token", ["BOX", "HOLDING", "AUT", "XX", "S-TN23N2", "SPL-JB1:1", "TEXT"])
def test_not_placeholders(detector, token):
    assert detector.check_token(token) is None


def test_hold_exception(detector):
    assert detector.check_token("HOLD", "VALVE HOLD OPEN") is None
    assert detector.check_token("HOLD", "TERMINAL HOLD") is not None


def test_phrase_on_line(detector):
    hits = detector.check_line("SPL-JB1:2   REF  TERM XXX")
    assert [h.text for h in hits] == ["REF TERM XXX"]
    assert detector.check_line("REF TERM 12") == []
