from __future__ import annotations

import os
from dataclasses import is_dataclass, replace as dataclass_replace
from datetime import datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault("KIS_TRADER_DISABLE_DOTENV", "1")
os.environ.setdefault("KIS_TRADER_DISABLE_CREDENTIAL_FILES", "1")

from app import main as main_module
from app.core.error_classification import (
    is_benign_empty_buy_scan,
    should_downgrade_empty_buy_scan_to_backoff,
)
from app.core.market_session import MarketSessionStatus
from app.core.time_utils import KOREA_TZ
from app.portfolio.schema import PortfolioSnapshot


_BENIGN_REASONS = (
    "cycle_budget_low",
    "quote_prefetch_deadline",
    "quote_prefetch_timeout",
    "quote_prefetch_failed",
    "quote_prefetch_missing",
    "previous_scan_running",
    "previous_scan_worker_stale",
    "api_transient_backoff",
    "api_backoff",
    "api_budget_limited",
    "api_request_budget_limited",
    "api_request_budget_wait",
    "cadence_not_reached",
)

_STRUCTURAL_REASONS = (
    "pre_gated_scan_blocked",
    "pre_gated_all_symbols",
    "shallow_shortlist_empty",
)


@pytest.mark.parametrize("reason", _BENIGN_REASONS)
def test_benign_reason_with_empty_scan_downgrades(reason: str) -> None:
    assert (
        is_benign_empty_buy_scan(
            buy_scan_evaluated_count=0,
            scan_results_count=0,
            buy_scan_skipped_reason=reason,
            buy_scan_partial_budget=False,
        )
        is True
    )


def test_partial_budget_with_no_reason_downgrades() -> None:
    assert (
        is_benign_empty_buy_scan(
            buy_scan_evaluated_count=0,
            scan_results_count=0,
            buy_scan_skipped_reason=None,
            buy_scan_partial_budget=True,
        )
        is True
    )


@pytest.mark.parametrize("reason", _BENIGN_REASONS)
def test_evaluated_count_positive_never_downgrades(reason: str) -> None:
    assert (
        is_benign_empty_buy_scan(
            buy_scan_evaluated_count=3,
            scan_results_count=0,
            buy_scan_skipped_reason=reason,
            buy_scan_partial_budget=True,
        )
        is False
    )


def test_scan_results_present_never_downgrades() -> None:
    assert (
        is_benign_empty_buy_scan(
            buy_scan_evaluated_count=0,
            scan_results_count=2,
            buy_scan_skipped_reason="cycle_budget_low",
            buy_scan_partial_budget=True,
        )
        is False
    )


def test_unexplained_empty_scan_still_raises() -> None:
    assert (
        is_benign_empty_buy_scan(
            buy_scan_evaluated_count=0,
            scan_results_count=0,
            buy_scan_skipped_reason=None,
            buy_scan_partial_budget=False,
        )
        is False
    )


@pytest.mark.parametrize("reason", _STRUCTURAL_REASONS)
def test_structural_reason_still_raises(reason: str) -> None:
    assert (
        is_benign_empty_buy_scan(
            buy_scan_evaluated_count=0,
            scan_results_count=0,
            buy_scan_skipped_reason=reason,
            buy_scan_partial_budget=False,
        )
        is False
    )


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_reason_treated_as_none(blank: str) -> None:
    # Blank/whitespace reason behaves like None: only partial_budget can downgrade.
    assert (
        is_benign_empty_buy_scan(
            buy_scan_evaluated_count=0,
            scan_results_count=0,
            buy_scan_skipped_reason=blank,
            buy_scan_partial_budget=False,
        )
        is False
    )
    assert (
        is_benign_empty_buy_scan(
            buy_scan_evaluated_count=0,
            scan_results_count=0,
            buy_scan_skipped_reason=blank,
            buy_scan_partial_budget=True,
        )
        is True
    )


def test_rate_limit_downgrade_unchanged_for_buy_scan() -> None:
    # The sibling classifier must NOT absorb rate-limit semantics: the existing
    # rate-limit downgrade stays owned by should_downgrade_empty_buy_scan_to_backoff.
    assert (
        should_downgrade_empty_buy_scan_to_backoff(
            rate_limit_triggered=True,
            rate_limit_source="buy_scan",
            buy_scan_evaluated_count=0,
            scan_results_count=0,
            buy_scan_skipped_reason="rate_limit_detected",
            rate_limit_partial_stop=False,
        )
        is True
    )
    assert (
        should_downgrade_empty_buy_scan_to_backoff(
            rate_limit_triggered=False,
            rate_limit_source="buy_scan",
            buy_scan_evaluated_count=0,
            scan_results_count=0,
            buy_scan_skipped_reason="rate_limit_detected",
            rate_limit_partial_stop=False,
        )
        is False
    )
    # rate-limit reasons must NOT be in the benign set.
    assert (
        is_benign_empty_buy_scan(
            buy_scan_evaluated_count=0,
            scan_results_count=0,
            buy_scan_skipped_reason="rate_limit_detected",
            buy_scan_partial_budget=False,
        )
        is False
    )


