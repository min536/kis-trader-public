import json
import math
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Mapping
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth.settings import (
    build_startup_sanity_report,
    build_runtime_parameter_validation_report,
    get_slack_notify_order_submitted,
    get_settings,
    PROJECT_ROOT,
)
from app.autotuner.runtime_activation import (
    apply_high_risk_overrides,
    apply_runtime_overrides,
)
from app.auth.account_scope import (
    get_account_scope_context,
    get_account_signature,
    get_cycle_snapshots_path,
    get_order_log_path,
    get_performance_snapshots_path,
    get_performance_summary_path,
    get_runtime_state_path,
    sync_account_scope_meta,
)
from app.core.session_lock import (
    acquire_app_main_lock,
    AppMainAlreadyRunningError,
)
from app.core.costs import calc_net_pnl
from app.core.error_classification import (
    looks_like_rate_limit_error as _looks_like_rate_limit_error,
    looks_like_transient_api_error as _looks_like_transient_api_error,
    rate_limit_source_from_exception as _rate_limit_source_from_exception,
    rate_limit_source_from_response_body as _rate_limit_source_from_response_body,
    transient_api_source_from_exception as _transient_api_source_from_exception,
)
from app.auth.token import (
    ApiHttpError,
    get_request_metrics_summary,
    issue_access_token,
    is_rate_limit_response,
    reset_request_metrics,
)
from app.core.formatters import (
    format_bps as _format_bps,
    format_krw,
    format_signed_krw as _format_signed_krw,
)
from app.core.market_session import (
    build_market_session_console_lines,
    get_korean_market_session,
)
from app.core.sell_watch_budget import (
    build_sell_watch_budget_plan,
    compute_effective_sell_check_interval_seconds,
    should_update_sell_watch_partial_memory,
)
from app.core.sell_watch_cursor import (
    build_sell_watch_cursor_plan,
    retry_anchor_to_restore_after_empty_budget,
)
from app.core.order_log import log_order_event
from app.core.order_log_rotation import rotate_order_log_if_oversized
from app.core.file_read_limits import local_read_max_bytes
from app.core.runtime_budget import (
    api_budget_backoff_active as _api_budget_backoff_active,
    api_budget_backoff_remaining_seconds as _api_budget_backoff_remaining_seconds,
    api_budget_can_request as _api_budget_can_request,
    api_budget_can_quote as _api_budget_can_quote,
    api_budget_min_wait_for_request_slot as _api_budget_min_wait_for_request_slot,
    api_budget_note_transient_api_error as _api_budget_note_transient_api_error,
    api_budget_preserves_buy_scan_reserve as _api_budget_preserves_buy_scan_reserve,
    api_budget_register_measured_extra_requests as _api_budget_register_measured_extra_requests,
    api_budget_register_request as _api_budget_register_request,
    api_budget_register_requests as _api_budget_register_requests,
    api_budget_remaining_requests as _api_budget_remaining_requests,
    api_budget_remaining_quotes as _api_budget_remaining_quotes,
    api_budget_request_window_size as _api_budget_request_window_size,
    api_budget_transient_backoff_active as _api_budget_transient_backoff_active,
    api_budget_transient_backoff_remaining_seconds as _api_budget_transient_backoff_remaining_seconds,
    build_api_budget_state as _build_api_budget_state,
    buy_scan_reserve_active as _buy_scan_reserve_active,
    prune_recent_rate_limit_hits as _prune_recent_rate_limit_hits,
    request_metrics_delta as _request_metrics_delta,
    summarize_api_budget_state as _summarize_api_budget_state,
    api_budget_update_rate_limit_recovery_state as _api_budget_update_rate_limit_recovery_state,
    api_budget_update_transient_recovery_state as _api_budget_update_transient_recovery_state,
)
from app.runtime.session_loop import (
    build_cycle_id as _build_cycle_id,
    build_scheduler_tick_decision as _build_scheduler_tick_decision,
    is_due as _is_due,
    note_rate_limit_backoff as _api_budget_note_rate_limit,
    print_engine_schedule_state as _print_engine_schedule_state,
    run_session_loop as _runtime_run_session_loop,
)
from app.runtime.cycle_phases import CycleContext
from app.runtime.cycle_phases.account_snapshot import run_account_snapshot_phase
from app.runtime.cycle_phases.budget_gate import run_budget_gate
from app.runtime.cycle_phases.buy_order_phase import run_buy_order_phase
from app.runtime.cycle_phases.buy_scan_phase import run_buy_scan_phase
from app.runtime.cycle_phases.finalize import finalize_cycle
from app.runtime.cycle_phases.prefetch_join import (
    copy_join_guard_fields,
    flatten_prefetch_metrics,
)
from app.runtime.cycle_phases.regime_phase import run_regime_phase
from app.runtime.cycle_phases.sell_order_phase import run_sell_order_phase
from app.runtime.cycle_phases.sell_watch_phase import run_sell_watch_phase
from app.runtime.cycle_phases.session_gate import run_session_gate
from app.runtime.sell_selftest import (
    print_test_mode,
    run_sell_guard_selftest,
    run_sell_test_cycle,
    sell_test_active,
)
from app.runtime.scan_only_diagnostic import (
    build_scan_only_diagnostic_portfolio_snapshot as _build_scan_only_diagnostic_portfolio_snapshot,
    build_scan_only_diagnostic_sell_analyses as _runtime_build_scan_only_diagnostic_sell_analyses,
    build_scan_only_diagnostic_summary as _runtime_build_scan_only_diagnostic_summary,
    build_scan_only_runtime_mode_preview as _runtime_build_scan_only_runtime_mode_preview,
    print_scan_only_diagnostic_buy_scan as _print_scan_only_diagnostic_buy_scan,
    print_scan_only_diagnostic_summary as _print_scan_only_diagnostic_summary,
    print_scan_only_notice as _print_scan_only_notice,
    print_scan_only_runtime_mode_preview as _print_scan_only_runtime_mode_preview,
)
from app.core.throttle import (
    get_adaptive_pacing_summary,
    get_throttle_metrics_summary,
    note_rate_limit,
    reset_throttle_metrics,
)
from app.core.time_utils import KOREA_TZ, get_korean_now
from app.core.reconciliation import (
    sync_reconciliation_state as _sync_reconciliation_state,
    print_reconciliation_report,
)
from app.domestic_stock.balance import inquire_balance
from app.domestic_stock.order import buy_market, sell_market
from app.domestic_stock.orderable import inquire_orderable_cash
from app.domestic_stock.quote import inquire_price
from app.execution import calculate_position_sizing, calculate_sell_position_sizing
from app.execution.order_guard import (
    evaluate_buy_order_guard,
)
from app.execution.schema import ExecutionSnapshot
from app.market_data.live_snapshot import (
    get_live_snapshot_signal,
    live_snapshot_status,
    load_live_snapshot_symbols,
    live_snapshot_worker_health,
)
from app.market_data.benchmark import (
    resolve_benchmark_snapshot as _resolve_benchmark_snapshot,
)
from app.market_data.schema import build_market_snapshot
from app.notifications.bottleneck import KIS_RATE_LIMIT_BACKOFF
from app.notifications.runtime_alerts import (
    get_slack_notifier as _get_slack_notifier,
    record_bottleneck as _record_bottleneck,
    record_cycle_health as _record_cycle_health,
    record_main_loop_exception_if_needed as _record_main_loop_exception_if_needed,
)
from app.notifications.runtime_status_snapshot import (
    build_slack_status_snapshot,
    write_slack_status_snapshot,
)
from app.notifications.main_runtime_hooks import (
    run_cycle_market_data_quality_sentinel as _run_cycle_market_data_quality_sentinel,
    slack_session_status_text as _slack_session_status_text,
)
from app.notifications.order_events import (
    build_order_slack_notification_payload as _build_order_slack_notification_payload,
    slack_event_type_for_order_action as _slack_event_type_for_order_action,
)
from app.core.order_log import classify_order_rejection
from app.notifications.account_alerts import maybe_emit_fatal_account_alert
from app.notifications.reconciliation_alerts import maybe_notify_manual_trade_suspects
from app.portfolio.schema import build_portfolio_snapshot
from app.pipeline import (
    BUY_SCAN_LANE_CONTROLLER,
    CycleBudget,
    OrderGate,
    SellIntent,
    LaneSchedulerRuntimeTelemetry,
    apply_lane_buy_scan_outcome_to_context,
    merge_lane_scheduler_runtime_state,
    merge_lane_scheduler_timing_summary,
    prefetch_metrics_from_result,
    restrict_symbols_to_prefetched,
    run_lane_scheduler_main_bridge,
    summarize_order_gate_result,
)
from app.reporting.daily_summary import (
    build_daily_summary,
    build_daily_summary_console_lines,
    build_cycle_stats_daily_summary,
    build_cycle_stats_console_lines,
)
from app.reporting.cycle_snapshots import (
    build_cycle_snapshot,
    persist_cycle_snapshot,
)
from app.reporting.candidate_outcome_logger import (
    append_candidate_outcomes,
    append_cycle_stats,
)
import app.execution.sell_flow as _sell_flow
from app.execution.buy_flow import (
    build_preview_orderable_output_from_portfolio as _build_preview_orderable_output_from_portfolio,
    build_rebalance_buy_preview as _build_rebalance_buy_preview,
    print_rebalance_buy_preview as _print_rebalance_buy_preview,
)
import app.execution.buy_flow as _buy_flow
import app.reporting.runtime_snapshots as _runtime_snapshots
from app.reporting.runtime_snapshots import (
    log_engine_event as _log_engine_event,
    print_last_action as _print_last_action,
    record_cycle_action as _record_cycle_action,
    print_runtime_state_summary as _print_runtime_state_summary,
    build_buy_candidate_outcome_records as _build_buy_candidate_outcome_records,
    build_buy_cycle_funnel_stats as _build_buy_cycle_funnel_stats,
    update_recent_market_snapshots as _update_recent_market_snapshots,
)
from app.portfolio.equity_state import (
    build_account_state_payload,
    build_current_drawdown_state,
)
from app.reporting.performance import (
    build_performance_console_lines,
    build_performance_report,
    persist_performance_report,
)
from app.reporting.performance import (
    build_today_realized_summary as _build_today_realized_summary,
)
from app.reporting.console import (
    emit_status as _emit_status,
    print_buy_strategy as _print_buy_strategy,
    print_buy_score_summary as _print_buy_score_summary,
    print_sell_decision as _print_sell_decision,
    print_sell_preview as _print_sell_preview,
    print_buy_orderable_preview as _print_buy_orderable_preview,
    print_runtime_mode as _print_runtime_mode,
    print_applied_settings as _print_applied_settings,
    print_runtime_parameter_validation as _print_runtime_parameter_validation,
    print_startup_sanity_report as _print_startup_sanity_report,
    print_cycle_header as _print_cycle_header,
    format_cycle_summary as _format_cycle_summary,
    phase_timing_summary as _phase_timing_summary,
    print_cycle_timing as _print_cycle_timing,
    print_api_usage as _print_api_usage,
    print_buy_scan_metrics as _print_buy_scan_metrics,
    print_sell_metrics as _print_sell_metrics,
    print_portfolio_positions as _print_portfolio_positions,
    print_account_balance_interpretation as _print_account_balance_interpretation,
    print_today_bought_tracking as _print_today_bought_tracking,
    print_today_performance_summary as _print_today_performance_summary,
    print_rebalance_skip as _print_rebalance_skip,
    _print_cycle_conclusion as _print_cycle_conclusion,
    _print_buy_pre_gating_summary as _print_buy_pre_gating_summary,
    _print_buy_runtime_filter_summary as _print_buy_runtime_filter_summary,
)
from app.reporting.cycle_context import (
    _build_position_sizing_block_context as _build_position_sizing_block_context,
    _build_concentration_metrics as _build_concentration_metrics,
    _serialize_rebalance_holding_option as _serialize_rebalance_holding_option,
    _serialize_replacement_candidate_option as _serialize_replacement_candidate_option,
    _build_rebalance_pair_evaluation as _build_rebalance_pair_evaluation,
)
from app.risk.guards import (
    build_risk_guard_console_lines,
    build_risk_guard_skipped_console_lines,
    evaluate_buy_risk_guards,
    evaluate_sell_risk_guards,
    serialize_risk_evaluation_for_log,
)
from app.risk.pnl_brake import (
    build_daily_pnl_brake_observability as _build_daily_pnl_brake_observability,
    build_daily_pnl_brake_state as _build_daily_pnl_brake_state,
    clear_daily_pnl_pause_if_expired as _risk_clear_daily_pnl_pause_if_expired,
    daily_pnl_brake_display_status as _daily_pnl_brake_display_status,
    print_daily_pnl_brake_state as _print_daily_pnl_brake_state,
)
from app.risk.regime import (
    build_regime_state as _risk_build_regime_state,
    print_premarket_wait_notice as _print_premarket_wait_notice,
    print_regime_state as _print_regime_state,
    should_run_light_session_cycle as _should_run_light_session_cycle,
)
from app.scanner import (
    calculate_selection_score,
    build_scan_console_lines,
    build_universe_console_lines,
    get_last_scan_diagnostics,
    resolve_mock_buy_price_floor_krw,
    scan_target_symbols,
    serialize_selection_details,
    select_top_analysis_result,
    select_top_candidate,
)
from app.scanner.quote_account import (
    buy_scan_uses_separate_quote_account,
    prefetch_buy_scan_prices,
)
from app.strategy.sell_decision import (
    SellAnalysisResult,
    build_sell_analysis,
    build_sell_watch_priority,
    select_top_sell_analysis,
    select_top_sell_candidate,
)
from app.strategy.reentry import evaluate_reentry_eligibility
from app.runtime_state import (
    load_runtime_state,
    mark_buy_blocked,
    record_reentry_decision,
    save_runtime_state,
    set_last_decision,
    start_cycle,
)
import app.scanner.runtime_scan as _rs
from app.scanner.runtime_scan import (
    build_buy_scan_pre_gating as _build_buy_scan_pre_gating,
    normalize_pre_gating_payload as _normalize_pre_gating_payload,
    normalize_buy_funnel_reason as _normalize_buy_funnel_reason,
    select_buy_scan_profile as _select_buy_scan_profile,
    build_buy_scan_layered_universe as _build_buy_scan_layered_universe,
    build_buy_scan_shallow_plan as _build_buy_scan_shallow_plan,
    build_buy_scan_deep_eval_symbols as _build_buy_scan_deep_eval_symbols,
    cap_buy_scan_deep_eval_symbols_for_api_budget as _cap_buy_scan_deep_eval_symbols_for_api_budget,
    print_buy_scan_stage_summary as _print_buy_scan_stage_summary,
    apply_buy_runtime_guards_to_scan_results as _apply_buy_runtime_guards_to_scan_results,
)


