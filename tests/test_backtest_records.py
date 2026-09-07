"""Tests for backtester.engine_backtest.records (R7-A1)."""
from __future__ import annotations

from datetime import date

import backtester.engine_backtest.runner as R
import backtester.engine_backtest.records as M


# ── pin tests ─────────────────────────────────────────────────────────────

def test_pin_day_record_same_object():
    assert R.DayRecord is M.DayRecord


def test_pin_backtest_result_same_object():
    assert R.BacktestResult is M.BacktestResult


# ── BacktestResult properties ──────────────────────────────────────────────

def test_backtest_result_total_return_pct_positive():
    """(initial 10_000_000, final 11_000_000) → 10.0 %."""
    result = M.BacktestResult(
        initial_cash=10_000_000,
        final_value=11_000_000,
        trade_log=[],
        daily_records=[],
        equity_curve=[],
    )
    assert result.total_return_pct == 10.0


def test_backtest_result_total_return_pct_zero_initial():
    """initial_cash=0 → 0.0 (no division by zero)."""
    result = M.BacktestResult(
        initial_cash=0,
        final_value=5_000_000,
        trade_log=[],
        daily_records=[],
        equity_curve=[],
    )
    assert result.total_return_pct == 0.0


def test_backtest_result_n_trades():
    """n_trades = len(trade_log) with 2 real ClosedTrade instances."""
    from backtester.engine_backtest.portfolio import ClosedTrade
    trade1 = ClosedTrade(
        symbol="005930",
        buy_date=date(2026, 1, 5),
        sell_date=date(2026, 1, 10),
        buy_price=70_000,
        sell_price=75_000,
        qty=10,
        gross_pnl_krw=50_000,
        net_pnl_krw=48_000,
        gross_pnl_pct=7.14,
        net_pnl_pct=6.86,
        hold_days=5,
        sell_trigger="take_profit",
    )
    trade2 = ClosedTrade(
        symbol="000660",
        buy_date=date(2026, 1, 12),
        sell_date=date(2026, 1, 20),
        buy_price=120_000,
        sell_price=118_000,
        qty=5,
        gross_pnl_krw=-10_000,
        net_pnl_krw=-12_000,
        gross_pnl_pct=-1.67,
        net_pnl_pct=-2.0,
        hold_days=8,
        sell_trigger="stop_loss",
    )
    result = M.BacktestResult(
        initial_cash=10_000_000,
        final_value=10_036_000,
        trade_log=[trade1, trade2],
        daily_records=[],
        equity_curve=[],
    )
    assert result.n_trades == 2


# ── DayRecord.to_dict — filled lists/dicts ────────────────────────────────

def test_day_record_to_dict_filled():
    """DayRecord with list/dict fields filled — to_dict equality."""
    dr = M.DayRecord(
        date=date(2026, 3, 15),
        portfolio_value=12_000_000,
        cash=3_000_000,
        buy_symbol="005930",
        buy_price=70_000,
        buy_qty=10,
        buy_reason="gap_down_open",
        sell_symbol="000660",
        sell_price=120_000,
        sell_qty=5,
        sell_trigger="take_profit",
        buy_signal_count=3,
        buy_scored_candidate_count=2,
        buy_candidate_symbols=["005930", "000660"],
        buy_selected_score=0.72,
        sell_evaluated_count=4,
        sell_triggered_count=1,
        sell_triggered_symbols=["000660"],
        skipped_symbols=["035720"],
        no_data_symbols=["251270"],
        buy_rule_names=["gap_down_open", "rebound_from_low"],
        buy_rule_enabled_counts={"gap_down_open": 1, "rebound_from_low": 1},
        buy_rule_pass_counts={"gap_down_open": 1, "rebound_from_low": 0},
        buy_rejection_reason_counts={"score_below_min": 1},
        buy_funnel={"total": 5, "rule_blocked": 2},
        buy_capacity={"max_positions": 3, "available_slots": 1},
        buy_score_stats={"mean": 0.55, "max": 0.72},
        buy_sizing={"recommended_qty": 10, "block_reason": None},
    )
    result = dr.to_dict()
    expected = {
        "date": "2026-03-15",
        "portfolio_value": 12_000_000,
        "cash": 3_000_000,
        "buy_symbol": "005930",
        "buy_price": 70_000,
        "buy_qty": 10,
        "buy_reason": "gap_down_open",
        "sell_symbol": "000660",
        "sell_price": 120_000,
        "sell_qty": 5,
        "sell_trigger": "take_profit",
        "buy_signal_count": 3,
        "buy_scored_candidate_count": 2,
        "buy_candidate_symbols": ["005930", "000660"],
        "buy_selected_score": 0.72,
        "sell_evaluated_count": 4,
        "sell_triggered_count": 1,
        "sell_triggered_symbols": ["000660"],
        "skipped_count": 1,
        "no_data_count": 1,
        "buy_rule_names": ["gap_down_open", "rebound_from_low"],
        "buy_rule_enabled_counts": {"gap_down_open": 1, "rebound_from_low": 1},
        "buy_rule_pass_counts": {"gap_down_open": 1, "rebound_from_low": 0},
        "buy_rejection_reason_counts": {"score_below_min": 1},
        "buy_funnel": {"total": 5, "rule_blocked": 2},
        "buy_capacity": {"max_positions": 3, "available_slots": 1},
        "buy_score_stats": {"mean": 0.55, "max": 0.72},
        "buy_sizing": {"recommended_qty": 10, "block_reason": None},
    }
    assert result == expected


# ── DayRecord.to_dict — default fields ────────────────────────────────────

def test_day_record_to_dict_defaults():
    """DayRecord with only required fields — all defaults match."""
    dr = M.DayRecord(date=date(2026, 1, 5), portfolio_value=1_000_000, cash=500_000)
    result = dr.to_dict()
    expected = {
        "date": "2026-01-05",
        "portfolio_value": 1_000_000,
        "cash": 500_000,
        "buy_symbol": None,
        "buy_price": None,
        "buy_qty": None,
        "buy_reason": None,
        "sell_symbol": None,
        "sell_price": None,
        "sell_qty": None,
        "sell_trigger": None,
        "buy_signal_count": 0,
        "buy_scored_candidate_count": 0,
        "buy_candidate_symbols": [],
        "buy_selected_score": None,
        "sell_evaluated_count": 0,
        "sell_triggered_count": 0,
        "sell_triggered_symbols": [],
        "skipped_count": 0,
        "no_data_count": 0,
        "buy_rule_names": [],
        "buy_rule_enabled_counts": {},
        "buy_rule_pass_counts": {},
        "buy_rejection_reason_counts": {},
        "buy_funnel": {},
        "buy_capacity": {},
        "buy_score_stats": {},
        "buy_sizing": {},
    }
    assert result == expected
