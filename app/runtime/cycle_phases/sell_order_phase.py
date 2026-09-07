"""``run_cycle`` SELL-order phase — Stage B-3 slice L (国면 11).

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 (国면 지도 11행 / "Stage B
실행 사양"): the SELL-candidate selection + SELL-order execution block of
``app.main.run_cycle`` — the ``scan_only`` diagnostic BUY path (unconditional
early return) and the normal SELL path (``select_top_sell_candidate`` ->
rate-limit defer -> ``consumed_by_sell = sell_order_flow(...)`` -> strict-sell-first
return) — is moved here **byte-verbatim** (logic / order / side effects unchanged;
only the leading indentation is dedented by 4 spaces from the ``run_cycle`` ``try``
body). The outer ``if ctx.portfolio_snapshot.held_positions and sell_check_due:``
guard moves with the block and is this function's first statement.

Early-``return`` mapping (国면 outcome -> ``run_cycle`` signal): the three original
bare ``return``s — scan_only end, rate-limit defer end, strict-sell-first end — map
to ``return True`` (``run_cycle`` returns immediately). The rate-limit defer is a
``return`` (SELL skipped, BUY short-circuited), not a fall-through. When the guard
is entered but no short-circuit fires (no candidate, or a non-strict consumed SELL,
or the guard body naturally reaches the bottom) — and when the guard itself is
False — the phase falls through to ``return False`` and ``run_cycle`` continues into
``run_buy_scan_phase``.

L-1 (defer cursor writes): the defer block reads ``ctx.prioritized_positions``
(promoted to ``ctx`` by slice K) via ``ctx.`` only, and writes
``state["sell_watch_retry_symbol"]`` / ``state["sell_watch_next_start_index"]``
verbatim (``state`` passed by reference so their position is neutral).

L-2 (OrderGate single writer): ``sell_order_flow`` (the ``run_cycle`` OrderGate
closure — ``order_gate_sell_order_flow`` / ``handle_sell`` /
``record_order_gate_summary`` / ``_run_sell_order_flow`` / ``execution_budget_wait``)
stays in ``run_cycle`` and is injected **by reference**; this module never
reconstructs or rewires it. Only the call site ``consumed_by_sell =
sell_order_flow(...)`` moves here.

L-4 deps discipline (§3 "deps 번들 주입" — individual keyword params, mirroring the
slice-J ``run_buy_scan_phase`` split): every ``app.main`` collaborator a test may
patch on ``main_module`` (blocklist-checked — the three blocklist hits in this
block are ``_api_budget_note_rate_limit`` / ``get_account_scope_context`` /
``scan_target_symbols``) is received as an **identically named keyword parameter**,
bound at the ``run_cycle`` call site from its enclosing/module scope. To stay
consistent with the sibling ``run_buy_scan_phase`` (which injects them so a test's
``main_module`` patch reaches the phase), the same collaborator set is injected
here: ``get_korean_now`` / ``select_top_candidate`` / ``resolve_mock_buy_price_floor_krw``
/ ``build_universe_console_lines`` / ``build_scan_console_lines`` /
``_apply_buy_runtime_guards_to_scan_results`` / ``_record_cycle_action`` /
``_log_engine_event`` / ``_print_cycle_conclusion`` / ``_print_buy_runtime_filter_summary``
/ ``get_last_scan_diagnostics`` / ``get_adaptive_pacing_summary`` /
``_api_budget_backoff_remaining_seconds``. The ``note`` /
``resolve_buy_universe_symbols`` / ``log_buy_universe_source`` run_cycle closures and
the run_cycle locals (``settings`` / ``state`` / ``api_budget_state`` / ``cycle_id`` /
``cycle_budget`` / ``order_type`` / ``sell_order_flow`` / ``execution_budget_wait``)
are likewise injected. ``get_korean_now`` is passed **by reference** (never
pre-called) so the ``side_effect`` mock sequences keep their call order.

Only non-blocklisted, un-patched pure utilities are imported directly — the same
imports the sibling phase modules already make. This module must NOT import
``app.main``.
"""

from __future__ import annotations

import time
from typing import Any

