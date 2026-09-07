import types
from unittest import mock

import pytest

from app.overseas_stock import market_data


def _settings():
    return types.SimpleNamespace(
        base_url="https://openapivts.koreainvestment.com:29443",
        cano="12345678",
        acnt_prdt_cd="01",
    )


def test_format_overseas_price_strips_trailing_zeros():
    assert market_data.format_overseas_price(145) == "145"
    assert market_data.format_overseas_price(1.40) == "1.4"
    assert market_data.format_overseas_price(145.5) == "145.5"


def test_format_overseas_price_rejects_non_positive():
    with pytest.raises(ValueError):
        market_data.format_overseas_price(0)


@pytest.mark.parametrize(
    "bad",
    [float("inf"), float("nan"), "inf", "Infinity", "nan", "abc", "", "  ", None],
)
def test_format_overseas_price_rejects_non_finite_and_non_numeric(bad):
    with pytest.raises(ValueError):
        market_data.format_overseas_price(bad)


def test_fetch_overseas_quote_parses_output():
    s = _settings()
    with mock.patch.object(
        market_data, "issue_access_token", return_value="TOK"
    ), mock.patch.object(
        market_data, "build_auth_headers", return_value={}
    ), mock.patch.object(
        market_data,
        "request_json",
        return_value={"output": {"last": "145.50", "rate": "1.2", "curr": "USD"}},
    ) as req:
        quote = market_data.fetch_overseas_quote("AAPL", settings=s)
    assert quote.last_price == 145.5
    assert quote.exchange_code == "NAS"
    assert quote.source_tr_id == "HHDFS00000300"
    assert quote.currency == "USD"
    params = req.call_args.kwargs["params"]
    assert params["EXCD"] == "NAS"
    assert params["SYMB"] == "AAPL"


def test_fetch_overseas_holdings_parses_one_row():
    s = _settings()
    row = {
        "ovrs_pdno": "AAPL",
        "ovrs_excg_cd": "NASD",
        "ovrs_cblc_qty": "5",
        "pchs_avg_pric": "140.00",
        "ovrs_now_pric": "145.50",
        "ovrs_stck_evlu_amt": "727.50",
        "frcr_evlu_pfls_amt": "27.50",
        "tr_crcy_cd": "USD",
    }
    with mock.patch.object(
        market_data, "issue_access_token", return_value="TOK"
    ), mock.patch.object(
        market_data, "build_auth_headers", return_value={}
    ), mock.patch.object(
        market_data, "request_json", return_value={"output1": [row]}
    ):
        holdings = market_data.fetch_overseas_holdings(settings=s)
    assert len(holdings) == 1
    assert holdings[0].symbol == "AAPL"
    assert holdings[0].quantity == 5.0


def test_fetch_overseas_holdings_empty_output_returns_empty_list():
    s = _settings()
    with mock.patch.object(
        market_data, "issue_access_token", return_value="TOK"
    ), mock.patch.object(
        market_data, "build_auth_headers", return_value={}
    ), mock.patch.object(
        market_data, "request_json", return_value={"output1": []}
    ):
        holdings = market_data.fetch_overseas_holdings(settings=s)
    assert holdings == []


def test_fetch_overseas_orderable_parses_output():
    s = _settings()
    out = {
        "max_ord_psbl_qty": "10",
        "ovrs_ord_psbl_amt": "1455.00",
        "exrt": "1360.5",
        "tr_crcy_cd": "USD",
    }
    with mock.patch.object(
        market_data, "issue_access_token", return_value="TOK"
    ), mock.patch.object(
        market_data, "build_auth_headers", return_value={}
    ), mock.patch.object(
        market_data, "request_json", return_value={"output": out}
    ) as req:
        orderable = market_data.fetch_overseas_orderable("AAPL", 145.5, settings=s)
    assert orderable.max_order_qty == 10
    assert orderable.orderable_cash == 1455.0
    assert orderable.fx_rate == 1360.5
    assert orderable.currency == "USD"
    params = req.call_args.kwargs["params"]
    assert params["OVRS_ORD_UNPR"] == "145.5"


def test_overseas_data_error_is_runtime_error():
    assert issubclass(market_data.OverseasDataError, RuntimeError)
