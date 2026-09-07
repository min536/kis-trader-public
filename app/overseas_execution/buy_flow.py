"""Overseas buy flow — stub."""
from dataclasses import dataclass


@dataclass(frozen=True)
class OverseasBuyFlowResult:
    action: str
    reason: str
    symbol: str
    exchange: str
    qty: int
    unit_price: float
    candidate: object
    sizing: object | None
    risk: object | None
    order_record: dict | None


def run_overseas_buy_flow(
    *,
    candidate,
    policy,
    state,
    risk_log_path,
    fetch_orderable,
    exchange="NASD",
    confirm_buy=False,
    now=None,
    market_open=None,
    submit_order=None,
    settings=None,
    token=None,
    notify=None,
) -> OverseasBuyFlowResult:
    from app.overseas_stock.session import get_us_eastern_now, is_us_regular_session
    from app.overseas_execution.limit_price import compute_overseas_limit_price

    now = now if now is not None else get_us_eastern_now()
    market_open = market_open if market_open is not None else is_us_regular_session(now)

    symbol = candidate.symbol
    quote_price = candidate.snapshot.current_price
    unit_price = compute_overseas_limit_price(
        side="buy", quote_price=quote_price, offset_bps=getattr(policy, "limit_offset_bps", 0.0)
    )

    if not market_open:
        return OverseasBuyFlowResult(
            action="blocked_market_closed",
            reason="US regular session is not open.",
            symbol=symbol,
            exchange=exchange,
            qty=0,
            unit_price=unit_price,
            candidate=candidate,
            sizing=None,
            risk=None,
            order_record=None,
        )

    from app.overseas_execution.position_sizing import calculate_overseas_position_sizing

    # 4. Fetch orderable capacity.
    orderable = fetch_orderable(symbol, unit_price, exchange)

    # 5. Size the position.
    sizing = calculate_overseas_position_sizing(
        unit_price=unit_price,
        orderable=orderable,
        max_budget_per_trade_usd=getattr(policy, "max_budget_per_trade_usd"),
        max_account_exposure_pct=getattr(policy, "max_account_exposure_pct"),
        max_qty_per_trade=getattr(policy, "max_qty_per_trade"),
        account_equity_usd=getattr(policy, "account_equity_usd", None),
    )
    qty = sizing.recommended_qty

    # 6. Block on zero quantity.
    if qty <= 0:
        return OverseasBuyFlowResult(
            action="blocked_zero_qty",
            reason="Position sizing returned zero quantity.",
            symbol=symbol,
            exchange=exchange,
            qty=0,
            unit_price=unit_price,
            candidate=candidate,
            sizing=sizing,
            risk=None,
            order_record=None,
        )

    from app.overseas_execution.order_guard import evaluate_overseas_buy_guard
    guard = evaluate_overseas_buy_guard(
        state=state, symbol=symbol, qty=qty,
        block_rebuy_symbols_bought_today=getattr(policy, "block_rebuy_symbols_bought_today", False),
        allow_one_buy_per_symbol_per_day=getattr(policy, "allow_one_buy_per_symbol_per_day", False),
        rebuy_cooldown_minutes=getattr(policy, "rebuy_cooldown_minutes", 0),
        same_symbol_max_buys_per_day=getattr(policy, "same_symbol_max_buys_per_day", 0),
        order_cooldown_minutes=getattr(policy, "order_cooldown_minutes", 0),
        blocked_cooldown_minutes=getattr(policy, "blocked_cooldown_minutes", 0),
        now=now,
    )
    if not guard.allowed:
        return OverseasBuyFlowResult(
            action="blocked_order_guard", reason=guard.reason,
            symbol=symbol, exchange=exchange, qty=qty, unit_price=unit_price,
            candidate=candidate, sizing=sizing, risk=None, order_record=None,
        )
    from app.overseas_risk.guards import evaluate_overseas_risk_guards
    planned_notional_usd = qty * unit_price
    risk = evaluate_overseas_risk_guards(
        log_path=risk_log_path, planned_notional_usd=planned_notional_usd,
        max_orders=getattr(policy, "max_orders", 0),
        max_notional_usd=getattr(policy, "max_notional_usd", 0.0),
        enabled=getattr(policy, "risk_enabled", True), now=now,
    )
    if not risk.allowed:
        return OverseasBuyFlowResult(
            action="blocked_risk_guard", reason=risk.reason,
            symbol=symbol, exchange=exchange, qty=qty, unit_price=unit_price,
            candidate=candidate, sizing=sizing, risk=risk, order_record=None,
        )
    if not confirm_buy:
        return OverseasBuyFlowResult(
            action="preview", reason="CONFIRM_BUY off — dry-run preview",
            symbol=symbol, exchange=exchange, qty=qty, unit_price=unit_price,
            candidate=candidate, sizing=sizing, risk=risk, order_record=None,
        )
    from app.overseas_stock.order import place_overseas_limit_order
    _submit = submit_order or place_overseas_limit_order
    record = _submit(symbol=symbol, exchange=exchange, qty=qty, unit_price=unit_price,
                     side="buy", settings=settings, token=token)
    if notify:
        notify(side="buy", symbol=symbol, qty=qty, unit_price=unit_price, status="submitted")
    return OverseasBuyFlowResult(
        action="submitted", reason="Order submitted.",
        symbol=symbol, exchange=exchange, qty=qty, unit_price=unit_price,
        candidate=candidate, sizing=sizing, risk=risk, order_record=record,
    )
