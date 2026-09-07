from types import MappingProxyType
from typing import Mapping

# ── Backtester cost-policy single source ───────────────────────────────────
# The research backtester REST payload takes single-value decimal-fraction cost
# params (commission_rate / tax_rate / slippage). Historically shadow_watch.py
# and run_proposal_backtest.py each hardcoded these; LEGACY_* centralizes the
# current values so there is ONE definition (value-preserving). These diverge
# from the live settings policy (tax 23 vs 15 bps, slippage 10 vs 5 bps);
# migrating the tools to settings-derived params realigns them but changes
# proposal-backtest results — an operator gate (re-run autotuner B-1).
LEGACY_BACKTEST_COST_PARAMS: Mapping[str, float] = MappingProxyType(
    {
        "commission_rate": 0.00015,  # 1.5 bps
        "tax_rate": 0.0023,  # 23 bps
        "slippage": 0.001,  # 10 bps
    }
)


def canonical_backtest_cost_params(settings) -> dict[str, float]:
    """Backtester cost params derived from the canonical live settings policy.

    The backtester takes single-value params; the sell side carries the full
    cost stack (fee + tax + slippage), so its bps map the payload. Buy/sell fee
    and slippage are equal under the defaults. This is the migration target the
    operator adopts to realign the tools with live (an operator gate).
    """
    return {
        "commission_rate": float(settings.sell_fee_bps) / 10_000.0,
        "tax_rate": float(settings.sell_tax_bps) / 10_000.0,
        "slippage": float(settings.sell_slippage_bps) / 10_000.0,
    }


def _calc_bps_amount(notional_krw: int, bps: float) -> int:
    notional = max(int(notional_krw), 0)
    rate_bps = max(float(bps), 0.0)
    return int(round(notional * rate_bps / 10_000))


def calc_buy_fee(notional_krw: int, fee_bps: float) -> int:
    return _calc_bps_amount(notional_krw, fee_bps)


def calc_sell_fee(notional_krw: int, fee_bps: float) -> int:
    return _calc_bps_amount(notional_krw, fee_bps)


def calc_sell_tax(notional_krw: int, tax_bps: float) -> int:
    return _calc_bps_amount(notional_krw, tax_bps)


def calc_slippage(notional_krw: int, slippage_bps: float) -> int:
    return _calc_bps_amount(notional_krw, slippage_bps)


def calc_round_trip_cost_estimate(
    buy_notional_krw: int,
    sell_notional_krw: int,
    settings,
) -> dict[str, object]:
    buy_notional = max(int(buy_notional_krw), 0)
    sell_notional = max(int(sell_notional_krw), 0)

    estimated_buy_fee_krw = calc_buy_fee(buy_notional, settings.buy_fee_bps)
    estimated_buy_slippage_krw = calc_slippage(
        buy_notional,
        settings.buy_slippage_bps,
    )
    estimated_sell_fee_krw = calc_sell_fee(sell_notional, settings.sell_fee_bps)
    estimated_sell_tax_krw = calc_sell_tax(sell_notional, settings.sell_tax_bps)
    estimated_sell_slippage_krw = calc_slippage(
        sell_notional,
        settings.sell_slippage_bps,
    )

    estimated_entry_cost_krw = (
        buy_notional + estimated_buy_fee_krw + estimated_buy_slippage_krw
    )
    estimated_exit_cost_krw = (
        estimated_sell_fee_krw
        + estimated_sell_tax_krw
        + estimated_sell_slippage_krw
    )
    estimated_round_trip_cost_krw = (
        estimated_buy_fee_krw
        + estimated_buy_slippage_krw
        + estimated_sell_fee_krw
        + estimated_sell_tax_krw
        + estimated_sell_slippage_krw
    )
    estimated_break_even_bps = 0.0
    if buy_notional > 0:
        estimated_break_even_bps = (
            estimated_round_trip_cost_krw / buy_notional
        ) * 10_000

    return {
        "buy_notional_krw": buy_notional,
        "sell_notional_krw": sell_notional,
        "estimated_buy_fee_krw": estimated_buy_fee_krw,
        "estimated_buy_slippage_krw": estimated_buy_slippage_krw,
        "estimated_entry_cost_krw": estimated_entry_cost_krw,
        "estimated_sell_fee_krw": estimated_sell_fee_krw,
        "estimated_sell_tax_krw": estimated_sell_tax_krw,
        "estimated_sell_slippage_krw": estimated_sell_slippage_krw,
        "estimated_exit_cost_krw": estimated_exit_cost_krw,
        "estimated_round_trip_cost_krw": estimated_round_trip_cost_krw,
        "estimated_break_even_bps": round(estimated_break_even_bps, 2),
    }


