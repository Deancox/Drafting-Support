import pytest

from drawing_qa.common.identifiers import IdentifierGrammar, clean_token
from drawing_qa.config import IdentifierConfig
from drawing_qa.model.enums import EntityType


@pytest.fixture
def grammar() -> IdentifierGrammar:
    return IdentifierGrammar(IdentifierConfig(areas=["LTL", "RCL", "SPL"]))


@pytest.mark.parametrize(
    ("raw", "kind", "key"),
    [
        ("S-TN23N2", EntityType.DEVICE, "S-TN23N2"),
        ("S-TN39N4", EntityType.DEVICE, "S-TN39N4"),
        ("TN23N2", EntityType.WIRE, "TN23N2"),
        ("TN15E2", EntityType.WIRE, "TN15E2"),
        ("SPL-JB1", EntityType.JUNCTION_BOX, "SPL-JB1"),
        ("LTL-JB2", EntityType.JUNCTION_BOX, "LTL-JB2"),
        ("SPL-JB1:1", EntityType.TERMINAL, "SPL-JB1:1"),
        ("LTL-PLC", EntityType.PANEL, "LTL-PLC"),
    ],
)
def test_canonical_identifiers(grammar, raw, kind, key):
    parsed = grammar.parse(raw)
    assert parsed is not None
    assert parsed.kind is kind
    assert parsed.key == key
    assert not parsed.is_variant


def test_device_parts(grammar):
    parsed = grammar.parse("S-TN23N2")
    assert parsed.parts["prefix"] == "S"
    assert parsed.parts["ffi"] == "TN"
    assert parsed.parts["sheet"] == "23"
    assert parsed.parts["grid"] == "N2"


@pytest.mark.parametrize(
    ("raw", "key"),
    [("SPL-JB-1", "SPL-JB1"), ("SPL JB1", "SPL-JB1"), ("spl-jb01", "SPL-JB1"), ("SPL-JB-1:4", "SPL-JB1:4")],
)
def test_variants_resolve_to_canonical_key(grammar, raw, key):
    parsed = grammar.parse(raw)
    assert parsed.key == key
    assert parsed.is_variant


def test_surrounding_punctuation_is_ignored(grammar):
    assert grammar.parse("(S-TN23N2),").key == "S-TN23N2"
    assert clean_token(" tn23n2; ") == "TN23N2"


@pytest.mark.parametrize("raw", ["", "HELLO", "24VDC", "DI16", "CH05", "TN-23N2", "PLC", "2C+E"])
def test_non_identifiers(grammar, raw):
    assert grammar.parse(raw) is None


def test_unknown_area_is_flagged_not_rejected(grammar):
    parsed = grammar.parse("ABC-JB3")
    assert parsed.kind is EntityType.JUNCTION_BOX
    assert parsed.parts["known_area"] is False


def test_iec_designation_prefix(grammar):
    parsed = grammar.parse("=K07.11+LTL-JB1")
    assert parsed.kind is EntityType.JUNCTION_BOX
    assert parsed.key == "LTL-JB1"
    assert parsed.parts["function"] == "K07.11"
    assert not parsed.is_variant


def test_alphanumeric_area_and_numbered_panel():
    g = IdentifierGrammar(IdentifierConfig(areas=["EER1"]))
    assert g.parse("=K07.11+EER1-PLC1").kind is EntityType.PANEL
    assert g.parse("EER1-PLC1").parts["number"] == "1"
    assert g.parse("EER1-UPS1").kind is EntityType.LOCATION


def test_generic_location_needs_known_area():
    assert IdentifierGrammar(IdentifierConfig(areas=["LTL"])).parse("LTL-LCS3").kind is EntityType.LOCATION
    assert IdentifierGrammar(IdentifierConfig()).parse("LTL-LCS3") is None
    assert IdentifierGrammar(IdentifierConfig(areas=["LTL"])).parse("ABC-LCS3") is None


@pytest.mark.parametrize("raw", ["H-TN51F", "S-TN5", "S-TN23N", "S-TN2N2"])
def test_malformed_device_tags_are_kept_and_flagged(grammar, raw):
    parsed = grammar.parse(raw)
    assert parsed.kind is EntityType.DEVICE
    assert parsed.parts["format_valid"] is False


def test_jb_without_number_is_flagged(grammar):
    parsed = grammar.parse("SPL-JB")
    assert parsed.kind is EntityType.JUNCTION_BOX
    assert parsed.parts["format_valid"] is False


def test_valid_device_is_flagged_valid(grammar):
    assert grammar.parse("H-TN51D8").parts["format_valid"] is True


def test_configured_ffi_list_filters_wires():
    g = IdentifierGrammar(IdentifierConfig(ffis=["TN"]))
    assert g.parse("TN23N2").kind is EntityType.WIRE
    assert g.parse("AB23N2") is None
    # Devices with an unknown FFI are still parsed, and flagged for TAG-002.
    assert g.parse("S-AB23N2").parts["known_ffi"] is False
