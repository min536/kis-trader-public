"""Buy-pass sampling logic (R7-A2 extraction)."""
from __future__ import annotations

from datetime import date
from typing import Any

from app.execution.position_sizing import calculate_position_sizing
from app.scanner.service import calculate_selection_score
from app.strategy.buy_decision import evaluate_buy_decision

from backtester.engine_backtest.data_provider import BacktestDataProvider
from backtester.engine_backtest.portfolio import BacktestPortfolio


def collect_buy_pass_samples(
    *,
    portfolio: BacktestPortfolio,
    data_provider: BacktestDataProvider,
    symbols: list[str],
    settings,
    state: dict,
    trading_date: date,
    prices: dict[str, int],
) -> list[dict[str, Any]]:
    current_portfolio_value = portfolio.total_value(prices)
    max_positions = _max_positions(settings, portfolio_value=current_portfolio_value)
    positions_before_buy = len(portfolio.positions)
    available_slots = max(max_positions - positions_before_buy, 0)
    selected_buy_symbol: str | None = None
    selected_buy_score: float | None = None

    samples: list[dict[str, Any]] = []
    candidates: list[tuple[float, str, Any, Any, dict[str, Any]]] = []

    def _base_sample(symbol: str) -> dict[str, Any]:
        return {
            "date": trading_date.isoformat(),
            "symbol": symbol,
            "stage": "unseen",
            "stage_reason": None,
            "positions_before_buy_pass": positions_before_buy,
            "max_positions": max_positions,
            "available_slots_before_buy_pass": available_slots,
            "current_price": None,
            "open_price": None,
            "low_price": None,
            "prev_day_change_pct": None,
            "rule_gate_passed": False,
            "rule_pass_count": 0,
            "rule_enabled_count": 0,
            "rule_fail_count": 0,
            "rule_pass_signature": "",
            "passed_rule_names": [],
            "failed_rule_names": [],
            "score": None,
            "score_margin_to_threshold": None,
            "min_score_threshold": round(float(settings.buy_min_score), 4),
            "score_components": {},
            "candidate_rank": None,
            "selected_candidate": False,
            "executed": False,
            "selected_buy_symbol_for_day": None,
            "selected_buy_score_for_day": None,
            "sizing_block_reason": None,
            "sizing_recommended_qty": None,
        }

    if positions_before_buy >= max_positions:
        return samples

    for symbol in symbols:
        sample = _base_sample(symbol)
        if symbol in state["symbols_bought_today"]:
            sample["stage"] = "already_bought_today"
            sample["stage_reason"] = "already_bought_today"
            samples.append(sample)
            continue
        if symbol in portfolio.positions:
            sample["stage"] = "already_holding"
            sample["stage_reason"] = "already_holding"
            samples.append(sample)
            continue

        snapshot = data_provider.get_snapshot(symbol, trading_date)
        if snapshot is None:
            sample["stage"] = "missing_snapshot"
            sample["stage_reason"] = "missing_snapshot"
            samples.append(sample)
            continue

        sample["current_price"] = int(snapshot.current_price)
        sample["open_price"] = int(snapshot.open_price)
        sample["low_price"] = int(snapshot.low_price)
        sample["prev_day_change_pct"] = float(snapshot.prev_day_change_pct)

        buy_result = evaluate_buy_decision(
            snapshot=snapshot,
            symbol=symbol,
            qty=settings.qty,
            enable_intraday_pullback=settings.buy_rule_enable_intraday_pullback,
            enable_rebound_from_low=settings.buy_rule_enable_rebound_from_low,
            enable_controlled_down_day=settings.buy_rule_enable_controlled_down_day,
            enable_gap_down_open=settings.buy_rule_enable_gap_down_open,
            enable_range_recovery=settings.buy_rule_enable_range_recovery,
            rebound_from_low_pct=settings.buy_rule_rebound_from_low_pct,
            controlled_down_day_min=settings.buy_rule_controlled_down_day_min,
            controlled_down_day_max=settings.buy_rule_controlled_down_day_max,
            gap_down_open_min_pct=settings.buy_rule_gap_down_open_min_pct,
            gap_down_open_max_pct=settings.buy_rule_gap_down_open_max_pct,
            range_recovery_min_ratio=settings.buy_rule_range_recovery_min_ratio,
            required_pass_count=settings.buy_rule_required_pass_count,
            enable_live_volume_rank=settings.buy_rule_enable_live_volume_rank,
            enable_live_volume_power_rank=settings.buy_rule_enable_live_volume_power_rank,
        )

        passed_rule_names = [
            result.strategy_name
            for result in buy_result.rule_results
            if result.passed
        ]
        failed_rule_names = [
            result.strategy_name
            for result in buy_result.rule_results
            if result.enabled and not result.passed
        ]
        sample["rule_gate_passed"] = bool(buy_result.should_attempt_buy)
        sample["rule_pass_count"] = int(buy_result.passed_count)
        sample["rule_enabled_count"] = int(buy_result.enabled_count)
        sample["rule_fail_count"] = max(int(buy_result.enabled_count) - int(buy_result.passed_count), 0)
        sample["rule_pass_signature"] = "|".join(passed_rule_names)
        sample["passed_rule_names"] = passed_rule_names
        sample["failed_rule_names"] = failed_rule_names

        if not buy_result.should_attempt_buy:
            sample["stage"] = "rule_blocked"
            sample["stage_reason"] = "buy_decision_blocked"
            samples.append(sample)
            continue

        score, score_components = calculate_selection_score(
            snapshot=snapshot,
            strategy_result=buy_result,
            rebound_from_low_pct=settings.buy_rule_rebound_from_low_pct,
            controlled_down_day_min=settings.buy_rule_controlled_down_day_min,
            controlled_down_day_max=settings.buy_rule_controlled_down_day_max,
            gap_down_open_min_pct=settings.buy_rule_gap_down_open_min_pct,
            gap_down_open_max_pct=settings.buy_rule_gap_down_open_max_pct,
            range_recovery_min_ratio=settings.buy_rule_range_recovery_min_ratio,
        )
        sample["score"] = round(float(score), 4)
        sample["score_margin_to_threshold"] = round(float(score) - float(settings.buy_min_score), 4)
        sample["score_components"] = dict(score_components)

        if score < settings.buy_min_score:
            sample["stage"] = "score_blocked"
            sample["stage_reason"] = "score_below_min"
            samples.append(sample)
            continue

        sample["stage"] = "scored_candidate"
        sample["stage_reason"] = "score_passed"
        candidates.append((float(score), symbol, snapshot, buy_result, sample))
        samples.append(sample)

    if not candidates:
        return samples

    candidates.sort(key=lambda item: item[0], reverse=True)
    for index, (_, _, _, _, sample) in enumerate(candidates, start=1):
        sample["candidate_rank"] = index

    top_score, top_symbol, top_snapshot, _, top_sample = candidates[0]
    selected_buy_symbol = top_symbol
    selected_buy_score = round(float(top_score), 4)

    portfolio_snap = portfolio.to_portfolio_snapshot(prices)
    exec_snap = portfolio.to_execution_snapshot(
        symbol=top_symbol,
        price=top_snapshot.current_price,
        qty=settings.qty,
    )
    sizing = calculate_position_sizing(
        execution_snapshot=exec_snap,
        portfolio_snapshot=portfolio_snap,
        max_budget_per_trade_krw=settings.buy_max_budget_per_trade_krw,
        max_account_exposure_pct=settings.buy_max_account_exposure_pct,
        max_qty_per_trade=settings.buy_max_qty_per_trade,
        settings=settings,
    )

    top_sample["selected_candidate"] = True
    top_sample["selected_buy_symbol_for_day"] = selected_buy_symbol
    top_sample["selected_buy_score_for_day"] = selected_buy_score
    top_sample["sizing_recommended_qty"] = int(sizing.recommended_qty)
    top_sample["sizing_block_reason"] = (
        str(sizing.details.get("block_reason_code") or "").strip() or None
    )
    if sizing.recommended_qty <= 0:
        top_sample["stage"] = "sizing_blocked"
        top_sample["stage_reason"] = "position_sizing_zero"
    else:
        top_sample["stage"] = "executed_buy"
        top_sample["stage_reason"] = "executed_buy"
        top_sample["executed"] = True

    for sample in samples:
        sample["selected_buy_symbol_for_day"] = selected_buy_symbol
        sample["selected_buy_score_for_day"] = selected_buy_score

    return samples


# ── helpers ────────────────────────────────────────────────────────────────

def _max_positions(settings, portfolio_value: int | None = None) -> int:
    """Estimate max simultaneous positions from strategy settings.

    Uses two complementary limits and returns the tighter one:

    1. **Exposure limit** — derived from ``buy_max_account_exposure_pct``.
       e.g. 50 % exposure ⟶ at most 2 concurrent positions.

    2. **Budget limit** — derived from ``buy_max_budget_per_trade_krw``
       relative to the current portfolio value.
       e.g. 10 M portfolio, 5 M budget per trade ⟶ at most 2 positions.

    If ``portfolio_value`` is not supplied the budget limit is skipped and
    only the exposure limit applies (backward-compatible fallback).
    """
    exposure_pct = getattr(settings, "buy_max_account_exposure_pct", 100.0)
    if exposure_pct <= 0:
        exposure_limit = 10
    else:
        exposure_limit = max(1, int(100 / exposure_pct))

    budget_per_trade = getattr(settings, "buy_max_budget_per_trade_krw", 0)
    if portfolio_value and portfolio_value > 0 and budget_per_trade > 0:
        budget_limit = max(1, int(portfolio_value / budget_per_trade))
        return min(exposure_limit, budget_limit)

    return exposure_limit