def _send_order_slack_notification(
    *,
    side: str,
    action: str,
    symbol: str,
    qty: int,
    status: str,
    reason: str | None = None,
    submitted_price_krw: int | None = None,
) -> None:
    event_type = _slack_event_type_for_order_action(action)
    if not event_type:
        return
    if event_type == "order_submitted" and not get_slack_notify_order_submitted():
        return

    try:
        payload = _build_order_slack_notification_payload(
            side=side,
            action=action,
            symbol=symbol,
            qty=qty,
            status=status,
            reason=reason,
            submitted_price_krw=submitted_price_krw,
        )
        notifier = _get_slack_notifier()
        notifier.send(
            event_type,
            payload.message,
            symbol=payload.symbol,
            details=payload.details,
        )
        # Fatal account-rejection one-shot operator alert. Observation-only: a
        # KIS 40910000 ("모의투자 주문 불가한 계좌") rejection means the account
        # itself is blocked; surface a distinct, session-deduped account.blocked
        # event so the operator can act. classify/emit are both total and
        # exception-swallowing, and this whole wrapper is already inside the
        # try boundary, so it can never crash an order-failure branch.
        if action in ("order_failed", "sell_order_failed") and (
            classify_order_rejection(reason) == "fatal_account"
        ):
            try:
                account_signature = get_account_signature()
            except Exception:
                account_signature = None
            maybe_emit_fatal_account_alert(
                reason=reason,
                side=side,
                symbol=symbol,
                account_signature=account_signature,
                notify=notifier.send,
            )
    except Exception as exc:
        print(f"[warn] Slack 주문 알림 실패: {exc.__class__.__name__}")


