import json
import os
import sys
from typing import Any

from app.auth.account_scope import (
    get_account_scope_context,
    get_cycle_snapshots_path,
)
from app.core.jsonl import SNAPSHOT_READ_LINE_MAX_BYTES, read_jsonl_objects
from app.reporting.cycle_serializers import (
    _extract_pre_gating_fields,
    _extract_staged_scan_fields,
    _resolve_primary_action_context,
    _serialize_buy_candidate,
    _serialize_market_snapshot,
    _serialize_observed_market_snapshots,
    _serialize_positions,
    _serialize_sell_candidate,
    _serialize_top_scan_candidates,
)

# Serializer cluster: canonical implementations live in
# app.reporting.cycle_serializers and are bound by the module-top import
# (R3-S8).


def build_cycle_snapshot(
    *,
    cycle_id: str,
    timestamp: str,
    environment: str,
    market_session,
    portfolio_snapshot,
    sell_analysis_results,
    scan_results,
    selected_buy_candidate,
    selected_sell_candidate,
    sell_watch_final_review,
    selection_details: dict[str, Any] | None,
    buy_execution_snapshot,
    buy_position_sizing,
    sell_position_sizing,
    buy_risk_guard,
    sell_risk_guard,
    rebalance_preview: dict[str, Any] | None,
    scheduler_state: dict[str, Any] | None,
    daily_pnl_brake_state: dict[str, Any] | None,
    regime_state: dict[str, Any] | None,
    runtime_state: dict[str, Any],
    observed_market_snapshots: dict[str, object] | None,
    error: str | None,
    top_scan_candidate_limit: int = 5,
    timing_summary: dict[str, Any] | None = None,
    api_usage_summary: dict[str, Any] | None = None,
    buy_scan_requested_count: int = 0,
    buy_scan_evaluated_count: int = 0,
    buy_scan_skipped_reason: str | None = None,
    buy_scan_quote_request_count: int = 0,
    buy_scan_guard_wait_ms: float = 0.0,
    buy_scan_sleep_ms: float = 0.0,
    buy_scan_effective_total_sleep_ms: float = 0.0,
    buy_scan_quote_response_ms: float = 0.0,
    buy_scan_calc_ms: float = 0.0,
    buy_scan_ranking_ms: float = 0.0,
    buy_scan_logging_ms: float = 0.0,
    buy_scan_throttle_sleep_events: int = 0,
    buy_scan_throttle_min_sleep_ms: float | None = None,
    buy_scan_throttle_total_sleep_ms: float = 0.0,
    buy_scan_throttle_immediate_pass_count: int = 0,
    buy_scan_average_sleep_per_event_ms: float = 0.0,
    sell_evaluated_count: int = 0,
    sell_quote_request_count: int = 0,
    sell_eval_guard_wait_ms: float = 0.0,
    sell_eval_throttle_sleep_ms: float = 0.0,
    sell_eval_effective_total_sleep_ms: float = 0.0,
    sell_eval_quote_response_ms: float = 0.0,
    sell_eval_calc_ms: float = 0.0,
    sell_watch_cursor_before: int = 0,
    sell_watch_cursor_after: int = 0,
    sell_watch_total_holdings: int = 0,
    sell_watch_evaluated_symbols: list[str] | None = None,
    sell_watch_skipped_symbols: list[str] | None = None,
    sell_watch_partial: bool = False,
    sell_watch_partial_reason: str | None = None,
    sell_watch_priority_preview: list[dict[str, Any]] | None = None,
    rate_limit_triggered: bool = False,
    rate_limit_source: str | None = None,
    backoff_applied_seconds: int = 0,
    adaptive_pacing_used: bool = False,
    adaptive_pacing_extra_delay_ms: float = 0.0,
    rate_limit_partial_stop: bool = False,
    rate_limit_partial_stop_symbol: str | None = None,
    rate_limit_partial_completed_count: int = 0,
    rate_limit_partial_remaining_count: int = 0,
    request_window_size_before_sell_watch: int = 0,
    request_window_size_before_buy_scan: int = 0,
    throttle_guard_triggered: bool = False,
    throttle_guard_reason: str | None = None,
    buy_scan_budget_reserved: bool = False,
    buy_scan_reserve_used: bool = False,
    buy_scan_partial_budget: bool = False,
    sell_watch_capped_for_buy_scan: bool = False,
    execution_tail_backoff_drain_ms: float = 0.0,
    sell_watch_backoff_drain_ms: float = 0.0,
) -> dict[str, Any]:
    account_scope = get_account_scope_context()
    positions = _serialize_positions(portfolio_snapshot, sell_analysis_results)
    pre_gating_fields = _extract_pre_gating_fields(selection_details)
    staged_scan_fields = _extract_staged_scan_fields(selection_details)
    primary_action_context = _resolve_primary_action_context(
        runtime_state=runtime_state,
        selected_buy_candidate=selected_buy_candidate,
        selected_sell_candidate=selected_sell_candidate,
    )

    cash_total_krw = None
    cash_orderable_krw = None
    equity_krw = None
    snapshot_status_reason = None
    integrity_warnings: list[str] = []
    holdings_summary = None
    if portfolio_snapshot is not None:
        holdings_market_value_krw = sum(
            int(position["market_value"] or 0) for position in positions
        )
        cash_total_krw = int(portfolio_snapshot.cash_total)
        cash_orderable_krw = int(portfolio_snapshot.cash_orderable)
        equity_krw = cash_orderable_krw + holdings_market_value_krw
        if len(portfolio_snapshot.held_positions) > 0 and holdings_market_value_krw == 0:
            integrity_warnings.append("보유 종목이 있는데 holdings_market_value_krw가 0원입니다.")
        if holdings_market_value_krw > 0 and equity_krw <= cash_orderable_krw:
            integrity_warnings.append("보유 평가금액이 있는데 equity 계산이 주문가능현금 중심으로만 보입니다.")
        if len(portfolio_snapshot.held_positions) > 0 and all(
            int(position["average_cost"] or 0) <= 0 for position in positions
        ):
            integrity_warnings.append("보유 종목이 있는데 평균단가/매입원금 정보가 비어 있습니다.")
        holdings_summary = {
            "position_count": len(portfolio_snapshot.held_positions),
            "cash_total_krw": cash_total_krw,
            "cash_orderable_krw": cash_orderable_krw,
            "cash_next_day_krw": int(portfolio_snapshot.cash_next_day),
            "holdings_market_value_krw": holdings_market_value_krw,
            "raw_balance_total_evaluation_amount_krw": int(
                portfolio_snapshot.total_evaluation_amount
            ),
            "equity_krw": equity_krw,
            "positions": positions,
        }
    else:
        snapshot_status_reason = "잔고 조회 이전에 사이클이 종료되어 계좌 상태를 채우지 못했습니다."
    buy_scan_empty_due_to_rate_limit = (
        bool(rate_limit_triggered)
        and str(rate_limit_source or "").strip() == "buy_scan"
    )
    if (
        buy_scan_requested_count > 0
        and buy_scan_evaluated_count == 0
        and not buy_scan_skipped_reason
        and not buy_scan_empty_due_to_rate_limit
    ):
        integrity_warnings.append("BUY scan 요청 수는 있는데 실제 평가 수가 0입니다.")
    api_quote_request_count = int(
        ((api_usage_summary or {}).get("categories") or {}).get("quote", {}).get("count", 0)
    )
    observed_snapshots_payload = _serialize_observed_market_snapshots(
        observed_market_snapshots,
    )

    execution_summary = None
    if buy_execution_snapshot is not None:
        execution_summary = {
            "symbol": buy_execution_snapshot.symbol,
            "current_price": buy_execution_snapshot.current_price,
            "orderable_cash": buy_execution_snapshot.orderable_cash,
            "orderable_qty": buy_execution_snapshot.orderable_qty,
            "expected_notional_krw": buy_execution_snapshot.expected_notional_krw,
        }

    return {
        "cycle_id": cycle_id,
        "timestamp": timestamp,
        "environment": environment,
        "pid": os.getpid(),
        "account_signature": account_scope["account_signature"],
        "account_environment": account_scope["account_environment"],
        "masked_account_display": account_scope["masked_account_display"],
        "account_scope_changed": bool(runtime_state.get("account_scope_changed")),
        "previous_account_signature": runtime_state.get("previous_account_signature"),
        "integrity_warnings": integrity_warnings,
        "cash_krw": cash_total_krw,
        "orderable_cash_krw": cash_orderable_krw,
        "equity_krw": equity_krw,
        "cycle_elapsed_ms": (timing_summary or {}).get("total_cycle_ms"),
        "lane_scheduler_enabled": bool(
            (timing_summary or {}).get("lane_scheduler_enabled", False)
        ),
        "sell_lane_running": bool(
            (timing_summary or {}).get("sell_lane_running", False)
        ),
        "buy_lane_running": bool(
            (timing_summary or {}).get("buy_lane_running", False)
        ),
        "sell_watch_skipped_reason": (timing_summary or {}).get(
            "sell_watch_skipped_reason"
        ),
        "order_gate_queue_depth": int(
            (timing_summary or {}).get("order_gate_queue_depth", 0) or 0
        ),
        "order_gate_processed_count": int(
            (timing_summary or {}).get("order_gate_processed_count", 0) or 0
        ),
        "order_gate_last_decision": (timing_summary or {}).get(
            "order_gate_last_decision"
        ),
        "order_gate_last_intent_type": (timing_summary or {}).get(
            "order_gate_last_intent_type"
        ),
        "order_gate_last_symbol": (timing_summary or {}).get("order_gate_last_symbol"),
        "order_gate_last_skip_reason": (timing_summary or {}).get(
            "order_gate_last_skip_reason"
        ),
        "buy_quote_prefetch_deadline_seconds": (timing_summary or {}).get(
            "buy_quote_prefetch_deadline_seconds"
        ),
        "buy_quote_prefetch_request_timeout_seconds": (timing_summary or {}).get(
            "buy_quote_prefetch_request_timeout_seconds"
        ),
        "buy_quote_prefetch_max_attempts": (timing_summary or {}).get(
            "buy_quote_prefetch_max_attempts"
        ),
        "buy_quote_prefetch_deadline_hit": bool(
            (timing_summary or {}).get("buy_quote_prefetch_deadline_hit", False)
        ),
        "buy_quote_prefetch_completed": int(
            (timing_summary or {}).get("buy_quote_prefetch_completed", 0) or 0
        ),
        "buy_quote_prefetch_failed": int(
            (timing_summary or {}).get("buy_quote_prefetch_failed", 0) or 0
        ),
        "buy_quote_prefetch_skipped_deadline": int(
            (timing_summary or {}).get("buy_quote_prefetch_skipped_deadline", 0) or 0
        ),
        "buy_quote_prefetch_budget_skipped": int(
            (timing_summary or {}).get("buy_quote_prefetch_budget_skipped", 0) or 0
        ),
        "buy_quote_prefetch_timeout_count": int(
            (timing_summary or {}).get("buy_quote_prefetch_timeout_count", 0) or 0
        ),
        "buy_quote_prefetch_elapsed_ms": (timing_summary or {}).get(
            "buy_quote_prefetch_elapsed_ms"
        ),
        "buy_quote_prefetch_join_wait_ms": (timing_summary or {}).get(
            "buy_quote_prefetch_join_wait_ms"
        ),
        "buy_quote_prefetch_success_ratio": (timing_summary or {}).get(
            "buy_quote_prefetch_success_ratio"
        ),
        "buy_quote_prefetch_worker_detached": bool(
            (timing_summary or {}).get("buy_quote_prefetch_worker_detached", False)
        ),
        "buy_quote_prefetch_cleanup_nonblocking": bool(
            (timing_summary or {}).get("buy_quote_prefetch_cleanup_nonblocking", False)
        ),
        "buy_quote_prefetch_future_done": bool(
            (timing_summary or {}).get("buy_quote_prefetch_future_done", True)
        ),
        "buy_scan_guard_released": bool(
            (timing_summary or {}).get("buy_scan_guard_released", False)
        ),
        "buy_scan_guard_release_reason": (timing_summary or {}).get(
            "buy_scan_guard_release_reason"
        ),
        "quote_age_max_ms": (timing_summary or {}).get("quote_age_max_ms"),
        "quote_age_avg_ms": (timing_summary or {}).get("quote_age_avg_ms"),
        "suspected_shared_rate_bucket": bool(
            (timing_summary or {}).get("suspected_shared_rate_bucket", False)
        ),
        "cycle_budget_exceeded": bool(
            (timing_summary or {}).get("cycle_budget_exceeded", False)
        ),
        "cycle_budget_ms": (timing_summary or {}).get("cycle_budget_ms"),
        "budget_exceeded_stage": (timing_summary or {}).get("budget_exceeded_stage"),
        "buy_scan_requested_count": buy_scan_requested_count,
        "buy_scan_evaluated_count": buy_scan_evaluated_count,
        "buy_scan_skipped_reason": buy_scan_skipped_reason,
        "buy_scan_quote_request_count": buy_scan_quote_request_count,
        "buy_scan_guard_wait_ms": round(float(buy_scan_guard_wait_ms or 0.0), 1),
        "buy_scan_sleep_ms": round(float(buy_scan_sleep_ms or 0.0), 1),
        "buy_scan_effective_total_sleep_ms": round(
            float(buy_scan_effective_total_sleep_ms or 0.0),
            1,
        ),
        "buy_scan_quote_response_ms": round(float(buy_scan_quote_response_ms or 0.0), 1),
        "buy_scan_calc_ms": round(float(buy_scan_calc_ms or 0.0), 1),
        "buy_scan_ranking_ms": round(float(buy_scan_ranking_ms or 0.0), 1),
        "buy_scan_logging_ms": round(float(buy_scan_logging_ms or 0.0), 1),
        "buy_scan_throttle_sleep_events": int(buy_scan_throttle_sleep_events or 0),
        "buy_scan_throttle_min_sleep_ms": (
            None
            if buy_scan_throttle_min_sleep_ms is None
            else round(float(buy_scan_throttle_min_sleep_ms or 0.0), 1)
        ),
        "buy_scan_throttle_total_sleep_ms": round(float(buy_scan_throttle_total_sleep_ms or 0.0), 1),
        "buy_scan_throttle_immediate_pass_count": int(buy_scan_throttle_immediate_pass_count or 0),
        "buy_scan_average_sleep_per_event_ms": round(
            float(buy_scan_average_sleep_per_event_ms or 0.0),
            1,
        ),
        "buy_scan_network_ms": round(float(buy_scan_quote_response_ms or 0.0), 1),
        "buy_scan_pacing_sleep_ms": round(float(buy_scan_throttle_total_sleep_ms or 0.0), 1),
        "sell_evaluated_count": sell_evaluated_count,
        "sell_quote_request_count": sell_quote_request_count,
        "sell_eval_guard_wait_ms": round(float(sell_eval_guard_wait_ms or 0.0), 1),
        "sell_eval_throttle_sleep_ms": round(float(sell_eval_throttle_sleep_ms or 0.0), 1),
        "sell_eval_effective_total_sleep_ms": round(
            float(sell_eval_effective_total_sleep_ms or 0.0),
            1,
        ),
        "sell_eval_quote_response_ms": round(float(sell_eval_quote_response_ms or 0.0), 1),
        "sell_eval_calc_ms": round(float(sell_eval_calc_ms or 0.0), 1),
        "sell_watch_cursor_before": int(sell_watch_cursor_before or 0),
        "sell_watch_cursor_after": int(sell_watch_cursor_after or 0),
        "sell_watch_total_holdings": int(sell_watch_total_holdings or 0),
        "sell_watch_evaluated_symbols": list(sell_watch_evaluated_symbols or []),
        "sell_watch_skipped_symbols": list(sell_watch_skipped_symbols or []),
        "sell_watch_partial": bool(sell_watch_partial),
        "sell_watch_partial_reason": sell_watch_partial_reason,
        "sell_watch_priority_preview": list(sell_watch_priority_preview or []),
        "rate_limit_triggered": bool(rate_limit_triggered),
        "rate_limit_source": rate_limit_source,
        "backoff_applied_seconds": int(backoff_applied_seconds or 0),
        "adaptive_pacing_used": bool(adaptive_pacing_used),
        "adaptive_pacing_extra_delay_ms": round(
            float(adaptive_pacing_extra_delay_ms or 0.0),
            1,
        ),
        "rate_limit_partial_stop": bool(rate_limit_partial_stop),
        "rate_limit_partial_stop_symbol": rate_limit_partial_stop_symbol,
        "rate_limit_partial_completed_count": int(rate_limit_partial_completed_count or 0),
        "rate_limit_partial_remaining_count": int(rate_limit_partial_remaining_count or 0),
        "request_window_size_before_sell_watch": int(request_window_size_before_sell_watch or 0),
        "request_window_size_before_buy_scan": int(request_window_size_before_buy_scan or 0),
        "throttle_guard_triggered": bool(throttle_guard_triggered),
        "throttle_guard_reason": throttle_guard_reason,
        "buy_scan_budget_reserved": bool(buy_scan_budget_reserved),
        "buy_scan_reserve_used": bool(buy_scan_reserve_used),
        "buy_scan_partial_budget": bool(buy_scan_partial_budget),
        "sell_watch_capped_for_buy_scan": bool(sell_watch_capped_for_buy_scan),
        "execution_tail_backoff_drain_ms": round(float(execution_tail_backoff_drain_ms or 0.0), 1),
        "sell_watch_backoff_drain_ms": round(float(sell_watch_backoff_drain_ms or 0.0), 1),
        "api_request_count": int((api_usage_summary or {}).get("total_requests", 0)),
        "api_quote_request_count": api_quote_request_count,
        "timing_summary": timing_summary or {},
        "api_usage_summary": api_usage_summary or {},
        "snapshot_status_reason": snapshot_status_reason,
        "market_session": (
            {
                "session": market_session.session,
                "order_allowed": market_session.order_allowed,
                "reason": market_session.reason,
            }
            if market_session is not None
            else None
        ),
        "holdings_summary": holdings_summary,
        "selected_buy_candidate": _serialize_buy_candidate(selected_buy_candidate),
        "selected_sell_candidate": _serialize_sell_candidate(selected_sell_candidate),
        "sell_watch_final_review": _serialize_sell_candidate(sell_watch_final_review),
        **primary_action_context,
        "scanner_candidates_top": _serialize_top_scan_candidates(
            scan_results,
            limit=top_scan_candidate_limit,
        ),
        "scanner_candidate_count": len(scan_results or ()),
        "observed_market_snapshots": observed_snapshots_payload,
        "observed_market_snapshot_count": len(observed_snapshots_payload),
        "selection_details": selection_details or {},
        **pre_gating_fields,
        **staged_scan_fields,
        "buy_strategy_details": (
            selected_buy_candidate.strategy_result.to_log_payload()
            if selected_buy_candidate is not None
            else None
        ),
        "sell_strategy_details": (
            selected_sell_candidate.sell_decision.to_log_payload()
            if selected_sell_candidate is not None
            else None
        ),
        "buy_execution_snapshot": execution_summary,
        "buy_position_sizing": (
            buy_position_sizing.details if buy_position_sizing is not None else None
        ),
        "sell_position_sizing": (
            sell_position_sizing.details if sell_position_sizing is not None else None
        ),
        "risk_guard_result": {
            "buy": buy_risk_guard,
            "sell": sell_risk_guard,
        },
        "rebalance_preview": rebalance_preview,
        "scheduler_state": scheduler_state or {},
        "daily_pnl_brake_state": daily_pnl_brake_state or {},
        "regime_state": regime_state or {},
        "last_budget_status": runtime_state.get("last_budget_status"),
        "final_action": runtime_state.get("last_action"),
        "final_reason": runtime_state.get("last_decision_reason"),
        "last_selected_symbol": runtime_state.get("last_selected_symbol"),
        "last_order_side": runtime_state.get("last_order_side"),
        "last_sell_check_at": runtime_state.get("last_sell_check_at"),
        "last_buy_scan_at": runtime_state.get("last_buy_scan_at"),
        "intraday_pnl_baseline_krw": runtime_state.get("intraday_pnl_baseline_krw"),
        "intraday_pnl_pct": runtime_state.get("intraday_pnl_pct"),
        "current_brake_state": runtime_state.get("current_brake_state"),
        "current_regime": runtime_state.get("current_regime"),
        "regime_reason": runtime_state.get("regime_reason"),
        "regime_multiplier": runtime_state.get("regime_multiplier"),
        "buy_entries_by_symbol_today": runtime_state.get("buy_entries_by_symbol_today"),
        "last_buy_entry_at_by_symbol": runtime_state.get("last_buy_entry_at_by_symbol"),
        "last_reentry_blocked_at_by_symbol": runtime_state.get(
            "last_reentry_blocked_at_by_symbol"
        ),
        "last_reentry_state_by_symbol": runtime_state.get("last_reentry_state_by_symbol"),
        "last_reentry_reason_by_symbol": runtime_state.get("last_reentry_reason_by_symbol"),
        "last_exit_reason_by_symbol_runtime": runtime_state.get("last_exit_reason_by_symbol"),
        "last_exit_at_by_symbol": runtime_state.get("last_exit_at_by_symbol"),
        "last_exit_price_by_symbol": runtime_state.get("last_exit_price_by_symbol"),
        "last_exit_qty_by_symbol": runtime_state.get("last_exit_qty_by_symbol"),
        "last_exit_was_full_close_by_symbol": runtime_state.get(
            "last_exit_was_full_close_by_symbol"
        ),
        "last_exit_trigger_context_by_symbol": runtime_state.get(
            "last_exit_trigger_context_by_symbol"
        ),
        "pending_sell_intents_by_symbol": runtime_state.get("pending_sell_intents_by_symbol"),
        "reconciliation_summary": runtime_state.get("last_reconciliation_summary"),
        "reconciliation_events": runtime_state.get("last_reconciliation_events"),
        "error": error,
    }


