"""ET-dated runtime state writer for the overseas order cycle."""
from __future__ import annotations

from app.overseas_stock.session import get_us_eastern_now


def new_overseas_runtime_state() -> dict:
    return {
        "recent_orders": [],
        "symbols_sold_today": [],
        "sell_triggered_symbols_today": [],
    }


def record_overseas_order_in_state(state, *, side, symbol, qty, now=None, action="order_submitted"):
    now = now if now is not None else get_us_eastern_now()
    state.setdefault("recent_orders", []).append(
        {
            "date": now.date().isoformat(),
            "side": str(side).upper(),
            "symbol": str(symbol),
            "qty": int(qty),
            "action": action,
            "timestamp": now.isoformat(),
        }
    )