def _runtime_settings(**overrides: object) -> SimpleNamespace:
    values = {
        "run_mode": "mock",
        "run_once": True,
        "run_interval_seconds": 60,
        "confirm_buy": "NO",
        "qty": 1,
        "target_symbols": ("005930",),
        "target_symbols_source": "test",
        "target_symbols_raw": "005930",
        "target_symbols_split_items": ("005930",),
        "sell_check_interval_seconds": 60,
        "buy_scan_interval_seconds": 60,
        "lane_scheduler_enabled": False,
        "order_gate_enabled": True,
        "enable_sell_guard_selftest": False,
        "enable_sell_test_scenarios": False,
        "sell_test_mode": "off",
        "enable_daily_pnl_brake": False,
        "enable_premarket_wait": False,
        "live_snapshot_refresh_interval_seconds": 30,
        "scan_symbols_max_per_cycle": 1,
        "buy_scan_profile_rotation_enabled": False,
        "buy_scan_exploration_ratio": 0.0,
        "buy_scan_core_fraction": 1.0,
        "buy_scan_rotating_fraction": 0.0,
        "buy_scan_shallow_top_k": 1,
        "buy_scan_deep_eval_limit": 1,
        "buy_scan_core_max": 1,
        "buy_scan_top_k_candidates": 1,
        "buy_scan_quote_prefetch_deadline_seconds": 0.0,
        "buy_scan_quote_request_timeout_seconds": 0.0,
        "buy_scan_quote_max_attempts": 1,
        "buy_scan_total_budget_seconds": 25.0,
        "session_cycle_hard_budget_seconds": 60.0,
        "api_soft_max_requests_per_second": 100,
        "api_soft_max_quotes_per_tick": 100,
        "api_min_inter_request_seconds": 0.0,
        "api_buy_scan_min_request_reserve": 0,
        "api_buy_scan_min_quote_reserve": 0,
        "api_backoff_seconds_on_rate_limit": 1,
        "degraded_mode_enabled": False,
        "slack_notify_order_submitted": False,
        "buy_max_budget_per_trade_krw": 1_000_000,
        "buy_max_account_exposure_pct": 100.0,
        "buy_max_qty_per_trade": 10,
        "rebuy_cooldown_minutes": 0,
        "same_symbol_max_buys_per_day": 4,
        "buy_daily_max_order_submissions": 10,
        "buy_daily_max_notional_krw": 10_000_000,
        "buy_block_on_blocked_preview": True,
        "buy_enable_risk_guards": True,
        "sell_daily_max_order_submissions": 10,
        "sell_daily_max_notional_krw": 10_000_000,
        "strict_sell_first": True,
        "enable_rebalance_sell": False,
        "enable_quality_rebalance_preview": False,
        "rebalance_sell_max_submissions_per_day": 10,
        "buy_fee_bps": 0.0,
        "sell_fee_bps": 0.0,
        "sell_tax_bps": 0.0,
        "buy_slippage_bps": 0.0,
        "sell_slippage_bps": 0.0,
        "expected_slippage_bps_base": 0.0,
        "expected_cost_block_bps": 0.0,
        "min_net_edge_bps": 0.0,
        "min_net_profit_buffer_bps": 0.0,
        "use_cost_aware_pnl": False,
        "performance_benchmark_symbol": "069500",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _empty_portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        positions=(),
        cash_total=1_000_000,
        cash_orderable=1_000_000,
        cash_next_day=1_000_000,
        total_evaluation_amount=1_000_000,
    )


