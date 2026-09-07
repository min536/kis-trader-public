"""Unit tests for backtester/engine_backtest/metrics.py"""
from __future__ import annotations

import math
import unittest
from datetime import date, timedelta

from backtester.engine_backtest.metrics import (
    _calmar,
    _cagr,
    _max_drawdown,
    _profit_factor,
    _recovery_factor,
    _sharpe,
    _sortino,
    compute_metrics,
)
from backtester.engine_backtest.portfolio import ClosedTrade
from backtester.engine_backtest.runner import BacktestResult, DayRecord


# ── helpers ────────────────────────────────────────────────────────────────

def _make_trade(
    net_pnl_krw: int,
    gross_pnl_krw: int | None = None,
    net_pnl_pct: float | None = None,
    gross_pnl_pct: float = 0.0,
    hold_days: int = 5,
    sell_trigger: str = "take_profit",
) -> ClosedTrade:
    if gross_pnl_krw is None:
        gross_pnl_krw = net_pnl_krw
    if net_pnl_pct is None:
        net_pnl_pct = float(net_pnl_krw) / 1_000_000 * 100  # dummy
    return ClosedTrade(
        symbol="AAA",
        buy_date=date(2026, 1, 1),
        sell_date=date(2026, 1, 1) + timedelta(days=hold_days),
        buy_price=10_000,
        sell_price=10_000,
        qty=100,
        gross_pnl_krw=gross_pnl_krw,
        net_pnl_krw=net_pnl_krw,
        gross_pnl_pct=gross_pnl_pct,
        net_pnl_pct=net_pnl_pct,
        hold_days=hold_days,
        sell_trigger=sell_trigger,
    )


def _make_equity(values: list[int], start: date | None = None) -> list[tuple[date, int]]:
    if start is None:
        start = date(2026, 1, 2)
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def _make_result(
    initial_cash: int,
    equity: list[tuple[date, int]],
    trades: list[ClosedTrade],
) -> BacktestResult:
    final = equity[-1][1] if equity else initial_cash
    return BacktestResult(
        initial_cash=initial_cash,
        final_value=final,
        trade_log=trades,
        daily_records=[],
        equity_curve=equity,
    )


# ── _profit_factor ──────────────────────────────────────────────────────────

class ProfitFactorTests(unittest.TestCase):
    def test_basic(self) -> None:
        trades = [_make_trade(300_000), _make_trade(-100_000)]
        self.assertAlmostEqual(_profit_factor(trades), 3.0)

    def test_no_losing_trades_returns_inf(self) -> None:
        trades = [_make_trade(100_000), _make_trade(200_000)]
        self.assertEqual(_profit_factor(trades), float("inf"))

    def test_all_losing_returns_zero(self) -> None:
        trades = [_make_trade(-100_000), _make_trade(-200_000)]
        self.assertAlmostEqual(_profit_factor(trades), 0.0)

    def test_empty_trades_returns_zero(self) -> None:
        self.assertEqual(_profit_factor([]), 0.0)


# ── _sharpe ────────────────────────────────────────────────────────────────

class SharpeTests(unittest.TestCase):
    def test_flat_equity_returns_zero(self) -> None:
        equity = _make_equity([10_000_000] * 50)
        self.assertEqual(_sharpe(equity, 10_000_000), 0.0)

    def test_single_point_equity_returns_zero(self) -> None:
        equity = _make_equity([10_000_000])
        self.assertEqual(_sharpe(equity, 10_000_000), 0.0)

    def test_positive_trend_has_positive_sharpe(self) -> None:
        # Steady 0.1% daily gains for 100 days
        values = [10_000_000]
        for _ in range(99):
            values.append(int(values[-1] * 1.001))
        equity = _make_equity(values)
        sharpe = _sharpe(equity, 10_000_000)
        self.assertGreater(sharpe, 0)

    def test_volatile_down_equity_has_negative_sharpe(self) -> None:
        import random
        random.seed(42)
        values = [10_000_000]
        for _ in range(99):
            values.append(max(1, int(values[-1] * (1 - random.uniform(0.005, 0.02)))))
        equity = _make_equity(values)
        sharpe = _sharpe(equity, 10_000_000)
        self.assertLess(sharpe, 0)


# ── _sortino ────────────────────────────────────────────────────────────────

