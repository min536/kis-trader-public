import types
from unittest import mock

from app.overseas_stock.tr_ids import resolve_price_detail_tr_id
from app.overseas_stock import market_data


def _settings():
    return types.SimpleNamespace(
        base_url="https://openapivts.koreainvestment.com:29443",
        cano="12345678",
        acnt_prdt_cd="01",
    )


def test_resolve_price_detail_tr_id():
    assert resolve_price_detail_tr_id("mock") == "HHDFS76200200"
    assert resolve_price_detail_tr_id("live") == resolve_price_detail_tr_id("mock")


def test_fetch_overseas_price_detail_builds_request():
    s = _settings()
    with mock.patch.object(
        market_data, "issue_access_token", return_value="TOK"
    ), mock.patch.object(
        market_data, "build_auth_headers", return_value={}
    ), mock.patch.object(
        market_data,
        "request_json",
        return_value={"output": {"last": "150.0", "open": "151.0"}},
    ) as req:
        result = market_data.fetch_overseas_price_detail(
            "AAPL", exchange="NASD", settings=s, token="TOK"
        )
    assert result == {"last": "150.0", "open": "151.0"}
    url = req.call_args.args[1]
    assert url.endswith("/quotations/price-detail")
