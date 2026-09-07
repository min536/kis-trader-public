"""Core backtest event loop.

Calls real engine functions (evaluate_buy_decision, evaluate_sell_decision,
calculate_selection_score, calculate_position_sizing) directly — no API calls.
Market data is supplied by BacktestDataProvider.

Daily resolution: one sell check + one buy scan per trading day.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.execution.position_sizing import calculate_position_sizing
from app.strategy.buy_decision import evaluate_buy_decision
from app.strategy.sell_decision import evaluate_sell_decision
from app.scanner.service import calculate_selection_score

from backtester.ai_integration import AISignalProvider, CandidateFeatureExporter
from backtester.engine_backtest.data_provider import BacktestDataProvider
from backtester.engine_backtest.portfolio import BacktestPortfolio
from backtester.engine_backtest.state import make_backtest_state, reset_daily_state
from backtester.engine_backtest.records import BacktestResult, DayRecord  # facade: canonical in records
from backtester.engine_backtest.sampling import collect_buy_pass_samples, _max_positions  # facade: canonical in sampling

logger = logging.getLogger(__name__)

_BUY_RULE_NAMES = [
    "intraday_pullback",
    "rebound_from_low",
    "controlled_down_day",
    "gap_down_open",
    "range_recovery",
]


# ── result types ──────────────────────────────────────────────────────────


# ── main entry point ───────────────────────────────────────────────────────

def run_backtest(
    data_provider: BacktestDataProvider,
    settings,
    *,
    initial_cash: int = 10_000_000,
    symbols: list[str] | None = None,
    sell_first: bool = True,
    verbose: bool = False,
    feature_exporter: CandidateFeatureExporter | None = None,
    ai_provider: AISignalProvider | None = None,
) -> BacktestResult:
    """Run a full backtest over all available dates in the data provider.

    Parameters
    ----------
    data_provider:
        Historical OHLCV source.
    settings:
        Engine settings object (same type as get_settings() returns).
        API credential fields are ignored — only strategy params matter.
    initial_cash:
        Starting cash in KRW.
    symbols:
        Subset of symbols to trade. Defaults to all symbols in data_provider.
    sell_first:
        Check sell before buy each day (mirrors real engine default).
    verbose:
        Print a one-line summary per day.
    feature_exporter:
        Optional candidate feature exporter for AI training data.
    ai_provider:
        Optional AI signal provider. When None or disabled, the engine
        runs byte-for-byte identically to the pre-AI version.
    """
    if symbols is None:
        symbols = data_provider.symbols()

    trading_dates = data_provider.universe_dates(symbols)

    portfolio = BacktestPortfolio(initial_cash=initial_cash)
    state = make_backtest_state(trading_dates[0] if trading_dates else date.today())

    daily_records: list[DayRecord] = []
    equity_curve: list[tuple[date, int]] = []
    prices: dict[str, int] = {}  # populated each day; initialized here so EOD close is safe if trading_dates is empty

    for trading_date in trading_dates:
        reset_daily_state(state, trading_date)

        # Build price lookup for today
        prices: dict[str, int] = {}
        for sym in symbols:
            row = data_provider.get_row(sym, trading_date)
            if row is not None:
                prices[sym] = row.close_price

        record = DayRecord(
            date=trading_date,
            portfolio_value=portfolio.total_value(prices),
            cash=portfolio.cash,
        )
        record.buy_capacity = {
            "positions_at_day_start": len(portfolio.positions),
        }

        # ── sell pass ────────────────────────────────────────────────────
        if sell_first and settings.sell_enable:
            _run_sell_pass(
                portfolio=portfolio,
                data_provider=data_provider,
                settings=settings,
                state=state,
                trading_date=trading_date,
                prices=prices,
                record=record,
            )

        # ── buy pass ─────────────────────────────────────────────────────
        _run_buy_pass(
            portfolio=portfolio,
            data_provider=data_provider,
            symbols=symbols,
            settings=settings,
            state=state,
            trading_date=trading_date,
            prices=prices,
            record=record,
            feature_exporter=feature_exporter,
            ai_provider=ai_provider,
        )

        # update record with end-of-day values
        record.portfolio_value = portfolio.total_value(prices)
        record.cash = portfolio.cash

        # update high-water marks after close
        portfolio.update_high_water_marks(prices)

        equity_curve.append((trading_date, record.portfolio_value))
        daily_records.append(record)

        if verbose:
            pct = (record.portfolio_value - initial_cash) / initial_cash * 100 if initial_cash else 0.0
            action = ""
            if record.buy_symbol:
                action += f" BUY {record.buy_symbol}@{record.buy_price}×{record.buy_qty}"
            if record.sell_symbol:
                action += f" SELL {record.sell_symbol}@{record.sell_price}({record.sell_trigger})"
            logger.info(
                "%s  value=%,d  return=%.2f%%%s",
                trading_date,
                record.portfolio_value,
                pct,
                action,
            )

    # close all remaining positions at last available price
    for sym, pos in list(portfolio.positions.items()):
        last_price = prices.get(sym, pos.avg_cost)
        portfolio.execute_sell(
            symbol=sym,
            qty=pos.qty,
            price=last_price,
            trading_date=trading_dates[-1] if trading_dates else date.today(),
            sell_trigger="eod",
            settings=settings,
        )

    final_value = portfolio.cash  # all positions closed

    return BacktestResult(
        initial_cash=initial_cash,
        final_value=final_value,
        trade_log=list(portfolio.trade_log),
        daily_records=daily_records,
        equity_curve=equity_curve,
    )


# ── sell pass ──────────────────────────────────────────────────────────────

def _run_sell_pass(
    *,
    portfolio: BacktestPortfolio,
    data_provider: BacktestDataProvider,
    settings,
    state: dict,
    trading_date: date,
    prices: dict[str, int],
    record: DayRecord,
) -> None:
    portfolio_snap = portfolio.to_portfolio_snapshot(prices)

    for symbol in list(portfolio.positions.keys()):
        snapshot = data_provider.get_snapshot(symbol, trading_date)
        if snapshot is None:
            record.no_data_symbols.append(symbol)
            continue
        record.sell_evaluated_count += 1

        # Re-evaluate whether the symbol still meets buy criteria today
        buy_result = evaluate_buy_decision(
            snapshot=snapshot,
            symbol=symbol,
            qty=portfolio.positions[symbol].qty,
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

        sell_result = evaluate_sell_decision(
            snapshot=snapshot,
            portfolio_snapshot=portfolio_snap,
            symbol=symbol,
            buy_strategy_result=buy_result,
            enabled=settings.sell_enable,
            stop_loss_pct=settings.sell_stop_loss_pct,
            take_profit_pct=settings.sell_take_profit_pct,
            trailing_stop_pct=settings.sell_trailing_stop_pct,
            settings=settings,
            enable_live_leadership_loss=settings.sell_rule_enable_live_leadership_loss,
            enable_live_power_breakdown=settings.sell_rule_enable_live_power_breakdown,
        )

        if sell_result.should_attempt_sell:
            pos = portfolio.positions.get(symbol)
            if pos is None:
                continue
            trigger = sell_result.triggered_rule_name or "signal"
            trade = portfolio.execute_sell(
                symbol=symbol,
                qty=pos.qty,
                price=snapshot.current_price,
                trading_date=trading_date,
                sell_trigger=trigger,
                settings=settings,
            )
            if trade is not None:
                record.sell_symbol = symbol
                record.sell_price = snapshot.current_price
                record.sell_qty = trade.qty
                record.sell_trigger = trigger
                record.sell_triggered_count = 1
                record.sell_triggered_symbols.append(symbol)
                # mark in state so buy guard knows about this sell
                state["symbols_sold_today"].append(symbol)
            break  # one sell per day (mirrors real engine sell-first)


# ── buy pass ───────────────────────────────────────────────────────────────

def _run_buy_pass(
    *,
    portfolio: BacktestPortfolio,
    data_provider: BacktestDataProvider,
    symbols: list[str],
    settings,
    state: dict,
    trading_date: date,
    prices: dict[str, int],
    record: DayRecord,
    feature_exporter: CandidateFeatureExporter | None = None,
    ai_provider: AISignalProvider | None = None,
) -> None:
    # `_ai_on` gates every hook. When False, every AI-related code path is
    # skipped entirely — no arithmetic, no sort changes, no logging — so
    # the run is byte-for-byte identical to the pre-AI engine.
    _ai_on = ai_provider is not None and ai_provider.mode != "disabled"
    if not record.buy_rule_names:
        record.buy_rule_names = list(_BUY_RULE_NAMES)

    current_portfolio_value = portfolio.total_value(prices)
    max_positions = _max_positions(settings, portfolio_value=current_portfolio_value)
    signal_scores: list[float] = []
    rejected_scores: list[float] = []
    candidate_scores: list[float] = []

    positions_at_day_start = int(record.buy_capacity.get("positions_at_day_start", len(portfolio.positions)) or 0)
    positions_before_buy = len(portfolio.positions)
    record.buy_capacity = {
        "positions_at_day_start": positions_at_day_start,
        "positions_before_buy_pass": positions_before_buy,
        "positions_after_sell_pass": positions_before_buy,
        "positions_sold_in_sell_pass": max(positions_at_day_start - positions_before_buy, 0),
        "max_positions": max_positions,
        "available_slots_before_buy_pass": max(max_positions - positions_before_buy, 0),
        "capacity_blocked_before_rule_eval": positions_before_buy >= max_positions,
    }

    def _score_bucket(scores: list[float]) -> dict[str, object]:
        if not scores:
            return {"count": 0, "min": None, "avg": None, "max": None}
        return {
            "count": len(scores),
            "min": round(min(scores), 4),
            "avg": round(sum(scores) / len(scores), 4),
            "max": round(max(scores), 4),
        }

    def _finalize() -> None:
        record.buy_capacity["positions_after_buy_pass"] = len(portfolio.positions)
        record.buy_score_stats = {
            "min_score_threshold": round(float(settings.buy_min_score), 4),
            "signal_scores": _score_bucket(signal_scores),
            "score_rejected": _score_bucket(rejected_scores),
            "scored_candidates": _score_bucket(candidate_scores),
        }

    funnel = {
        "max_positions_blocked": 0,
        "symbols_considered": 0,
        "already_bought_today_blocked": 0,
        "already_held_blocked": 0,
        "no_data": 0,
        "decision_evaluated": 0,
        "rule_failed": 0,
        "buy_signal": 0,
        "score_rejected": 0,
        "scored_candidate": 0,
        "sizing_blocked": 0,
        "executed_buy": 0,
    }

    def _bump(counter: dict[str, int], key: str) -> None:
        counter[key] = int(counter.get(key, 0) or 0) + 1

    # Skip if we already hold max positions (1 for simplicity — real engine can hold multiple)
    # or if daily buy limit reached
    if len(portfolio.positions) >= max_positions:
        funnel["max_positions_blocked"] = 1
        record.buy_funnel = funnel
        _bump(record.buy_rejection_reason_counts, "max_positions_reached")
        _finalize()
        return

    candidates: list[tuple[float, str, Any, Any]] = []  # (score, symbol, snapshot, buy_result)
    # Per-symbol feature payloads keyed by symbol — only populated when
    # feature_exporter is present, so the disabled path allocates nothing.
    _pending_components: dict[str, dict[str, float]] = {}

    for symbol in symbols:
        funnel["symbols_considered"] += 1
        # Skip symbols already bought or held today
        if symbol in state["symbols_bought_today"]:
            funnel["already_bought_today_blocked"] += 1
            _bump(record.buy_rejection_reason_counts, "already_bought_today")
            continue
        if symbol in portfolio.positions:
            funnel["already_held_blocked"] += 1
            _bump(record.buy_rejection_reason_counts, "already_holding")
            continue  # already holding

        snapshot = data_provider.get_snapshot(symbol, trading_date)
        if snapshot is None:
            record.no_data_symbols.append(symbol)
            funnel["no_data"] += 1
            _bump(record.buy_rejection_reason_counts, "missing_snapshot")
            continue

        # Injection #2: AI veto before buy_decision rule engine.
        if _ai_on:
            veto_reason = ai_provider.get_veto(trading_date, symbol)
            if veto_reason is not None:
                record.skipped_symbols.append(symbol)
                _bump(record.buy_rejection_reason_counts, f"ai_veto:{veto_reason}")
                continue

        funnel["decision_evaluated"] += 1
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
        for result in buy_result.rule_results:
            if result.enabled:
                _bump(record.buy_rule_enabled_counts, result.strategy_name)
            if result.passed:
                _bump(record.buy_rule_pass_counts, result.strategy_name)

        if not buy_result.should_attempt_buy:
            record.skipped_symbols.append(symbol)
            funnel["rule_failed"] += 1
            _bump(record.buy_rejection_reason_counts, "buy_decision_blocked")
            continue
        record.buy_signal_count += 1
        funnel["buy_signal"] += 1

        # Score the candidate
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
        # Injection #1: AI score delta after calculate_selection_score.
        if _ai_on:
            score = float(score) + ai_provider.get_score_delta(trading_date, symbol)
        signal_scores.append(float(score))

        if score < settings.buy_min_score:
            record.skipped_symbols.append(symbol)
            funnel["score_rejected"] += 1
            _bump(record.buy_rejection_reason_counts, "score_below_min")
            rejected_scores.append(float(score))
            if feature_exporter is not None:
                feature_exporter.record(
                    trading_date=trading_date,
                    ticker=symbol,
                    base_score=float(score),
                    score_components=score_components,
                    decision="rejected",
                    decision_reason="score_below_min",
                    rule_gate_passed=True,
                    score_gate_passed=False,
                    candidate_rank=None,
                    executed=False,
                )
            continue

        candidates.append((score, symbol, snapshot, buy_result))
        candidate_scores.append(float(score))
        if feature_exporter is not None:
            _pending_components[symbol] = dict(score_components)

    if not candidates:
        record.buy_funnel = funnel
        _finalize()
        return

    record.buy_scored_candidate_count = len(candidates)
    record.buy_candidate_symbols = [symbol for _, symbol, _, _ in candidates]
    funnel["scored_candidate"] = len(candidates)

    # Pick highest-scoring candidate
    candidates.sort(key=lambda c: c[0], reverse=True)
    # Injection #3: AI reranking after candidate filtering.
    # When rerank mode is inactive (including disabled), the provider
    # returns the same list and the score-descending order is preserved.
    if _ai_on:
        candidates = ai_provider.get_reranking(trading_date, candidates)
    top_score, top_symbol, top_snapshot, _ = candidates[0]

    # Capture rank ordering for the exporter before sizing (executed flag
    # filled in below once we know whether the buy actually fires).
    _ranked_symbols = [symbol for _, symbol, _, _ in candidates]

    # Position sizing
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
    record.buy_sizing = {
        "symbol": top_symbol,
        "score": round(float(top_score), 4),
        "recommended_qty": int(sizing.recommended_qty),
        "reason": sizing.reason,
        "block_reason_code": str(sizing.details.get("block_reason_code") or "").strip() or None,
        "block_reason_label": str(sizing.details.get("block_reason_label") or "").strip() or None,
        "max_affordable_qty": int(sizing.max_affordable_qty),
        "budget_limited_qty": int(sizing.budget_limited_qty),
        "exposure_limited_qty": int(sizing.exposure_limited_qty),
        "max_qty_limited_qty": int(sizing.max_qty_limited_qty),
    }

    final_qty = sizing.recommended_qty
    # Injection #4: AI regime multiplier on recommended_qty.
    # Floor to int, clamp to >= 0 — no synthetic positions.
    if _ai_on:
        regime = ai_provider.get_regime_multiplier(trading_date)
        if regime != 1.0:
            final_qty = max(int(final_qty * regime), 0)
    if final_qty <= 0:
        funnel["sizing_blocked"] = 1
        record.buy_funnel = funnel
        _bump(record.buy_rejection_reason_counts, "position_sizing_zero")
        _emit_approved_feature_records(
            feature_exporter=feature_exporter,
            trading_date=trading_date,
            candidates=candidates,
            ranked_symbols=_ranked_symbols,
            pending_components=_pending_components,
            executed_symbol=None,
            sizing_blocked_symbol=top_symbol,
        )
        _finalize()
        return

    portfolio.execute_buy(
        symbol=top_symbol,
        qty=final_qty,
        price=top_snapshot.current_price,
        trading_date=trading_date,
        settings=settings,
    )

    state["symbols_bought_today"].append(top_symbol)

    record.buy_symbol = top_symbol
    record.buy_price = top_snapshot.current_price
    record.buy_qty = final_qty
    record.buy_reason = f"score={top_score:.2f}"
    record.buy_selected_score = round(top_score, 4)
    funnel["executed_buy"] = 1
    record.buy_funnel = funnel
    _emit_approved_feature_records(
        feature_exporter=feature_exporter,
        trading_date=trading_date,
        candidates=candidates,
        ranked_symbols=_ranked_symbols,
        pending_components=_pending_components,
        executed_symbol=top_symbol,
        sizing_blocked_symbol=None,
    )
    _finalize()


def _emit_approved_feature_records(
    *,
    feature_exporter: CandidateFeatureExporter | None,
    trading_date: date,
    candidates: list[tuple[float, str, Any, Any]],
    ranked_symbols: list[str],
    pending_components: dict[str, dict[str, float]],
    executed_symbol: str | None,
    sizing_blocked_symbol: str | None,
) -> None:
    """Emit approved-candidate records. No-op when exporter is None."""
    if feature_exporter is None:
        return
    rank_by_symbol = {sym: idx + 1 for idx, sym in enumerate(ranked_symbols)}
    for score, symbol, _snapshot, _buy_result in candidates:
        components = pending_components.get(symbol)
        if components is None:
            continue
        if symbol == executed_symbol:
            reason = "executed"
            executed = True
        elif symbol == sizing_blocked_symbol:
            reason = "sizing_blocked"
            executed = False
        else:
            reason = "not_selected"
            executed = False
        feature_exporter.record(
            trading_date=trading_date,
            ticker=symbol,
            base_score=float(score),
            score_components=components,
            decision="approved",
            decision_reason=reason,
            rule_gate_passed=True,
            score_gate_passed=True,
            candidate_rank=rank_by_symbol.get(symbol),
            executed=executed,
        )