class SortinoTests(unittest.TestCase):
    def test_flat_equity_is_negative_vs_risk_free(self) -> None:
        """0% daily return < risk-free daily → all below threshold → negative Sortino."""
        equity = _make_equity([10_000_000] * 50)
        result = _sortino(equity, 10_000_000)
        self.assertLess(result, 0.0)

    def test_single_point_returns_zero(self) -> None:
        self.assertEqual(_sortino(_make_equity([10_000_000]), 10_000_000), 0.0)

    def test_sortino_higher_than_sharpe_for_asymmetric_returns(self) -> None:
        # Strategy with large upswings and small downswings → sortino > sharpe
        values = [10_000_000]
        for i in range(99):
            if i % 3 == 0:
                values.append(int(values[-1] * 0.999))   # small loss
            else:
                values.append(int(values[-1] * 1.005))   # big gains
        equity = _make_equity(values)
        sharpe = _sharpe(equity, 10_000_000)
        sortino = _sortino(equity, 10_000_000)
        self.assertGreater(sortino, sharpe)

    def test_no_downside_returns_zero(self) -> None:
        """When all daily returns exceed risk-free, downside std = 0 → returns 0."""
        # 0.5% daily >> 3.5% annual → no downside at all
        values = [10_000_000]
        for _ in range(99):
            values.append(int(values[-1] * 1.005))
        equity = _make_equity(values)
        self.assertEqual(_sortino(equity, 10_000_000), 0.0)


# ── _max_drawdown ────────────────────────────────────────────────────────────

class MaxDrawdownTests(unittest.TestCase):
    def test_empty_equity(self) -> None:
        mdd, s, e = _max_drawdown([])
        self.assertEqual(mdd, 0.0)
        self.assertIsNone(s)
        self.assertIsNone(e)

    def test_no_drawdown_when_equity_always_rises(self) -> None:
        equity = _make_equity([10_000_000, 11_000_000, 12_000_000])
        mdd, _, _ = _max_drawdown(equity)
        self.assertAlmostEqual(mdd, 0.0)

    def test_drawdown_calculated_correctly(self) -> None:
        # Peak 12M, trough 9M → MDD = (12M - 9M) / 12M = 25%
        equity = _make_equity([10_000_000, 12_000_000, 9_000_000, 11_000_000])
        mdd, mdd_start, mdd_end = _max_drawdown(equity)
        self.assertAlmostEqual(mdd, 25.0, places=1)
        self.assertEqual(mdd_start, equity[1][0])  # peak at 12M
        self.assertEqual(mdd_end, equity[2][0])    # trough at 9M

    def test_mdd_picks_worst_drawdown_across_multiple_dips(self) -> None:
        # Two dips: -10% then -20% from a new peak
        equity = _make_equity([
            10_000_000, 9_000_000,   # -10%
            11_000_000, 8_800_000,   # -20%
        ])
        mdd, _, _ = _max_drawdown(equity)
        self.assertAlmostEqual(mdd, 20.0, places=1)


# ── _cagr ────────────────────────────────────────────────────────────────────

class CAGRTests(unittest.TestCase):
    def test_zero_days_returns_zero(self) -> None:
        d = date(2026, 1, 1)
        equity = [(d, 11_000_000), (d, 11_000_000)]
        self.assertEqual(_cagr(equity, 10_000_000), 0.0)

    def test_single_year_doubles_returns_100_pct(self) -> None:
        start = date(2025, 1, 1)
        end = date(2026, 1, 1)
        equity = [(start, 10_000_000), (end, 20_000_000)]
        cagr = _cagr(equity, 10_000_000)
        self.assertAlmostEqual(cagr, 100.0, delta=1.0)

    def test_zero_initial_returns_zero(self) -> None:
        equity = _make_equity([0, 1_000_000])
        self.assertEqual(_cagr(equity, 0), 0.0)

    def test_negative_final_value_returns_negative_100(self) -> None:
        start = date(2025, 1, 1)
        end = date(2026, 1, 1)
        equity = [(start, 10_000_000), (end, 0)]
        self.assertEqual(_cagr(equity, 10_000_000), -100.0)


# ── _calmar ────────────────────────────────────────────────────────────────

class CalmarTests(unittest.TestCase):
    def test_basic(self) -> None:
        # CAGR 20%, MDD 10% → Calmar = 2.0
        self.assertAlmostEqual(_calmar(20.0, 10.0), 2.0)

    def test_zero_mdd_returns_zero(self) -> None:
        self.assertEqual(_calmar(20.0, 0.0), 0.0)

    def test_negative_cagr(self) -> None:
        self.assertAlmostEqual(_calmar(-10.0, 20.0), -0.5)


# ── _recovery_factor ────────────────────────────────────────────────────────

class RecoveryFactorTests(unittest.TestCase):
    def test_basic(self) -> None:
        # MDD 10% of 10M = 1M.  Net P&L 2M → recovery = 2.0
        self.assertAlmostEqual(_recovery_factor(2_000_000, 10.0, 10_000_000), 2.0)

    def test_zero_mdd_returns_zero(self) -> None:
        self.assertEqual(_recovery_factor(1_000_000, 0.0, 10_000_000), 0.0)

    def test_zero_initial_returns_zero(self) -> None:
        self.assertEqual(_recovery_factor(1_000_000, 10.0, 0), 0.0)

    def test_negative_pnl(self) -> None:
        self.assertAlmostEqual(_recovery_factor(-500_000, 10.0, 10_000_000), -0.5)


