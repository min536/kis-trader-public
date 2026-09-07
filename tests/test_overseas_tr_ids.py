import pytest

from app.overseas_stock.tr_ids import (
    resolve_balance_tr_id,
    resolve_order_tr_id,
    resolve_psamount_tr_id,
    resolve_quote_tr_id,
)


def test_quote_tr_id_same_for_mock_and_live():
    assert resolve_quote_tr_id("mock") == "HHDFS00000300"
    assert resolve_quote_tr_id("live") == "HHDFS00000300"


def test_balance_tr_id_mock_vs_live():
    assert resolve_balance_tr_id("mock") == "VTTS3012R"
    assert resolve_balance_tr_id("live") == "TTTS3012R"


def test_psamount_tr_id_mock_vs_live():
    assert resolve_psamount_tr_id("mock") == "VTTS3007R"
    assert resolve_psamount_tr_id("live") == "TTTS3007R"


def test_order_tr_id_mock_buy():
    assert resolve_order_tr_id("mock", "buy", "NASD") == "VTTT1002U"


def test_order_tr_id_mock_sell():
    assert resolve_order_tr_id("mock", "sell", "NASD") == "VTTT1006U"


def test_order_tr_id_live_buy_raises():
    with pytest.raises(NotImplementedError):
        resolve_order_tr_id("live", "buy", "NASD")


def test_order_tr_id_live_sell_raises():
    with pytest.raises(NotImplementedError):
        resolve_order_tr_id("live", "sell", "NASD")


def test_order_tr_id_non_us_exchange_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        resolve_order_tr_id("live", "buy", "SEHK")


def test_order_tr_id_bad_side_raises_value_error():
    with pytest.raises(ValueError):
        resolve_order_tr_id("live", "hold", "NASD")


def test_bad_env_raises_value_error():
    with pytest.raises(ValueError):
        resolve_order_tr_id("paper", "buy", "NASD")
