"""Tests for backtester.engine_backtest.sampling (R7-A2)."""
from __future__ import annotations

import types
from datetime import date

import backtester.engine_backtest.runner as R
import backtester.engine_backtest.sampling as M


# ── pin tests ─────────────────────────────────────────────────────────────

def test_pin_collect_buy_pass_samples_same_object():
    assert R.collect_buy_pass_samples is M.collect_buy_pass_samples


def test_pin_max_positions_same_object():
    assert R._max_positions is M._max_positions


# ── _max_positions direct assertions ─────────────────────────────────────

def test_max_positions_exposure_50pct_gives_2():
    """exposure_pct=50 → 100/50 = 2."""
    settings = types.SimpleNamespace(
        buy_max_account_exposure_pct=50.0,
        buy_max_budget_per_trade_krw=0,
    )
    assert M._max_positions(settings) == 2


def test_max_positions_budget_tighter():
    """Budget limit tighter: 10M portfolio / 4M budget = 2, exposure gives 10."""
    settings = types.SimpleNamespace(
        buy_max_account_exposure_pct=10.0,
        buy_max_budget_per_trade_krw=4_000_000,
    )
    assert M._max_positions(settings, portfolio_value=10_000_000) == 2


def test_max_positions_exposure_tighter():
    """Exposure limit tighter: 10M / 2M budget = 5, 50% exposure gives 2."""
    settings = types.SimpleNamespace(
        buy_max_account_exposure_pct=50.0,
        buy_max_budget_per_trade_krw=2_000_000,
    )
    assert M._max_positions(settings, portfolio_value=10_000_000) == 2


def test_max_positions_zero_exposure_gives_10():
    """exposure_pct=0 → fallback to 10."""
    settings = types.SimpleNamespace(
        buy_max_account_exposure_pct=0,
        buy_max_budget_per_trade_krw=0,
    )
    assert M._max_positions(settings) == 10


def test_max_positions_portfolio_none_uses_exposure_only():
    """portfolio_value=None → skip budget limit, only exposure applies."""
    settings = types.SimpleNamespace(
        buy_max_account_exposure_pct=25.0,
        buy_max_budget_per_trade_krw=1_000_000,
    )
    assert M._max_positions(settings, portfolio_value=None) == 4


# ── collect_buy_pass_samples — early exit branches ────────────────────────

def test_collect_buy_pass_samples_positions_full_returns_empty():
    """positions_before_buy >= max_positions → empty list returned immediately."""
    from backtester.engine_backtest.data_provider import BacktestDataProvider
    from backtester.engine_backtest.portfolio import BacktestPortfolio

    # 50% exposure → max_positions=2; seed 2 positions to fill slots
    settings = types.SimpleNamespace(
        buy_min_score=0.5,
        qty=10,
        buy_max_account_exposure_pct=50.0,
        buy_max_budget_per_trade_krw=0,
    )
    portfolio = BacktestPortfolio(initial_cash=10_000_000)
    portfolio.seed_position(symbol="AAA", qty=10, avg_cost=1000, buy_date=date(2026, 1, 2))
    portfolio.seed_position(symbol="BBB", qty=10, avg_cost=1000, buy_date=date(2026, 1, 2))
    provider = BacktestDataProvider.from_records([])
    state = {"symbols_bought_today": set()}
    result = M.collect_buy_pass_samples(
        portfolio=portfolio,
        data_provider=provider,
        symbols=["CCC"],
        settings=settings,
        state=state,
        trading_date=date(2026, 1, 5),
        prices={"AAA": 1000, "BBB": 1000},
    )
    assert result == []