# Writer-side invariant for cycle_snapshots (mirror of the order-log W1 guard).
# One snapshot per cycle carries scan context; the unbounded piece is
# selection_details.candidates. W0 keeps that compact, so this proactive trim is
# the last line of defence against a NEW bulk leak. scanner_candidates_top (top-5)
# is always preserved — it's the summary consumers read. 900KB « the 8MB snapshot
# reader cap (SNAPSHOT_READ_LINE_MAX_BYTES), so a written line is always readable.
SNAPSHOT_LINE_SOFT_MAX_BYTES = 900_000

# F1 (E1): the scan-cycle bulk is scheduler_state.lane_scheduler_runtime_context
# .buy_scan_outcome — a raw scan payload measured at 2.03MB (89% of the line) that
# no persisted-snapshot consumer reads (the top-5 summary is already at top-level
# scanner_candidates_top). Compact scheduler_state at persist time on a copy: drop
# non-essential sub-values above this size so the embedded telemetry stays small
# regardless of which key holds the bulk. Telemetry-only — the caller's in-memory
# snapshot and the order path are untouched.
SCHEDULER_STATE_SUBVALUE_MAX_BYTES = 65_536

# scheduler_state keys the dashboard reads from persisted snapshots — never drop.
_SCHEDULER_STATE_ESSENTIAL_KEYS = frozenset(
    {
        "decision",
        "last_sell_check_at",
        "last_buy_scan_at",
        "sell_check_due",
        "buy_scan_due",
        "cycle_id",
        "lane_scheduler_entered",
        "effective_sell_watch_max_holdings_per_tick",
    }
)

