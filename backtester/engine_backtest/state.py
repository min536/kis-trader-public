"""Backtest-local runtime state dict.

Mirrors the shape of app/runtime_state.py's _default_state() but avoids
calling get_account_scope_context() which requires live KIS env vars.
Used by evaluate_buy_order_guard / evaluate_sell_order_guard.
"""
from __future__ import annotations

from datetime import date


def make_backtest_state(trading_date: date) -> dict:
    """Return a fresh runtime_state dict for the given trading date."""
    return {
        "account_signature": "backtest",
        "account_environment": "backtest",
        "masked_account_display": "BACKTEST",
        "account_scope_changed": False,
        "previous_account_signature": None,
        "trading_date": trading_date.isoformat(),
        "last_cycle_id": None,
        "last_cycle_started_at": None,
        "last_cycle_result": None,
        "last_cycle_elapsed_ms": None,
        "last_final_action": None,
        "last_final_reason": None,
        "last_warning_count": 0,
        "last_error_count": 0,
        "last_snapshot_write_ok": None,
        "last_performance_write_ok": None,
        "last_snapshot_write_failed_at": None,
        "last_performance_write_failed_at": None,
        "snapshot_write_failures_today": 0,
        "performance_write_failures_today": 0,
        "last_action": None,
        "last_buy_symbol": None,
        "last_buy_qty": 0,
        "last_sell_symbol": None,
        "last_sell_qty": 0,
        "last_decision_reason": None,
        "last_order_side": None,
        "last_order_date": None,
        "last_selected_symbol": None,
        "last_buy_attempt_signature": None,
        "last_buy_attempt_at": None,
        "last_sell_attempt_signature": None,
        "last_sell_attempt_at": None,
        "last_sell_check_at": None,
        "last_buy_scan_at": None,
        "buy_scan_profile_cursor": 0,
        "buy_scan_last_profile": None,
        "buy_scan_rotating_cursor": 0,
        "buy_scan_exploration_cursor": 0,
        "recent_market_snapshots_by_symbol": {},
        "sell_watch_next_start_index": 0,
        "last_sell_watch_cursor_before": 0,
        "last_sell_watch_cursor_after": 0,
        "last_sell_watch_total_holdings": 0,
        "last_sell_watch_partial": False,
        "last_sell_watch_partial_reason": None,
        "last_sell_watch_evaluated_symbols": [],
        "last_sell_watch_skipped_symbols": [],
        "last_market_session": None,
        "last_budget_status": None,
        "last_reentry_blocked_at": None,
        "last_reentry_blocked_at_by_symbol": {},
        "last_reentry_state_by_symbol": {},
        "last_reentry_reason_by_symbol": {},
        "pending_sell_intents_by_symbol": {},
        "last_exit_reason_by_symbol": {},
        "last_exit_at_by_symbol": {},
        "last_exit_price_by_symbol": {},
        "last_exit_qty_by_symbol": {},
        "last_exit_was_full_close_by_symbol": {},
        "last_exit_trigger_context_by_symbol": {},
        "broker_last_synced_positions_by_symbol": {},
        "broker_last_synced_at": None,
        "last_reconciliation_summary": None,
        "last_reconciliation_events": [],
        "intraday_pnl_baseline_krw": None,
        "intraday_pnl_pct": None,
        "current_brake_state": None,
        "current_regime": None,
        "regime_reason": None,
        "regime_multiplier": None,
        "daily_pnl_pause_state": None,
        "daily_pnl_pause_until": None,
        "daily_pnl_pause_reason": None,
        "symbols_bought_today": [],
        "symbols_sold_today": [],
        "buy_attempted_symbols_today": [],
        "sell_triggered_symbols_today": [],
        "blocked_buy_symbols_today": [],
        "blocked_sell_symbols_today": [],
        "buy_entries_by_symbol_today": {},
        "last_buy_entry_at_by_symbol": {},
        "buy_cooldown_blocked_symbols_today": [],
        "sell_cooldown_blocked_symbols_today": [],
        "buy_blocked_symbols_today": [],
        "sell_blocked_symbols_today": [],
        "rebalance_sell_submissions_today": 0,
        "recent_orders": [],
    }


def reset_daily_state(state: dict, trading_date: date) -> None:
    """Reset all per-day counters for a new trading date."""
    state["trading_date"] = trading_date.isoformat()
    state["last_buy_attempt_at"] = None
    state["last_sell_attempt_at"] = None
    state["last_sell_check_at"] = None
    state["last_buy_scan_at"] = None
    state["buy_scan_profile_cursor"] = 0
    state["symbols_bought_today"] = []
    state["symbols_sold_today"] = []
    state["buy_attempted_symbols_today"] = []
    state["sell_triggered_symbols_today"] = []
    state["blocked_buy_symbols_today"] = []
    state["blocked_sell_symbols_today"] = []
    state["buy_entries_by_symbol_today"] = {}
    state["last_buy_entry_at_by_symbol"] = {}
    state["buy_cooldown_blocked_symbols_today"] = []
    state["sell_cooldown_blocked_symbols_today"] = []
    state["buy_blocked_symbols_today"] = []
    state["sell_blocked_symbols_today"] = []
    state["rebalance_sell_submissions_today"] = 0
    state["recent_orders"] = []
    state["sell_watch_next_start_index"] = 0
