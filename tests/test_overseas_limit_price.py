"""Tests for app.overseas_execution.limit_price (Module C)."""
import pytest


# Test 13: buy with offset 0 -> price == quote (rounded)
def test_buy_zero_offset():
    from app.overseas_execution.limit_price import compute_overseas_limit_price

    price = compute_overseas_limit_price(side="buy", quote_price=100.00, offset_bps=0.0)
    assert price == pytest.approx(100.00)


# Test 14: buy with offset_bps > 0 crosses UP (price > quote)
def test_buy_with_offset_crosses_up():
    from app.overseas_execution.limit_price import compute_overseas_limit_price

    price = compute_overseas_limit_price(side="buy", quote_price=100.00, offset_bps=10.0)
    # 100 * (1 + 10/10000) = 100.10
    assert price == pytest.approx(100.10)
    assert price > 100.00


# Test 15: sell with offset_bps > 0 crosses DOWN (price < quote)
def test_sell_with_offset_crosses_down():
    from app.overseas_execution.limit_price import compute_overseas_limit_price

    price = compute_overseas_limit_price(side="sell", quote_price=100.00, offset_bps=10.0)
    # 100 * (1 - 10/10000) = 99.90
    assert price == pytest.approx(99.90)
    assert price < 100.00


# Test 16: non-positive quote raises ValueError; bad side raises ValueError
def test_invalid_inputs_raise():
    from app.overseas_execution.limit_price import compute_overseas_limit_price

    with pytest.raises(ValueError):
        compute_overseas_limit_price(side="buy", quote_price=0.0)
    with pytest.raises(ValueError):
        compute_overseas_limit_price(side="buy", quote_price=-10.0)
    with pytest.raises(ValueError):
        compute_overseas_limit_price(side="short", quote_price=100.0)