# main.py-boundary: allow-helper — held in main.py (NOT migrated in Stage A2-c):
# tests/test_main_slack_order_hooks.py:270-281 patches the collaborator
# `write_slack_status_snapshot` in main's scope, so a static migration to
# app.notifications.runtime_status_snapshot would bypass that patch. Per the
# slimming-plan §2.1 core constraint this stays main-scoped until Stage B.
def _write_slack_runtime_status_snapshot(
    *,
    runtime_state: Mapping[str, object],
    daily_summary=None,
    session_status=None,
) -> bool:
    try:
        slack_status_snapshot = build_slack_status_snapshot(
            runtime_state=runtime_state,
            daily_summary=daily_summary,
            session_status=_slack_session_status_text(session_status),
        )
        return write_slack_status_snapshot(slack_status_snapshot)
    except Exception:
        return False


_build_math_sizing_context = _buy_flow.build_math_sizing_context


# Bodies moved verbatim to app/runtime/sell_selftest.py (R1 core slimming);
# the legacy underscore names stay bound so run_cycle call sites and
# characterization stubs keep working.
_print_test_mode = print_test_mode
_sell_test_active = sell_test_active


_run_sell_guard_selftest = run_sell_guard_selftest


# main.py-boundary: allow-helper — held in main.py (NOT migrated in Stage A2-e):
# tests/test_autotuner_runtime_activation.py:161-189 patches the collaborator
# `apply_runtime_overrides` in main's scope, so a static migration to
# app.runtime.session_loop would bypass that patch. Per the slimming-plan §2.1
# core constraint this stays main-scoped until Stage B.
def _build_runtime_rate_control(
    *,
    settings,
    api_budget_state: dict[str, object],
    now: datetime,
) -> dict[str, object]:
    from app.runtime.session_loop import build_runtime_rate_control

    runtime_rate_control = build_runtime_rate_control(
        settings=settings,
        api_budget_state=api_budget_state,
        now=now,
    )
    return apply_runtime_overrides(
        runtime_rate_control,
        settings=settings,
        project_root=PROJECT_ROOT,
        now=now,
    )


_build_scan_only_runtime_mode_preview = partial(
    _runtime_build_scan_only_runtime_mode_preview,
    build_runtime_rate_control=_build_runtime_rate_control,
    now_fn=get_korean_now,
)

_build_scan_only_diagnostic_summary = partial(
    _runtime_build_scan_only_diagnostic_summary,
    daily_pnl_brake_display_status=_daily_pnl_brake_display_status,
)


def _run_session_loop(*, settings) -> None:
    return _runtime_run_session_loop(
        settings=settings,
        build_api_budget_state=_build_api_budget_state,
        build_runtime_rate_control=_build_runtime_rate_control,
        compute_effective_sell_check_interval_seconds=compute_effective_sell_check_interval_seconds,
        is_due_callback=_is_due,
        build_scheduler_tick_decision=_build_scheduler_tick_decision,
        api_budget_transient_backoff_remaining_seconds=_api_budget_transient_backoff_remaining_seconds,
        api_budget_backoff_remaining_seconds=_api_budget_backoff_remaining_seconds,
        run_cycle=run_cycle,
        emit_status=_emit_status,
        record_main_loop_exception_if_needed=_record_main_loop_exception_if_needed,
        get_now=get_korean_now,
        sleep=time.sleep,
        print_message=print,
        record_cycle_health=_record_cycle_health,
    )


def _wait_for_execution_request_budget(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
    phase: str,
    request_cost: int,
    request_reserve: int,
    note_callback: Callable[[str, str], None],
) -> float:
    if _api_budget_backoff_active(
        api_budget_state,
        now=now,
    ) or _api_budget_transient_backoff_active(api_budget_state, now=now):
        return 0.0
    wait_seconds = _api_budget_min_wait_for_request_slot(
        api_budget_state,
        now=now,
        request_cost=request_cost,
        request_reserve=request_reserve,
    )
    if wait_seconds <= 0:
        return 0.0
    wait_ms = round(wait_seconds * 1000.0, 1)
    reserve_text = (
        f" | preserve_follow_up_requests={request_reserve}"
        if request_reserve > 0
        else ""
    )
    print(
        "[info] execution reserve wait"
        f" | phase={phase} | wait={int(round(wait_ms))}ms{reserve_text}"
    )
    note_callback(
        "INFO",
        "execution tail request reserve를 확보하기 위해 "
        f"{phase} 전에 {int(round(wait_ms))}ms 대기합니다.",
    )
    time.sleep(wait_seconds)
    return wait_ms


_clear_daily_pnl_pause_if_expired = _risk_clear_daily_pnl_pause_if_expired


def _build_regime_state(
    *,
    settings,
    daily_pnl_brake_state: dict[str, object] | None,
    drawdown_state: dict[str, object] | None,
) -> dict[str, object]:
    regime_state = _risk_build_regime_state(
        settings=settings,
        daily_pnl_brake_state=daily_pnl_brake_state,
        drawdown_state=drawdown_state,
    )
    # Phase 4 autotuner Tier-B activation (default-OFF, mock-only, safer-of):
    # can only lengthen rebuy cooldown / shrink same-symbol cap vs the protective
    # regime clamp; never raises. No-op unless explicitly enabled in mock.
    return apply_high_risk_overrides(
        regime_state,
        settings=settings,
        project_root=PROJECT_ROOT,
        now=get_korean_now(),
    )


# Body moved verbatim to app/strategy/sell_decision.py (R1 core slimming);
# the legacy underscore name stays bound so run_cycle call sites and
# characterization stubs keep working.
_build_sell_analysis = build_sell_analysis


def _run_sell_order_flow(
    *,
    state: dict,
    settings,
    token: str,
    portfolio_snapshot,
    analysis: SellAnalysisResult,
    sell_sizing,
    sell_log_context: dict[str, object],
    sell_raw_response: dict[str, object],
    market_open: bool,
    session_status,
    cycle_reason: str,
    cycle_action_label: str,
    is_rebalance: bool = False,
    flow_context: dict[str, object] | None = None,
    api_budget_state: dict[str, object] | None = None,
    wait_for_execution_request_budget: Callable[..., float] | None = None,
) -> bool:
    return _sell_flow.run_sell_order_flow(
        state=state,
        settings=settings,
        token=token,
        portfolio_snapshot=portfolio_snapshot,
        analysis=analysis,
        sell_sizing=sell_sizing,
        sell_log_context=sell_log_context,
        sell_raw_response=sell_raw_response,
        market_open=market_open,
        session_status=session_status,
        cycle_reason=cycle_reason,
        cycle_action_label=cycle_action_label,
        is_rebalance=is_rebalance,
        flow_context=flow_context,
        api_budget_state=api_budget_state,
        print_sell_preview=_print_sell_preview,
        send_order_slack_notification=_send_order_slack_notification,
        wait_for_execution_request_budget=(
            wait_for_execution_request_budget
            or partial(_wait_for_execution_request_budget, note_callback=_emit_status)
        ),
    )