# Top-level snapshot keys the generalized shrink must always preserve.
_SNAPSHOT_SHRINK_ESSENTIAL_KEYS = frozenset(
    {
        "cycle_id",
        "timestamp",
        "final_action",
        "final_reason",
        "error",
        "scanner_candidates_top",
        "selected_buy_candidate",
        "timing_summary",
        "scheduler_state",
        "selection_details",
        "selection_details_truncated",
        "selection_details_original_bytes",
    }
)


def _serialized_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def compact_scheduler_state_for_snapshot(
    scheduler_state: Any,
    *,
    max_subvalue_bytes: int = SCHEDULER_STATE_SUBVALUE_MAX_BYTES,
) -> dict[str, Any]:
    """Return a snapshot-safe copy of ``scheduler_state`` with raw payloads dropped.

    Drops non-essential sub-values (recursively, depth ≤ 2) exceeding
    ``max_subvalue_bytes`` and records them under ``_compacted_dropped``. Essential
    consumer keys are always preserved. Pure — never mutates the input, never
    raises; a non-mapping input yields ``{}``.
    """
    if not isinstance(scheduler_state, dict):
        return {}
    dropped: list[dict[str, Any]] = []

    def _compact(mapping: dict[str, Any], prefix: str, depth: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in mapping.items():
            if key in _SCHEDULER_STATE_ESSENTIAL_KEYS:
                out[key] = value
                continue
            if _serialized_bytes(value) <= max_subvalue_bytes:
                out[key] = value
                continue
            # Oversized & non-essential: recurse into mappings to salvage small
            # siblings; otherwise drop wholesale.
            if isinstance(value, dict) and depth < 2:
                out[key] = _compact(value, f"{prefix}{key}.", depth + 1)
            else:
                dropped.append({"path": f"{prefix}{key}", "bytes": _serialized_bytes(value)})
        return out

    compacted = _compact(scheduler_state, "", 0)
    if dropped:
        compacted["_compacted_dropped"] = dropped
    return compacted


def _prepare_snapshot_for_persist(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Compact scheduler_state on a shallow copy so persistence stays bounded."""
    scheduler_state = snapshot.get("scheduler_state")
    if not isinstance(scheduler_state, dict) or not scheduler_state:
        return snapshot
    prepared = dict(snapshot)
    prepared["scheduler_state"] = compact_scheduler_state_for_snapshot(scheduler_state)
    return prepared


def _drop_until_under_cap(snapshot: dict[str, Any], *, cap: int) -> list[dict[str, Any]]:
    """Drop the largest non-essential top-level keys until under ``cap``.

    Second-defense shrink: keeps the written line under cap even when the bulk
    migrates to a key the targeted trims don't cover. Records each dropped key.
    """
    dropped: list[dict[str, Any]] = []
    while _serialized_bytes(snapshot) > cap:
        candidates = [
            (key, _serialized_bytes(value))
            for key, value in snapshot.items()
            if key not in _SNAPSHOT_SHRINK_ESSENTIAL_KEYS and not key.startswith("_")
        ]
        candidates = [c for c in candidates if c[1] > 0]
        if not candidates:
            break
        candidates.sort(key=lambda item: item[1], reverse=True)
        biggest_key, biggest_bytes = candidates[0]
        del snapshot[biggest_key]
        dropped.append({"key": biggest_key, "bytes": biggest_bytes})
    return dropped


def _shrink_oversize_snapshot(
    snapshot: dict[str, Any], *, original_bytes: int
) -> dict[str, Any]:
    shrunk = dict(snapshot)
    selection_details = shrunk.get("selection_details")
    if isinstance(selection_details, dict) and "candidates" in selection_details:
        trimmed = dict(selection_details)
        dropped = trimmed.pop("candidates", None)
        trimmed["dropped_candidate_count"] = (
            len(dropped) if isinstance(dropped, list) else 0
        )
        shrunk["selection_details"] = trimmed
    shrunk["selection_details_truncated"] = True
    shrunk["selection_details_original_bytes"] = original_bytes
    # Second defense: if still over cap, drop the largest non-essential top-level
    # keys (bulk may have migrated off selection_details.candidates — E1).
    generalized = _drop_until_under_cap(shrunk, cap=SNAPSHOT_LINE_SOFT_MAX_BYTES)
    if generalized:
        shrunk["oversize_dropped_keys"] = generalized
    return shrunk


def persist_cycle_snapshot(snapshot: dict[str, Any]) -> bool:
    try:
        snapshot = _prepare_snapshot_for_persist(snapshot)
        serialized = json.dumps(snapshot, ensure_ascii=False, default=str)
        line_bytes = len(serialized.encode("utf-8"))
        if line_bytes > SNAPSHOT_LINE_SOFT_MAX_BYTES:
            snapshot = _shrink_oversize_snapshot(snapshot, original_bytes=line_bytes)
            serialized = json.dumps(snapshot, ensure_ascii=False, default=str)
            print(
                f"[warn] cycle_snapshot 라인이 {line_bytes} bytes로 상한을 초과해 "
                "비필수 대용량 키를 드롭했습니다 (candidates/oversize keys).",
                file=sys.stderr,
            )
        cycle_snapshots_file = get_cycle_snapshots_path()
        cycle_snapshots_file.parent.mkdir(parents=True, exist_ok=True)
        with cycle_snapshots_file.open("a", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.write("\n")
        return True
    except OSError:
        return False


def load_recent_cycle_snapshots(limit: int = 5) -> list[dict[str, Any]]:
    cycle_snapshots_file = get_cycle_snapshots_path()
    if limit <= 0 or not cycle_snapshots_file.exists():
        return []

    snapshots, _errors = read_jsonl_objects(
        cycle_snapshots_file, max_line_bytes=SNAPSHOT_READ_LINE_MAX_BYTES
    )

    if not snapshots:
        return []

    return snapshots[-limit:]


def build_replay_console_lines(records: list[dict[str, Any]]) -> list[str]:
    lines = ["=== 최근 사이클 리플레이 ==="]
    if not records:
        lines.append("기록된 cycle snapshot 이 없습니다.")
        return lines

    for record in records:
        market_session = record.get("market_session") or {}
        selected_buy = record.get("selected_buy_candidate") or {}
        selected_sell = record.get("selected_sell_candidate") or {}
        selected_name = (
            record.get("selected_primary_action_name")
            or record.get("selected_primary_action_symbol")
            or selected_buy.get("display_name")
            or selected_sell.get("display_name")
            or record.get("last_selected_symbol")
            or "-"
        )
        lines.append(
            f"{record.get('timestamp', '-')} | "
            f"cycle_id={record.get('cycle_id', '-')} | "
            f"session={market_session.get('session', '-')} | "
            f"final_action={record.get('final_action', '-')} | "
            f"selected={selected_name} | "
            f"reason={record.get('final_reason', '-')}"
        )
    return lines