def calc_net_pnl(
    avg_cost_krw: int,
    current_price_krw: int,
    qty: int,
    settings,
) -> dict[str, object]:
    average_cost = max(int(avg_cost_krw), 0)
    current_price = max(int(current_price_krw), 0)
    quantity = max(int(qty), 0)

    buy_notional_krw = average_cost * quantity
    sell_notional_krw = current_price * quantity
    gross_pnl_krw = sell_notional_krw - buy_notional_krw

    cost_estimate = calc_round_trip_cost_estimate(
        buy_notional_krw=buy_notional_krw,
        sell_notional_krw=sell_notional_krw,
        settings=settings,
    )
    net_pnl_krw = gross_pnl_krw - int(cost_estimate["estimated_round_trip_cost_krw"])

    gross_pnl_pct = 0.0
    net_pnl_pct = 0.0
    gross_pnl_bps = 0.0
    net_pnl_bps = 0.0
    if buy_notional_krw > 0:
        gross_pnl_pct = (gross_pnl_krw / buy_notional_krw) * 100
        net_pnl_pct = (net_pnl_krw / buy_notional_krw) * 100
        gross_pnl_bps = (gross_pnl_krw / buy_notional_krw) * 10_000
        net_pnl_bps = (net_pnl_krw / buy_notional_krw) * 10_000

    return {
        "avg_cost_krw": average_cost,
        "current_price_krw": current_price,
        "qty": quantity,
        "buy_notional_krw": buy_notional_krw,
        "sell_notional_krw": sell_notional_krw,
        "gross_pnl_krw": gross_pnl_krw,
        "net_pnl_krw": net_pnl_krw,
        "gross_pnl_pct": round(gross_pnl_pct, 2),
        "net_pnl_pct": round(net_pnl_pct, 2),
        "gross_pnl_bps": round(gross_pnl_bps, 2),
        "net_pnl_bps": round(net_pnl_bps, 2),
        **cost_estimate,
    }


def calc_expected_net_profit_buffer_bps(
    *,
    entry_price_krw: int,
    target_exit_price_krw: int,
    qty: int,
    settings,
) -> dict[str, object]:
    entry_price = max(int(entry_price_krw), 0)
    target_exit_price = max(int(target_exit_price_krw), 0)
    quantity = max(int(qty), 0)

    buy_notional_krw = entry_price * quantity
    sell_notional_krw = target_exit_price * quantity
    gross_expected_profit_krw = sell_notional_krw - buy_notional_krw
    cost_estimate = calc_round_trip_cost_estimate(
        buy_notional_krw=buy_notional_krw,
        sell_notional_krw=sell_notional_krw,
        settings=settings,
    )
    net_expected_profit_krw = gross_expected_profit_krw - int(
        cost_estimate["estimated_round_trip_cost_krw"]
    )
    net_expected_profit_bps = 0.0
    if buy_notional_krw > 0:
        net_expected_profit_bps = (
            net_expected_profit_krw / buy_notional_krw
        ) * 10_000

    return {
        "entry_price_krw": entry_price,
        "target_exit_price_krw": target_exit_price,
        "qty": quantity,
        "gross_expected_profit_krw": gross_expected_profit_krw,
        "net_expected_profit_krw": net_expected_profit_krw,
        "net_expected_profit_bps": round(net_expected_profit_bps, 2),
        **cost_estimate,
    }