_run_sell_test_cycle = run_sell_test_cycle


def run_cycle(
    settings,
    *,
    sell_check_due: bool = True,
    buy_scan_due: bool = True,
    scheduler_state: dict[str, object] | None = None,
    api_budget_state: dict[str, object] | None = None,
) -> None:
    reset_request_metrics()
    reset_throttle_metrics()
    cycle_started_perf = time.perf_counter()
    cycle_budget = CycleBudget(
        started_at=cycle_started_perf,
        hard_budget_seconds=float(
            getattr(settings, "session_cycle_hard_budget_seconds", 60.0) or 60.0
        ),
        clock=time.perf_counter,
    )
    settings_scheduler_started_perf = time.perf_counter()
    scheduler_state = scheduler_state or {}
    api_budget_state = api_budget_state or _build_api_budget_state(settings)
    _print_runtime_mode(settings)
    _print_applied_settings(settings)
    _print_test_mode(settings)
    _print_cycle_header(settings)
    _print_engine_schedule_state(
        settings=settings,
        sell_check_due=sell_check_due,
        buy_scan_due=buy_scan_due,
        effective_sell_check_interval_seconds=int(
            scheduler_state.get("effective_sell_check_interval_seconds")
            or settings.sell_check_interval_seconds
        ),
        effective_buy_scan_interval_seconds=int(
            scheduler_state.get("effective_buy_scan_interval_seconds")
            or settings.buy_scan_interval_seconds
        ),
        last_sell_check_at=scheduler_state.get("last_sell_check_at"),
        last_buy_scan_at=scheduler_state.get("last_buy_scan_at"),
        scheduler_decision=str(scheduler_state.get("decision") or ""),
        runtime_rate_control=(
            scheduler_state.get("runtime_rate_control")
            if isinstance(scheduler_state.get("runtime_rate_control"), dict)
            else None
        ),
        api_budget_state=_summarize_api_budget_state(
            api_budget_state,
            now=get_korean_now(),
        ),
    )
    account_scope_status = sync_account_scope_meta(settings)
    account_scope_context = get_account_scope_context(settings)
    print(
        "[info] account scope: "
        f"{account_scope_context['account_signature']} | "
        f"account={account_scope_context['masked_account_display']}"
    )
    if bool(account_scope_status.get("account_scope_changed")):
        previous_signature = str(
            account_scope_status.get("previous_account_signature") or "-"
        ).strip() or "-"
        print(
            "[info] account scope changed: resetting persisted state/history isolation for "
            f"{account_scope_context['account_signature']} "
            f"(previous={previous_signature})"
        )
    state = load_runtime_state()
    state["account_signature"] = account_scope_context["account_signature"]
    state["account_environment"] = account_scope_context["account_environment"]
    state["masked_account_display"] = account_scope_context["masked_account_display"]
    state["account_scope_changed"] = bool(
        account_scope_status.get("account_scope_changed")
    )
    state["previous_account_signature"] = account_scope_status.get(
        "previous_account_signature"
    )
    state["last_sell_check_at"] = (
        scheduler_state.get("last_sell_check_at").isoformat()
        if isinstance(scheduler_state.get("last_sell_check_at"), datetime)
        else None
    )
    state["last_buy_scan_at"] = (
        scheduler_state.get("last_buy_scan_at").isoformat()
        if isinstance(scheduler_state.get("last_buy_scan_at"), datetime)
        else None
    )
    state["current_brake_state"] = (
        "DATA_INSUFFICIENT" if settings.enable_daily_pnl_brake else "OFF"
    )
    previous_action = state.get("last_action")
    previous_reason = state.get("last_decision_reason")
    cycle_id = _build_cycle_id()
    cycle_started_at = get_korean_now().isoformat()
    start_cycle(state, started_at=cycle_started_at, cycle_id=cycle_id)
    _write_slack_runtime_status_snapshot(
        runtime_state=state,
        session_status=state.get("last_market_session"),
    )
    ctx = CycleContext(
        timing_summary={
            "settings_scheduler": _phase_timing_summary(
                elapsed_ms=(time.perf_counter() - settings_scheduler_started_perf) * 1000
            )
        },
        buy_scan_separate_quote_lane=buy_scan_uses_separate_quote_account(settings),
        buy_quote_prefetch_deadline_seconds=float(
            getattr(settings, "buy_scan_quote_prefetch_deadline_seconds", 0.0) or 0.0
        ),
        buy_quote_prefetch_request_timeout_seconds=float(
            getattr(settings, "buy_scan_quote_request_timeout_seconds", 0.0) or 0.0
        ),
        buy_quote_prefetch_max_attempts=int(
            getattr(settings, "buy_scan_quote_max_attempts", 1) or 1
        ),
        lane_scheduler_enabled=bool(getattr(settings, "lane_scheduler_enabled", False)),
        buy_lane_running=BUY_SCAN_LANE_CONTROLLER.running,
        cycle_budget_ms=round(cycle_budget.budget_ms, 1),
        regime_state={
            "current_regime": "DATA_INSUFFICIENT",
            "regime_reason": "계좌 상태를 읽기 전이라 regime를 아직 계산하지 않았습니다.",
            "regime_multiplier": None,
            "current_drawdown_pct": None,
            "effective_buy_max_budget_per_trade_krw": settings.buy_max_budget_per_trade_krw,
            "effective_buy_max_account_exposure_pct": settings.buy_max_account_exposure_pct,
            "effective_buy_max_qty_per_trade": settings.buy_max_qty_per_trade,
            "effective_rebuy_cooldown_minutes": settings.rebuy_cooldown_minutes,
            "effective_same_symbol_max_buys_per_day": settings.same_symbol_max_buys_per_day,
            "effective_buy_daily_max_order_submissions": settings.buy_daily_max_order_submissions,
        },
        effective_buy_settings=settings,
        buy_scan_due=buy_scan_due,
    )

    def note(level: str, message: str) -> None:
        if level in {"WARN", "DEGRADED"}:
            ctx.cycle_warning_count += 1
        elif level == "ERROR":
            ctx.cycle_error_count += 1
        _emit_status(level, message)

    # Execution pacing must report through this cycle's counters before broker calls.
    execution_budget_wait = partial(
        _wait_for_execution_request_budget,
        note_callback=note,
    )

    def record_order_gate_summary(result) -> None:
        summary = summarize_order_gate_result(result)
        ctx.order_gate_queue_depth += int(summary["order_gate_queue_depth"] or 0)
        ctx.order_gate_processed_count += int(summary["order_gate_processed_count"] or 0)
        ctx.order_gate_last_decision = summary["order_gate_last_decision"]
        ctx.order_gate_last_intent_type = summary["order_gate_last_intent_type"]
        ctx.order_gate_last_symbol = summary["order_gate_last_symbol"]
        ctx.order_gate_last_skip_reason = summary["order_gate_last_skip_reason"]

    def order_gate_sell_order_flow(**kwargs) -> bool:
        analysis = kwargs.get("analysis")
        symbol = str(getattr(analysis, "symbol", "") or kwargs.get("symbol") or "-")
        reason = str(kwargs.get("cycle_reason") or kwargs.get("reason") or "sell")
        intent = SellIntent(
            intent_id=f"{cycle_id}:sell:{symbol}",
            source_cycle_id=cycle_id,
            source_lane="sell_watch",
            symbol=symbol,
            reason=reason,
            created_at=get_korean_now(),
        )

        def handle_sell(_intent) -> bool:
            call_kwargs = dict(kwargs)
            call_kwargs.setdefault(
                "wait_for_execution_request_budget",
                execution_budget_wait,
            )
            return _run_sell_order_flow(**call_kwargs)

        gate_result = OrderGate(sell_handler=handle_sell).process_ready_intents(
            (intent,),
            now=get_korean_now(),
        )
        record_order_gate_summary(gate_result)
        decision = gate_result.last_decision
        if (
            decision is not None
            and decision.status == "failed"
            and "exception" in decision.payload
        ):
            raise decision.payload["exception"]
        if decision is None or decision.status != "processed":
            return False
        return bool(decision.payload.get("handler_result"))

    sell_order_flow = (
        order_gate_sell_order_flow
        if bool(getattr(settings, "order_gate_enabled", True))
        else partial(
            _run_sell_order_flow,
            wait_for_execution_request_budget=execution_budget_wait,
        )
    )
    BUY_SCAN_LANE_CONTROLLER.reap_completed()
    ctx.buy_lane_running = BUY_SCAN_LANE_CONTROLLER.running

    def resolve_buy_universe_symbols() -> tuple[tuple[str, ...] | None, dict[str, object]]:
        snapshot_symbols = load_live_snapshot_symbols()
        snapshot_info = dict(live_snapshot_status())
        snapshot_info["worker_health"] = live_snapshot_worker_health(
            refresh_interval_seconds=int(
                snapshot_info.get("refresh_interval_seconds")
                or settings.live_snapshot_refresh_interval_seconds
            )
        )
        snapshot_info["used"] = bool(snapshot_symbols)
        snapshot_info["source"] = "live_snapshot" if snapshot_symbols else "settings"
        return snapshot_symbols, snapshot_info

    def log_buy_universe_source(snapshot_info: dict[str, object]) -> None:
        worker_health = (
            snapshot_info.get("worker_health")
            if isinstance(snapshot_info.get("worker_health"), dict)
            else {}
        )
        for warning in tuple(worker_health.get("warnings", ())):
            note("WARN", str(warning))

        if bool(snapshot_info.get("used")):
            snapshot_detail = str(snapshot_info.get("detail") or "fresh_within_refresh_window")
            refresh_age = snapshot_info.get("age_seconds")
            ttl_seconds = snapshot_info.get("ttl_seconds")
            refresh_interval_seconds = snapshot_info.get("refresh_interval_seconds")
            note(
                "INFO",
                "buy universe source=live_snapshot"
                f" | symbols={int(snapshot_info.get('symbol_count', 0) or 0)}"
                f" | updated_at={snapshot_info.get('updated_at') or '-'}"
                f" | age={refresh_age if refresh_age is not None else '-'}s"
                f" | ttl={ttl_seconds if ttl_seconds is not None else '-'}s"
                f" | refresh={refresh_interval_seconds if refresh_interval_seconds is not None else '-'}s"
                f" | status={snapshot_detail}"
                f" | worker_status={worker_health.get('status') or '-'}"
                f" | worker_heartbeat_age={worker_health.get('heartbeat_age_seconds') if worker_health.get('heartbeat_age_seconds') is not None else '-'}s"
                f" | worker_failures={worker_health.get('consecutive_failures') or 0}"
                f" | errors={len(list(snapshot_info.get('errors') or []))}",
            )
            return

        fallback_reason = str(snapshot_info.get("reason") or "unavailable")
        fallback_detail = str(snapshot_info.get("detail") or "").strip()
        age_seconds = snapshot_info.get("age_seconds")
        ttl_seconds = snapshot_info.get("ttl_seconds")
        refresh_interval_seconds = snapshot_info.get("refresh_interval_seconds")
        note(
            "INFO",
            "buy universe source=settings"
            f" | symbols={len(settings.target_symbols)}"
            f" | fallback_reason={fallback_reason}"
            f" | fallback_detail={fallback_detail or '-'}"
            f" | last_snapshot_age={age_seconds if age_seconds is not None else '-'}s"
            f" | ttl={ttl_seconds if ttl_seconds is not None else '-'}s"
            f" | refresh={refresh_interval_seconds if refresh_interval_seconds is not None else '-'}s"
            f" | worker_status={worker_health.get('status') or '-'}"
            f" | worker_heartbeat_age={worker_health.get('heartbeat_age_seconds') if worker_health.get('heartbeat_age_seconds') is not None else '-'}s"
            f" | worker_failures={worker_health.get('consecutive_failures') or 0}",
        )

    try:
        if settings.enable_sell_guard_selftest:
            ctx.cycle_environment = "mock_test"
            _run_sell_guard_selftest(settings)
            return
        if _sell_test_active(settings):
            ctx.cycle_environment = "mock_test"
            _run_sell_test_cycle(settings, state)
            return
        if ctx.lane_scheduler_enabled:
            lane_bridge = run_lane_scheduler_main_bridge(
                settings,
                cycle_id=cycle_id,
                sell_check_due=sell_check_due,
                buy_scan_due=ctx.buy_scan_due,
                scheduler_state=scheduler_state,
                api_budget_state=api_budget_state,
                state=state,
                timing_summary=ctx.timing_summary,
                send_order_slack_notification=_send_order_slack_notification,
                wait_for_execution_request_budget=execution_budget_wait,
            )
            ctx.lane_scheduler_final_overrides = lane_bridge.final_overrides
            if bool(ctx.lane_scheduler_final_overrides.get("rate_limit_triggered")):
                ctx.rate_limit_triggered = True
                ctx.rate_limit_source = (
                    str(ctx.lane_scheduler_final_overrides.get("rate_limit_source") or "").strip()
                    or "unknown"
                )
                _api_budget_note_rate_limit(
                    api_budget_state,
                    now=get_korean_now(),
                    source=ctx.rate_limit_source,
                )
                ctx.backoff_applied_seconds = int(
                    _api_budget_backoff_remaining_seconds(
                        api_budget_state,
                        now=get_korean_now(),
                    )
                )
            ctx.sell_watch_skipped_reason = lane_bridge.sell_watch_skipped_reason
            ctx.buy_scan_skipped_reason = lane_bridge.buy_scan_skipped_reason
            ctx.sell_status_text = lane_bridge.sell_status_text
            ctx.buy_status_text = lane_bridge.buy_status_text
            ctx.sell_evaluated_count = int(
                ctx.lane_scheduler_final_overrides.get("sell_evaluated_count", 0) or 0
            )
            ctx.sell_watch_total_holdings = int(
                ctx.lane_scheduler_final_overrides.get("sell_watch_total_holdings", 0) or 0
            )
            ctx.sell_watch_partial = bool(
                ctx.lane_scheduler_final_overrides.get("sell_watch_partial", False)
            )
            ctx.sell_watch_partial_reason = (
                str(ctx.lane_scheduler_final_overrides.get("sell_watch_partial_reason") or "").strip()
                or None
            )
            ctx.sell_watch_budget_plan_limit = int(
                ctx.lane_scheduler_final_overrides.get("sell_watch_budget_plan_limit", 0) or 0
            )
            ctx.sell_watch_budget_plan_pressure_level = (
                str(
                    ctx.lane_scheduler_final_overrides.get(
                        "sell_watch_budget_plan_pressure_level"
                    )
                    or ""
                ).strip()
                or None
            )
            ctx.sell_watch_budget_plan_reason = (
                str(ctx.lane_scheduler_final_overrides.get("sell_watch_budget_plan_reason") or "").strip()
                or None
            )
            ctx.sell_watch_evaluated_symbols = list(
                ctx.lane_scheduler_final_overrides.get("sell_watch_evaluated_symbols") or []
            )
            ctx.sell_watch_skipped_symbols = list(
                ctx.lane_scheduler_final_overrides.get("sell_watch_skipped_symbols") or []
            )
            # P1 observability parity (Stage 1): map the lane's own session object
            # onto ctx.session_status by identity (C2 — no recompute; the bridge
            # read it from scheduler_state['lane_scheduler_runtime_context']). This
            # restores cycle_stats `session` and the finalize candidate-outcome
            # builder's market_session under the lane path. If the lane resolved no
            # session, this stays None.
            # market_open intentionally not mapped — sell-cadence memory stays
            # frozen under lane mode; see
            # docs/eod_lane_account_incident_20260704.md follow-ups.
            ctx.session_status = lane_bridge.session_status
            # Observability-only bridge: the lane already fetched this balance
            # snapshot for its decision path. Reuse it in common finalization so
            # account-scoped performance artifacts are not left at N/A.
            ctx.portfolio_snapshot = lane_bridge.portfolio_snapshot
            if ctx.session_status is not None:
                state["last_market_session"] = ctx.session_status.session
            # P1 Stage 2: map the scan/candidate telemetry the lane genuinely
            # computed so finalize regenerates candidate_outcomes (C4 excludes
            # bulk selection_details; C5 covers scanned symbols as layer-selected).
            apply_lane_buy_scan_outcome_to_context(ctx, lane_bridge.buy_scan_outcome)
            return

        order_type = "market_buy"
        session_check_started_perf = time.perf_counter()
        if run_session_gate(
            ctx,
            settings=settings,
            state=state,
            api_budget_state=api_budget_state,
            cycle_id=cycle_id,
            session_check_started_perf=session_check_started_perf,
            get_korean_market_session=get_korean_market_session,
            get_korean_now=get_korean_now,
            build_market_session_console_lines=build_market_session_console_lines,
            _summarize_api_budget_state=_summarize_api_budget_state,
            _should_run_light_session_cycle=_should_run_light_session_cycle,
            _print_premarket_wait_notice=_print_premarket_wait_notice,
            _print_cycle_conclusion=_print_cycle_conclusion,
            _record_cycle_action=_record_cycle_action,
            _log_engine_event=_log_engine_event,
            _write_slack_runtime_status_snapshot=_write_slack_runtime_status_snapshot,
        ):
            return
        if run_budget_gate(
            ctx,
            settings=settings,
            state=state,
            api_budget_state=api_budget_state,
            scheduler_state=scheduler_state,
            sell_check_due=sell_check_due,
            cycle_id=cycle_id,
            note=note,
            get_korean_now=get_korean_now,
            _buy_scan_reserve_active=_buy_scan_reserve_active,
            _api_budget_transient_backoff_active=_api_budget_transient_backoff_active,
            _api_budget_transient_backoff_remaining_seconds=_api_budget_transient_backoff_remaining_seconds,
            _api_budget_backoff_active=_api_budget_backoff_active,
            _api_budget_backoff_remaining_seconds=_api_budget_backoff_remaining_seconds,
            _api_budget_can_request=_api_budget_can_request,
            _print_cycle_conclusion=_print_cycle_conclusion,
            _record_cycle_action=_record_cycle_action,
            _log_engine_event=_log_engine_event,
        ):
            return
        if run_account_snapshot_phase(
            ctx,
            settings=settings,
            state=state,
            api_budget_state=api_budget_state,
            scheduler_state=scheduler_state,
            sell_check_due=sell_check_due,
            cycle_id=cycle_id,
            cycle_budget=cycle_budget,
            note=note,
            resolve_buy_universe_symbols=resolve_buy_universe_symbols,
            log_buy_universe_source=log_buy_universe_source,
            get_korean_now=get_korean_now,
            issue_access_token=issue_access_token,
            inquire_balance=inquire_balance,
            build_portfolio_snapshot=build_portfolio_snapshot,
            build_account_state_payload=build_account_state_payload,
            build_current_drawdown_state=build_current_drawdown_state,
            _api_budget_register_requests=_api_budget_register_requests,
            _api_budget_register_request=_api_budget_register_request,
            _api_budget_register_measured_extra_requests=_api_budget_register_measured_extra_requests,
            _api_budget_min_wait_for_request_slot=_api_budget_min_wait_for_request_slot,
            _api_budget_can_request=_api_budget_can_request,
            _api_budget_note_rate_limit=_api_budget_note_rate_limit,
            _api_budget_backoff_remaining_seconds=_api_budget_backoff_remaining_seconds,
            _sync_reconciliation_state=_sync_reconciliation_state,
            _build_today_realized_summary=_build_today_realized_summary,
            _build_daily_pnl_brake_state=_build_daily_pnl_brake_state,
            _build_daily_pnl_brake_observability=_build_daily_pnl_brake_observability,
            _build_regime_state=_build_regime_state,
            _build_scan_only_runtime_mode_preview=_build_scan_only_runtime_mode_preview,
            _build_scan_only_diagnostic_summary=_build_scan_only_diagnostic_summary,
            _record_cycle_action=_record_cycle_action,
            _log_engine_event=_log_engine_event,
            _print_cycle_conclusion=_print_cycle_conclusion,
            _print_account_balance_interpretation=_print_account_balance_interpretation,
            _print_regime_state=_print_regime_state,
            _print_daily_pnl_brake_state=_print_daily_pnl_brake_state,
            _print_portfolio_positions=_print_portfolio_positions,
            _print_today_performance_summary=_print_today_performance_summary,
            _notify_reconciliation=lambda report: maybe_notify_manual_trade_suspects(report, notify=_get_slack_notifier().send),
        ):
            return
        run_sell_watch_phase(
            ctx,
            settings=settings,
            state=state,
            api_budget_state=api_budget_state,
            scheduler_state=scheduler_state,
            cycle_id=cycle_id,
            sell_check_due=sell_check_due,
            note=note,
            get_korean_now=get_korean_now,
            inquire_price=inquire_price,
            get_account_scope_context=get_account_scope_context,
            build_sell_watch_budget_plan=build_sell_watch_budget_plan,
            _log_engine_event=_log_engine_event,
            _api_budget_note_rate_limit=_api_budget_note_rate_limit,
            _api_budget_request_window_size=_api_budget_request_window_size,
            _api_budget_remaining_requests=_api_budget_remaining_requests,
            _api_budget_remaining_quotes=_api_budget_remaining_quotes,
            _api_budget_min_wait_for_request_slot=_api_budget_min_wait_for_request_slot,
            _api_budget_can_quote=_api_budget_can_quote,
            _api_budget_can_request=_api_budget_can_request,
            _api_budget_register_request=_api_budget_register_request,
            _api_budget_backoff_remaining_seconds=_api_budget_backoff_remaining_seconds,
        )

        run_regime_phase(
            ctx,
            settings=settings,
            state=state,
            sell_check_due=sell_check_due,
            build_account_state_payload=build_account_state_payload,
            build_current_drawdown_state=build_current_drawdown_state,
            _build_today_realized_summary=_build_today_realized_summary,
            _build_daily_pnl_brake_state=_build_daily_pnl_brake_state,
            _build_daily_pnl_brake_observability=_build_daily_pnl_brake_observability,
            _build_regime_state=_build_regime_state,
            replace=replace,
            _print_account_balance_interpretation=_print_account_balance_interpretation,
            _print_regime_state=_print_regime_state,
            _print_daily_pnl_brake_state=_print_daily_pnl_brake_state,
            _print_portfolio_positions=_print_portfolio_positions,
            _print_today_performance_summary=_print_today_performance_summary,
            _print_today_bought_tracking=_print_today_bought_tracking,
        )

        if run_sell_order_phase(
            ctx,
            settings=settings,
            state=state,
            api_budget_state=api_budget_state,
            cycle_id=cycle_id,
            cycle_budget=cycle_budget,
            sell_check_due=sell_check_due,
            note=note,
            sell_order_flow=sell_order_flow,
            execution_budget_wait=execution_budget_wait,
            resolve_buy_universe_symbols=resolve_buy_universe_symbols,
            log_buy_universe_source=log_buy_universe_source,
            get_korean_now=get_korean_now,
            get_account_scope_context=get_account_scope_context,
            scan_target_symbols=scan_target_symbols,
            get_last_scan_diagnostics=get_last_scan_diagnostics,
            get_adaptive_pacing_summary=get_adaptive_pacing_summary,
            select_top_candidate=select_top_candidate,
            build_universe_console_lines=build_universe_console_lines,
            build_scan_console_lines=build_scan_console_lines,
            resolve_mock_buy_price_floor_krw=resolve_mock_buy_price_floor_krw,
            _apply_buy_runtime_guards_to_scan_results=_apply_buy_runtime_guards_to_scan_results,
            _record_cycle_action=_record_cycle_action,
            _log_engine_event=_log_engine_event,
            _print_cycle_conclusion=_print_cycle_conclusion,
            _print_buy_runtime_filter_summary=_print_buy_runtime_filter_summary,
            _api_budget_note_rate_limit=_api_budget_note_rate_limit,
            _api_budget_backoff_remaining_seconds=_api_budget_backoff_remaining_seconds,
        ):
            return

        if run_buy_scan_phase(
            ctx,
            settings=settings,
            state=state,
            api_budget_state=api_budget_state,
            scheduler_state=scheduler_state,
            cycle_id=cycle_id,
            cycle_budget=cycle_budget,
            order_type=order_type,
            note=note,
            resolve_buy_universe_symbols=resolve_buy_universe_symbols,
            log_buy_universe_source=log_buy_universe_source,
            get_korean_now=get_korean_now,
            _api_budget_backoff_active=_api_budget_backoff_active,
            _api_budget_backoff_remaining_seconds=_api_budget_backoff_remaining_seconds,
            _api_budget_transient_backoff_active=_api_budget_transient_backoff_active,
            _api_budget_request_window_size=_api_budget_request_window_size,
            _api_budget_can_quote=_api_budget_can_quote,
            _api_budget_can_request=_api_budget_can_request,
            _api_budget_min_wait_for_request_slot=_api_budget_min_wait_for_request_slot,
            _api_budget_note_rate_limit=_api_budget_note_rate_limit,
            _cap_buy_scan_deep_eval_symbols_for_api_budget=_cap_buy_scan_deep_eval_symbols_for_api_budget,
            _select_buy_scan_profile=_select_buy_scan_profile,
            _build_buy_scan_layered_universe=_build_buy_scan_layered_universe,
            _build_buy_scan_pre_gating=_build_buy_scan_pre_gating,
            _build_buy_scan_shallow_plan=_build_buy_scan_shallow_plan,
            _build_buy_scan_deep_eval_symbols=_build_buy_scan_deep_eval_symbols,
            _apply_buy_runtime_guards_to_scan_results=_apply_buy_runtime_guards_to_scan_results,
            _record_cycle_action=_record_cycle_action,
            _log_engine_event=_log_engine_event,
            _print_cycle_conclusion=_print_cycle_conclusion,
            _print_buy_pre_gating_summary=_print_buy_pre_gating_summary,
            _print_buy_scan_stage_summary=_print_buy_scan_stage_summary,
            _print_buy_runtime_filter_summary=_print_buy_runtime_filter_summary,
            _print_buy_strategy=_print_buy_strategy,
            _print_buy_score_summary=_print_buy_score_summary,
            scan_target_symbols=scan_target_symbols,
            get_last_scan_diagnostics=get_last_scan_diagnostics,
            get_adaptive_pacing_summary=get_adaptive_pacing_summary,
            select_top_candidate=select_top_candidate,
            select_top_analysis_result=select_top_analysis_result,
            serialize_selection_details=serialize_selection_details,
            buy_scan_uses_separate_quote_account=buy_scan_uses_separate_quote_account,
            build_universe_console_lines=build_universe_console_lines,
            build_scan_console_lines=build_scan_console_lines,
            log_order_event=log_order_event,
            mark_buy_blocked=mark_buy_blocked,
            resolve_mock_buy_price_floor_krw=resolve_mock_buy_price_floor_krw,
        ):
            return

        if run_buy_order_phase(
            ctx,
            settings=settings,
            state=state,
            api_budget_state=api_budget_state,
            cycle_id=cycle_id,
            cycle_budget=cycle_budget,
            order_type=order_type,
            sell_check_due=sell_check_due,
            record_order_gate_summary=record_order_gate_summary,
            sell_order_flow=sell_order_flow,
            execution_budget_wait=execution_budget_wait,
            get_korean_now=get_korean_now,
            _record_cycle_action=_record_cycle_action,
            _print_cycle_conclusion=_print_cycle_conclusion,
            _send_order_slack_notification=_send_order_slack_notification,
        ):
            return
    except Exception as exc:
        if _looks_like_rate_limit_error(exc):
            ctx.rate_limit_triggered = True
            ctx.rate_limit_source = (
                ctx.rate_limit_source
                or _rate_limit_source_from_exception(exc)
                or "unknown"
            )
            _api_budget_note_rate_limit(
                api_budget_state,
                now=get_korean_now(),
                source=ctx.rate_limit_source or "unknown",
            )
            ctx.backoff_applied_seconds = int(
                _api_budget_backoff_remaining_seconds(
                    api_budget_state, now=get_korean_now()
                )
            )
            note(
                "ERROR",
                "KIS rate limit 경고를 감지해 잠시 backoff를 적용합니다. "
                f"({ctx.backoff_applied_seconds}s)",
            )
        elif _looks_like_transient_api_error(exc):
            ctx.api_transient_error_source = (
                _transient_api_source_from_exception(exc) or "unknown"
            )
            _api_budget_note_transient_api_error(
                api_budget_state,
                now=get_korean_now(),
                source=ctx.api_transient_error_source,
            )
            ctx.api_transient_backoff_applied_seconds = int(
                _api_budget_transient_backoff_remaining_seconds(
                    api_budget_state,
                    now=get_korean_now(),
                )
            )
            note(
                "DEGRADED",
                "KIS API/network 일시 오류를 감지해 다음 tick들을 잠시 쉬게 합니다. "
                f"(source={ctx.api_transient_error_source}, "
                f"backoff={ctx.api_transient_backoff_applied_seconds}s)",
            )
        else:
            note("ERROR", f"cycle critical error: {exc}")
        # F2-3 (E6): stamp the exception type so a message-less exception still
        # records an actionable cause (today's 9 cycle_error records were blank).
        cycle_error_detail = f"{type(exc).__name__}: {exc}"
        ctx.cycle_error = cycle_error_detail
        set_last_decision(
            state,
            action="CYCLE_ERROR",
            reason=cycle_error_detail,
            order_side=None,
            symbol=None,
            qty=0,
            selected_symbol=state.get("last_selected_symbol"),
        )
        _log_engine_event(
            action="cycle_error",
            reason=cycle_error_detail,
            cycle_id=cycle_id,
            market_open=bool(ctx.session_status.order_allowed) if ctx.session_status is not None else False,
            market_session=ctx.session_status.session if ctx.session_status is not None else None,
            error_type=type(exc).__name__,
        )
        raise
    finally:
        finalize_cycle(
            ctx,
            settings=settings,
            api_budget_state=api_budget_state,
            scheduler_state=scheduler_state,
            sell_check_due=sell_check_due,
            state=state,
            cycle_id=cycle_id,
            cycle_started_at=cycle_started_at,
            cycle_started_perf=cycle_started_perf,
            cycle_budget=cycle_budget,
            _api_budget_note_rate_limit=_api_budget_note_rate_limit,
            BUY_SCAN_LANE_CONTROLLER=BUY_SCAN_LANE_CONTROLLER,
            note=note,
            _api_budget_backoff_remaining_seconds=_api_budget_backoff_remaining_seconds,
            _api_budget_update_rate_limit_recovery_state=_api_budget_update_rate_limit_recovery_state,
            _api_budget_update_transient_recovery_state=_api_budget_update_transient_recovery_state,
            _build_buy_candidate_outcome_records=_build_buy_candidate_outcome_records,
            _build_buy_cycle_funnel_stats=_build_buy_cycle_funnel_stats,
            _format_cycle_summary=_format_cycle_summary,
            _get_slack_notifier=_get_slack_notifier,
            _print_api_usage=_print_api_usage,
            _print_buy_scan_metrics=_print_buy_scan_metrics,
            _print_cycle_timing=_print_cycle_timing,
            _print_runtime_state_summary=_print_runtime_state_summary,
            _print_sell_metrics=_print_sell_metrics,
            _resolve_benchmark_snapshot=_resolve_benchmark_snapshot,
            _run_cycle_market_data_quality_sentinel=_run_cycle_market_data_quality_sentinel,
            _summarize_api_budget_state=_summarize_api_budget_state,
            _update_recent_market_snapshots=_update_recent_market_snapshots,
            _write_slack_runtime_status_snapshot=_write_slack_runtime_status_snapshot,
            append_candidate_outcomes=append_candidate_outcomes,
            append_cycle_stats=append_cycle_stats,
            build_cycle_snapshot=build_cycle_snapshot,
            build_cycle_stats_console_lines=build_cycle_stats_console_lines,
            build_cycle_stats_daily_summary=build_cycle_stats_daily_summary,
            build_daily_summary=build_daily_summary,
            build_daily_summary_console_lines=build_daily_summary_console_lines,
            build_performance_console_lines=build_performance_console_lines,
            build_performance_report=build_performance_report,
            get_cycle_snapshots_path=get_cycle_snapshots_path,
            get_korean_now=get_korean_now,
            get_runtime_state_path=get_runtime_state_path,
            persist_cycle_snapshot=persist_cycle_snapshot,
            persist_performance_report=persist_performance_report,
            save_runtime_state=save_runtime_state,
        )