def _patch_runtime_main_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    *,
    state: dict[str, object],
    events: list[dict[str, object]],
    scan_target_symbols,
    scan_diagnostics: dict[str, object],
) -> None:
    fixed_now = datetime(2026, 6, 30, 9, 30, tzinfo=KOREA_TZ)
    session = MarketSessionStatus(
        session="REGULAR",
        order_allowed=True,
        reason="regular",
        buy_block_action=None,
        sell_block_action=None,
    )

    def fake_replace(obj, **changes):
        if is_dataclass(obj):
            return dataclass_replace(obj, **changes)
        data = vars(obj).copy()
        data.update(changes)
        return SimpleNamespace(**data)

    patches: dict[str, object] = {
        "replace": fake_replace,
        "get_korean_now": lambda: fixed_now,
        "_print_runtime_mode": lambda *_args, **_kwargs: None,
        "_print_applied_settings": lambda *_args, **_kwargs: None,
        "_print_test_mode": lambda *_args, **_kwargs: None,
        "_print_cycle_header": lambda *_args, **_kwargs: None,
        "_print_engine_schedule_state": lambda **_kwargs: None,
        "_print_account_balance_interpretation": lambda **_kwargs: None,
        "_print_regime_state": lambda **_kwargs: None,
        "_print_daily_pnl_brake_state": lambda **_kwargs: None,
        "_print_portfolio_positions": lambda *_args, **_kwargs: None,
        "_print_today_performance_summary": lambda **_kwargs: None,
        "_print_today_bought_tracking": lambda **_kwargs: None,
        "_print_buy_runtime_filter_summary": lambda **_kwargs: None,
        "_print_cycle_conclusion": lambda **_kwargs: None,
        "_print_cycle_timing": lambda **_kwargs: None,
        "_print_api_usage": lambda *_args, **_kwargs: None,
        "_print_sell_metrics": lambda **_kwargs: None,
        "_print_buy_scan_metrics": lambda **_kwargs: None,
        "_print_runtime_state_summary": lambda *_args, **_kwargs: None,
        "build_market_session_console_lines": lambda *_args, **_kwargs: [],
        "build_universe_console_lines": lambda *_args, **_kwargs: [],
        "build_scan_console_lines": lambda *_args, **_kwargs: [],
        "build_risk_guard_console_lines": lambda *_args, **_kwargs: [],
        "build_risk_guard_skipped_console_lines": lambda *_args, **_kwargs: [],
        "sync_account_scope_meta": lambda *_args, **_kwargs: {
            "account_scope_changed": False
        },
        "get_account_scope_context": lambda *_args, **_kwargs: {
            "account_signature": "mock_test_01",
            "account_environment": "mock",
            "masked_account_display": "mock_****_01",
        },
        "load_runtime_state": lambda: state,
        "save_runtime_state": lambda _state: True,
        "_write_slack_runtime_status_snapshot": lambda **_kwargs: True,
        "get_korean_market_session": lambda: session,
        "_should_run_light_session_cycle": lambda **_kwargs: False,
        "_api_budget_transient_backoff_active": lambda *_args, **_kwargs: False,
        "_api_budget_backoff_active": lambda *_args, **_kwargs: False,
        "_api_budget_can_request": lambda *_args, **_kwargs: True,
        "_api_budget_can_quote": lambda *_args, **_kwargs: True,
        "_api_budget_remaining_requests": lambda *_args, **_kwargs: 100,
        "_api_budget_remaining_quotes": lambda *_args, **_kwargs: 100,
        "_api_budget_request_window_size": lambda *_args, **_kwargs: 0,
        "_api_budget_min_wait_for_request_slot": lambda *_args, **_kwargs: 0,
        "_api_budget_backoff_remaining_seconds": lambda *_args, **_kwargs: 0,
        "_api_budget_transient_backoff_remaining_seconds": lambda *_args, **_kwargs: 0,
        "_api_budget_register_request": lambda *_args, **_kwargs: None,
        "_api_budget_register_requests": lambda *_args, **_kwargs: None,
        "_api_budget_register_measured_extra_requests": lambda *_args, **_kwargs: 0,
        "_api_budget_note_transient_api_error": lambda *_args, **_kwargs: None,
        "_api_budget_update_rate_limit_recovery_state": lambda *_args, **_kwargs: None,
        "_api_budget_update_transient_recovery_state": lambda *_args, **_kwargs: None,
        "_summarize_api_budget_state": lambda *_args, **_kwargs: {},
        "issue_access_token": lambda: "TOKEN",
        "inquire_balance": lambda *, token: {"rt_cd": "0", "output1": [], "output2": []},
        "build_portfolio_snapshot": lambda _data: _empty_portfolio(),
        "_sync_reconciliation_state": lambda **_kwargs: {},
        "build_account_state_payload": lambda *_args, **_kwargs: {
            "deployment_invariant_equity_krw": 1_000_000,
            "operating_equity_krw": 1_000_000,
        },
        "_build_today_realized_summary": lambda *_args, **_kwargs: {},
        "_build_daily_pnl_brake_state": lambda **_kwargs: {"buy_paused": False},
        "_build_daily_pnl_brake_observability": lambda **_kwargs: {},
        "build_current_drawdown_state": lambda **_kwargs: {},
        "_build_regime_state": lambda **_kwargs: {
            "current_regime": "NORMAL",
            "regime_reason": "normal",
            "regime_multiplier": 1.0,
            "effective_buy_max_budget_per_trade_krw": 1_000_000,
            "effective_buy_max_account_exposure_pct": 100.0,
            "effective_buy_max_qty_per_trade": 10,
            "effective_rebuy_cooldown_minutes": 0,
            "effective_same_symbol_max_buys_per_day": 4,
            "effective_buy_daily_max_order_submissions": 10,
        },
        "load_live_snapshot_symbols": lambda: None,
        "live_snapshot_status": lambda: {},
        "live_snapshot_worker_health": lambda **_kwargs: {"warnings": ()},
        "buy_scan_uses_separate_quote_account": lambda _settings: False,
        "_select_buy_scan_profile": lambda *_args, **_kwargs: {
            "profile": "momentum",
            "rotation_enabled": False,
        },
        "_build_buy_scan_layered_universe": lambda *_args, **_kwargs: {
            "selected_symbols": ("005930",),
            "layer_by_symbol": {},
            "core_count": 1,
            "rotating_count": 0,
            "exploration_count": 0,
            "selected_core_count": 1,
            "selected_rotating_count": 0,
            "selected_exploration_count": 0,
            "excluded_count": 0,
            "preview": {},
        },
        "_build_buy_scan_pre_gating": lambda *_args, **_kwargs: {
            "scan_allowed": True,
            "allowed_symbols": ("005930",),
            "rejected": [],
            "reason_counts": {},
            "early_reject_count": 0,
        },
        "_build_buy_scan_shallow_plan": lambda *_args, **_kwargs: {
            "ranked_count": 1,
            "deep_eval_limit": 1,
            "shortlist_symbols": ("005930",),
            "shortlist_preview": [],
            "exploration_quota_used": 0,
        },
        "_build_buy_scan_deep_eval_symbols": lambda *_args, **_kwargs: ("005930",),
        "_cap_buy_scan_deep_eval_symbols_for_api_budget": lambda *, symbols, **_kwargs: (
            tuple(symbols),
            {
                "budget_cap_applied": False,
                "budget_cap_reasons": [],
                "quote_budget_original_count": len(tuple(symbols)),
                "budget_capped_count": len(tuple(symbols)),
                "quote_budget_remaining_before_scan": 100,
                "request_budget_remaining_before_scan": 100,
                "execution_request_reserve": 0,
                "min_scan_request_floor": 0,
            },
        ),
        "get_adaptive_pacing_summary": lambda: {},
        "scan_target_symbols": scan_target_symbols,
        "get_last_scan_diagnostics": lambda: dict(scan_diagnostics),
        "_apply_buy_runtime_guards_to_scan_results": lambda results, **_kwargs: tuple(
            results
        ),
        "select_top_candidate": lambda _results, **_kwargs: None,
        "select_top_analysis_result": lambda _results: None,
        "serialize_selection_details": lambda **_kwargs: {
            "selection_reason": "no candidate",
            "selected_symbol": None,
        },
        "_log_engine_event": lambda **kwargs: events.append(dict(kwargs)),
        "_send_order_slack_notification": lambda **_kwargs: None,
        "_resolve_benchmark_snapshot": lambda **_kwargs: None,
        "build_performance_report": lambda **_kwargs: {},
        "persist_performance_report": lambda _report: {
            "snapshot_saved": False,
            "report_saved": False,
        },
        "build_performance_console_lines": lambda _report: [],
        "build_cycle_snapshot": lambda **_kwargs: {},
        "_build_buy_cycle_funnel_stats": lambda **_kwargs: {},
        "_build_buy_candidate_outcome_records": lambda **_kwargs: [],
        "persist_cycle_snapshot": lambda _snapshot: True,
        "append_candidate_outcomes": lambda *_args, **_kwargs: True,
        "append_cycle_stats": lambda *_args, **_kwargs: True,
        "_run_cycle_market_data_quality_sentinel": lambda *_args, **_kwargs: None,
        "_format_cycle_summary": lambda **_kwargs: "cycle summary",
        "_update_recent_market_snapshots": lambda *_args, **_kwargs: None,
        "build_daily_summary": lambda: {},
        "build_daily_summary_console_lines": lambda _summary: [],
        "build_cycle_stats_daily_summary": lambda: {},
        "build_cycle_stats_console_lines": lambda _summary: [],
        "get_runtime_state_path": lambda *_args, **_kwargs: "runtime.json",
        "get_cycle_snapshots_path": lambda *_args, **_kwargs: "cycle.jsonl",
    }
    monkeypatch.setattr(main_module.time, "sleep", lambda _seconds: None)
    for name, value in patches.items():
        monkeypatch.setattr(main_module, name, value)


