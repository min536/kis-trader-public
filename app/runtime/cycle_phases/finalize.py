"""``run_cycle`` finalize phase — Stage B-3 slice F.

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 (국면 지도 F행 / "Stage B
실행 사양"): the entire ``finally:`` body of ``app.main.run_cycle`` — cycle
reporting, snapshot/telemetry persistence, and runtime-state finalize — is moved
here **byte-verbatim** (logic/order/output unchanged; only indentation adjusted).
``run_cycle`` keeps the ``try/except/finally`` skeleton and its ``finally`` block
is now a single ``finalize_cycle(...)`` call.

Deps discipline (§3 "deps 번들 주입" — but individual keyword params, not a bundle):
every collaborator that a test patches on ``app.main`` (the 139-name blocklist)
plus the run_cycle-local closure ``note`` is received as an **identically named
keyword parameter**, bound at the call site from ``run_cycle``'s enclosing/module
scope. This keeps the moved body source-identical (source-pin preserved) and keeps
the late-binding patch surface alive (``run_cycle`` resolves the names against the
``app.main`` globals at call time). Pure, un-patched utilities are imported
directly below. This module must NOT import ``app.main``.
"""

from __future__ import annotations

import time
from typing import Any

from app.auth.account_scope import (
    get_performance_snapshots_path,
    get_performance_summary_path,
)
from app.auth.token import get_request_metrics_summary
from app.core.sell_watch_budget import should_update_sell_watch_partial_memory
from app.notifications.account_alerts import maybe_emit_order_log_integrity_alert
from app.core.throttle import get_throttle_metrics_summary
from app.core.runtime_budget import request_metrics_delta as _request_metrics_delta
from app.pipeline import (
    LaneSchedulerRuntimeTelemetry,
    merge_lane_scheduler_runtime_state,
    merge_lane_scheduler_timing_summary,
    prefetch_metrics_from_result,
)
from app.reporting.console import phase_timing_summary as _phase_timing_summary
from app.runtime_state import prune_stale_sell_intents_with_audit
from app.runtime.cycle_phases.prefetch_join import (
    copy_join_guard_fields,
    flatten_prefetch_metrics,
)