from app.auth.token import get_request_metrics_summary
from app.core.runtime_budget import request_metrics_delta as _request_metrics_delta
from app.execution import calculate_sell_position_sizing
from app.pipeline import (
    BUY_SCAN_LANE_CONTROLLER,
    prefetch_metrics_from_result,
    restrict_symbols_to_prefetched,
)
from app.reporting.console import (
    phase_timing_summary as _phase_timing_summary,
    print_rebalance_skip as _print_rebalance_skip,
)
from app.runtime.cycle_phases.prefetch_join import (
    copy_join_guard_fields,
    flatten_prefetch_metrics,
)
from app.runtime.scan_only_diagnostic import print_scan_only_notice as _print_scan_only_notice
from app.strategy.sell_decision import (
    select_top_sell_analysis,
    select_top_sell_candidate,
)


def run_sell_order_phase(
    ctx: Any,
    *,
    settings: Any,
    state: Any,
    api_budget_state: Any,
    cycle_id: Any,
    cycle_budget: Any,
    sell_check_due: Any,
    note: Any,
    sell_order_flow: Any,
    execution_budget_wait: Any,
    resolve_buy_universe_symbols: Any,
    log_buy_universe_source: Any,
    get_korean_now: Any,
    get_account_scope_context: Any,
    scan_target_symbols: Any,
    get_last_scan_diagnostics: Any,
    get_adaptive_pacing_summary: Any,
    select_top_candidate: Any,
    build_universe_console_lines: Any,
    build_scan_console_lines: Any,
    resolve_mock_buy_price_floor_krw: Any,
    _apply_buy_runtime_guards_to_scan_results: Any,
    _record_cycle_action: Any,
    _log_engine_event: Any,
    _print_cycle_conclusion: Any,
    _print_buy_runtime_filter_summary: Any,
    _api_budget_note_rate_limit: Any,
    _api_budget_backoff_remaining_seconds: Any,
) -> bool:
    if ctx.portfolio_snapshot.held_positions and sell_check_due:
        ctx.selected_sell_candidate = select_top_sell_candidate(ctx.sell_analysis_results)
        ctx.sell_watch_final_review = ctx.selected_sell_candidate or select_top_sell_analysis(
            ctx.sell_analysis_results
        )

        if settings.run_mode == "scan_only":
            for line in build_universe_console_lines(settings):
                print(line)
            print()
            if ctx.selected_sell_candidate is not None:
                sell_sizing = calculate_sell_position_sizing(
                    trigger=ctx.selected_sell_candidate.sell_decision.triggered_rule_name,
                    holding_qty=ctx.selected_sell_candidate.holding_qty,
                    current_price=ctx.selected_sell_candidate.market_snapshot.current_price,
                    settings=settings,
                )
                _print_cycle_conclusion(
                    side="SELL",
                    display_name=ctx.selected_sell_candidate.display_name,
                    reason=ctx.selected_sell_candidate.sell_decision.triggered_rule_name
                    or "-",
                    planned_qty=sell_sizing.recommended_sell_qty,
                )
                _record_cycle_action(
                    state,
                    action="SELL_SCAN_CANDIDATE",
                    reason="scan_only 사이클에서 SELL 후보를 확인했습니다.",
                    order_side="SELL",
                    symbol=ctx.selected_sell_candidate.symbol,
                    qty=sell_sizing.recommended_sell_qty,
                    selected_symbol=ctx.selected_sell_candidate.symbol,
                )

            adaptive_pacing_summary = get_adaptive_pacing_summary()
            ctx.adaptive_pacing_used = bool(adaptive_pacing_summary.get("active"))
            ctx.adaptive_pacing_extra_delay_ms = float(
                adaptive_pacing_summary.get("extra_delay_ms", 0.0) or 0.0
            )
            print(
                f"[info] adaptive pacing active={'YES' if ctx.adaptive_pacing_used else 'NO'}"
                f" | extra delay={int(round(ctx.adaptive_pacing_extra_delay_ms))}ms"
            )
            if ctx.buy_quote_prefetch_snapshot_info is not None:
                live_snapshot_symbols = ctx.buy_quote_prefetch_symbols
                snapshot_info = ctx.buy_quote_prefetch_snapshot_info
            else:
                live_snapshot_symbols, snapshot_info = resolve_buy_universe_symbols()
            log_buy_universe_source(snapshot_info)
            requested_scan_symbols = tuple(
                live_snapshot_symbols or settings.target_symbols
            )[: settings.scan_symbols_max_per_cycle]
            if ctx.buy_quote_prefetch_future is not None:
                join_timeout_seconds = cycle_budget.bounded_wait_seconds(
                    desired_seconds=ctx.buy_quote_prefetch_deadline_seconds,
                    reserve_seconds=3.0,
                )
                prefetch_join = BUY_SCAN_LANE_CONTROLLER.join_active_prefetch(
                    timeout_seconds=join_timeout_seconds
                )
                copy_join_guard_fields(
                    ctx,
                    prefetch_join,
                    lane_running=BUY_SCAN_LANE_CONTROLLER.running,
                )
                if prefetch_join.result is not None:
                    metrics = prefetch_metrics_from_result(prefetch_join.result)
                    ctx.buy_quote_prefetch_price_data_by_symbol = metrics[
                        "price_data_by_symbol"
                    ]
                    flatten_prefetch_metrics(ctx, metrics)
                    ctx.quote_age_max_ms = ctx.buy_quote_prefetch_elapsed_ms
                    ctx.quote_age_avg_ms = (
                        ctx.buy_quote_prefetch_elapsed_ms
                        if ctx.buy_quote_prefetch_completed > 0
                        else None
                    )
                    print(
                        "[info] BUY quote prefetch joined"
                        f" | completed={ctx.buy_quote_prefetch_completed}"
                        f"/{len(prefetch_join.result.requested_symbols)}"
                        f" | failed={ctx.buy_quote_prefetch_failed}"
                        f" | skipped_deadline={ctx.buy_quote_prefetch_skipped_deadline}"
                        f" | deadline_hit={'YES' if ctx.buy_quote_prefetch_deadline_hit else 'NO'}"
                        f" | wait={int(round(ctx.buy_quote_prefetch_join_wait_ms))}ms"
                    )
                    if ctx.buy_scan_separate_quote_lane:
                        requested_scan_symbols = restrict_symbols_to_prefetched(
                            requested_scan_symbols,
                            ctx.buy_quote_prefetch_price_data_by_symbol,
                        )
                    if ctx.buy_quote_prefetch_deadline_hit and not requested_scan_symbols:
                        ctx.buy_scan_skipped_reason = "quote_prefetch_deadline"
                        ctx.buy_status_text = "skipped(quote_prefetch_deadline)"
                        note(
                            "DEGRADED",
                            "BUY quote prefetch deadline reached before any usable quote payload.",
                        )
                    if bool(metrics["rate_limit_triggered"]):
                        ctx.rate_limit_triggered = True
                        ctx.rate_limit_source = "buy_scan"
                        _api_budget_note_rate_limit(
                            api_budget_state,
                            now=get_korean_now(),
                            source="buy_scan",
                        )
                        requested_scan_symbols = restrict_symbols_to_prefetched(
                            requested_scan_symbols,
                            ctx.buy_quote_prefetch_price_data_by_symbol,
                        )
                elif prefetch_join.timed_out:
                    ctx.buy_quote_prefetch_deadline_hit = True
                    ctx.buy_quote_prefetch_worker_detached = prefetch_join.worker_detached
                    ctx.buy_quote_prefetch_cleanup_nonblocking = (
                        prefetch_join.cleanup_nonblocking
                    )
                    ctx.buy_quote_prefetch_future_done = prefetch_join.future_done
                    ctx.buy_scan_guard_released = prefetch_join.guard_released
                    ctx.buy_scan_guard_release_reason = (
                        prefetch_join.guard_release_reason
                    )
                    ctx.buy_scan_skipped_reason = "quote_prefetch_timeout"
                    ctx.buy_status_text = "skipped(quote_prefetch_timeout)"
                    requested_scan_symbols = ()
                    note(
                        "DEGRADED",
                        "BUY quote prefetch did not finish before the bounded join timeout.",
                    )
                elif prefetch_join.status == "failed":
                    ctx.buy_quote_prefetch_cleanup_nonblocking = (
                        prefetch_join.cleanup_nonblocking
                    )
                    ctx.buy_quote_prefetch_future_done = prefetch_join.future_done
                    ctx.buy_scan_guard_released = prefetch_join.guard_released
                    ctx.buy_scan_guard_release_reason = (
                        prefetch_join.guard_release_reason
                    )
                    ctx.buy_scan_skipped_reason = "quote_prefetch_failed"
                    ctx.buy_status_text = "skipped(quote_prefetch_failed)"
                    requested_scan_symbols = ()
                    note(
                        "DEGRADED",
                        f"BUY quote prefetch failed; BUY scan skipped safely: {prefetch_join.error}",
                    )
                ctx.buy_quote_prefetch_future = None
            buy_scan_started_perf = time.perf_counter()
            buy_scan_metrics_before = get_request_metrics_summary()
            ctx.buy_scan_requested_count = len(requested_scan_symbols)
            ctx.raw_scan_results = scan_target_symbols(
                settings=settings,
                token=ctx.token,
                portfolio_snapshot=ctx.portfolio_snapshot,
                symbols=requested_scan_symbols,
                layer_by_symbol=dict(ctx.buy_scan_layered_universe.get("layer_by_symbol") or {}),
                price_data_by_symbol=ctx.buy_quote_prefetch_price_data_by_symbol,
                allow_inline_quote_fetch=not ctx.buy_scan_separate_quote_lane,
            )
            scan_diagnostics = get_last_scan_diagnostics()
            ctx.buy_scan_quote_account_mode = str(
                scan_diagnostics.get("quote_account_mode") or ctx.buy_scan_quote_account_mode
            )
            ctx.buy_scan_quote_account_env = str(
                scan_diagnostics.get("quote_account_env") or ctx.buy_scan_quote_account_env
            )
            if bool(scan_diagnostics.get("rate_limit_triggered")):
                ctx.rate_limit_triggered = True
                ctx.rate_limit_source = "buy_scan"
                _api_budget_note_rate_limit(
                    api_budget_state,
                    now=get_korean_now(),
                    source="buy_scan",
                )
                ctx.backoff_applied_seconds = int(
                    _api_budget_backoff_remaining_seconds(
                        api_budget_state, now=get_korean_now()
                    )
                )
            ctx.buy_scan_evaluated_count = len(ctx.raw_scan_results)
            ctx.buy_status_text = f"evaluated({ctx.buy_scan_evaluated_count}/{ctx.buy_scan_requested_count})"
            ctx.buy_scan_top_k_count = min(
                settings.buy_scan_top_k_candidates,
                ctx.buy_scan_evaluated_count,
            )
            ranking_started_perf = time.perf_counter()
            ctx.scan_results = _apply_buy_runtime_guards_to_scan_results(
                ctx.raw_scan_results,
                state=state,
                settings=ctx.effective_buy_settings,
                portfolio_snapshot=ctx.portfolio_snapshot,
                regime_state=ctx.regime_state,
                daily_pnl_brake_state=ctx.daily_pnl_brake_state,
            )
            _print_buy_runtime_filter_summary(
                before_results=ctx.raw_scan_results,
                after_results=ctx.scan_results,
                cycle_id=cycle_id,
                market_open=ctx.market_open,
                market_session=ctx.session_status.session,
            )
            ctx.selected_candidate = select_top_candidate(
                ctx.scan_results,
                min_price_krw=resolve_mock_buy_price_floor_krw(settings),
            )
            ctx.buy_ranking_ms = (time.perf_counter() - ranking_started_perf) * 1000
            buy_scan_metrics_after = get_request_metrics_summary()
            buy_scan_delta = _request_metrics_delta(
                buy_scan_metrics_before,
                buy_scan_metrics_after,
            )
            ctx.buy_scan_quote_request_count = ctx.buy_quote_prefetch_request_count + int(
                ((buy_scan_delta.get("categories") or {}).get("quote") or {}).get(
                    "count",
                    0,
                )
            )
            ctx.buy_scan_sleep_ms = (
                ctx.buy_quote_prefetch_throttle_sleep_ms
                + float(scan_diagnostics.get("quote_wait_sleep_ms", 0.0) or 0.0)
            )
            ctx.buy_scan_quote_response_ms = (
                ctx.buy_quote_prefetch_response_ms
                + float(scan_diagnostics.get("quote_response_ms", 0.0) or 0.0)
            )
            ctx.buy_scan_parse_ms = float(scan_diagnostics.get("quote_parse_ms", 0.0) or 0.0)
            ctx.buy_scan_score_calc_ms = float(scan_diagnostics.get("score_calc_ms", 0.0) or 0.0)
            ctx.buy_scan_candidate_build_ms = float(scan_diagnostics.get("candidate_build_ms", 0.0) or 0.0)
            ctx.buy_scan_calc_ms = float(scan_diagnostics.get("quote_calc_total_ms", 0.0) or 0.0)
            ctx.buy_scan_throttle_sleep_events = int(scan_diagnostics.get("throttle_sleep_events", 0) or 0)
            throttle_min_sleep = scan_diagnostics.get("throttle_min_sleep_ms")
            ctx.buy_scan_throttle_min_sleep_ms = (
                None if throttle_min_sleep is None else float(throttle_min_sleep)
            )
            ctx.buy_scan_throttle_total_sleep_ms = (
                ctx.buy_quote_prefetch_throttle_sleep_ms
                + float(scan_diagnostics.get("throttle_total_sleep_ms", 0.0) or 0.0)
            )
            ctx.buy_scan_average_sleep_per_event_ms = float(
                scan_diagnostics.get("throttle_average_sleep_ms", 0.0) or 0.0
            )
            ctx.buy_scan_effective_total_sleep_ms = round(
                float(ctx.buy_scan_guard_wait_ms)
                + float(ctx.buy_scan_throttle_total_sleep_ms),
                1,
            )
            ctx.buy_scan_throttle_immediate_pass_count = int(
                scan_diagnostics.get("throttle_immediate_pass_count", 0) or 0
            )
            ctx.buy_scan_sample_symbols = list(scan_diagnostics.get("sample_symbols", []) or [])
            ctx.rate_limit_partial_stop = bool(scan_diagnostics.get("rate_limit_partial_stop"))
            ctx.rate_limit_partial_stop_symbol = (
                str(scan_diagnostics.get("rate_limit_partial_stop_symbol") or "").strip() or None
            )
            ctx.rate_limit_partial_completed_count = int(
                scan_diagnostics.get("rate_limit_partial_completed_count", 0) or 0
            )
            ctx.rate_limit_partial_remaining_count = int(
                scan_diagnostics.get("rate_limit_partial_remaining_count", 0) or 0
            )
            if ctx.rate_limit_partial_stop:
                print(
                    "[info] BUY scan partial due to rate limit | "
                    f"stop_symbol={ctx.rate_limit_partial_stop_symbol or '-'} | "
                    f"completed={ctx.rate_limit_partial_completed_count} | "
                    f"remaining={ctx.rate_limit_partial_remaining_count}"
                )
            if ctx.buy_scan_budget_reserved and ctx.buy_scan_evaluated_count < ctx.buy_scan_requested_count:
                ctx.buy_scan_partial_budget = True
                print("[info] BUY scan partial due to remaining budget")
                _log_engine_event(
                    action="buy_scan_partial_budget",
                    reason="남은 API budget 안에서 BUY universe 일부만 평가했습니다.",
                    cycle_id=cycle_id,
                    market_open=ctx.market_open,
                    market_session=ctx.session_status.session,
                )
            logging_started_perf = time.perf_counter()
            for line in build_scan_console_lines(
                results=ctx.scan_results,
                selected_result=ctx.selected_candidate,
                requested_count=ctx.buy_scan_requested_count,
                evaluated_count=ctx.buy_scan_evaluated_count,
                top_k=settings.buy_scan_top_k_candidates,
            ):
                print(line)
            print()
            ctx.buy_scan_logging_ms = (time.perf_counter() - logging_started_perf) * 1000
            ctx.timing_summary["buy_scan"] = _phase_timing_summary(
                elapsed_ms=(time.perf_counter() - buy_scan_started_perf) * 1000,
                request_delta=buy_scan_delta,
            )
            _print_scan_only_notice()
            _record_cycle_action(
                state,
                action="SCAN_ONLY",
                reason="scan_only 모드라 주문 검토를 생략했습니다.",
                selected_symbol=ctx.selected_candidate.symbol if ctx.selected_candidate else None,
            )
            return True

        if ctx.selected_sell_candidate is not None:
            if ctx.rate_limit_triggered and ctx.rate_limit_source == "sell_watch":
                deferred_reason = (
                    "SELL watch에서 rate limit이 감지되어 이번 사이클의 매도 주문은 "
                    "다음 tick으로 넘깁니다."
                )
                ctx.sell_status_text = "skipped(rate_limit_backoff)"
                ctx.buy_status_text = "skipped(sell_rate_limit_backoff)"
                # Prefer retrying the actionable SELL candidate over the quote
                # symbol that first reported rate limit; the remaining positions
                # still follow the rotated partial cursor on the next tick.
                state["sell_watch_retry_symbol"] = ctx.selected_sell_candidate.symbol
                sell_watch_retry_index = next(
                    (
                        index
                        for index, position in enumerate(ctx.prioritized_positions)
                        if position.symbol == ctx.selected_sell_candidate.symbol
                    ),
                    None,
                )
                if sell_watch_retry_index is not None:
                    ctx.sell_watch_cursor_after = sell_watch_retry_index
                    state["sell_watch_next_start_index"] = sell_watch_retry_index
                note("DEGRADED", deferred_reason)
                _print_cycle_conclusion(
                    side="HOLD",
                    display_name=ctx.selected_sell_candidate.display_name,
                    reason=deferred_reason,
                    planned_qty=0,
                )
                _record_cycle_action(
                    state,
                    action="SELL_DEFERRED_RATE_LIMIT_BACKOFF",
                    reason=deferred_reason,
                    order_side="SELL",
                    symbol=ctx.selected_sell_candidate.symbol,
                    qty=0,
                    selected_symbol=ctx.selected_sell_candidate.symbol,
                )
                _log_engine_event(
                    action="sell_order_deferred_rate_limit_backoff",
                    reason=deferred_reason,
                    cycle_id=cycle_id,
                    market_open=ctx.market_open,
                    market_session=ctx.session_status.session,
                )
                return True
            if settings.enable_rebalance_sell:
                _print_rebalance_skip(
                    "일반 SELL 후보가 있어서 rebalance를 검토하지 않습니다."
                )
            ctx.active_sell_analysis = ctx.selected_sell_candidate
            ctx.sell_position_sizing = calculate_sell_position_sizing(
                trigger=ctx.active_sell_analysis.sell_decision.triggered_rule_name,
                holding_qty=ctx.active_sell_analysis.holding_qty,
                current_price=ctx.active_sell_analysis.market_snapshot.current_price,
                settings=settings,
            )
            sell_log_context = {
                "symbol": ctx.active_sell_analysis.symbol,
                "qty": ctx.sell_position_sizing.recommended_sell_qty,
                "order_type": "market_sell",
                "confirm_buy": settings.confirm_buy,
                "market_open": ctx.market_open,
                "cycle_id": cycle_id,
            }
            sell_raw_response = {
                "sell_strategy_details": ctx.active_sell_analysis.sell_decision.to_log_payload(),
                "buy_strategy_details": ctx.active_sell_analysis.buy_strategy_result.to_log_payload(),
                "sell_position_sizing": ctx.sell_position_sizing.details,
                "trigger": ctx.sell_position_sizing.sell_trigger,
                "recommended_sell_qty": ctx.sell_position_sizing.recommended_sell_qty,
                "sell_plan": {
                    "current_price_krw": ctx.active_sell_analysis.market_snapshot.current_price,
                    "qty": ctx.sell_position_sizing.recommended_sell_qty,
                    "notional_krw": ctx.sell_position_sizing.recommended_notional_krw,
                    "estimated_sell_fee_krw": ctx.sell_position_sizing.details["estimated_sell_fee_krw"],
                    "estimated_sell_tax_krw": ctx.sell_position_sizing.details["estimated_sell_tax_krw"],
                    "estimated_sell_slippage_krw": ctx.sell_position_sizing.details[
                        "estimated_sell_slippage_krw"
                    ],
                    "estimated_net_proceeds_krw": ctx.sell_position_sizing.details[
                        "estimated_net_proceeds_krw"
                    ],
                },
            }
            sell_flow_context: dict[str, object] = {"risk_guard_payload": None}
            consumed_by_sell = sell_order_flow(
                state=state,
                settings=settings,
                token=ctx.token,
                portfolio_snapshot=ctx.portfolio_snapshot,
                analysis=ctx.active_sell_analysis,
                sell_sizing=ctx.sell_position_sizing,
                sell_log_context=sell_log_context,
                sell_raw_response=sell_raw_response,
                market_open=ctx.market_open,
                session_status=ctx.session_status,
                cycle_reason=ctx.sell_position_sizing.sell_trigger or "-",
                cycle_action_label="매도 주문",
                flow_context=sell_flow_context,
                api_budget_state=api_budget_state,
                wait_for_execution_request_budget=execution_budget_wait,
            )
            ctx.sell_risk_guard_payload = sell_flow_context.get("risk_guard_payload")
            if consumed_by_sell:
                if settings.strict_sell_first:
                    print("BUY 생략 사유: SELL 우선 정책 적용")
                    print()
                    return True
                print("STRICT_SELL_FIRST=false 이므로 BUY 검토를 계속합니다.")
                print()
    return False
