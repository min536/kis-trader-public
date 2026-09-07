"""USD/whole-share position sizing for overseas (US) stocks — Module A."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OverseasPositionSizing:
    recommended_qty: int
    unit_price: float
    notional_usd: float
    cash_cap_qty: int
    budget_cap_qty: int
    exposure_cap_qty: int | None
    max_qty_cap: int
    binding_cap: str


def calculate_overseas_position_sizing(
    *,
    unit_price: float,
    orderable,
    max_budget_per_trade_usd: float,
    max_account_exposure_pct: float,
    max_qty_per_trade: int,
    account_equity_usd: float | None = None,
) -> OverseasPositionSizing:
    import math
    if not math.isfinite(unit_price) or unit_price <= 0:
        raise ValueError(f"unit_price must be finite and > 0, got {unit_price!r}")

    cash = orderable.orderable_cash
    broker_max = orderable.max_order_qty

    if cash is not None and broker_max is not None:
        cash_cap_qty = min(int(cash // unit_price), int(broker_max))
    elif cash is not None:
        cash_cap_qty = int(cash // unit_price)
    elif broker_max is not None:
        cash_cap_qty = int(broker_max)
    else:
        cash_cap_qty = 0
    budget_cap_qty = int(max_budget_per_trade_usd // unit_price)
    max_qty_cap = int(max_qty_per_trade)

    exposure_cap_qty: int | None = None
    if account_equity_usd is not None:
        exposure_cap_qty = int((account_equity_usd * max_account_exposure_pct / 100.0) // unit_price)

    caps = [("cash", cash_cap_qty), ("budget", budget_cap_qty), ("max_qty", max_qty_cap)]
    if exposure_cap_qty is not None:
        caps.append(("exposure", exposure_cap_qty))

    recommended_qty = min(v for _, v in caps)
    # determine binding cap: prefer cash, budget, exposure, max_qty
    priority = ["cash", "budget", "exposure", "max_qty"]
    cap_map = {name: val for name, val in caps}
    binding_cap = "cash"
    for name in priority:
        if name in cap_map and cap_map[name] == recommended_qty:
            binding_cap = name
            break
    notional_usd = recommended_qty * unit_price

    return OverseasPositionSizing(
        recommended_qty=recommended_qty,
        unit_price=unit_price,
        notional_usd=notional_usd,
        cash_cap_qty=cash_cap_qty,
        budget_cap_qty=budget_cap_qty,
        exposure_cap_qty=exposure_cap_qty,
        max_qty_cap=max_qty_cap,
        binding_cap=binding_cap,
    )
