"""Overseas (US) sell position sizing — Module B."""
from __future__ import annotations

from dataclasses import dataclass

FULL_EXIT_TRIGGERS = frozenset({"stop_loss", "rebalance"})
HALF_EXIT_TRIGGERS = frozenset({"take_profit", "trailing_stop", "live_leadership_loss", "live_power_breakdown"})


@dataclass(frozen=True)
class OverseasSellSizing:
    recommended_sell_qty: int
    trigger: str | None
    fraction_label: str  # "full" | "half" | "none"


def calculate_overseas_sell_sizing(*, holding_qty: int, trigger: str | None) -> OverseasSellSizing:
    qty = max(int(holding_qty), 0)
    # Mirrors app/execution/sell_position_sizing.py: a zero holding sells nothing
    # regardless of trigger (the upfront holding<=0 guard there).
    if qty <= 0:
        return OverseasSellSizing(recommended_sell_qty=0, trigger=trigger, fraction_label="none")
    if trigger in FULL_EXIT_TRIGGERS:
        return OverseasSellSizing(recommended_sell_qty=qty, trigger=trigger, fraction_label="full")
    if trigger in HALF_EXIT_TRIGGERS:
        # max(1, qty // 2) — a half-exit on a tiny holding still sells at least one
        # share (a 1-share take_profit sells 1, not 0), matching domestic.
        return OverseasSellSizing(recommended_sell_qty=max(1, qty // 2), trigger=trigger, fraction_label="half")
    return OverseasSellSizing(recommended_sell_qty=0, trigger=trigger, fraction_label="none")
