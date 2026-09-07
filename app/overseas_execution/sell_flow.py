"""Overseas sell flow — stub."""
from dataclasses import dataclass


@dataclass(frozen=True)
class OverseasSellFlowResult:
    action: str
    reason: str
    symbol: str
    exchange: str
    qty: int
    unit_price: float
    trigger: str | None
    sizing: object | None
    risk: object | None
    order_record: dict | None


def run_overseas_sell_flow(*, symbol, holding_qty, trigger, quote_price, policy, state, risk_log_path, exchange="NASD", confirm_sell=False, now=None, market_open=None, submit_order=None, settings=None, token=None, notify=None) -> OverseasSellFlowResult:  # noqa: E501
    from app.overseas_stock.session import get_us_eastern_now, is_us_regular_session
    from app.overseas_execution.limit_price import compute_overseas_limit_price
    now = now if now is not None else get_us_eastern_now()
    market_open = market_open if market_open is not None else is_us_regular_session(now)
    unit_price = compute_overseas_limit_price(side="sell", quote_price=float(quote_price), offset_bps=getattr(policy, "limit_offset_bps", 0.0))
    if not market_open:
        return OverseasSellFlowResult(action="blocked_market_closed", reason="US regular session is not open.", symbol=symbol, exchange=exchange, qty=0, unit_price=unit_price, trigger=trigger, sizing=None, risk=None, order_record=None)
    if int(holding_qty) <= 0:
        return OverseasSellFlowResult(action="blocked_no_holding", reason="No shares held to sell.", symbol=symbol, exchange=exchange, qty=0, unit_price=unit_price, trigger=trigger, sizing=None, risk=None, order_record=None)
    from app.overseas_execution.sell_sizing import calculate_overseas_sell_sizing
    sizing = calculate_overseas_sell_sizing(holding_qty=holding_qty, trigger=trigger)
    qty = sizing.recommended_sell_qty
    if qty <= 0:
        return OverseasSellFlowResult(action="blocked_zero_qty", reason="Sell sizing returned zero quantity.", symbol=symbol, exchange=exchange, qty=0, unit_price=unit_price, trigger=trigger, sizing=sizing, risk=None, order_record=None)
    from app.overseas_execution.order_guard import evaluate_overseas_sell_guard
    guard = evaluate_overseas_sell_guard(
        state=state, symbol=symbol, holding_qty=holding_qty, qty=qty,
        block_resell_symbols_sold_today=getattr(policy, "block_resell_symbols_sold_today", False),
        allow_one_sell_trigger_per_symbol_per_day=getattr(policy, "allow_one_sell_trigger_per_symbol_per_day", False),
        order_cooldown_minutes=getattr(policy, "order_cooldown_minutes", 0),
        blocked_cooldown_minutes=getattr(policy, "blocked_cooldown_minutes", 0),
        trigger=trigger, now=now,
    )
    if not guard.allowed:
        return OverseasSellFlowResult(action="blocked_order_guard", reason=guard.reason, symbol=symbol, exchange=exchange, qty=qty, unit_price=unit_price, trigger=trigger, sizing=sizing, risk=None, order_record=None)
    from app.overseas_risk.guards import evaluate_overseas_risk_guards
    risk = evaluate_overseas_risk_guards(log_path=risk_log_path, planned_notional_usd=qty * unit_price, max_orders=getattr(policy, "max_orders", 0), max_notional_usd=getattr(policy, "max_notional_usd", 0.0), enabled=getattr(policy, "risk_enabled", True), now=now)
    if not risk.allowed:
        return OverseasSellFlowResult(action="blocked_risk_guard", reason=risk.reason, symbol=symbol, exchange=exchange, qty=qty, unit_price=unit_price, trigger=trigger, sizing=sizing, risk=risk, order_record=None)
    if not confirm_sell:
        return OverseasSellFlowResult(action="preview", reason="CONFIRM_SELL off — dry-run preview", symbol=symbol, exchange=exchange, qty=qty, unit_price=unit_price, trigger=trigger, sizing=sizing, risk=risk, order_record=None)
    from app.overseas_stock.order import place_overseas_limit_order
    _submit = submit_order or place_overseas_limit_order
    record = _submit(symbol=symbol, exchange=exchange, qty=qty, unit_price=unit_price, side="sell", settings=settings, token=token)
    if notify:
        notify(side="sell", symbol=symbol, qty=qty, unit_price=unit_price, status="submitted")
    return OverseasSellFlowResult(action="submitted", reason="Order submitted.", symbol=symbol, exchange=exchange, qty=qty, unit_price=unit_price, trigger=trigger, sizing=sizing, risk=risk, order_record=record)
