"""``run_cycle`` SELL-watch phase — Stage B-3 slice K (国면 9).

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 (国면 지도 9행 / "Stage B
실행 사양"): the SELL watch evaluation loop of ``app.main.run_cycle`` — from the
``sell_eval_started_perf = time.perf_counter()`` timing start through the SELL
evaluation timing block (``ctx.sell_evaluated_count = len(ctx.sell_analysis_results)``
… ``ctx.sell_lane_running = False``) — is moved here **byte-verbatim** (logic /
order / side effects unchanged; only the leading indentation is adjusted from the
``run_cycle`` ``try`` body's 8 spaces to this function body's 4 spaces). The three
cursor writes (``state["sell_watch_retry_symbol"]`` ×2 in the 200-OK body /
``ApiHttpError`` rate-limit branches, ``state["sell_watch_next_start_index"]`` at
the loop tail) move with the block; ``state`` is passed by reference so their
position is neutral.

The original block has **no early ``return``** — the two rate-limit branches and
the budget-cap branches use ``break``/``continue`` inside the ``for`` loop, and the
phase always falls through. So this function always returns ``False`` (convention
consistency with the sibling phases; ``run_cycle`` continues into the phase-10
``run_regime_phase`` call immediately after).

``ctx.prioritized_positions`` (gate K-1) is the priority-ordered holdings list this
phase builds; it is stored on ``ctx`` so the phase-11 SELL-order defer block can
read it after ``run_cycle`` returns from this phase.

Deps discipline (§3 "deps 번들 주입" — individual keyword params, following the
slice-I/J precedent): every ``app.main`` collaborator that a test may patch
(blocklist-checked against ``main_patched_names.txt`` — the ``_api_budget_*``
family, ``inquire_price`` / ``get_account_scope_context`` / ``get_korean_now``
(all blocklist), ``build_sell_watch_budget_plan`` (blocklist), ``_log_engine_event``
(blocklist), and ``_api_budget_note_rate_limit`` (spec K-3, patched on
``main_module`` by ``test_buy_order_flow_characterization.py`` and injected by every
sibling phase)) is received as an **identically named keyword parameter**, bound at
the call site from ``run_cycle``'s enclosing/module scope. ``get_korean_now`` is
passed **by reference** (never pre-called) so the ``side_effect`` mock sequences in
the rate-limit tests keep their call order. The ``note`` run_cycle closure and the
run_cycle locals (``settings`` / ``state`` / ``api_budget_state`` /
``scheduler_state`` / ``cycle_id`` / ``sell_check_due``) are likewise injected.

Only non-blocklisted, un-patched pure utilities are imported directly — the same
imports the sibling phase modules already make (``time``,
``get_request_metrics_summary`` / ``get_throttle_metrics_summary``,
``_request_metrics_delta`` / ``_phase_timing_summary``, the pure builders
``build_sell_watch_priority`` / ``build_sell_watch_cursor_plan`` /
``retry_anchor_to_restore_after_empty_budget`` / ``build_market_snapshot`` /
``_build_sell_analysis``, the classifiers ``is_rate_limit_response`` /
``_looks_like_rate_limit_error`` / ``_api_budget_preserves_buy_scan_reserve``, and
``ApiHttpError`` / ``SellAnalysisResult``). A quoted
5-pattern grep confirmed none of these are patched on ``main_module``. This module
must NOT import ``app.main``.
"""

from __future__ import annotations

import time
from typing import Any

from app.auth.token import (
    ApiHttpError,
    get_request_metrics_summary,
    is_rate_limit_response,
)
from app.core.error_classification import (
    looks_like_rate_limit_error as _looks_like_rate_limit_error,
)
from app.core.runtime_budget import (
    api_budget_preserves_buy_scan_reserve as _api_budget_preserves_buy_scan_reserve,
    request_metrics_delta as _request_metrics_delta,
)
from app.core.sell_watch_cursor import (
    build_sell_watch_cursor_plan,
    retry_anchor_to_restore_after_empty_budget,
)
from app.core.throttle import get_throttle_metrics_summary
from app.market_data.schema import build_market_snapshot
from app.reporting.console import phase_timing_summary as _phase_timing_summary
from app.strategy.sell_decision import (
    SellAnalysisResult,
    build_sell_analysis as _build_sell_analysis,
    build_sell_watch_priority,
)


