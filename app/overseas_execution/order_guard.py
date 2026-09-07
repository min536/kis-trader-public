"""Overseas order guard — thin ET-clock wrappers around the domestic guard."""
from app.execution.order_guard import (
    OrderGuardResult,
    evaluate_buy_order_guard,
    evaluate_sell_order_guard,
)
from app.overseas_stock.session import get_us_eastern_now


def evaluate_overseas_sell_guard(*, now=None, **kwargs) -> OrderGuardResult:
    """evaluate_sell_order_guard with the US/Eastern clock injected."""
    return evaluate_sell_order_guard(
        now=now if now is not None else get_us_eastern_now(),
        **kwargs,
    )


def evaluate_overseas_buy_guard(*, now=None, **kwargs) -> OrderGuardResult:
    """evaluate_buy_order_guard with the US/Eastern clock injected."""
    return evaluate_buy_order_guard(
        now=now if now is not None else get_us_eastern_now(),
        **kwargs,
    )
