"""Tests for backtester.engine_backtest.parity_proxy (R7-B2)."""
from __future__ import annotations

from datetime import date, datetime

import pytest


# ---------------------------------------------------------------------------
# Step 1: Pin 7 symbols — assertIs
# ---------------------------------------------------------------------------

PINNED_NAMES = [
    "_pick_latest_symbol_rows",
    "_estimate_prev_close",
    "_build_provider_from_signal_rows",
    "_select_seed_snapshot",
    "_seed_portfolio_from_snapshot",
    "_resolve_initial_cash",
    "_run_proxy_replay",
]


def test_pin_symbols_assertIs():
    import backtester.engine_backtest.parity as parity
    import backtester.engine_backtest.parity_proxy as parity_proxy

    for name in PINNED_NAMES:
        obj_parity = getattr(parity, name)
        obj_proxy = getattr(parity_proxy, name)
        assert obj_parity is obj_proxy, (
            f"parity.{name} is not parity_proxy.{name} — facade re-export missing or wrong"
        )


# ---------------------------------------------------------------------------
# Step 2: _estimate_prev_close
# ---------------------------------------------------------------------------

def test_estimate_prev_close_normal():
    """current_price=10000, prev_day_change_pct=10.0 → 10000/1.1 = 9090 (rounded)"""
    from backtester.engine_backtest.parity_proxy import _estimate_prev_close
    # 10000 / 1.1 = 9090.909... → rounded = 9091
    result = _estimate_prev_close(10000, 10.0)
    assert result == 9091


def test_estimate_prev_close_zero_denominator():
    """prev_day_change_pct = -100.0 → denominator = 0 → return 0"""
    from backtester.engine_backtest.parity_proxy import _estimate_prev_close
    result = _estimate_prev_close(10000, -100.0)
    assert result == 0


def test_estimate_prev_close_negative_result():
    """If estimated result is ≤ 0, return 0"""
    from backtester.engine_backtest.parity_proxy import _estimate_prev_close
    # prev_day_change_pct = -200 → denominator = 1 + (-2) = -1
    # estimated = 10000 / -1 = -10000 → clamp to 0
    result = _estimate_prev_close(10000, -200.0)
    assert result == 0


# ---------------------------------------------------------------------------
# Step 3: _pick_latest_symbol_rows
# ---------------------------------------------------------------------------

def test_pick_latest_symbol_rows_latest_wins():
    """Two rows for same symbol — newer timestamp wins"""
    from backtester.engine_backtest.parity_proxy import _pick_latest_symbol_rows
    rows = [
        {
            "symbol": "A001",
            "ts": "2026-04-03T09:00:00",
            "current_price": 5000,
            "open_price": 4900,
            "low_price": 4800,
        },
        {
            "symbol": "A001",
            "ts": "2026-04-03T09:30:00",
            "current_price": 5100,
            "open_price": 4900,
            "low_price": 4800,
        },
    ]
    result = _pick_latest_symbol_rows(rows)
    assert len(result) == 1
    assert result[0]["current_price"] == 5100


def test_pick_latest_symbol_rows_missing_price_excluded():
    """Row with missing current_price is excluded"""
    from backtester.engine_backtest.parity_proxy import _pick_latest_symbol_rows
    rows = [
        {
            "symbol": "B001",
            "ts": "2026-04-03T09:00:00",
            "current_price": None,
            "open_price": 3000,
            "low_price": 2900,
        },
        {
            "symbol": "C001",
            "ts": "2026-04-03T09:00:00",
            "current_price": 7000,
            "open_price": 6900,
            "low_price": 6800,
        },
    ]
    result = _pick_latest_symbol_rows(rows)
    assert len(result) == 1
    assert result[0]["symbol"] == "C001"


def test_pick_latest_symbol_rows_sorted():
    """Result is sorted by symbol"""
    from backtester.engine_backtest.parity_proxy import _pick_latest_symbol_rows
    rows = [
        {"symbol": "Z001", "ts": "2026-04-03T09:00:00", "current_price": 1000, "open_price": 900, "low_price": 800},
        {"symbol": "A001", "ts": "2026-04-03T09:00:00", "current_price": 2000, "open_price": 1900, "low_price": 1800},
    ]
    result = _pick_latest_symbol_rows(rows)
    assert [r["symbol"] for r in result] == ["A001", "Z001"]


