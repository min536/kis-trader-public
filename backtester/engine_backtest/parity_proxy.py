"""Proxy replay cluster extracted from parity.py (R7-B2)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from backtester.engine_backtest.data_provider import BacktestDataProvider
from backtester.engine_backtest.parity_summary import _float, _int, _parse_ts
from backtester.engine_backtest.portfolio import BacktestPortfolio
from backtester.engine_backtest.runner import DayRecord, _run_buy_pass, _run_sell_pass
from backtester.engine_backtest.settings_factory import make_settings
from backtester.engine_backtest.state import make_backtest_state, reset_daily_state


def _pick_latest_symbol_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        current_price = _int(row.get("current_price"))
        open_price = _int(row.get("open_price"))
        low_price = _int(row.get("low_price"))
        if not current_price or not open_price or not low_price:
            continue
        ts = _parse_ts(row.get("ts") or row.get("timestamp"))
        previous = latest.get(symbol)
        prev_ts = _parse_ts(previous.get("ts") or previous.get("timestamp")) if previous else None
        if previous is None or (ts is not None and (prev_ts is None or ts >= prev_ts)):
            latest[symbol] = row
    return sorted(latest.values(), key=lambda row: str(row.get("symbol") or ""))


def _estimate_prev_close(current_price: int, prev_day_change_pct: float) -> int:
    denominator = 1.0 + (prev_day_change_pct / 100.0)
    if abs(denominator) < 1e-9:
        return 0
    estimated = int(round(current_price / denominator))
    return estimated if estimated > 0 else 0


def _build_provider_from_signal_rows(
    *,
    rows: list[dict[str, Any]],
    trading_date: date,
    seed_snapshot: dict[str, Any] | None = None,
) -> tuple[BacktestDataProvider, dict[str, int]]:
    records: list[dict[str, Any]] = []
    prices: dict[str, int] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        current_price = _int(row.get("current_price")) or 0
        open_price = _int(row.get("open_price")) or current_price
        low_price = _int(row.get("low_price")) or min(open_price, current_price)
        prev_day_change_pct = _float(row.get("prev_day_change_pct")) or 0.0
        high_price = max(open_price, low_price, current_price)
        prev_close = _estimate_prev_close(current_price, prev_day_change_pct)
        records.append(
            {
                "date": trading_date.isoformat(),
                "symbol": symbol,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": current_price,
                "volume": 0,
                "prev_close": prev_close,
                "prev_day_change_pct": prev_day_change_pct,
            }
        )
        prices[symbol] = current_price

    holdings = (seed_snapshot or {}).get("holdings_summary") if isinstance(seed_snapshot, dict) else None
    positions = (holdings or {}).get("positions") if isinstance(holdings, dict) else None
    if isinstance(positions, list):
        for position in positions:
            if not isinstance(position, dict):
                continue
            symbol = str(position.get("symbol") or "").strip()
            if not symbol or symbol in prices:
                continue
            current_price = _int(position.get("current_price")) or _int(position.get("average_cost")) or 0
            if current_price <= 0:
                continue
            records.append(
                {
                    "date": trading_date.isoformat(),
                    "symbol": symbol,
                    "open": current_price,
                    "high": current_price,
                    "low": current_price,
                    "close": current_price,
                    "volume": 0,
                    "prev_close": current_price,
                    "prev_day_change_pct": 0.0,
                }
            )
            prices[symbol] = current_price
    return BacktestDataProvider.from_records(records), prices


def _select_seed_snapshot(snapshot_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    sorted_rows = sorted(
        snapshot_rows,
        key=lambda row: _parse_ts(row.get("timestamp") or row.get("ts")) or datetime.min,
    )
    for row in sorted_rows:
        holdings = row.get("holdings_summary")
        if isinstance(holdings, dict) and holdings.get("positions"):
            return row
    return sorted_rows[0] if sorted_rows else None


def _seed_portfolio_from_snapshot(
    portfolio: BacktestPortfolio,
    *,
    seed_snapshot: dict[str, Any] | None,
    trading_date: date,
) -> int:
    if not isinstance(seed_snapshot, dict):
        return 0
    holdings = seed_snapshot.get("holdings_summary")
    positions = (holdings or {}).get("positions") if isinstance(holdings, dict) else None
    if not isinstance(positions, list):
        return 0
    seeded = 0
    for position in positions:
        if not isinstance(position, dict):
            continue
        symbol = str(position.get("symbol") or "").strip()
        qty = _int(position.get("holding_qty") or position.get("quantity")) or 0
        avg_cost = _int(position.get("average_cost") or position.get("average_price")) or 0
        high_water_mark = _int(position.get("current_price")) or avg_cost
        if not symbol or qty <= 0 or avg_cost <= 0:
            continue
        portfolio.seed_position(
            symbol=symbol,
            qty=qty,
            avg_cost=avg_cost,
            buy_date=trading_date,
            high_water_mark=high_water_mark,
        )
        seeded += 1
    return seeded


def _resolve_initial_cash(
    *,
    seed_snapshot: dict[str, Any] | None,
    fallback_cash: int | None,
) -> int:
    if fallback_cash is not None and fallback_cash > 0:
        return fallback_cash
    if isinstance(seed_snapshot, dict):
        for key in ("cash_krw", "orderable_cash_krw"):
            resolved = _int(seed_snapshot.get(key))
            if resolved is not None and resolved > 0:
                return resolved
        holdings = seed_snapshot.get("holdings_summary")
        if isinstance(holdings, dict):
            for key in ("cash_total_krw", "cash_orderable_krw"):
                resolved = _int(holdings.get(key))
                if resolved is not None and resolved > 0:
                    return resolved
    return 10_000_000


def _run_proxy_replay(
    *,
    trading_date: date,
    latest_signal_rows: list[dict[str, Any]],
    snapshot_rows: list[dict[str, Any]],
    initial_cash: int | None,
) -> dict[str, Any]:
    settings = make_settings()
    seed_snapshot = _select_seed_snapshot(snapshot_rows)
    provider, prices = _build_provider_from_signal_rows(
        rows=latest_signal_rows,
        trading_date=trading_date,
        seed_snapshot=seed_snapshot,
    )
    resolved_cash = _resolve_initial_cash(seed_snapshot=seed_snapshot, fallback_cash=initial_cash)
    portfolio = BacktestPortfolio(initial_cash=resolved_cash)
    seeded_positions = _seed_portfolio_from_snapshot(
        portfolio,
        seed_snapshot=seed_snapshot,
        trading_date=trading_date,
    )

    state = make_backtest_state(trading_date)
    reset_daily_state(state, trading_date)

    record = DayRecord(
        date=trading_date,
        portfolio_value=portfolio.total_value(prices),
        cash=portfolio.cash,
    )
    record.buy_capacity = {
        "positions_at_day_start": len(portfolio.positions),
    }

    if settings.sell_enable:
        _run_sell_pass(
            portfolio=portfolio,
            data_provider=provider,
            settings=settings,
            state=state,
            trading_date=trading_date,
            prices=prices,
            record=record,
        )

    _run_buy_pass(
        portfolio=portfolio,
        data_provider=provider,
        symbols=provider.symbols(),
        settings=settings,
        state=state,
        trading_date=trading_date,
        prices=prices,
        record=record,
    )

    record.portfolio_value = portfolio.total_value(prices)
    record.cash = portfolio.cash

    buy_symbols = [record.buy_symbol] if record.buy_symbol else []
    sell_symbols = [record.sell_symbol] if record.sell_symbol else []
    return {
        "mode": "historical_signal_proxy",
        "input_symbol_count": len(latest_signal_rows),
        "initial_cash": resolved_cash,
        "seeded_position_count": seeded_positions,
        "buy_signal_count": record.buy_signal_count,
        "buy_scored_candidate_count": record.buy_scored_candidate_count,
        "buy_candidate_symbols": list(record.buy_candidate_symbols),
        "buy_rule_names": list(record.buy_rule_names),
        "buy_rule_enabled_counts": dict(record.buy_rule_enabled_counts),
        "buy_rule_pass_counts": dict(record.buy_rule_pass_counts),
        "buy_rejection_reason_counts": dict(record.buy_rejection_reason_counts),
        "buy_funnel": dict(record.buy_funnel),
        "buy_capacity": dict(record.buy_capacity),
        "buy_score_stats": dict(record.buy_score_stats),
        "buy_sizing": dict(record.buy_sizing),
        "selected_buy_symbols": buy_symbols,
        "selected_buy_score": record.buy_selected_score,
        "final_candidate_count": len(buy_symbols),
        "executed_buy_count": len(buy_symbols),
        "sell_evaluated_count": record.sell_evaluated_count,
        "sell_triggered_count": record.sell_triggered_count,
        "selected_sell_symbols": sell_symbols,
        "executed_sell_count": len(sell_symbols),
        "sell_reason_distribution": {record.sell_trigger: 1} if record.sell_trigger else {},
    }
