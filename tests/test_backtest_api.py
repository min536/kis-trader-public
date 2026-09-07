"""M1 native backtest entry point — docs/backtester_redesign_20260706.md §M1."""

from __future__ import annotations

from types import SimpleNamespace

from app.gate2.schema import default_artifact
from app.research.replay import backtest_api
from app.research.replay.backtest_api import (
    BacktestResult,
    run_backtest,
    sim_cost_params_from_settings,
)
from app.research.replay.backtest_api import _result_from_report
from app.research.replay.broker_sim import SimCostParams
from app.research.replay.sim_runner import (
    SimReplayReport,
    SimTradeRecord,
    run_portfolio_replay,
)

_ZERO_COSTS = SimCostParams(
    buy_fee_bps=0.0,
    sell_fee_bps=0.0,
    sell_tax_bps=0.0,
    buy_slippage_bps=0.0,
    sell_slippage_bps=0.0,
)


def test_sim_cost_params_from_settings_maps_all_five_bps() -> None:
    settings = SimpleNamespace(
        buy_fee_bps=1.5,
        sell_fee_bps=1.5,
        sell_tax_bps=15.0,
        buy_slippage_bps=5.0,
        sell_slippage_bps=5.0,
    )

    costs = sim_cost_params_from_settings(settings)

    assert costs == SimCostParams(
        buy_fee_bps=1.5,
        sell_fee_bps=1.5,
        sell_tax_bps=15.0,
        buy_slippage_bps=5.0,
        sell_slippage_bps=5.0,
    )


def test_sim_cost_params_tolerates_missing_fields() -> None:
    costs = sim_cost_params_from_settings(SimpleNamespace())

    assert costs == SimCostParams(0.0, 0.0, 0.0, 0.0, 0.0)


def test_run_backtest_empty_window_is_flat_and_faithful_to_report() -> None:
    """An empty date window never touches provider/settings; the result must
    normalize the underlying SimReplayReport exactly."""
    artifact = default_artifact()
    reference = run_portfolio_replay(
        artifact=artifact,
        provider=None,
        dates=[],
        settings=None,
        costs=_ZERO_COSTS,
        initial_cash=1_000_000.0,
    )

    result = run_backtest(
        artifact=artifact,
        provider=None,
        dates=[],
        settings=None,
        initial_capital=1_000_000.0,
        costs=_ZERO_COSTS,
    )

    assert isinstance(result, BacktestResult)
    assert result.day_count == 0
    assert result.trade_count == 0
    assert result.initial_capital == 1_000_000.0
    assert result.final_equity == reference.final_equity
    assert result.total_return_pct == reference.total_return_pct
    assert result.report == reference.to_dict()


def test_result_normalizes_a_report_with_trades_and_equity_curve() -> None:
    """Closes the coverage boundary the empty-window test leaves: a NON-empty
    report (trades + a multi-point equity curve) must normalize field-for-field."""
    report = SimReplayReport(
        total_return_pct=9.0,
        mdd_pct=-3.5,
        trade_count=2,
        win_rate=0.5,
        equity_curve=[("2026-01-02", 1_050_000.0), ("2026-01-03", 1_090_000.0)],
        per_symbol_pnl_top=[("005930", 40_000.0)],
        per_symbol_pnl_bottom=[("000660", -5_000.0)],
        initial_cash=1_000_000.0,
        final_equity=1_090_000.0,
        trades=(
            SimTradeRecord("005930", "BUY", "2026-01-02T09:29:00", 10, 69_500.0, "buy"),
            SimTradeRecord("005930", "SELL", "2026-01-03T10:00:00", 10, 73_000.0, "take_profit", pnl=35_000.0),
        ),
    )

    result = _result_from_report(report, initial_capital=1_000_000.0, day_count=2)

    assert result.final_equity == 1_090_000.0
    assert result.total_return_pct == 9.0
    assert result.mdd_pct == -3.5
    assert result.trade_count == 2
    assert result.win_rate == 0.5
    assert result.day_count == 2
    assert result.equity_curve == (
        ("2026-01-02", 1_050_000.0),
        ("2026-01-03", 1_090_000.0),
    )
    assert result.report == report.to_dict()
    assert len(result.report["trades"]) == 2


def test_run_backtest_defaults_costs_from_settings(monkeypatch) -> None:
    """When ``costs`` is omitted, the live cost policy must be derived from
    settings and passed to the replay engine."""
    captured: dict = {}

    def fake_replay(**kwargs):
        captured.update(kwargs)
        return run_portfolio_replay(**kwargs)

    monkeypatch.setattr(backtest_api, "run_portfolio_replay", fake_replay)

    settings = SimpleNamespace(
        buy_fee_bps=2.0,
        sell_fee_bps=2.0,
        sell_tax_bps=18.0,
        buy_slippage_bps=6.0,
        sell_slippage_bps=6.0,
    )

    run_backtest(
        artifact=default_artifact(),
        provider=None,
        dates=[],
        settings=settings,
        initial_capital=500_000.0,
    )

    assert captured["costs"] == sim_cost_params_from_settings(settings)
    assert captured["initial_cash"] == 500_000.0


def test_run_backtest_forwards_on_day_end_and_should_stop_conditionally(monkeypatch) -> None:
    """S2 propagation (bonus coverage): on_day_end/should_stop are new kw-only
    pass-throughs to run_portfolio_replay, forwarded ONLY when not None — a
    call that omits both (the shape run_gate2_weight_search.py's existing
    _stage_b_replay call site uses) must not even pass them as explicit None."""
    captured_with: dict = {}
    captured_without: dict = {}

    def fake_replay_with(**kwargs):
        captured_with.update(kwargs)
        return run_portfolio_replay(**kwargs)

    def _on_day_end(day_index, day, equity):
        pass

    def _should_stop():
        return False

    monkeypatch.setattr(backtest_api, "run_portfolio_replay", fake_replay_with)
    run_backtest(
        artifact=default_artifact(),
        provider=None,
        dates=[],
        settings=None,
        initial_capital=500_000.0,
        costs=_ZERO_COSTS,
        on_day_end=_on_day_end,
        should_stop=_should_stop,
    )
    assert captured_with["on_day_end"] is _on_day_end
    assert captured_with["should_stop"] is _should_stop

    def fake_replay_without(**kwargs):
        captured_without.update(kwargs)
        return run_portfolio_replay(**kwargs)

    monkeypatch.setattr(backtest_api, "run_portfolio_replay", fake_replay_without)
    run_backtest(
        artifact=default_artifact(),
        provider=None,
        dates=[],
        settings=None,
        initial_capital=500_000.0,
        costs=_ZERO_COSTS,
    )
    assert "on_day_end" not in captured_without
    assert "should_stop" not in captured_without


def test_result_maps_partial_and_completed_days_from_report() -> None:
    """BacktestResult.partial/completed_days must mirror the underlying
    SimReplayReport's new S2 fields."""
    report = SimReplayReport(
        total_return_pct=0.0,
        mdd_pct=0.0,
        trade_count=0,
        win_rate=0.0,
        equity_curve=[("2026-01-02", 1_000_000.0)],
        per_symbol_pnl_top=[],
        per_symbol_pnl_bottom=[],
        initial_cash=1_000_000.0,
        final_equity=1_000_000.0,
        partial=True,
        completed_days=1,
        requested_days=2,
    )

    result = _result_from_report(report, initial_capital=1_000_000.0, day_count=2)

    assert result.partial is True
    assert result.completed_days == 1
