"""ADR-6 marketable-limit price selection for overseas (US) stocks — Module C."""
from __future__ import annotations

import math


def compute_overseas_limit_price(*, side: str, quote_price: float, offset_bps: float = 0.0) -> float:
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if not math.isfinite(quote_price) or quote_price <= 0:
        raise ValueError(f"quote_price must be finite and > 0, got {quote_price!r}")
    if side == "buy":
        price = quote_price * (1 + offset_bps / 10000.0)
    else:
        price = quote_price * (1 - offset_bps / 10000.0)
    if price >= 1.0:
        return round(price, 2)
    return round(price, 4)
