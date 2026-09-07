"""Overseas flow policy — Phase 5.

A frozen dataclass bundling the sizing caps, order-guard knobs, and risk caps that
parameterize the overseas buy/sell flows. Being a dataclass (not a free-form kwargs
bag) means an unknown field is a TypeError and the three required caps must be
supplied — a typo'd cap can't silently fall back to a default and mis-size an order.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class OverseasFlowPolicy:
    # sizing caps (required)
    max_budget_per_trade_usd: float
    max_account_exposure_pct: float
    max_qty_per_trade: int
    # sizing / pricing (optional)
    account_equity_usd: float | None = None
    limit_offset_bps: float = 0.0
    # order-guard knobs
    block_rebuy_symbols_bought_today: bool = False
    allow_one_buy_per_symbol_per_day: bool = False
    rebuy_cooldown_minutes: int = 0
    same_symbol_max_buys_per_day: int = 0
    order_cooldown_minutes: int = 0
    blocked_cooldown_minutes: int = 0
    block_resell_symbols_sold_today: bool = False
    allow_one_sell_trigger_per_symbol_per_day: bool = False
    # risk caps
    max_orders: int = 0
    max_notional_usd: float = 0.0
    risk_enabled: bool = True