# ---------------------------------------------------------------------------
# Step 4: _resolve_initial_cash
# ---------------------------------------------------------------------------

def test_resolve_initial_cash_fallback():
    """fallback_cash > 0 takes priority"""
    from backtester.engine_backtest.parity_proxy import _resolve_initial_cash
    snapshot = {"cash_krw": 3_000_000}
    assert _resolve_initial_cash(seed_snapshot=snapshot, fallback_cash=2_000_000) == 2_000_000


def test_resolve_initial_cash_seed_cash_krw():
    """seed_snapshot.cash_krw used when fallback absent"""
    from backtester.engine_backtest.parity_proxy import _resolve_initial_cash
    snapshot = {"cash_krw": 4_500_000}
    assert _resolve_initial_cash(seed_snapshot=snapshot, fallback_cash=None) == 4_500_000


def test_resolve_initial_cash_holdings_total():
    """seed_snapshot.holdings_summary.cash_total_krw used"""
    from backtester.engine_backtest.parity_proxy import _resolve_initial_cash
    snapshot = {
        "holdings_summary": {"cash_total_krw": 8_000_000}
    }
    assert _resolve_initial_cash(seed_snapshot=snapshot, fallback_cash=None) == 8_000_000


def test_resolve_initial_cash_default():
    """Nothing found → 10_000_000"""
    from backtester.engine_backtest.parity_proxy import _resolve_initial_cash
    assert _resolve_initial_cash(seed_snapshot=None, fallback_cash=None) == 10_000_000


# ---------------------------------------------------------------------------
# Step 5: _select_seed_snapshot
# ---------------------------------------------------------------------------

def test_select_seed_snapshot_positions_priority():
    """Row with positions takes priority over row without, even if later ts"""
    from backtester.engine_backtest.parity_proxy import _select_seed_snapshot
    rows = [
        {
            "ts": "2026-04-03T09:30:00",
            "holdings_summary": {"positions": [{"symbol": "A001"}]},
        },
        {
            "ts": "2026-04-03T10:00:00",
            "holdings_summary": {"positions": []},
        },
    ]
    result = _select_seed_snapshot(rows)
    # First row with positions (sorted by ts, earliest that has positions)
    assert result["ts"] == "2026-04-03T09:30:00"


def test_select_seed_snapshot_empty():
    """Empty list returns None"""
    from backtester.engine_backtest.parity_proxy import _select_seed_snapshot
    assert _select_seed_snapshot([]) is None


# ---------------------------------------------------------------------------
# Step 6: _seed_portfolio_from_snapshot — real BacktestPortfolio
# ---------------------------------------------------------------------------

def test_seed_portfolio_from_snapshot_normal():
    """Seeds positions correctly, skips qty<=0 or avg_cost<=0"""
    from backtester.engine_backtest.parity_proxy import _seed_portfolio_from_snapshot
    from backtester.engine_backtest.portfolio import BacktestPortfolio

    portfolio = BacktestPortfolio(initial_cash=10_000_000)
    snapshot = {
        "holdings_summary": {
            "positions": [
                {"symbol": "A001", "holding_qty": 10, "average_cost": 5000, "current_price": 5200},
                {"symbol": "B002", "holding_qty": 5, "average_cost": 3000, "current_price": 3100},
                # skipped: qty=0
                {"symbol": "C003", "holding_qty": 0, "average_cost": 1000, "current_price": 1000},
                # skipped: avg_cost=0
                {"symbol": "D004", "holding_qty": 3, "average_cost": 0, "current_price": 1000},
            ]
        }
    }
    seeded = _seed_portfolio_from_snapshot(
        portfolio, seed_snapshot=snapshot, trading_date=date(2026, 4, 3)
    )
    assert seeded == 2
    assert "A001" in portfolio.positions
    assert "B002" in portfolio.positions
    assert "C003" not in portfolio.positions
    assert portfolio.positions["A001"].qty == 10
    assert portfolio.positions["A001"].avg_cost == 5000


