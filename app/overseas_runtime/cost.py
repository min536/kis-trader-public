"""US equity cost model for overseas orders.

US has NO securities-transaction tax, unlike KR.
Costs: broker commission (both sides), SEC fee (sell only), FINRA TAF (sell only).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class OverseasCostModel:
    commission_bps: float = 25.0          # broker commission, both sides
    sec_fee_rate: float = 0.0000278       # SEC fee, SELL only (provisional)
    taf_per_share: float = 0.000166       # FINRA TAF per share, SELL only (provisional)
    taf_max: float = 8.30                 # TAF cap, SELL only
    min_commission_usd: float = 0.0


def estimate_overseas_cost(
    *, notional_usd: float, qty: int, side: str, model: OverseasCostModel | None = None
) -> dict:
    """Estimate the total cost of an overseas (US equity) order.

    US has NO securities-transaction tax, unlike KR.
    """
    model = model or OverseasCostModel()
    side_lower = side.lower()
    if side_lower not in ("buy", "sell"):
        raise ValueError(f"Invalid side: {side!r}. Must be 'buy' or 'sell'.")

    commission = max(
        notional_usd * model.commission_bps / 10000.0,
        model.min_commission_usd,
    )
    if side_lower == "sell":
        sec_fee = notional_usd * model.sec_fee_rate
        taf = min(qty * model.taf_per_share, model.taf_max)
    else:
        sec_fee = 0.0
        taf = 0.0

    total = commission + sec_fee + taf
    return {
        "commission_usd": round(commission, 4),
        "sec_fee_usd": round(sec_fee, 4),
        "taf_usd": round(taf, 4),
        "total_cost_usd": round(total, 4),
    }
