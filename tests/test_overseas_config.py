import os
import types
from unittest import mock

from app.overseas_stock.config import load_overseas_config


MOCK_URL = "https://openapivts.koreainvestment.com"
LIVE_URL = "https://openapi.koreainvestment.com"


def _settings(base_url=MOCK_URL, cano="12345678", acnt_prdt_cd="01"):
    return types.SimpleNamespace(base_url=base_url, cano=cano, acnt_prdt_cd=acnt_prdt_cd)


def test_scan_symbols_split_and_source():
    s = _settings()
    with mock.patch.dict(
        os.environ, {"OVERSEAS_SCAN_SYMBOLS": "AAPL, NVDA TSLA"}, clear=False
    ):
        cfg = load_overseas_config(s)
    assert cfg.scan_symbols == ("AAPL", "NVDA", "TSLA")
    assert cfg.scan_symbols_source == "OVERSEAS_SCAN_SYMBOLS"


def test_no_scan_symbols_empty_tuple_and_blank_source():
    s = _settings()
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OVERSEAS_SCAN_SYMBOLS", None)
        cfg = load_overseas_config(s)
    assert cfg.scan_symbols == ()
    assert cfg.scan_symbols_source == ""


def test_acnt_prdt_cd_falls_back_to_domestic():
    s = _settings(base_url=MOCK_URL, acnt_prdt_cd="01")
    with mock.patch.dict(os.environ, {}, clear=False):
        for key in (
            "KIS_OVRS_ACNT_PRDT_CD",
            "KIS_OVRS_ACNT_PRDT_CD_MOCK",
            "KIS_OVRS_ACNT_PRDT_CD_LIVE",
        ):
            os.environ.pop(key, None)
        cfg = load_overseas_config(s)
    assert cfg.acnt_prdt_cd == "01"


def test_acnt_prdt_cd_scoped_mock_override():
    s = _settings(base_url=MOCK_URL, acnt_prdt_cd="01")
    with mock.patch.dict(
        os.environ, {"KIS_OVRS_ACNT_PRDT_CD_MOCK": "29"}, clear=False
    ):
        os.environ.pop("KIS_OVRS_ACNT_PRDT_CD", None)
        cfg = load_overseas_config(s)
    assert cfg.acnt_prdt_cd == "29"


def test_default_exchange_and_currency_defaults():
    s = _settings(base_url=MOCK_URL, cano="12345678")
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("KIS_OVERSEAS_EXCHANGE", None)
        os.environ.pop("KIS_OVERSEAS_CURRENCY", None)
        cfg = load_overseas_config(s)
    assert cfg.default_exchange == "NASD"
    assert cfg.currency == "USD"
    assert cfg.cano == "12345678"
    assert cfg.base_url == MOCK_URL


def test_settings_none_uses_get_settings():
    s = _settings(base_url=MOCK_URL, cano="12345678", acnt_prdt_cd="01")
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OVERSEAS_SCAN_SYMBOLS", None)
        with mock.patch(
            "app.overseas_stock.config.get_settings", return_value=s
        ) as get_s:
            cfg = load_overseas_config()
    get_s.assert_called_once()
    assert cfg.cano == "12345678"
    assert cfg.base_url == MOCK_URL
