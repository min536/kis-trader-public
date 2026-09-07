"""M1 — native backtest entry point (docs/backtester_redesign_20260706.md §M1).

A library call that replaces the sidecar ``open-trading-api`` REST boundary
(``:8002 /api/backtest/run-custom``). It delegates to the proven native replay
``app.research.replay.sim_runner.run_portfolio_replay`` — the SAME production
scan + sell logic the live engine runs — so a backtest and the live engine can
never diverge by re-implementation (the sidecar re-implemented the strategy in a
Lean DSL, which is exactly the drift the redesign retires).

No HTTP server, no Lean, no broker calls. ``provider``/``settings`` are injected
so the caller (autotuner, shadow_watch) controls the data window; ``costs``
default to the live cost policy so a backtest costs trades exactly as the live
engine does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.research.replay.broker_sim import SimCostParams
from app.research.replay.sim_runner import run_portfolio_replay


@dataclass(frozen=True)
class BacktestResult:
    """Normalized backtest outcome — the native replacement for the sidecar's
    ``run-custom`` JSON response. ``report`` carries the full
    ``SimReplayReport.to_dict()`` for callers that need trade-level detail."""

    initial_capital: float
    final_equity: float
    total_return_pct: float
    mdd_pct: float
    trade_count: int
    win_rate: float
    day_count: int
    equity_curve: tuple[tuple[str, float], ...]
    report: dict
    partial: bool = False
    completed_days: int | None = None


def sim_cost_params_from_settings(settings: Any) -> SimCostParams:
    """Map the live cost policy (settings bps) into replay ``SimCostParams``.

    This is the canonical cost bridge the redesign targets — a backtest and the
    live engine cost trades from the SAME bps, replacing the sidecar's
    ``LEGACY_BACKTEST_COST_PARAMS`` (``app.core.costs``). Missing/blank fields
    resolve to 0.0 so a partial settings double never raises.
    """

    def _bps(name: str) -> float:
        try:
            return float(getattr(settings, name, 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    return SimCostParams(
        buy_fee_bps=_bps("buy_fee_bps"),
        sell_fee_bps=_bps("sell_fee_bps"),
        sell_tax_bps=_bps("sell_tax_bps"),
        buy_slippage_bps=_bps("buy_slippage_bps"),
        sell_slippage_bps=_bps("sell_slippage_bps"),
    )


def run_backtest(
    *,
    artifact: Any,
    provider: Any,
    dates: Any,
    settings: Any,
    initial_capital: float,
    costs: SimCostParams | None = None,
    intrabar_mode: str = "none",
    on_day_end=None,
    should_stop=None,
) -> BacktestResult:
    """Run a native minute-replay backtest and return a normalized result.

    Delegates to ``run_portfolio_replay`` and normalizes its ``SimReplayReport``.
    ``costs`` defaults to ``sim_cost_params_from_settings(settings)`` when omitted
    so the backtest uses the live cost policy. ``on_day_end``/``should_stop``
    (S2) are forwarded to ``run_portfolio_replay`` only when not ``None``, so an
    omitted call keeps the exact pre-S2 call shape (patch-surface: the
    ``fake_replay`` stub in tests/test_backtest_api.py; the unmodified
    run_gate2_weight_search.py call site).
    """
    materialized_dates = list(dates)
    resolved_costs = (
        costs if costs is not None else sim_cost_params_from_settings(settings)
    )
    optional_kwargs: dict = {}
    if on_day_end is not None:
        optional_kwargs["on_day_end"] = on_day_end
    if should_stop is not None:
        optional_kwargs["should_stop"] = should_stop
    report = run_portfolio_replay(
        artifact=artifact,
        provider=provider,
        dates=materialized_dates,
        settings=settings,
        costs=resolved_costs,
        initial_cash=float(initial_capital),
        intrabar_mode=intrabar_mode,
        **optional_kwargs,
    )
    return _result_from_report(
        report,
        initial_capital=float(initial_capital),
        day_count=len(materialized_dates),
    )


def _result_from_report(
    report: Any, *, initial_capital: float, day_count: int
) -> BacktestResult:
    equity_curve = tuple(
        (str(day), float(equity))
        for day, equity in getattr(report, "equity_curve", ()) or ()
    )
    return BacktestResult(
        initial_capital=float(initial_capital),
        final_equity=float(getattr(report, "final_equity", 0.0) or 0.0),
        total_return_pct=float(getattr(report, "total_return_pct", 0.0) or 0.0),
        mdd_pct=float(getattr(report, "mdd_pct", 0.0) or 0.0),
        trade_count=int(getattr(report, "trade_count", 0) or 0),
        win_rate=float(getattr(report, "win_rate", 0.0) or 0.0),
        day_count=int(day_count),
        equity_curve=equity_curve,
        report=report.to_dict() if hasattr(report, "to_dict") else {},
        partial=bool(getattr(report, "partial", False)),
        completed_days=getattr(report, "completed_days", None),
    )
