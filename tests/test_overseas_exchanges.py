import pytest

from app.overseas_stock.exchanges import (
    parse_universe_entry,
    resolve_us_instrument,
)


def test_default_exchange_is_nasd_with_excd_nas():
    inst = resolve_us_instrument("AAPL")
    assert inst.symbol == "AAPL"
    assert inst.ovrs_excg_cd == "NASD"
    assert inst.excd == "NAS"
    assert inst.currency == "USD"


def test_nyse_maps_to_nys():
    inst = resolve_us_instrument("IBM", "NYSE")
    assert inst.ovrs_excg_cd == "NYSE"
    assert inst.excd == "NYS"


def test_amex_maps_to_ams():
    inst = resolve_us_instrument("GE", "AMEX")
    assert inst.ovrs_excg_cd == "AMEX"
    assert inst.excd == "AMS"


def test_quote_alias_nas_maps_to_nasd():
    inst = resolve_us_instrument("AAPL", "NAS")
    assert inst.ovrs_excg_cd == "NASD"
    assert inst.excd == "NAS"


def test_lowercase_nasd_normalizes_to_nasd():
    inst = resolve_us_instrument("AAPL", "nasd")
    assert inst.ovrs_excg_cd == "NASD"
    assert inst.excd == "NAS"


def test_unknown_exchange_raises_value_error():
    with pytest.raises(ValueError):
        resolve_us_instrument("AAPL", "XXXX")


def test_empty_symbol_raises_value_error():
    with pytest.raises(ValueError):
        resolve_us_instrument("   ")


def test_parse_universe_entry_with_at_separator():
    inst = parse_universe_entry("AAPL@NYSE")
    assert inst.symbol == "AAPL"
    assert inst.ovrs_excg_cd == "NYSE"
    assert inst.excd == "NYS"


def test_parse_universe_entry_bare_symbol_defaults_nasd():
    inst = parse_universe_entry("aapl")
    assert inst.symbol == "AAPL"
    assert inst.ovrs_excg_cd == "NASD"
    assert inst.excd == "NAS"