def test_collect_buy_pass_samples_already_bought_today():
    """Symbol in symbols_bought_today → stage=already_bought_today sample."""
    from backtester.engine_backtest.data_provider import BacktestDataProvider
    from backtester.engine_backtest.portfolio import BacktestPortfolio

    settings = types.SimpleNamespace(
        buy_min_score=0.5,
        qty=10,
        buy_max_account_exposure_pct=50.0,
        buy_max_budget_per_trade_krw=0,
    )
    portfolio = BacktestPortfolio(initial_cash=10_000_000)
    provider = BacktestDataProvider.from_records([])
    state = {"symbols_bought_today": {"SYM001"}}
    result = M.collect_buy_pass_samples(
        portfolio=portfolio,
        data_provider=provider,
        symbols=["SYM001"],
        settings=settings,
        state=state,
        trading_date=date(2026, 1, 5),
        prices={},
    )
    expected = [
        {
            "date": "2026-01-05",
            "symbol": "SYM001",
            "stage": "already_bought_today",
            "stage_reason": "already_bought_today",
            "positions_before_buy_pass": 0,
            "max_positions": 2,
            "available_slots_before_buy_pass": 2,
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
            "min_score_threshold": 0.5,
            "score_components": {},
            "candidate_rank": None,
            "selected_candidate": False,
            "executed": False,
            "selected_buy_symbol_for_day": None,
            "selected_buy_score_for_day": None,
            "sizing_block_reason": None,
            "sizing_recommended_qty": None,
        }
    ]
    assert result == expected


def test_collect_buy_pass_samples_already_holding():
    """Symbol in portfolio.positions → stage=already_holding sample."""
    from backtester.engine_backtest.data_provider import BacktestDataProvider
    from backtester.engine_backtest.portfolio import BacktestPortfolio

    settings = types.SimpleNamespace(
        buy_min_score=0.5,
        qty=10,
        buy_max_account_exposure_pct=50.0,
        buy_max_budget_per_trade_krw=0,
    )
    portfolio = BacktestPortfolio(initial_cash=10_000_000)
    portfolio.seed_position(symbol="SYM002", qty=5, avg_cost=2000, buy_date=date(2026, 1, 2))
    provider = BacktestDataProvider.from_records([])
    state = {"symbols_bought_today": set()}
    result = M.collect_buy_pass_samples(
        portfolio=portfolio,
        data_provider=provider,
        symbols=["SYM002"],
        settings=settings,
        state=state,
        trading_date=date(2026, 1, 5),
        prices={"SYM002": 2000},
    )
    expected = [
        {
            "date": "2026-01-05",
            "symbol": "SYM002",
            "stage": "already_holding",
            "stage_reason": "already_holding",
            "positions_before_buy_pass": 1,
            "max_positions": 2,
            "available_slots_before_buy_pass": 1,
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
            "min_score_threshold": 0.5,
            "score_components": {},
            "candidate_rank": None,
            "selected_candidate": False,
            "executed": False,
            "selected_buy_symbol_for_day": None,
            "selected_buy_score_for_day": None,
            "sizing_block_reason": None,
            "sizing_recommended_qty": None,
        }
    ]
    assert result == expected


def test_collect_buy_pass_samples_missing_snapshot():
    """Symbol with no snapshot → stage=missing_snapshot sample."""
    from backtester.engine_backtest.data_provider import BacktestDataProvider
    from backtester.engine_backtest.portfolio import BacktestPortfolio

    settings = types.SimpleNamespace(
        buy_min_score=0.5,
        qty=10,
        buy_max_account_exposure_pct=50.0,
        buy_max_budget_per_trade_krw=0,
    )
    portfolio = BacktestPortfolio(initial_cash=10_000_000)
    provider = BacktestDataProvider.from_records([])
    state = {"symbols_bought_today": set()}
    result = M.collect_buy_pass_samples(
        portfolio=portfolio,
        data_provider=provider,
        symbols=["NOSYM"],
        settings=settings,
        state=state,
        trading_date=date(2026, 1, 5),
        prices={},
    )
    expected = [
        {
            "date": "2026-01-05",
            "symbol": "NOSYM",
            "stage": "missing_snapshot",
            "stage_reason": "missing_snapshot",
            "positions_before_buy_pass": 0,
            "max_positions": 2,
            "available_slots_before_buy_pass": 2,
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
            "min_score_threshold": 0.5,
            "score_components": {},
            "candidate_rank": None,
            "selected_candidate": False,
            "executed": False,
            "selected_buy_symbol_for_day": None,
            "selected_buy_score_for_day": None,
            "sizing_block_reason": None,
            "sizing_recommended_qty": None,
        }
    ]
    assert result == expected