def run_sell_watch_phase(
    ctx: Any,
    *,
    settings: Any,
    state: Any,
    api_budget_state: Any,
    scheduler_state: Any,
    cycle_id: Any,
    sell_check_due: Any,
    note: Any,
    get_korean_now: Any,
    inquire_price: Any,
    get_account_scope_context: Any,
    build_sell_watch_budget_plan: Any,
    _log_engine_event: Any,
    _api_budget_note_rate_limit: Any,
    _api_budget_request_window_size: Any,
    _api_budget_remaining_requests: Any,
    _api_budget_remaining_quotes: Any,
    _api_budget_min_wait_for_request_slot: Any,
    _api_budget_can_quote: Any,
    _api_budget_can_request: Any,
    _api_budget_register_request: Any,
    _api_budget_backoff_remaining_seconds: Any,
) -> bool:
    sell_eval_started_perf = time.perf_counter()
    sell_metrics_before = get_request_metrics_summary()
    sell_throttle_before = get_throttle_metrics_summary()
    held_positions = list(ctx.portfolio_snapshot.held_positions)
    ctx.sell_watch_total_holdings = len(held_positions)
    if held_positions and sell_check_due:
        ctx.sell_lane_running = True
        sell_analyses: list[SellAnalysisResult] = []
        sell_priority_rows = [
            (
                position,
                build_sell_watch_priority(
                    position=position,
                    portfolio_snapshot=ctx.portfolio_snapshot,
                ),
            )
            for position in held_positions
        ]
        sell_priority_rows.sort(
            key=lambda item: (
                -float(item[1].get("priority_score", 0.0) or 0.0),
                str(item[0].symbol),
            )
        )
        ctx.prioritized_positions = [item[0] for item in sell_priority_rows]
        ctx.sell_watch_priority_preview = [
            {
                "symbol": row[0].symbol,
                "name": row[0].name,
                "priority_score": float(row[1].get("priority_score", 0.0) or 0.0),
                "summary": str(row[1].get("summary") or "").strip() or None,
                "weight_pct": float(row[1].get("weight_pct", 0.0) or 0.0),
                "loss_pressure": float(row[1].get("loss_pressure", 0.0) or 0.0),
            }
            for row in sell_priority_rows[:5]
        ]
        ctx.sell_watch_cursor_before = int(state.get("sell_watch_next_start_index", 0) or 0)
        if ctx.sell_watch_total_holdings > 0:
            ctx.sell_watch_cursor_before %= ctx.sell_watch_total_holdings
        else:
            ctx.sell_watch_cursor_before = 0
        sell_watch_retry_symbol = str(state.get("sell_watch_retry_symbol") or "").strip()
        # Task 2: risk-first evaluation ordering — when recent partial evaluations
        # have been logged (budget pressure), skip cursor rotation and put highest
        # priority (deepest loss / stop-loss proximity) positions first so they
        # are never pushed to the back of a partial evaluation window.
        _recent_partial = bool(api_budget_state.get("last_sell_watch_partial") or False)
        _top_priority_score = float(
            sell_priority_rows[0][1].get("priority_score", 0.0) if sell_priority_rows else 0.0
        )
        _risk_first_override = _recent_partial and _top_priority_score >= 1.0
        sell_watch_cursor_plan = build_sell_watch_cursor_plan(
            symbols=tuple(position.symbol for position in ctx.prioritized_positions),
            next_start_index=ctx.sell_watch_cursor_before,
            retry_symbol=sell_watch_retry_symbol,
            risk_first_override=_risk_first_override,
        )
        ordered_positions = [
            ctx.prioritized_positions[index]
            for index in sell_watch_cursor_plan.ordered_indices
        ]
        ctx.sell_watch_cursor_before = sell_watch_cursor_plan.start_index
        if sell_watch_cursor_plan.clear_retry_symbol:
            state["sell_watch_retry_symbol"] = None
        if sell_watch_cursor_plan.used_retry_anchor:
            print(
                "[info] SELL watch retry anchor: "
                f"symbol={sell_watch_retry_symbol} | cursor={sell_watch_cursor_plan.retry_index}"
            )
        elif _risk_first_override:
            print(
                "[info] SELL watch risk-first override: recent partial + high-priority score "
                f"(score={_top_priority_score:.2f}) → cursor reset to 0"
            )
        print(
            f"[info] SELL watch cursor start={ctx.sell_watch_cursor_before} / holdings={ctx.sell_watch_total_holdings}"
        )
        if ctx.sell_watch_priority_preview:
            top_priority_text = ", ".join(
                [
                    f"{item['symbol']}(score={float(item['priority_score']):.2f}, {item['summary'] or '-'})"
                    for item in ctx.sell_watch_priority_preview[:3]
                ]
            )
            print(f"[info] SELL watch risk-priority top={top_priority_text}")
        visited_holdings = 0
        if ctx.market_open:
            scheduler_decision = str(scheduler_state.get("decision") or "")
            allow_sell_watch_during_backoff = scheduler_decision == "API_BACKOFF_WAIT"
            ctx.request_window_size_before_sell_watch = _api_budget_request_window_size(
                api_budget_state,
                now=get_korean_now(),
            )
            remaining_requests_for_sell_watch = _api_budget_remaining_requests(
                api_budget_state,
                now=get_korean_now(),
                allow_during_backoff=allow_sell_watch_during_backoff,
            )
            if allow_sell_watch_during_backoff:
                remaining_requests_for_sell_watch = min(1, remaining_requests_for_sell_watch)
            sell_watch_budget_plan = build_sell_watch_budget_plan(
                total_holdings=ctx.sell_watch_total_holdings,
                remaining_requests=remaining_requests_for_sell_watch,
                remaining_quotes=_api_budget_remaining_quotes(api_budget_state),
                request_window_size=ctx.request_window_size_before_sell_watch,
                soft_request_limit=settings.api_soft_max_requests_per_second,
                buy_scan_due=ctx.buy_scan_due,
                buy_scan_request_reserve=(
                    settings.api_buy_scan_min_request_reserve
                    if ctx.buy_scan_budget_reserved
                    else 0
                ),
                buy_scan_quote_reserve=(
                    settings.api_buy_scan_min_quote_reserve
                    if ctx.buy_scan_budget_reserved
                    else 0
                ),
                execution_request_reserve=(
                    0
                    if ctx.buy_scan_budget_reserved
                    else 2 if settings.confirm_buy == "YES" and ctx.market_open else 0
                ),
                recent_partial=bool(api_budget_state.get("last_sell_watch_partial") or False),
                last_rate_limit_source=str(api_budget_state.get("last_rate_limit_source") or ""),
                rate_limit_hits=int(api_budget_state.get("rate_limit_hits", 0) or 0),
                recent_partial_streak=int(
                    api_budget_state.get("consecutive_sell_watch_partial_cycles", 0) or 0
                ),
            )
            ctx.sell_watch_budget_plan_pressure_level = sell_watch_budget_plan.pressure_level
            ctx.sell_watch_budget_plan_reason = sell_watch_budget_plan.reason
            ctx.sell_watch_budget_plan_limit = int(sell_watch_budget_plan.max_evaluations)
            runtime_sell_watch_cap = scheduler_state.get(
                "effective_sell_watch_max_holdings_per_tick"
            )
            if runtime_sell_watch_cap is not None:
                ctx.sell_watch_budget_plan_limit = min(
                    ctx.sell_watch_budget_plan_limit,
                    max(1, int(runtime_sell_watch_cap or 0)),
                )
                base_reason = str(ctx.sell_watch_budget_plan_reason or "").strip()
                extra_reason = f"degraded cap={ctx.sell_watch_budget_plan_limit}"
                ctx.sell_watch_budget_plan_reason = (
                    f"{base_reason} | {extra_reason}" if base_reason else extra_reason
                )
            print(
                f"[info] request window size before sell_watch={ctx.request_window_size_before_sell_watch}"
            )
            print(
                "[info] SELL watch protection plan | "
                f"pressure={ctx.sell_watch_budget_plan_pressure_level} | "
                f"cap={ctx.sell_watch_budget_plan_limit}/{ctx.sell_watch_total_holdings}"
            )
            if ctx.sell_watch_budget_plan_reason:
                print(f"[info] {ctx.sell_watch_budget_plan_reason}")
            retry_symbol_to_restore = retry_anchor_to_restore_after_empty_budget(
                budget_limit=ctx.sell_watch_budget_plan_limit,
                retry_symbol=sell_watch_retry_symbol,
                retry_anchor_consumed=sell_watch_cursor_plan.used_retry_anchor,
            )
            if retry_symbol_to_restore:
                state["sell_watch_retry_symbol"] = retry_symbol_to_restore
            for position in ordered_positions:
                if visited_holdings >= ctx.sell_watch_budget_plan_limit:
                    ctx.sell_watch_partial = True
                    ctx.sell_watch_partial_reason = (
                        ctx.sell_watch_budget_plan_reason
                        or "SELL watch 보호 cap에 도달해 남은 종목은 다음 tick으로 미룹니다."
                    )
                    note("INFO", ctx.sell_watch_partial_reason)
                    _log_engine_event(
                        action="sell_watch_partial_budget_protection",
                        reason=ctx.sell_watch_partial_reason,
                        cycle_id=cycle_id,
                        market_open=ctx.market_open,
                        market_session=ctx.session_status.session,
                    )
                    break
                quote_request_at = get_korean_now()
                pre_quote_wait_seconds = _api_budget_min_wait_for_request_slot(
                    api_budget_state,
                    now=quote_request_at,
                    request_cost=1,
                    request_reserve=(
                        settings.api_buy_scan_min_request_reserve
                        if ctx.buy_scan_budget_reserved
                        else 0
                    ),
                )
                if pre_quote_wait_seconds > 0:
                    ctx.sell_eval_guard_wait_ms += pre_quote_wait_seconds * 1000.0
                    ctx.throttle_guard_triggered = True
                    ctx.throttle_guard_reason = "sell_watch_request_window"
                    print(
                        f"[info] throttle guard triggered before sell_watch | wait={int(round(pre_quote_wait_seconds * 1000))}ms"
                    )
                    time.sleep(pre_quote_wait_seconds)
                    quote_request_at = get_korean_now()
                if ctx.buy_scan_budget_reserved and not _api_budget_preserves_buy_scan_reserve(
                    api_budget_state,
                    now=quote_request_at,
                    request_cost=1,
                    quote_cost=1,
                    request_reserve=settings.api_buy_scan_min_request_reserve,
                    quote_reserve=settings.api_buy_scan_min_quote_reserve,
                ):
                    ctx.sell_watch_capped_for_buy_scan = True
                    ctx.buy_scan_reserve_used = True
                    ctx.sell_watch_partial = True
                    reserve_reason = (
                        "SELL watch를 일부만 보고 중단해 BUY scan 최소 reserve를 남깁니다."
                    )
                    ctx.sell_watch_partial_reason = reserve_reason
                    note("INFO", reserve_reason)
                    print("[info] SELL watch capped for BUY reserve")
                    print(f"[info] {reserve_reason}")
                    _log_engine_event(
                        action="sell_watch_capped_for_buy_scan",
                        reason=reserve_reason,
                        cycle_id=cycle_id,
                        market_open=ctx.market_open,
                        market_session=ctx.session_status.session,
                    )
                    break
                if not _api_budget_can_quote(api_budget_state, quote_cost=1):
                    ctx.sell_watch_partial = True
                    ctx.sell_watch_partial_reason = (
                        "예산 부족으로 남은 종목은 다음 tick으로 미룹니다."
                    )
                    note(
                        "DEGRADED",
                        "SELL watch quote 예산을 모두 사용해 남은 보유 종목 현재가 조회는 다음 tick으로 미룹니다.",
                    )
                    _log_engine_event(
                        action="sell_watch_partial_budget",
                        reason="SELL watch quote 예산 부족으로 일부 종목만 평가했습니다.",
                        cycle_id=cycle_id,
                        market_open=ctx.market_open,
                        market_session=ctx.session_status.session,
                    )
                    break
                if not _api_budget_can_request(
                    api_budget_state,
                    now=quote_request_at,
                    allow_during_backoff=allow_sell_watch_during_backoff,
                ):
                    ctx.sell_watch_partial = True
                    ctx.sell_watch_partial_reason = (
                        "예산 부족으로 남은 종목은 다음 tick으로 미룹니다."
                    )
                    note(
                        "DEGRADED",
                        "SELL watch request 예산이 부족해 남은 보유 종목 현재가 조회는 다음 tick으로 미룹니다.",
                    )
                    _log_engine_event(
                        action="sell_watch_partial_budget",
                        reason="SELL watch request 예산 부족으로 일부 종목만 평가했습니다.",
                        cycle_id=cycle_id,
                        market_open=ctx.market_open,
                        market_session=ctx.session_status.session,
                    )
                    break
                _api_budget_register_request(
                    api_budget_state,
                    now=quote_request_at,
                    quote_cost=1,
                )
                visited_holdings += 1
                ctx.sell_quote_request_count += 1
                try:
                    price_data = inquire_price(position.symbol, token=ctx.token)
                    if price_data.get("rt_cd") != "0":
                        if is_rate_limit_response(price_data):
                            state["sell_watch_retry_symbol"] = position.symbol
                            ctx.rate_limit_triggered = True
                            ctx.rate_limit_source = "sell_watch"
                            _api_budget_note_rate_limit(
                                api_budget_state,
                                now=get_korean_now(),
                                source="sell_watch",
                            )
                            ctx.backoff_applied_seconds = int(
                                _api_budget_backoff_remaining_seconds(
                                    api_budget_state, now=get_korean_now()
                                )
                            )
                            ctx.sell_watch_partial = True
                            ctx.sell_watch_partial_reason = (
                                "SELL watch에서 rate limit이 감지되어 남은 종목은 다음 tick으로 미룹니다."
                            )
                            note(
                                "ERROR",
                                f"rate limit detected at sell_watch({position.symbol}) | backoff applied: {ctx.backoff_applied_seconds}s",
                            )
                            _log_engine_event(
                                action="rate_limit_detected_sell_watch",
                                reason=f"{position.symbol} SELL watch 200-OK body에서 EGW00201을 감지했습니다.",
                                cycle_id=cycle_id,
                                market_open=ctx.market_open,
                                market_session=ctx.session_status.session,
                            )
                            break
                        note(
                            "DEGRADED",
                            f"보유 종목 현재가 조회 실패({position.symbol}): {price_data}",
                        )
                        continue
                    sell_calc_started_perf = time.perf_counter()
                    market_snapshot = build_market_snapshot(price_data["output"])
                    ctx.observed_market_snapshots[position.symbol] = market_snapshot
                    ctx.sell_watch_evaluated_symbols.append(position.symbol)
                    sell_analyses.append(
                        _build_sell_analysis(
                            symbol=position.symbol,
                            holding_qty=position.holding_qty,
                            average_cost=position.average_cost,
                            market_snapshot=market_snapshot,
                            portfolio_snapshot=ctx.portfolio_snapshot,
                            settings=settings,
                        )
                    )
                    ctx.sell_eval_calc_ms += (
                        time.perf_counter() - sell_calc_started_perf
                    ) * 1000.0
                except ApiHttpError as exc:
                    if _looks_like_rate_limit_error(exc):
                        state["sell_watch_retry_symbol"] = position.symbol
                        ctx.rate_limit_triggered = True
                        ctx.rate_limit_source = "sell_watch"
                        _api_budget_note_rate_limit(
                            api_budget_state,
                            now=get_korean_now(),
                            source="sell_watch",
                        )
                        ctx.backoff_applied_seconds = int(
                            _api_budget_backoff_remaining_seconds(
                                api_budget_state, now=get_korean_now()
                            )
                        )
                        ctx.sell_watch_partial = True
                        ctx.sell_watch_partial_reason = (
                            "SELL watch에서 rate limit이 감지되어 남은 종목은 다음 tick으로 미룹니다."
                        )
                        note(
                            "ERROR",
                            f"rate limit detected at sell_watch({position.symbol}) | backoff applied: {ctx.backoff_applied_seconds}s",
                        )
                        _log_engine_event(
                            action="rate_limit_detected_sell_watch",
                            reason=f"{position.symbol} SELL watch에서 EGW00201을 감지했습니다.",
                            cycle_id=cycle_id,
                            market_open=ctx.market_open,
                            market_session=ctx.session_status.session,
                        )
                        break
                    note(
                        "DEGRADED",
                        f"보유 종목 현재가 조회 건너뜀({position.symbol}): {exc}",
                    )
                    continue
                except Exception as exc:
                    note(
                        "DEGRADED",
                        f"보유 종목 현재가 조회 예외({position.symbol}): {exc}",
                    )
                    continue
        else:
            print(
                "[info] 정규장 외 세션이라 보유 종목 현재가는 잔고 평가 데이터 기준으로 표시합니다."
            )
        if ctx.sell_watch_total_holdings > 0:
            ctx.sell_watch_cursor_after = (
                (ctx.sell_watch_cursor_before + visited_holdings) % ctx.sell_watch_total_holdings
                if visited_holdings > 0
                else ctx.sell_watch_cursor_before
            )
        remaining_positions = (
            ordered_positions[visited_holdings:]
            if visited_holdings < len(ordered_positions)
            else []
        )
        ctx.sell_watch_skipped_symbols = [position.symbol for position in remaining_positions]
        if ctx.sell_watch_partial:
            print(
                f"[info] SELL watch partial: evaluated={len(ctx.sell_watch_evaluated_symbols)}, remaining={len(ctx.sell_watch_skipped_symbols)}"
            )
        print(f"[info] SELL watch cursor next={ctx.sell_watch_cursor_after}")
        if sell_analyses:
            ctx.sell_analysis_results = tuple(sell_analyses)
        state["sell_watch_next_start_index"] = ctx.sell_watch_cursor_after
    elif not held_positions:
        state["sell_watch_next_start_index"] = 0
    ctx.sell_evaluated_count = len(ctx.sell_analysis_results)
    sell_metrics_after = get_request_metrics_summary()
    sell_throttle_after = get_throttle_metrics_summary()
    sell_request_delta = _request_metrics_delta(
        sell_metrics_before,
        sell_metrics_after,
    )
    ctx.sell_eval_quote_response_ms = float(
        ((sell_request_delta.get("categories") or {}).get("quote") or {}).get(
            "elapsed_ms",
            0.0,
        )
    )
    ctx.sell_eval_throttle_sleep_ms = max(
        float(sell_throttle_after.get("total_sleep_ms", 0.0) or 0.0)
        - float(sell_throttle_before.get("total_sleep_ms", 0.0) or 0.0),
        0.0,
    )
    ctx.sell_eval_effective_total_sleep_ms = round(
        float(ctx.sell_eval_guard_wait_ms) + float(ctx.sell_eval_throttle_sleep_ms),
        1,
    )
    ctx.timing_summary["sell_evaluation"] = _phase_timing_summary(
        elapsed_ms=(time.perf_counter() - sell_eval_started_perf) * 1000,
        request_delta=sell_request_delta,
    )
    ctx.sell_lane_running = False