def test_runtime_benign_empty_buy_scan_records_hold_not_cycle_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state: dict[str, object] = {}
    events: list[dict[str, object]] = []
    _patch_runtime_main_boundaries(
        monkeypatch,
        state=state,
        events=events,
        scan_target_symbols=lambda **_kwargs: (),
        scan_diagnostics={"interrupted_reason": "cycle_budget_low"},
    )

    main_module.run_cycle(
        _runtime_settings(),
        sell_check_due=False,
        buy_scan_due=True,
        scheduler_state={},
        api_budget_state={},
    )

    assert state["last_action"] == "HOLD_BUY_SCAN_SKIPPED"
    assert state["last_buy_scan_skipped_reason"] == "cycle_budget_low"
    assert all(event.get("action") != "cycle_error" for event in events)
    assert any(event.get("action") == "skipped_buy_scan_empty" for event in events)


def test_runtime_real_buy_scan_exception_remains_cycle_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state: dict[str, object] = {}
    events: list[dict[str, object]] = []

    def raise_scanner_error(**_kwargs):
        raise RuntimeError("scanner exploded")

    _patch_runtime_main_boundaries(
        monkeypatch,
        state=state,
        events=events,
        scan_target_symbols=raise_scanner_error,
        scan_diagnostics={},
    )

    with pytest.raises(RuntimeError, match="scanner exploded"):
        main_module.run_cycle(
            _runtime_settings(),
            sell_check_due=False,
            buy_scan_due=True,
            scheduler_state={},
            api_budget_state={},
        )

    assert state["last_action"] == "CYCLE_ERROR"
    assert any(event.get("action") == "cycle_error" for event in events)


