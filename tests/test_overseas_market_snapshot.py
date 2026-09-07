"""TDD tests for app/overseas_stock/market_snapshot.py."""

import pytest


@pytest.mark.parametrize("field,value", [
    ("last", "0"),
    ("open", "0"),
    ("low", "0"),
    ("base", "0"),
])
def test_build_rejects_nonpositive_required_fields(field, value):
    from app.overseas_stock.market_snapshot import build_overseas_market_snapshot, OverseasSnapshotError

    base = {
        "symb": "AAPL",
        "last": "145.50",
        "open": "148.00",
        "high": "149.0",
        "low": "144.0",
        "base": "147.00",
        "rate": "-1.02",
        "curr": "USD",
    }
    base[field] = value
    with pytest.raises(OverseasSnapshotError):
        build_overseas_market_snapshot(base)


def test_build_handles_commas():
    from app.overseas_stock.market_snapshot import build_overseas_market_snapshot

    output = {
        "symb": "TSLA",
        "last": "1,234.50",
        "open": "1,200.00",
        "high": "1,250.00",
        "low": "1,180.00",
        "base": "1,210.00",
        "rate": "2.01",
        "curr": "USD",
    }
    snapshot = build_overseas_market_snapshot(output)
    assert snapshot.current_price == 1234.5


def test_build_parses_price_detail_ohlc():
    from app.overseas_stock.market_snapshot import build_overseas_market_snapshot

    output = {
        "symb": "AAPL",
        "last": "145.50",
        "open": "148.00",
        "high": "149.0",
        "low": "144.0",
        "base": "147.00",
        "rate": "-1.02",
        "curr": "USD",
    }
    snapshot = build_overseas_market_snapshot(output)
    assert snapshot.current_price == 145.5
    assert snapshot.open_price == 148.0
    assert snapshot.low_price == 144.0
    assert snapshot.prev_close == 147.0
    assert snapshot.prev_day_change_pct == -1.02
    assert snapshot.currency == "USD"
    assert snapshot.symbol == "AAPL"
