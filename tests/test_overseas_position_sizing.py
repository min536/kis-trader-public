"""Tests for app.overseas_execution.position_sizing (Module A)."""
import types

import pytest


def make_orderable(*, max_order_qty=None, orderable_cash=None):
    return types.SimpleNamespace(max_order_qty=max_order_qty, orderable_cash=orderable_cash)


# Test 1: cash/orderable cap binds
def test_cash_cap_binds():
    from app.overseas_execution.position_sizing import calculate_overseas_position_sizing

    orderable = make_orderable(orderable_cash=500.0, max_order_qty=None)
    result = calculate_overseas_position_sizing(
        unit_price=100.0,
        orderable=orderable,
        max_budget_per_trade_usd=10000.0,
        max_account_exposure_pct=50.0,
        max_qty_per_trade=1000,
        account_equity_usd=None,
    )
    assert result.recommended_qty == 5
    assert result.binding_cap == "cash"


# Test 2: budget cap binds
def test_budget_cap_binds():
    from app.overseas_execution.position_sizing import calculate_overseas_position_sizing

    orderable = make_orderable(orderable_cash=10000.0, max_order_qty=None)
    result = calculate_overseas_position_sizing(
        unit_price=100.0,
        orderable=orderable,
        max_budget_per_trade_usd=300.0,
        max_account_exposure_pct=50.0,
        max_qty_per_trade=1000,
        account_equity_usd=None,
    )
    assert result.recommended_qty == 3
    assert result.binding_cap == "budget"


# Test 3: max_qty cap binds
def test_max_qty_cap_binds():
    from app.overseas_execution.position_sizing import calculate_overseas_position_sizing

    orderable = make_orderable(orderable_cash=10000.0, max_order_qty=None)
    result = calculate_overseas_position_sizing(
        unit_price=10.0,
        orderable=orderable,
        max_budget_per_trade_usd=5000.0,
        max_account_exposure_pct=50.0,
        max_qty_per_trade=7,
        account_equity_usd=None,
    )
    assert result.recommended_qty == 7
    assert result.binding_cap == "max_qty"


# Test 4: exposure cap binds when account_equity_usd given
def test_exposure_cap_binds():
    from app.overseas_execution.position_sizing import calculate_overseas_position_sizing

    # account_equity=1000, 20% exposure cap => max 200 USD => at 100/share => 2 shares
    orderable = make_orderable(orderable_cash=50000.0, max_order_qty=None)
    result = calculate_overseas_position_sizing(
        unit_price=100.0,
        orderable=orderable,
        max_budget_per_trade_usd=10000.0,
        max_account_exposure_pct=20.0,
        max_qty_per_trade=1000,
        account_equity_usd=1000.0,
    )
    assert result.recommended_qty == 2
    assert result.binding_cap == "exposure"
    assert result.exposure_cap_qty == 2


# Test 5: broker max_order_qty caps below cash-derived qty
def test_broker_max_order_qty_caps():
    from app.overseas_execution.position_sizing import calculate_overseas_position_sizing

    # cash would give 50 shares, but broker allows only 10
    orderable = make_orderable(orderable_cash=5000.0, max_order_qty=10)
    result = calculate_overseas_position_sizing(
        unit_price=100.0,
        orderable=orderable,
        max_budget_per_trade_usd=50000.0,
        max_account_exposure_pct=100.0,
        max_qty_per_trade=1000,
        account_equity_usd=None,
    )
    assert result.cash_cap_qty == 10
    assert result.recommended_qty == 10
    assert result.binding_cap == "cash"


# Test 6: whole-share floor — fractional notional floors to int
def test_whole_share_floor():
    from app.overseas_execution.position_sizing import calculate_overseas_position_sizing

    # 250 USD / 99.99 per share = 2.5002... => floor to 2
    orderable = make_orderable(orderable_cash=250.0, max_order_qty=None)
    result = calculate_overseas_position_sizing(
        unit_price=99.99,
        orderable=orderable,
        max_budget_per_trade_usd=10000.0,
        max_account_exposure_pct=100.0,
        max_qty_per_trade=1000,
        account_equity_usd=None,
    )
    assert result.recommended_qty == 2
    assert result.notional_usd == pytest.approx(2 * 99.99)


# Test 7: zero cash => recommended_qty == 0, binding_cap == "cash"
def test_zero_cash():
    from app.overseas_execution.position_sizing import calculate_overseas_position_sizing

    orderable = make_orderable(orderable_cash=0.0, max_order_qty=None)
    result = calculate_overseas_position_sizing(
        unit_price=100.0,
        orderable=orderable,
        max_budget_per_trade_usd=10000.0,
        max_account_exposure_pct=100.0,
        max_qty_per_trade=1000,
        account_equity_usd=None,
    )
    assert result.recommended_qty == 0
    assert result.binding_cap == "cash"


# Test 8: non-positive unit_price raises ValueError
def test_nonpositive_unit_price_raises():
    from app.overseas_execution.position_sizing import calculate_overseas_position_sizing

    orderable = make_orderable(orderable_cash=1000.0, max_order_qty=None)
    with pytest.raises(ValueError):
        calculate_overseas_position_sizing(
            unit_price=0.0,
            orderable=orderable,
            max_budget_per_trade_usd=10000.0,
            max_account_exposure_pct=50.0,
            max_qty_per_trade=100,
            account_equity_usd=None,
        )
    with pytest.raises(ValueError):
        calculate_overseas_position_sizing(
            unit_price=-5.0,
            orderable=orderable,
            max_budget_per_trade_usd=10000.0,
            max_account_exposure_pct=50.0,
            max_qty_per_trade=100,
            account_equity_usd=None,
        )
