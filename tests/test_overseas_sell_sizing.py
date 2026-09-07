"""Tests for app.overseas_execution.sell_sizing (Module B)."""
import pytest


# Test 9: stop_loss -> full holding
def test_stop_loss_full_exit():
    from app.overseas_execution.sell_sizing import calculate_overseas_sell_sizing

    result = calculate_overseas_sell_sizing(holding_qty=10, trigger="stop_loss")
    assert result.recommended_sell_qty == 10
    assert result.fraction_label == "full"


# Test 10: take_profit -> holding//2
def test_take_profit_half_exit():
    from app.overseas_execution.sell_sizing import calculate_overseas_sell_sizing

    result = calculate_overseas_sell_sizing(holding_qty=10, trigger="take_profit")
    assert result.recommended_sell_qty == 5
    assert result.fraction_label == "half"


# Test 11: unknown trigger -> 0, label "none"
def test_unknown_trigger_no_exit():
    from app.overseas_execution.sell_sizing import calculate_overseas_sell_sizing

    result = calculate_overseas_sell_sizing(holding_qty=10, trigger="some_unknown_trigger")
    assert result.recommended_sell_qty == 0
    assert result.fraction_label == "none"


# Test 12: rebalance -> full
def test_rebalance_full_exit():
    from app.overseas_execution.sell_sizing import calculate_overseas_sell_sizing

    result = calculate_overseas_sell_sizing(holding_qty=15, trigger="rebalance")
    assert result.recommended_sell_qty == 15
    assert result.fraction_label == "full"


# Test 13: half-exit on a tiny holding sells at least 1 (mirrors domestic
# max(1, qty // 2)), but a zero holding sells nothing. A 1-share take_profit
# must not silently sell 0.
def test_half_exit_small_holding_floors_to_one_not_zero():
    from app.overseas_execution.sell_sizing import calculate_overseas_sell_sizing

    one = calculate_overseas_sell_sizing(holding_qty=1, trigger="take_profit")
    assert one.recommended_sell_qty == 1
    zero = calculate_overseas_sell_sizing(holding_qty=0, trigger="take_profit")
    assert zero.recommended_sell_qty == 0
