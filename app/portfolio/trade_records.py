"""Normalized closed-trade records from the order log (W7 reader).

Each ``sell_order_succeeded`` event is self-contained: it carries the position
cost basis (``raw_response.sell_strategy_details.details.average_cost``), the
sell price (``sell_plan.current_price_krw``), qty, and the exit ``trigger`` — so
a closed trade needs no FIFO buy-pairing. PnL is recomputed with ``calc_net_pnl``
on the same ``average_cost`` field as ``performance.py``'s realized summary, so
the two agree **record-for-record** (both inherit the broker's integer avg-cost
rounding, i.e. neither is penny-exact vs. share-level ground truth).

Scope / v1 limitations:
- **Mock (paper) records only** — live-scoped records are skipped.
- **All-time, not today** — every closed trade in the log is included (no date
  gate), so this is a cumulative scorecard, a *superset* of the today-filtered
  ``build_today_realized_summary``. Surfaces should label it "to date".
- **``hold_days`` is 0.0** — a sell record carries the *average* cost basis, not a
  single entry timestamp, so holding period is not derivable per sell without
  separate position-open tracking. Per-symbol/per-trigger PnL, win rate, and
  turnover are exact; only the holding-time distribution is degraded (and the
  renderer omits it rather than print a misleading 0).
"""
from __future__ import annotations

from typing import Mapping

from app.core.costs import calc_net_pnl

UNKNOWN_SYMBOL = "UNKNOWN"
UNKNOWN_TRIGGER = "unknown"


def _coerce_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def build_closed_trade_records(records, *, settings) -> list[dict]:
    """Order-log records → normalized closed-trade records (W3 analytics input)."""
    trades: list[dict] = []
    for record in records or ():
        if not isinstance(record, Mapping):
            continue
        if str(record.get("action", "")).strip() != "sell_order_succeeded":
            continue
        if str(record.get("environment", "mock")).strip() != "mock":
            continue
        raw_response = record.get("raw_response")
        if not isinstance(raw_response, Mapping):
            continue
        sell_plan = raw_response.get("sell_plan")
        strategy_details = raw_response.get("sell_strategy_details")
        if not isinstance(sell_plan, Mapping) or not isinstance(strategy_details, Mapping):
            continue
        details = strategy_details.get("details")
        if not isinstance(details, Mapping):
            continue

        qty = _coerce_int(sell_plan.get("qty"))
        sell_price = _coerce_int(sell_plan.get("current_price_krw"))
        avg_cost = _coerce_int(details.get("average_cost"))
        if qty <= 0 or sell_price <= 0 or avg_cost <= 0:
            continue

        pnl = calc_net_pnl(
            avg_cost_krw=avg_cost,
            current_price_krw=sell_price,
            qty=qty,
            settings=settings,
        )
        trades.append(
            {
                "symbol": str(record.get("symbol", "")).strip() or UNKNOWN_SYMBOL,
                "sell_trigger": str(raw_response.get("trigger", "")).strip() or UNKNOWN_TRIGGER,
                "hold_days": 0.0,
                "buy_notional_krw": int(pnl["buy_notional_krw"]),
                "sell_notional_krw": int(pnl["sell_notional_krw"]),
                "gross_pnl_krw": int(pnl["gross_pnl_krw"]),
                "net_pnl_krw": int(pnl["net_pnl_krw"]),
            }
        )
    return trades
