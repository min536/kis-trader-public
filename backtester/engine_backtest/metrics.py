"""Backtest performance metrics.

All functions take the BacktestResult produced by runner.run_backtest()
and return plain dicts for easy serialization.
"""
from __future__ import annotations

import math
from datetime import date

from backtester.analytics.robust_risk import build_risk_diagnostics
from backtester.engine_backtest.portfolio import ClosedTrade
from backtester.engine_backtest.runner import BacktestResult


def compute_metrics(result: BacktestResult) -> dict:
    """Return a summary metrics dict from a BacktestResult."""
    trades = result.trade_log
    equity = result.equity_curve
    initial = result.initial_cash
    daily_records = result.daily_records or []

    total_return_pct = result.total_return_pct
    n_trades = len(trades)
    n_wins = sum(1 for t in trades if t.net_pnl_krw > 0)
    n_losses = n_trades - n_wins

    win_rate = n_wins / n_trades * 100 if n_trades > 0 else 0.0
    avg_pnl_pct = (
        sum(t.net_pnl_pct for t in trades) / n_trades if n_trades > 0 else 0.0
    )
    avg_hold_days = (
        sum(t.hold_days for t in trades) / n_trades if n_trades > 0 else 0.0
    )

    profit_factor = _profit_factor(trades)
    sharpe = _sharpe(equity, initial)
    sortino = _sortino(equity, initial)
    mdd_pct, mdd_start, mdd_end = _max_drawdown(equity)
    cagr = _cagr(equity, initial)
    calmar = _calmar(cagr, mdd_pct)
    risk_diagnostics = build_risk_diagnostics(equity)
    rolling_volatility = risk_diagnostics["rolling_volatility"]
    winsorized_returns = risk_diagnostics["winsorized_returns"]

    total_gross_pnl = sum(t.gross_pnl_krw for t in trades)
    total_net_pnl = sum(t.net_pnl_krw for t in trades)
    recovery_factor = _recovery_factor(total_net_pnl, mdd_pct, initial)

    trigger_counts: dict[str, int] = {}
    for t in trades:
        trigger_counts[t.sell_trigger] = trigger_counts.get(t.sell_trigger, 0) + 1

    avg_win_pct = (
        sum(t.net_pnl_pct for t in trades if t.net_pnl_krw > 0) / n_wins
        if n_wins > 0 else 0.0
    )
    avg_loss_pct = (
        sum(t.net_pnl_pct for t in trades if t.net_pnl_krw < 0) / n_losses
        if n_losses > 0 else 0.0
    )
    total_buy_signals = sum(int(getattr(dr, "buy_signal_count", 0) or 0) for dr in daily_records)
    buy_signal_days = sum(
        1 for dr in daily_records if int(getattr(dr, "buy_signal_count", 0) or 0) > 0
    )
    total_scored_candidates = sum(
        int(getattr(dr, "buy_scored_candidate_count", 0) or 0) for dr in daily_records
    )
    scored_candidate_days = sum(
        1
        for dr in daily_records
        if int(getattr(dr, "buy_scored_candidate_count", 0) or 0) > 0
    )
    total_executed_buys = sum(1 for dr in daily_records if getattr(dr, "buy_symbol", None))
    executed_buy_days = total_executed_buys
    cash_pcts = [
        (float(getattr(dr, "cash", 0) or 0) / float(getattr(dr, "portfolio_value", 0) or 0)) * 100
        for dr in daily_records
        if float(getattr(dr, "portfolio_value", 0) or 0) > 0
    ]
    avg_cash_pct = sum(cash_pcts) / len(cash_pcts) if cash_pcts else 100.0
    avg_invested_pct = 100.0 - avg_cash_pct
    buy_signal_to_execution_pct = (
        total_executed_buys / total_buy_signals * 100 if total_buy_signals > 0 else 0.0
    )
    scored_candidate_to_execution_pct = (
        total_executed_buys / total_scored_candidates * 100
        if total_scored_candidates > 0 else 0.0
    )

    return {
        "initial_cash": initial,
        "final_value": result.final_value,
        "total_return_pct": round(total_return_pct, 2),
        "cagr_pct": round(cagr, 2),
        "sharpe_ratio": round(sharpe, 3),
        "sortino_ratio": round(sortino, 3),
        "calmar_ratio": round(calmar, 3),
        "recovery_factor": round(recovery_factor, 3),
        "max_drawdown_pct": round(mdd_pct, 2),
        "max_drawdown_start": mdd_start.isoformat() if mdd_start else None,
        "max_drawdown_end": mdd_end.isoformat() if mdd_end else None,
        "current_drawdown_pct": risk_diagnostics["current_drawdown_pct"],
        "rolling_volatility_20d_pct": rolling_volatility["latest_annualized_pct"],
        "winsorized_return_clipped_count": winsorized_returns["clipped_count"],
        "n_trades": n_trades,
        "n_wins": n_wins,
        "n_losses": n_losses,
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(profit_factor, 3) if math.isfinite(profit_factor) else None,
        "avg_win_pct": round(avg_win_pct, 2),
        "avg_loss_pct": round(avg_loss_pct, 2),
        "avg_pnl_pct": round(avg_pnl_pct, 2),
        "avg_hold_days": round(avg_hold_days, 1),
        "total_gross_pnl_krw": total_gross_pnl,
        "total_net_pnl_krw": total_net_pnl,
        "total_buy_signals": total_buy_signals,
        "buy_signal_days": buy_signal_days,
        "total_buy_scored_candidates": total_scored_candidates,
        "buy_scored_candidate_days": scored_candidate_days,
        "total_executed_buys": total_executed_buys,
        "executed_buy_days": executed_buy_days,
        "buy_signal_to_execution_pct": round(buy_signal_to_execution_pct, 1),
        "scored_candidate_to_execution_pct": round(scored_candidate_to_execution_pct, 1),
        "avg_cash_pct": round(avg_cash_pct, 2),
        "avg_invested_pct": round(avg_invested_pct, 2),
        "sell_trigger_breakdown": trigger_counts,
        "trading_days": len(equity),
    }