def finalize_cycle(
    ctx: Any,
    *,
    settings: Any,
    api_budget_state: Any,
    scheduler_state: Any,
    sell_check_due: Any,
    state: Any,
    cycle_id: Any,
    cycle_started_at: Any,
    cycle_started_perf: Any,
    cycle_budget: Any,
    _api_budget_note_rate_limit: Any,
    BUY_SCAN_LANE_CONTROLLER: Any,
    note: Any,
    _api_budget_backoff_remaining_seconds: Any,
    _api_budget_update_rate_limit_recovery_state: Any,
    _api_budget_update_transient_recovery_state: Any,
    _build_buy_candidate_outcome_records: Any,
    _build_buy_cycle_funnel_stats: Any,
    _format_cycle_summary: Any,
    _get_slack_notifier: Any,
    _print_api_usage: Any,
    _print_buy_scan_metrics: Any,
    _print_cycle_timing: Any,
    _print_runtime_state_summary: Any,
    _print_sell_metrics: Any,
    _resolve_benchmark_snapshot: Any,
    _run_cycle_market_data_quality_sentinel: Any,
    _summarize_api_budget_state: Any,
    _update_recent_market_snapshots: Any,
    _write_slack_runtime_status_snapshot: Any,
    append_candidate_outcomes: Any,
    append_cycle_stats: Any,
    build_cycle_snapshot: Any,
    build_cycle_stats_console_lines: Any,
    build_cycle_stats_daily_summary: Any,
    build_daily_summary: Any,
    build_daily_summary_console_lines: Any,
    build_performance_console_lines: Any,
    build_performance_report: Any,
    get_cycle_snapshots_path: Any,
    get_korean_now: Any,
    get_runtime_state_path: Any,
    persist_cycle_snapshot: Any,
    persist_performance_report: Any,
    save_runtime_state: Any,
) -> None:
    if ctx.buy_quote_prefetch_future is not None:
        if not ctx.buy_quote_prefetch_future.done():
            print("[info] BUY quote prefetch cleanup poll: worker still running")
        prefetch_join = BUY_SCAN_LANE_CONTROLLER.join_active_prefetch(
            timeout_seconds=0.0
        )
        copy_join_guard_fields(
            ctx,
            prefetch_join,
            lane_running=BUY_SCAN_LANE_CONTROLLER.running,
        )
        if prefetch_join.result is not None:
            metrics = prefetch_metrics_from_result(prefetch_join.result)
            flatten_prefetch_metrics(ctx, metrics)
            if bool(metrics["rate_limit_triggered"]):
                ctx.rate_limit_triggered = True
                ctx.rate_limit_source = ctx.rate_limit_source or "buy_scan"
                _api_budget_note_rate_limit(
                    api_budget_state,
                    now=get_korean_now(),
                    source="buy_scan",
                )
                ctx.backoff_applied_seconds = int(
                    _api_budget_backoff_remaining_seconds(
                        api_budget_state,
                        now=get_korean_now(),
                    )
                )
        elif prefetch_join.timed_out:
            ctx.buy_quote_prefetch_deadline_hit = True
            ctx.buy_lane_running = True
            ctx.buy_quote_prefetch_worker_detached = prefetch_join.worker_detached
            ctx.buy_quote_prefetch_cleanup_nonblocking = prefetch_join.cleanup_nonblocking
            ctx.buy_quote_prefetch_future_done = prefetch_join.future_done
            ctx.buy_scan_guard_released = prefetch_join.guard_released
            ctx.buy_scan_guard_release_reason = prefetch_join.guard_release_reason
        elif prefetch_join.status == "failed":
            ctx.buy_quote_prefetch_cleanup_nonblocking = prefetch_join.cleanup_nonblocking
            ctx.buy_quote_prefetch_future_done = prefetch_join.future_done
            ctx.buy_scan_guard_released = prefetch_join.guard_released
            ctx.buy_scan_guard_release_reason = prefetch_join.guard_release_reason
            note("WARN", f"BUY quote prefetch cleanup failed: {prefetch_join.error}")
        ctx.buy_quote_prefetch_future = None
    # W6: market-data quality sentinel — fail-safe, writes the observability
    # artifact every cycle from the snapshots seen this cycle; only emits a
    # Slack alert when MARKET_DATA_QUALITY_ALERTS_ENABLED is set (default off).
    _run_cycle_market_data_quality_sentinel(
        ctx.observed_market_snapshots.values(),
        settings=settings,
        checked_at=cycle_started_at,
        alert_sender=lambda text: _get_slack_notifier().send("market_data_quality", text),
    )
    reporting_started_perf = time.perf_counter()
    reporting_metrics_before = get_request_metrics_summary()
    cycle_snapshot_status = False
    persist_status = {"snapshot_saved": False, "report_saved": False}
    performance_console_lines: list[str] = []
    performance_integrity_warnings: list[str] = []
    if ctx.portfolio_snapshot is not None:
        try:
            benchmark_snapshot = _resolve_benchmark_snapshot(
                settings=settings,
                token=ctx.token,
                observed_market_snapshots=ctx.observed_market_snapshots,
                cached_market_snapshots=state.get("recent_market_snapshots_by_symbol") or {},
            )
            performance_report = build_performance_report(
                portfolio_snapshot=ctx.portfolio_snapshot,
                sell_analysis_results=ctx.sell_analysis_results,
                benchmark_snapshot=benchmark_snapshot,
            )
            performance_integrity_warnings = list(
                performance_report.get("integrity_warnings", [])
            )
            persist_status = persist_performance_report(performance_report)
            performance_console_lines = build_performance_console_lines(
                performance_report
            )
        except Exception as exc:
            print(f"[warn] 성과 리포트 생성 실패: {exc}")
    ctx.timing_summary["reporting"] = _phase_timing_summary(
        elapsed_ms=(time.perf_counter() - reporting_started_perf) * 1000,
        request_delta=_request_metrics_delta(
            reporting_metrics_before,
            get_request_metrics_summary(),
        ),
    )
    ctx.timing_summary["buy_ranking_ms"] = round(float(ctx.buy_ranking_ms or 0.0), 1)
    ctx.timing_summary["buy_scan_guard_wait_ms"] = round(float(ctx.buy_scan_guard_wait_ms or 0.0), 1)
    ctx.timing_summary["buy_scan_sleep_ms"] = round(float(ctx.buy_scan_sleep_ms or 0.0), 1)
    ctx.timing_summary["buy_scan_effective_total_sleep_ms"] = round(
        float(ctx.buy_scan_effective_total_sleep_ms or 0.0),
        1,
    )
    ctx.timing_summary["buy_scan_quote_response_ms"] = round(float(ctx.buy_scan_quote_response_ms or 0.0), 1)
    ctx.timing_summary["buy_scan_quote_parse_ms"] = round(float(ctx.buy_scan_parse_ms or 0.0), 1)
    ctx.timing_summary["buy_scan_score_calc_ms"] = round(float(ctx.buy_scan_score_calc_ms or 0.0), 1)
    ctx.timing_summary["buy_scan_candidate_build_ms"] = round(float(ctx.buy_scan_candidate_build_ms or 0.0), 1)
    ctx.timing_summary["buy_scan_calc_ms"] = round(float(ctx.buy_scan_calc_ms or 0.0), 1)
    ctx.timing_summary["buy_scan_logging_ms"] = round(float(ctx.buy_scan_logging_ms or 0.0), 1)
    ctx.timing_summary["buy_scan_throttle_sleep_events"] = int(ctx.buy_scan_throttle_sleep_events or 0)
    ctx.timing_summary["buy_scan_throttle_min_sleep_ms"] = (
        None
        if ctx.buy_scan_throttle_min_sleep_ms is None
        else round(float(ctx.buy_scan_throttle_min_sleep_ms or 0.0), 1)
    )
    ctx.timing_summary["buy_scan_throttle_total_sleep_ms"] = round(
        float(ctx.buy_scan_throttle_total_sleep_ms or 0.0),
        1,
    )
    ctx.timing_summary["buy_scan_throttle_immediate_pass_count"] = int(
        ctx.buy_scan_throttle_immediate_pass_count or 0
    )
    ctx.timing_summary["buy_scan_average_sleep_per_event_ms"] = round(
        float(ctx.buy_scan_average_sleep_per_event_ms or 0.0),
        1,
    )
    ctx.timing_summary["buy_scan_quote_account_mode"] = ctx.buy_scan_quote_account_mode
    ctx.timing_summary["buy_scan_quote_account_env"] = ctx.buy_scan_quote_account_env
    ctx.timing_summary["buy_quote_prefetch_request_count"] = int(
        ctx.buy_quote_prefetch_request_count or 0
    )
    ctx.timing_summary["buy_quote_prefetch_response_ms"] = round(
        float(ctx.buy_quote_prefetch_response_ms or 0.0),
        1,
    )
    ctx.timing_summary["buy_quote_prefetch_throttle_sleep_ms"] = round(
        float(ctx.buy_quote_prefetch_throttle_sleep_ms or 0.0),
        1,
    )
    ctx.timing_summary["buy_quote_prefetch_join_wait_ms"] = round(
        float(ctx.buy_quote_prefetch_join_wait_ms or 0.0),
        1,
    )
    ctx.timing_summary["buy_quote_prefetch_deadline_seconds"] = round(
        float(ctx.buy_quote_prefetch_deadline_seconds or 0.0),
        3,
    )
    ctx.timing_summary["buy_quote_prefetch_request_timeout_seconds"] = round(
        float(ctx.buy_quote_prefetch_request_timeout_seconds or 0.0),
        3,
    )
    ctx.timing_summary["buy_quote_prefetch_max_attempts"] = int(
        ctx.buy_quote_prefetch_max_attempts or 0
    )
    ctx.timing_summary["buy_quote_prefetch_deadline_hit"] = bool(
        ctx.buy_quote_prefetch_deadline_hit
    )
    ctx.timing_summary["buy_quote_prefetch_completed"] = int(
        ctx.buy_quote_prefetch_completed or 0
    )
    ctx.timing_summary["buy_quote_prefetch_failed"] = int(
        ctx.buy_quote_prefetch_failed or 0
    )
    ctx.timing_summary["buy_quote_prefetch_skipped_deadline"] = int(
        ctx.buy_quote_prefetch_skipped_deadline or 0
    )
    ctx.timing_summary["buy_quote_prefetch_budget_skipped"] = int(
        ctx.buy_quote_prefetch_budget_skipped or 0
    )
    ctx.timing_summary["buy_quote_prefetch_timeout_count"] = int(
        ctx.buy_quote_prefetch_timeout_count or 0
    )
    ctx.timing_summary["buy_quote_prefetch_elapsed_ms"] = round(
        float(ctx.buy_quote_prefetch_elapsed_ms or 0.0),
        1,
    )
    ctx.timing_summary["buy_quote_prefetch_success_ratio"] = round(
        float(ctx.buy_quote_prefetch_success_ratio or 0.0),
        4,
    )
    ctx.timing_summary["buy_quote_prefetch_worker_detached"] = bool(
        ctx.buy_quote_prefetch_worker_detached
    )
    ctx.timing_summary["buy_quote_prefetch_cleanup_nonblocking"] = bool(
        ctx.buy_quote_prefetch_cleanup_nonblocking
    )
    ctx.timing_summary["buy_quote_prefetch_future_done"] = bool(
        ctx.buy_quote_prefetch_future_done
    )
    ctx.timing_summary["buy_scan_guard_released"] = bool(ctx.buy_scan_guard_released)
    ctx.timing_summary["buy_scan_guard_release_reason"] = ctx.buy_scan_guard_release_reason
    ctx.timing_summary["buy_scan_network_ms"] = round(
        float(ctx.buy_scan_quote_response_ms or 0.0),
        1,
    )
    ctx.timing_summary["buy_scan_pacing_sleep_ms"] = round(
        float(ctx.buy_scan_throttle_total_sleep_ms or 0.0),
        1,
    )
    ctx.timing_summary["rebalance_selection_ms"] = (
        round(float(ctx.rebalance_selection_ms), 1)
        if ctx.rebalance_selection_ms is not None
        else None
    )
    ctx.timing_summary["sell_eval_guard_wait_ms"] = round(float(ctx.sell_eval_guard_wait_ms or 0.0), 1)
    ctx.timing_summary["sell_eval_throttle_sleep_ms"] = round(
        float(ctx.sell_eval_throttle_sleep_ms or 0.0),
        1,
    )
    ctx.timing_summary["sell_eval_effective_total_sleep_ms"] = round(
        float(ctx.sell_eval_effective_total_sleep_ms or 0.0),
        1,
    )
    ctx.timing_summary["sell_eval_quote_response_ms"] = round(
        float(ctx.sell_eval_quote_response_ms or 0.0),
        1,
    )
    ctx.timing_summary["sell_eval_calc_ms"] = round(float(ctx.sell_eval_calc_ms or 0.0), 1)
    ctx.cycle_budget_exceeded = ctx.cycle_budget_exceeded or cycle_budget.exceeded
    if ctx.cycle_budget_exceeded and not ctx.budget_exceeded_stage:
        ctx.budget_exceeded_stage = "cycle_end"
    ctx.buy_lane_running = BUY_SCAN_LANE_CONTROLLER.running
    ctx.suspected_shared_rate_bucket = bool(
        ctx.rate_limit_triggered
        and ctx.buy_scan_separate_quote_lane
        and str(ctx.rate_limit_source or "").strip() in {"", "unknown", "buy_scan", "sell_watch"}
    )
    lane_scheduler_runtime_telemetry = LaneSchedulerRuntimeTelemetry(
        enabled=bool(ctx.lane_scheduler_enabled),
        sell_lane_running=bool(ctx.sell_lane_running),
        buy_lane_running=bool(ctx.buy_lane_running),
        buy_lane_previous_scan_id=ctx.buy_lane_previous_scan_id,
        buy_lane_previous_started_at=ctx.buy_lane_previous_started_at,
        sell_watch_skipped_reason=ctx.sell_watch_skipped_reason,
        order_gate_queue_depth=int(ctx.order_gate_queue_depth),
        order_gate_processed_count=int(ctx.order_gate_processed_count),
        order_gate_last_decision=ctx.order_gate_last_decision,
        order_gate_last_intent_type=ctx.order_gate_last_intent_type,
        order_gate_last_symbol=ctx.order_gate_last_symbol,
        order_gate_last_skip_reason=ctx.order_gate_last_skip_reason,
        quote_age_max_ms=ctx.quote_age_max_ms,
        quote_age_avg_ms=ctx.quote_age_avg_ms,
        suspected_shared_rate_bucket=bool(ctx.suspected_shared_rate_bucket),
        cycle_budget_exceeded=bool(ctx.cycle_budget_exceeded),
        cycle_budget_ms=float(ctx.cycle_budget_ms),
        budget_exceeded_stage=ctx.budget_exceeded_stage,
    )
    merge_lane_scheduler_timing_summary(
        ctx.timing_summary,
        lane_scheduler_runtime_telemetry,
        final_overrides=ctx.lane_scheduler_final_overrides,
    )
    ctx.timing_summary["total_cycle_ms"] = round(
        (time.perf_counter() - cycle_started_perf) * 1000,
        1,
    )
    ctx.api_usage_summary = get_request_metrics_summary()
    state["last_cycle_elapsed_ms"] = ctx.timing_summary["total_cycle_ms"]
    state["last_timing_summary"] = ctx.timing_summary
    state["last_api_usage_summary"] = ctx.api_usage_summary
    state["last_buy_scan_requested_count"] = ctx.buy_scan_requested_count
    state["last_buy_scan_evaluated_count"] = ctx.buy_scan_evaluated_count
    state["last_sell_evaluated_count"] = ctx.sell_evaluated_count
    state["last_buy_scan_skipped_reason"] = ctx.buy_scan_skipped_reason
    state["last_buy_scan_quote_account_mode"] = ctx.buy_scan_quote_account_mode
    state["last_buy_scan_quote_account_env"] = ctx.buy_scan_quote_account_env
    state["last_buy_quote_prefetch_request_count"] = int(
        ctx.buy_quote_prefetch_request_count or 0
    )
    state["last_buy_quote_prefetch_join_wait_ms"] = round(
        float(ctx.buy_quote_prefetch_join_wait_ms or 0.0),
        1,
    )
    state["last_buy_quote_prefetch_deadline_hit"] = bool(
        ctx.buy_quote_prefetch_deadline_hit
    )
    state["last_buy_quote_prefetch_success_ratio"] = round(
        float(ctx.buy_quote_prefetch_success_ratio or 0.0),
        4,
    )
    state["buy_quote_prefetch_request_timeout_seconds"] = round(
        float(ctx.buy_quote_prefetch_request_timeout_seconds or 0.0),
        3,
    )
    state["buy_quote_prefetch_max_attempts"] = int(
        ctx.buy_quote_prefetch_max_attempts or 0
    )
    state["buy_quote_prefetch_timeout_count"] = int(
        ctx.buy_quote_prefetch_timeout_count or 0
    )
    state["buy_quote_prefetch_budget_skipped"] = int(
        ctx.buy_quote_prefetch_budget_skipped or 0
    )
    state["buy_quote_prefetch_worker_detached"] = bool(
        ctx.buy_quote_prefetch_worker_detached
    )
    state["buy_quote_prefetch_cleanup_nonblocking"] = bool(
        ctx.buy_quote_prefetch_cleanup_nonblocking
    )
    state["buy_quote_prefetch_future_done"] = bool(
        ctx.buy_quote_prefetch_future_done
    )
    state["buy_scan_guard_released"] = bool(ctx.buy_scan_guard_released)
    state["buy_scan_guard_release_reason"] = ctx.buy_scan_guard_release_reason
    merge_lane_scheduler_runtime_state(
        state,
        timing_summary=ctx.timing_summary,
        telemetry=lane_scheduler_runtime_telemetry,
        final_overrides=ctx.lane_scheduler_final_overrides,
    )
    state["last_throttle_summary"] = get_throttle_metrics_summary()
    state["buy_scan_budget_reserved"] = ctx.buy_scan_budget_reserved
    state["buy_scan_reserve_used"] = ctx.buy_scan_reserve_used
    state["buy_scan_partial_budget"] = ctx.buy_scan_partial_budget
    state["sell_watch_capped_for_buy_scan"] = ctx.sell_watch_capped_for_buy_scan
    state["rate_limit_triggered"] = ctx.rate_limit_triggered
    state["rate_limit_source"] = ctx.rate_limit_source
    state["backoff_applied_seconds"] = ctx.backoff_applied_seconds
    state["api_transient_error_source"] = ctx.api_transient_error_source
    state["api_transient_backoff_applied_seconds"] = (
        ctx.api_transient_backoff_applied_seconds
    )
    state["adaptive_pacing_used"] = ctx.adaptive_pacing_used
    state["adaptive_pacing_extra_delay_ms"] = ctx.adaptive_pacing_extra_delay_ms
    state["rate_limit_partial_stop"] = ctx.rate_limit_partial_stop
    state["rate_limit_partial_stop_symbol"] = ctx.rate_limit_partial_stop_symbol
    state["rate_limit_partial_completed_count"] = ctx.rate_limit_partial_completed_count
    state["rate_limit_partial_remaining_count"] = ctx.rate_limit_partial_remaining_count
    state["request_window_size_before_sell_watch"] = ctx.request_window_size_before_sell_watch
    state["request_window_size_before_buy_scan"] = ctx.request_window_size_before_buy_scan
    state["throttle_guard_triggered"] = ctx.throttle_guard_triggered
    state["throttle_guard_reason"] = ctx.throttle_guard_reason
    state["last_budget_status"] = _summarize_api_budget_state(
        api_budget_state,
        now=get_korean_now(),
    )
    state["last_sell_watch_cursor_before"] = ctx.sell_watch_cursor_before
    state["last_sell_watch_cursor_after"] = ctx.sell_watch_cursor_after
    state["last_sell_watch_total_holdings"] = ctx.sell_watch_total_holdings
    state["last_sell_watch_partial"] = ctx.sell_watch_partial
    state["last_sell_watch_partial_reason"] = ctx.sell_watch_partial_reason
    state["last_sell_watch_evaluated_symbols"] = ctx.sell_watch_evaluated_symbols
    state["last_sell_watch_skipped_symbols"] = ctx.sell_watch_skipped_symbols
    state["last_sell_watch_budget_plan_pressure_level"] = ctx.sell_watch_budget_plan_pressure_level
    state["last_sell_watch_budget_plan_reason"] = ctx.sell_watch_budget_plan_reason
    state["last_sell_watch_budget_plan_limit"] = ctx.sell_watch_budget_plan_limit
    api_budget_state["last_sell_watch_total_holdings"] = ctx.sell_watch_total_holdings
    sell_watch_partial_memory_observed = should_update_sell_watch_partial_memory(
        sell_check_due=bool(sell_check_due),
        market_open=bool(ctx.market_open),
        holding_count=ctx.sell_watch_total_holdings,
    )
    if sell_watch_partial_memory_observed:
        api_budget_state["last_sell_watch_partial"] = ctx.sell_watch_partial
        previous_sell_watch_partial_streak = int(
            api_budget_state.get("consecutive_sell_watch_partial_cycles", 0) or 0
        )
        api_budget_state["consecutive_sell_watch_partial_cycles"] = (
            previous_sell_watch_partial_streak + 1 if ctx.sell_watch_partial else 0
        )
    state["consecutive_sell_watch_partial_cycles"] = int(
        api_budget_state.get("consecutive_sell_watch_partial_cycles", 0) or 0
    )
    # Reset consecutive rate-limit hit counter only after a clean recovery.
    # If backoff is still active, keep the last source and hit count so repeated
    # EGW00201 responses continue to get the stronger exponential backoff.
    _api_budget_update_rate_limit_recovery_state(
        api_budget_state,
        now=get_korean_now(),
        rate_limit_source=ctx.rate_limit_source,
    )
    _api_budget_update_transient_recovery_state(
        api_budget_state,
        now=get_korean_now(),
        transient_error_source=ctx.api_transient_error_source,
    )
    api_budget_state["last_sell_watch_budget_plan_pressure_level"] = (
        ctx.sell_watch_budget_plan_pressure_level
    )
    try:
        cycle_snapshot = build_cycle_snapshot(
            cycle_id=cycle_id,
            timestamp=cycle_started_at,
            environment=ctx.cycle_environment,
            market_session=ctx.session_status,
            portfolio_snapshot=ctx.portfolio_snapshot,
            sell_analysis_results=ctx.sell_analysis_results,
            scan_results=ctx.scan_results,
            selected_buy_candidate=ctx.selected_candidate or ctx.top_analysis_result,
            selected_sell_candidate=ctx.active_sell_analysis or ctx.selected_sell_candidate,
            sell_watch_final_review=ctx.active_sell_analysis or ctx.sell_watch_final_review,
            selection_details=ctx.selection_details,
            buy_execution_snapshot=ctx.execution_snapshot,
            buy_position_sizing=ctx.position_sizing,
            sell_position_sizing=ctx.sell_position_sizing,
            buy_risk_guard=ctx.buy_risk_guard_payload,
            sell_risk_guard=ctx.sell_risk_guard_payload,
            rebalance_preview=ctx.rebalance_buy_preview,
            scheduler_state=scheduler_state,
            daily_pnl_brake_state=ctx.daily_pnl_brake_state,
            regime_state=ctx.regime_state,
            runtime_state=state,
            observed_market_snapshots=ctx.observed_market_snapshots,
            error=ctx.cycle_error,
            top_scan_candidate_limit=settings.buy_scan_top_k_candidates,
            timing_summary=ctx.timing_summary,
            api_usage_summary=ctx.api_usage_summary,
            buy_scan_requested_count=ctx.buy_scan_requested_count,
            buy_scan_evaluated_count=ctx.buy_scan_evaluated_count,
            buy_scan_skipped_reason=ctx.buy_scan_skipped_reason,
            buy_scan_quote_request_count=ctx.buy_scan_quote_request_count,
            buy_scan_guard_wait_ms=ctx.buy_scan_guard_wait_ms,
            buy_scan_sleep_ms=ctx.buy_scan_sleep_ms,
            buy_scan_effective_total_sleep_ms=ctx.buy_scan_effective_total_sleep_ms,
            buy_scan_quote_response_ms=ctx.buy_scan_quote_response_ms,
            buy_scan_calc_ms=ctx.buy_scan_calc_ms,
            buy_scan_ranking_ms=ctx.buy_ranking_ms,
            buy_scan_logging_ms=ctx.buy_scan_logging_ms,
            buy_scan_throttle_sleep_events=ctx.buy_scan_throttle_sleep_events,
            buy_scan_throttle_min_sleep_ms=ctx.buy_scan_throttle_min_sleep_ms,
            buy_scan_throttle_total_sleep_ms=ctx.buy_scan_throttle_total_sleep_ms,
            buy_scan_throttle_immediate_pass_count=ctx.buy_scan_throttle_immediate_pass_count,
            buy_scan_average_sleep_per_event_ms=ctx.buy_scan_average_sleep_per_event_ms,
            sell_evaluated_count=ctx.sell_evaluated_count,
            sell_quote_request_count=ctx.sell_quote_request_count,
            sell_eval_guard_wait_ms=ctx.sell_eval_guard_wait_ms,
            sell_eval_throttle_sleep_ms=ctx.sell_eval_throttle_sleep_ms,
            sell_eval_effective_total_sleep_ms=ctx.sell_eval_effective_total_sleep_ms,
            sell_eval_quote_response_ms=ctx.sell_eval_quote_response_ms,
            sell_eval_calc_ms=ctx.sell_eval_calc_ms,
            sell_watch_cursor_before=ctx.sell_watch_cursor_before,
            sell_watch_cursor_after=ctx.sell_watch_cursor_after,
            sell_watch_total_holdings=ctx.sell_watch_total_holdings,
            sell_watch_evaluated_symbols=ctx.sell_watch_evaluated_symbols,
            sell_watch_skipped_symbols=ctx.sell_watch_skipped_symbols,
            sell_watch_partial=ctx.sell_watch_partial,
            sell_watch_partial_reason=ctx.sell_watch_partial_reason,
            sell_watch_priority_preview=ctx.sell_watch_priority_preview,
            rate_limit_triggered=ctx.rate_limit_triggered,
            rate_limit_source=ctx.rate_limit_source,
            backoff_applied_seconds=ctx.backoff_applied_seconds,
            adaptive_pacing_used=ctx.adaptive_pacing_used,
            adaptive_pacing_extra_delay_ms=ctx.adaptive_pacing_extra_delay_ms,
            rate_limit_partial_stop=ctx.rate_limit_partial_stop,
            rate_limit_partial_stop_symbol=ctx.rate_limit_partial_stop_symbol,
            rate_limit_partial_completed_count=ctx.rate_limit_partial_completed_count,
            rate_limit_partial_remaining_count=ctx.rate_limit_partial_remaining_count,
            request_window_size_before_sell_watch=ctx.request_window_size_before_sell_watch,
            request_window_size_before_buy_scan=ctx.request_window_size_before_buy_scan,
            throttle_guard_triggered=ctx.throttle_guard_triggered,
            throttle_guard_reason=ctx.throttle_guard_reason,
            buy_scan_budget_reserved=ctx.buy_scan_budget_reserved,
            buy_scan_reserve_used=ctx.buy_scan_reserve_used,
            buy_scan_partial_budget=ctx.buy_scan_partial_budget,
            sell_watch_capped_for_buy_scan=ctx.sell_watch_capped_for_buy_scan,
            execution_tail_backoff_drain_ms=ctx.execution_tail_backoff_drain_ms,
            sell_watch_backoff_drain_ms=ctx.sell_watch_backoff_drain_ms,
        )
        buy_cycle_funnel_stats = _build_buy_cycle_funnel_stats(
            cycle_id=cycle_id,
            timestamp=cycle_started_at,
            market_session=ctx.session_status.session if ctx.session_status is not None else None,
            run_mode=settings.run_mode,
            regime_state=ctx.regime_state,
            requested_symbols=ctx.requested_buy_symbols,
            layered_universe=ctx.buy_scan_layered_universe,
            pre_gating=ctx.selection_details.get("pre_gating") or ctx.buy_pre_gating,
            shallow_plan=ctx.buy_scan_shallow_plan,
            raw_scan_results=ctx.raw_scan_results,
            selected_candidate=ctx.selected_candidate,
            runtime_state=state,
            sell_analysis_results=ctx.sell_analysis_results,
            sell_evaluated_count=ctx.sell_evaluated_count,
            api_usage_summary=ctx.api_usage_summary,
            cycle_elapsed_ms=ctx.timing_summary.get("total_cycle_ms"),
            buy_risk_guard_payload=ctx.buy_risk_guard_payload,
        )
        if buy_cycle_funnel_stats.get("buy_non_execution_reason") == "order_log_untrusted":
            # One-shot operator alert: the order-log integrity guard is
            # fail-closed-blocking ALL buys. Emitting here (reporting layer, off
            # the trading-critical path) keeps the guard itself untouched.
            _integrity_details: dict[str, object] = {}
            _guard_payload = ctx.buy_risk_guard_payload
            if isinstance(_guard_payload, dict):
                _guard_results = _guard_payload.get("guard_results")
                if isinstance(_guard_results, dict):
                    _integrity = _guard_results.get("order_log_integrity")
                    if isinstance(_integrity, dict) and isinstance(
                        _integrity.get("details"), dict
                    ):
                        _integrity_details = _integrity["details"]
            maybe_emit_order_log_integrity_alert(
                error_code=_integrity_details.get("order_log_error_code"),
                side="BUY",
                account_signature=str(state.get("account_signature") or "") or None,
                notify=_get_slack_notifier().send,
            )
        cycle_snapshot["buy_funnel_summary"] = {
            key: value
            for key, value in buy_cycle_funnel_stats.items()
            if key
            not in {"ts", "cycle_id", "session", "run_mode", "regime"}
        }
        cycle_snapshot["pre_gate_rejection_counts"] = dict(
            buy_cycle_funnel_stats.get("pre_gate_rejection_counts") or {}
        )
        cycle_snapshot["executed_order_count"] = int(
            buy_cycle_funnel_stats.get("executed_order_count", 0) or 0
        )
        cycle_snapshot["sell_triggered_count"] = int(
            buy_cycle_funnel_stats.get("sell_triggered_count", 0) or 0
        )
        candidate_outcome_rows = _build_buy_candidate_outcome_records(
            cycle_id=cycle_id,
            timestamp=cycle_started_at,
            market_session=ctx.session_status.session if ctx.session_status is not None else None,
            run_mode=settings.run_mode,
            regime_state=ctx.regime_state,
            daily_pnl_brake_state=ctx.daily_pnl_brake_state,
            portfolio_snapshot=ctx.portfolio_snapshot,
            requested_symbols=ctx.requested_buy_symbols,
            layered_universe=ctx.buy_scan_layered_universe,
            pre_gating=ctx.selection_details.get("pre_gating") or ctx.buy_pre_gating,
            shallow_plan=ctx.buy_scan_shallow_plan,
            raw_scan_results=ctx.raw_scan_results,
            scan_results=ctx.scan_results,
            selected_candidate=ctx.selected_candidate,
            runtime_state=state,
        )
        for warning in cycle_snapshot.get("integrity_warnings", []):
            note("WARN", f"cycle snapshot 검증: {warning}")
        cycle_snapshot_status = persist_cycle_snapshot(cycle_snapshot)
        try:
            if not append_candidate_outcomes(
                candidate_outcome_rows,
                ts=cycle_started_at,
            ):
                note(
                    "WARN",
                    "candidate outcome 로그 저장 실패(account-scoped candidate_outcomes_*.jsonl).",
                )
        except Exception as exc:
            note("WARN", f"candidate outcome 로그 저장 실패: {exc}")
        try:
            if not append_cycle_stats(
                buy_cycle_funnel_stats,
                ts=cycle_started_at,
            ):
                note(
                    "WARN",
                    "cycle stats 로그 저장 실패(account-scoped cycle_stats_*.jsonl).",
                )
        except Exception as exc:
            note("WARN", f"cycle stats 로그 저장 실패: {exc}")
    except Exception as exc:
        note("WARN", f"cycle snapshot 저장 실패({get_cycle_snapshots_path(settings)}): {exc}")
    for warning in performance_integrity_warnings:
        note("WARN", f"성과 데이터 검증: {warning}")
    for line in performance_console_lines:
        print(line)
    if performance_console_lines:
        print(
            "성과 파일 저장: "
            f"snapshot={'성공' if persist_status['snapshot_saved'] else '실패'}, "
            f"summary={'성공' if persist_status['report_saved'] else '실패'}"
        )
    if performance_console_lines and not persist_status.get("report_saved"):
        note("WARN", f"성과 요약 저장 실패({get_performance_summary_path(settings)}). 다음 cycle은 계속 진행합니다.")
    if performance_console_lines and not persist_status.get("snapshot_saved"):
        note("WARN", f"성과 snapshot 저장 실패({get_performance_snapshots_path(settings)}). 다음 cycle은 계속 진행합니다.")
    performance_write_status = (
        None
        if ctx.portfolio_snapshot is None and not performance_console_lines
        else bool(persist_status.get("report_saved"))
    )
    state["last_snapshot_write_ok"] = cycle_snapshot_status
    state["last_performance_write_ok"] = performance_write_status
    state["last_warning_count"] = ctx.cycle_warning_count
    state["last_error_count"] = ctx.cycle_error_count
    state["last_final_action"] = state.get("last_action")
    state["last_final_reason"] = state.get("last_decision_reason")
    if not cycle_snapshot_status:
        state["snapshot_write_failures_today"] = int(
            state.get("snapshot_write_failures_today", 0) or 0
        ) + 1
        state["last_snapshot_write_failed_at"] = get_korean_now().isoformat()
    if performance_console_lines and not persist_status.get("report_saved"):
        state["performance_write_failures_today"] = int(
            state.get("performance_write_failures_today", 0) or 0
        ) + 1
        state["last_performance_write_failed_at"] = get_korean_now().isoformat()
    cycle_result_line = _format_cycle_summary(
        market_session=ctx.session_status.session if ctx.session_status is not None else None,
        final_action=state.get("last_action"),
        sell_status=ctx.sell_status_text,
        buy_status=ctx.buy_status_text,
        requests=int(ctx.api_usage_summary.get("total_requests", 0)),
        elapsed_ms=float(ctx.timing_summary["total_cycle_ms"]),
        warning_count=ctx.cycle_warning_count,
        error_count=ctx.cycle_error_count,
        rate_limit_triggered=ctx.rate_limit_triggered,
        rate_limit_source=ctx.rate_limit_source,
        adaptive_pacing_used=ctx.adaptive_pacing_used,
        adaptive_pacing_extra_delay_ms=ctx.adaptive_pacing_extra_delay_ms,
    )
    state["last_cycle_result"] = cycle_result_line
    _update_recent_market_snapshots(
        state,
        observed_market_snapshots=ctx.observed_market_snapshots,
    )
    # Stale SELL-intent safety net at the convergence point of both runtime paths:
    # the lane-scheduler path skips account_snapshot (where reconciliation prunes),
    # so a stranded reservation would otherwise survive every lane cycle
    # (docs/todo_20260710.md §A-4b W6). Legacy path: reconciliation already
    # pruned → no-op. finalize runs inside run_cycle's `finally:`, so never let
    # this mask the original exception.
    try:
        for released_intent in prune_stale_sell_intents_with_audit(state):
            note(
                "WARN",
                "stale SELL intent 해제: "
                f"{released_intent['symbol']} qty={released_intent['qty']} "
                f"age={released_intent['age_minutes']}m "
                f"(last_order_action={released_intent['last_order_action'] or '-'})",
            )
    except Exception as exc:
        note("WARN", f"stale SELL intent prune 실패: {exc}")
    ctx.state_write_ok = save_runtime_state(state)
    if not ctx.state_write_ok:
        note("WARN", f"runtime_state 저장 실패({get_runtime_state_path(settings)}). 메모리 상태로만 cycle을 마무리합니다.")
    _print_cycle_timing(timing_summary=ctx.timing_summary)
    _print_api_usage(ctx.api_usage_summary)
    _print_sell_metrics(
        holdings_count=(
            ctx.portfolio_snapshot.position_count if ctx.portfolio_snapshot is not None else 0
        ),
        evaluated_count=ctx.sell_evaluated_count,
        quote_request_count=ctx.sell_quote_request_count,
        elapsed_ms=float(
            ((ctx.timing_summary.get("sell_evaluation") or {}).get("elapsed_ms", 0.0))
        ),
        guard_wait_ms=ctx.sell_eval_guard_wait_ms,
        throttle_sleep_ms=ctx.sell_eval_throttle_sleep_ms,
        effective_total_sleep_ms=ctx.sell_eval_effective_total_sleep_ms,
        quote_response_ms=ctx.sell_eval_quote_response_ms,
        calc_ms=ctx.sell_eval_calc_ms,
        cursor_before=ctx.sell_watch_cursor_before,
        cursor_after=ctx.sell_watch_cursor_after,
        partial=ctx.sell_watch_partial,
        skipped_count=len(ctx.sell_watch_skipped_symbols),
    )
    _print_buy_scan_metrics(
        requested_count=ctx.buy_scan_requested_count,
        evaluated_count=ctx.buy_scan_evaluated_count,
        quote_request_count=ctx.buy_scan_quote_request_count,
        top_k_count=ctx.buy_scan_top_k_count,
        skipped_reason=ctx.buy_scan_skipped_reason,
        guard_wait_ms=ctx.buy_scan_guard_wait_ms,
        sleep_ms=ctx.buy_scan_sleep_ms,
        effective_total_sleep_ms=ctx.buy_scan_effective_total_sleep_ms,
        quote_response_ms=ctx.buy_scan_quote_response_ms,
        quote_calc_ms=ctx.buy_scan_calc_ms,
        quote_parse_ms=ctx.buy_scan_parse_ms,
        score_calc_ms=ctx.buy_scan_score_calc_ms,
        candidate_build_ms=ctx.buy_scan_candidate_build_ms,
        ranking_ms=ctx.buy_ranking_ms,
        logging_ms=ctx.buy_scan_logging_ms,
        throttle_sleep_events=ctx.buy_scan_throttle_sleep_events,
        throttle_min_sleep_ms=ctx.buy_scan_throttle_min_sleep_ms,
        throttle_total_sleep_ms=ctx.buy_scan_throttle_total_sleep_ms,
        throttle_immediate_pass_count=ctx.buy_scan_throttle_immediate_pass_count,
        average_sleep_per_event_ms=ctx.buy_scan_average_sleep_per_event_ms,
        sample_symbols=ctx.buy_scan_sample_symbols,
    )
    daily_summary = build_daily_summary()
    if not _write_slack_runtime_status_snapshot(
        runtime_state=state,
        daily_summary=daily_summary,
        session_status=ctx.session_status,
    ):
        print("[warn] Slack runtime status snapshot 저장 실패. 다음 cycle은 계속 진행합니다.")
    for line in build_daily_summary_console_lines(daily_summary):
        print(line)
    # Tasks 4 & 5: core/non-core bucket breakdown and buy non-execution drop histogram
    try:
        _cycle_stats_summary = build_cycle_stats_daily_summary()
        for line in build_cycle_stats_console_lines(_cycle_stats_summary):
            print(line)
    except Exception as _cs_exc:
        print(f"[warn] cycle stats 일일 집계 출력 실패: {_cs_exc}")
    print(f"cycle snapshot 저장: {'성공' if cycle_snapshot_status else '실패'}")
    print(cycle_result_line)
    _print_runtime_state_summary(state, state_write_ok=ctx.state_write_ok)