def main() -> None:
    settings = get_settings()

    # Acquire a per-account fcntl lock before any shared file access.
    # Prevents two app.main processes with the same account_signature from
    # running simultaneously (e.g. an orphan from a prior session that the
    # shell wrapper failed to kill).  The handle must stay alive for the entire
    # process lifetime — closing it releases the lock.
    _account_sig = get_account_signature(settings)
    _app_main_lock_path = PROJECT_ROOT / "logs" / f"app_main_{_account_sig}.lock"
    try:
        _app_main_lock_handle = acquire_app_main_lock(  # noqa: F841 — held for lock lifetime
            _app_main_lock_path,
            account_signature=_account_sig,
            command=f"python -m app.main (account={_account_sig})",
        )
    except AppMainAlreadyRunningError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc

    # Bound the append-only order log so it never exceeds the local read-size
    # limit. An oversized log makes the order-log integrity guard fail-closed
    # and silently halts trading. Rotation runs here — lock held, before any
    # order-log read/write — so all of today's records land in the fresh file
    # and daily-limit accounting stays correct.
    _order_log_rotation = rotate_order_log_if_oversized(
        get_order_log_path(settings),
        max_bytes=local_read_max_bytes() // 2,
        archive_dir=PROJECT_ROOT / "archive",
    )
    if _order_log_rotation.rotated:
        print(
            "[info] 주문 로그가 "
            f"{_order_log_rotation.freed_bytes / 1024 / 1024:.0f}MB로 커져 무결성 가드 "
            "fail-closed 차단을 막기 위해 archive로 회전했습니다 → "
            f"{_order_log_rotation.archived_path}",
            flush=True,
        )

    runtime_parameter_validation = build_runtime_parameter_validation_report(settings)
    startup_sanity_report = build_startup_sanity_report(
        settings,
        runtime_parameter_report=runtime_parameter_validation,
    )
    _print_runtime_parameter_validation(runtime_parameter_validation)
    _print_startup_sanity_report(startup_sanity_report, run_mode=settings.run_mode)
    if runtime_parameter_validation.get("strict_blocked"):
        violations = ", ".join(
            str(item)
            for item in tuple(runtime_parameter_validation.get("strict_violations", ()))
        )
        raise RuntimeError(
            "STRICT_RUNTIME_PARAM_OVERRIDES=true 이고 추천 기본값과 다른 핵심 "
            f"런타임 override 가 감지되어 시작을 중단합니다: {violations}"
        )
    if settings.run_mode == "trade" and startup_sanity_report.get("blocked"):
        violations = ", ".join(
            str(item) for item in tuple(startup_sanity_report.get("errors", ()))
        )
        raise RuntimeError(
            "startup sanity check 실패로 trade 모드 시작을 중단합니다: "
            f"{violations}"
        )

    if settings.run_once:
        api_budget_state = _build_api_budget_state(settings)
        runtime_rate_control = _build_runtime_rate_control(
            settings=settings,
            api_budget_state=api_budget_state,
            now=get_korean_now(),
        )
        cycle_settings = replace(
            settings,
            sell_check_interval_seconds=int(
                runtime_rate_control["effective_sell_check_interval_seconds"]
            ),
            buy_scan_interval_seconds=int(
                runtime_rate_control["effective_buy_scan_interval_seconds"]
            ),
            scan_symbols_max_per_cycle=int(
                runtime_rate_control["effective_scan_symbols_max_per_cycle"]
            ),
            buy_scan_shallow_top_k=int(
                runtime_rate_control["effective_buy_scan_shallow_top_k"]
            ),
            buy_scan_deep_eval_limit=int(
                runtime_rate_control["effective_buy_scan_deep_eval_limit"]
            ),
        )
        try:
            run_cycle(
                cycle_settings,
                sell_check_due=True,
                buy_scan_due=True,
                scheduler_state={
                    "last_sell_check_at": None,
                    "last_buy_scan_at": None,
                    "decision": "RUN_ONCE_FULL_CYCLE",
                    "sell_check_due": True,
                    "buy_scan_due": True,
                    "effective_sell_check_interval_seconds": int(
                        runtime_rate_control["effective_sell_check_interval_seconds"]
                    ),
                    "effective_buy_scan_interval_seconds": int(
                        runtime_rate_control["effective_buy_scan_interval_seconds"]
                    ),
                    "effective_sell_watch_max_holdings_per_tick": runtime_rate_control.get(
                        "effective_sell_watch_max_holdings_per_tick"
                    ),
                    "runtime_rate_control": runtime_rate_control,
                },
                api_budget_state=api_budget_state,
            )
        except Exception as exc:
            _emit_status(
                "DEGRADED",
                f"사이클 실행 중 예외가 발생했지만 프로세스는 유지합니다: {exc}",
            )
            _record_main_loop_exception_if_needed(exc)
        return

    _run_session_loop(settings=settings)


if __name__ == "__main__":
    main()