# ── internal calculations ──────────────────────────────────────────────────

def _profit_factor(trades: list[ClosedTrade]) -> float:
    gross_profit = sum(t.net_pnl_krw for t in trades if t.net_pnl_krw > 0)
    gross_loss = abs(sum(t.net_pnl_krw for t in trades if t.net_pnl_krw < 0))
    if gross_loss == 0:
        # Pure-win streak → mathematically undefined (∞); no trades → 0.
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def _sharpe(
    equity: list[tuple[date, int]],
    initial: int,
    risk_free_annual_pct: float = 3.5,
) -> float:
    """Annualised Sharpe ratio using daily returns."""
    if len(equity) < 2:
        return 0.0

    values = [v for _, v in equity]
    daily_returns = [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] > 0
    ]
    if len(daily_returns) < 2:
        return 0.0

    n = len(daily_returns)
    mean_r = sum(daily_returns) / n
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / (n - 1)
    std_r = math.sqrt(variance)
    if std_r == 0:
        return 0.0

    rf_daily = (1 + risk_free_annual_pct / 100) ** (1 / 252) - 1
    return (mean_r - rf_daily) / std_r * math.sqrt(252)


def _max_drawdown(
    equity: list[tuple[date, int]],
) -> tuple[float, date | None, date | None]:
    """Return (max_drawdown_pct, peak_date, trough_date)."""
    if not equity:
        return 0.0, None, None

    peak_value = equity[0][1]
    peak_date = equity[0][0]
    mdd = 0.0
    mdd_start: date | None = None
    mdd_end: date | None = None

    for d, v in equity:
        if v > peak_value:
            peak_value = v
            peak_date = d
        if peak_value > 0:
            dd = (peak_value - v) / peak_value * 100
            if dd > mdd:
                mdd = dd
                mdd_start = peak_date
                mdd_end = d

    return mdd, mdd_start, mdd_end


def _sortino(
    equity: list[tuple[date, int]],
    initial: int,
    risk_free_annual_pct: float = 3.5,
) -> float:
    """Annualised Sortino ratio — uses downside deviation only."""
    if len(equity) < 2:
        return 0.0

    values = [v for _, v in equity]
    daily_returns = [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] > 0
    ]
    if len(daily_returns) < 2:
        return 0.0

    rf_daily = (1 + risk_free_annual_pct / 100) ** (1 / 252) - 1
    mean_r = sum(daily_returns) / len(daily_returns)
    downside = [min(r - rf_daily, 0.0) for r in daily_returns]
    downside_variance = sum(d ** 2 for d in downside) / len(downside)
    downside_std = math.sqrt(downside_variance)
    if downside_std == 0:
        return 0.0
    return (mean_r - rf_daily) / downside_std * math.sqrt(252)


def _calmar(cagr_pct: float, mdd_pct: float) -> float:
    """Calmar ratio = CAGR / MDD.  Returns 0 when MDD is zero."""
    if mdd_pct <= 0:
        return 0.0
    return cagr_pct / mdd_pct


def _recovery_factor(total_net_pnl_krw: int | float, mdd_pct: float, initial: int) -> float:
    """Recovery factor = total net P&L / max drawdown amount.

    Shows how many multiples of the deepest drawdown were recovered in profit.
    """
    if mdd_pct <= 0 or initial <= 0:
        return 0.0
    mdd_amount = initial * mdd_pct / 100.0
    return total_net_pnl_krw / mdd_amount


def _cagr(equity: list[tuple[date, int]], initial: int) -> float:
    """Compound annual growth rate."""
    if len(equity) < 2 or initial == 0:
        return 0.0
    start_date = equity[0][0]
    end_date = equity[-1][0]
    days = (end_date - start_date).days
    if days <= 0:
        return 0.0
    years = days / 365.25
    final = equity[-1][1]
    if final <= 0:
        return -100.0
    return ((final / initial) ** (1 / years) - 1) * 100