# ── compute_metrics integration ─────────────────────────────────────────────

class ComputeMetricsTests(unittest.TestCase):
    def _result(
        self,
        initial: int = 10_000_000,
        final: int | None = None,
        n_up: int = 3,
        n_down: int = 1,
    ) -> BacktestResult:
        trades = (
            [_make_trade(200_000, net_pnl_pct=2.0)] * n_up
            + [_make_trade(-100_000, net_pnl_pct=-1.0, sell_trigger="stop_loss")] * n_down
        )
        if final is None:
            final = initial + sum(t.net_pnl_krw for t in trades)
        equity = _make_equity([initial] * 50 + [final])
        return _make_result(initial, equity, trades)

    def test_new_keys_present(self) -> None:
        metrics = compute_metrics(self._result())
        for key in (
            "sortino_ratio",
            "calmar_ratio",
            "recovery_factor",
            "avg_win_pct",
            "avg_loss_pct",
            "current_drawdown_pct",
            "rolling_volatility_20d_pct",
            "winsorized_return_clipped_count",
        ):
            self.assertIn(key, metrics)

    def test_robust_risk_summary_values_match_equity_curve(self) -> None:
        equity = _make_equity([10_000_000, 12_000_000, 9_000_000, 10_800_000])
        metrics = compute_metrics(_make_result(10_000_000, equity, []))

        self.assertAlmostEqual(metrics["current_drawdown_pct"], -10.0)
        self.assertIsNone(metrics["rolling_volatility_20d_pct"])
        self.assertGreaterEqual(metrics["winsorized_return_clipped_count"], 0)

    def test_win_rate_correct(self) -> None:
        metrics = compute_metrics(self._result(n_up=3, n_down=1))
        self.assertAlmostEqual(metrics["win_rate_pct"], 75.0)

    def test_sell_trigger_breakdown(self) -> None:
        metrics = compute_metrics(self._result(n_up=2, n_down=2))
        breakdown = metrics["sell_trigger_breakdown"]
        self.assertEqual(breakdown.get("take_profit"), 2)
        self.assertEqual(breakdown.get("stop_loss"), 2)

    def test_no_trades(self) -> None:
        equity = _make_equity([10_000_000] * 30)
        result = _make_result(10_000_000, equity, [])
        metrics = compute_metrics(result)
        self.assertEqual(metrics["n_trades"], 0)
        self.assertEqual(metrics["win_rate_pct"], 0.0)
        self.assertEqual(metrics["profit_factor"], 0.0)

    def test_avg_win_and_loss_pct(self) -> None:
        trades = [
            _make_trade(100_000, net_pnl_pct=1.0),
            _make_trade(300_000, net_pnl_pct=3.0),
            _make_trade(-200_000, net_pnl_pct=-2.0, sell_trigger="stop_loss"),
        ]
        equity = _make_equity([10_000_000, 10_200_000])
        result = _make_result(10_000_000, equity, trades)
        metrics = compute_metrics(result)
        self.assertAlmostEqual(metrics["avg_win_pct"], 2.0)
        self.assertAlmostEqual(metrics["avg_loss_pct"], -2.0)

    def test_total_return_pct(self) -> None:
        equity = _make_equity([10_000_000, 11_000_000])
        result = _make_result(10_000_000, equity, [])
        metrics = compute_metrics(result)
        self.assertAlmostEqual(metrics["total_return_pct"], 10.0)

    def test_buy_activity_metrics_present(self) -> None:
        equity = _make_equity([10_000_000, 10_200_000])
        result = BacktestResult(
            initial_cash=10_000_000,
            final_value=10_200_000,
            trade_log=[],
            daily_records=[
                DayRecord(
                    date=equity[0][0],
                    portfolio_value=10_000_000,
                    cash=7_000_000,
                    buy_signal_count=2,
                    buy_scored_candidate_count=1,
                    buy_symbol="AAA",
                ),
                DayRecord(
                    date=equity[1][0],
                    portfolio_value=10_200_000,
                    cash=8_000_000,
                    buy_signal_count=1,
                    buy_scored_candidate_count=0,
                ),
            ],
            equity_curve=equity,
        )

        metrics = compute_metrics(result)

        self.assertEqual(metrics["total_buy_signals"], 3)
        self.assertEqual(metrics["buy_signal_days"], 2)
        self.assertEqual(metrics["total_buy_scored_candidates"], 1)
        self.assertEqual(metrics["total_executed_buys"], 1)
        self.assertAlmostEqual(metrics["buy_signal_to_execution_pct"], 33.3, places=1)
        self.assertAlmostEqual(metrics["avg_cash_pct"], 74.22, places=2)
        self.assertAlmostEqual(metrics["avg_invested_pct"], 25.78, places=2)


if __name__ == "__main__":
    unittest.main()