def test_runtime_cycle_error_records_exception_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2-3 (E6): cycle_error 레코드에 예외 타입/문자열이 배선된다."""
    state: dict[str, object] = {}
    events: list[dict[str, object]] = []

    def raise_scanner_error(**_kwargs):
        raise RuntimeError("scanner exploded")

    _patch_runtime_main_boundaries(
        monkeypatch,
        state=state,
        events=events,
        scan_target_symbols=raise_scanner_error,
        scan_diagnostics={},
    )

    with pytest.raises(RuntimeError, match="scanner exploded"):
        main_module.run_cycle(
            _runtime_settings(),
            sell_check_due=False,
            buy_scan_due=True,
            scheduler_state={},
            api_budget_state={},
        )

    cycle_error_events = [e for e in events if e.get("action") == "cycle_error"]
    assert cycle_error_events, "cycle_error event missing"
    event = cycle_error_events[-1]
    assert "RuntimeError" in str(event.get("reason")), event.get("reason")
    assert event.get("error_type") == "RuntimeError"
    assert "RuntimeError" in str(state.get("last_decision_reason", ""))


def test_runtime_messageless_exception_still_records_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2-3: str(exc)가 빈 예외도 타입으로 원인이 남는다 (오늘 9건 미기록 방지)."""

    class _SilentBoom(Exception):
        pass

    state: dict[str, object] = {}
    events: list[dict[str, object]] = []

    def raise_silent(**_kwargs):
        raise _SilentBoom()

    _patch_runtime_main_boundaries(
        monkeypatch,
        state=state,
        events=events,
        scan_target_symbols=raise_silent,
        scan_diagnostics={},
    )

    with pytest.raises(_SilentBoom):
        main_module.run_cycle(
            _runtime_settings(),
            sell_check_due=False,
            buy_scan_due=True,
            scheduler_state={},
            api_budget_state={},
        )

    cycle_error_events = [e for e in events if e.get("action") == "cycle_error"]
    assert cycle_error_events, "cycle_error event missing"
    reason = str(cycle_error_events[-1].get("reason"))
    assert reason.strip(), "reason must not be empty for a message-less exception"
    assert "_SilentBoom" in reason, reason