def test_seed_portfolio_no_snapshot():
    """None snapshot → 0 seeded"""
    from backtester.engine_backtest.parity_proxy import _seed_portfolio_from_snapshot
    from backtester.engine_backtest.portfolio import BacktestPortfolio
    portfolio = BacktestPortfolio(initial_cash=10_000_000)
    seeded = _seed_portfolio_from_snapshot(
        portfolio, seed_snapshot=None, trading_date=date(2026, 4, 3)
    )
    assert seeded == 0


# ---------------------------------------------------------------------------
# Step 7: _build_provider_from_signal_rows
# ---------------------------------------------------------------------------

def test_build_provider_from_signal_rows_basic():
    """Prices dict from rows + provider.symbols() contain the row symbols"""
    from backtester.engine_backtest.parity_proxy import _build_provider_from_signal_rows

    rows = [
        {
            "symbol": "A001",
            "current_price": 10000,
            "open_price": 9800,
            "low_price": 9700,
            "prev_day_change_pct": 2.0,
        },
        {
            "symbol": "B002",
            "current_price": 5000,
            "open_price": 4900,
            "low_price": 4800,
            "prev_day_change_pct": 0.0,
        },
    ]
    provider, prices = _build_provider_from_signal_rows(
        rows=rows,
        trading_date=date(2026, 4, 3),
        seed_snapshot=None,
    )
    assert prices == {"A001": 10000, "B002": 5000}
    assert "A001" in provider.symbols()
    assert "B002" in provider.symbols()


def test_build_provider_seed_snapshot_extra_symbol():
    """Holdings symbol not in rows gets added from snapshot"""
    from backtester.engine_backtest.parity_proxy import _build_provider_from_signal_rows

    rows = [
        {
            "symbol": "A001",
            "current_price": 10000,
            "open_price": 9800,
            "low_price": 9700,
            "prev_day_change_pct": 1.0,
        },
    ]
    seed_snapshot = {
        "holdings_summary": {
            "positions": [
                {"symbol": "Z999", "current_price": 3000, "average_cost": 2900},
            ]
        }
    }
    provider, prices = _build_provider_from_signal_rows(
        rows=rows,
        trading_date=date(2026, 4, 3),
        seed_snapshot=seed_snapshot,
    )
    assert "Z999" in provider.symbols()
    assert prices["Z999"] == 3000


# ---------------------------------------------------------------------------
# Step 8: _run_proxy_replay — small real integration test
# ---------------------------------------------------------------------------

def test_run_proxy_replay_integration():
    """Minimal end-to-end: check returned dict structure and mode"""
    from backtester.engine_backtest.parity_proxy import _run_proxy_replay

    latest_signal_rows = [
        {
            "symbol": "A001",
            "current_price": 10000,
            "open_price": 9800,
            "low_price": 9700,
            "prev_day_change_pct": 1.5,
        },
        {
            "symbol": "B002",
            "current_price": 5000,
            "open_price": 4900,
            "low_price": 4850,
            "prev_day_change_pct": 0.5,
        },
    ]
    snapshot_rows = [
        {
            "timestamp": "2026-04-03T09:00:00",
            "market_session": {"session": "REGULAR"},
            "holdings_summary": {
                "positions": [
                    {"symbol": "A001", "holding_qty": 3, "average_cost": 9500, "current_price": 10000},
                ],
                "cash_total_krw": 5_000_000,
            },
        }
    ]

    result = _run_proxy_replay(
        trading_date=date(2026, 4, 3),
        latest_signal_rows=latest_signal_rows,
        snapshot_rows=snapshot_rows,
        initial_cash=None,
    )

    assert result["mode"] == "historical_signal_proxy"
    assert result["initial_cash"] == 5_000_000
    assert result["seeded_position_count"] == 1
    # Check key set
    for key in [
        "mode", "input_symbol_count", "initial_cash", "seeded_position_count",
        "buy_signal_count", "buy_scored_candidate_count", "buy_candidate_symbols",
        "buy_rule_names", "buy_rule_enabled_counts", "buy_rule_pass_counts",
        "buy_rejection_reason_counts", "buy_funnel", "buy_capacity", "buy_score_stats",
        "buy_sizing", "selected_buy_symbols", "selected_buy_score", "final_candidate_count",
        "executed_buy_count", "sell_evaluated_count", "sell_triggered_count",
        "selected_sell_symbols", "executed_sell_count", "sell_reason_distribution",
    ]:
        assert key in result, f"Key '{key}' missing from _run_proxy_replay result"
