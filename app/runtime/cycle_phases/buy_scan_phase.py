"""``run_cycle`` BUY-scan phase — Stage B-3 slice J (국면 12·13·14).

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 (국면 지도 12·13·14행 /
"Stage B 실행 사양"): the BUY cadence gate → universe / layered / pre-gating /
shallow build → deep-eval budget cap / request-slot guard / **prefetch join** /
deep scan → ranking / candidate selection / scan-metric aggregation / scan_only
termination / empty-scan classification block of ``app.main.run_cycle`` — from the
``sell_watch`` rate-limit drain sleep at BUY entry through the no-candidate HOLD
``return`` — is moved here **byte-verbatim** (logic/order/output unchanged; only
indentation adjusted). Phases 12→13→14 share ``run_cycle``-local products
(``deep_eval_symbols`` / ``ctx.raw_scan_results`` / ``ctx.scan_results`` etc.), so
they are extracted as a **single function** to keep those locals local (minimal
ctx promotion — an AST scope check confirmed 0 downstream reads of this block's
function-locals). The internal phase boundaries survive as the original comments.

Early-``return`` mapping (§3 "조기 return 다수"): every original bare ``return``
(13 of them — cadence, cycle-budget, pre-gated×2, shallow-empty, deep-eval budget,
quote/request budget×2, prefetch-insufficient, scan_only, rate-limit downgrade,
benign, no-candidate HOLD) becomes ``return True`` so ``run_cycle`` re-emits a
``return``. The lone ``raise RuntimeError`` (no analysable symbols) is kept as a
raise — it propagates to ``run_cycle``'s top-level ``try``. When a candidate is
selected the block falls through to ``return False`` and ``run_cycle`` continues
into the BUY-order phase 15.

Deps discipline (§3 "deps 번들 주입" — individual keyword params, following the
slice-H/I precedent): every ``app.main`` collaborator that a test may patch
(blocklist-checked against ``main_patched_names.txt``, incl. the ``_api_budget_*``
/ ``_print_buy_*`` / ``_build_buy_scan_*`` families and the ``note`` /
``resolve_buy_universe_symbols`` / ``log_buy_universe_source`` run_cycle closures
and the ``settings`` / ``state`` / ``cycle_budget`` / ``order_type`` run_cycle
locals) is received as an **identically named keyword parameter**, bound at the
call site from ``run_cycle``'s enclosing/module scope. Only non-blocklisted,
un-patched pure utilities are imported directly — the same imports
``account_snapshot.py`` / ``finalize.py`` already make (the B-1 join/metrics
helpers ``copy_join_guard_fields`` / ``flatten_prefetch_metrics`` /
``prefetch_metrics_from_result`` / ``restrict_symbols_to_prefetched``, the
``app.pipeline`` ``BUY_SCAN_LANE_CONTROLLER``, ``get_request_metrics_summary``,
``_request_metrics_delta`` / ``_phase_timing_summary`` and the pure builders /
formatters). This module must NOT import ``app.main``.
"""

from __future__ import annotations

import time
from typing import Any

from app.auth.token import get_request_metrics_summary
from app.core.error_classification import (
    build_buy_scan_rate_limit_degraded_reason as _build_buy_scan_rate_limit_degraded_reason,
    is_benign_empty_buy_scan as _is_benign_empty_buy_scan,
    should_downgrade_empty_buy_scan_to_backoff as _should_downgrade_empty_buy_scan_to_backoff,
)
from app.core.runtime_budget import request_metrics_delta as _request_metrics_delta
from app.pipeline import (
    BUY_SCAN_LANE_CONTROLLER,
    prefetch_metrics_from_result,
    restrict_symbols_to_prefetched,
)
from app.reporting.console import phase_timing_summary as _phase_timing_summary
from app.reporting.cycle_context import _build_buy_analysis_block_context
from app.runtime.cycle_phases.prefetch_join import (
    copy_join_guard_fields,
    flatten_prefetch_metrics,
)
from app.runtime.scan_only_diagnostic import print_scan_only_notice as _print_scan_only_notice
from app.scanner.dead_symbol_quarantine import (
    DEAD_SYMBOL_MISS_THRESHOLD,
    filter_dead_symbols,
    update_dead_symbol_tracking,
)
from app.scanner.runtime_scan import normalize_pre_gating_payload as _normalize_pre_gating_payload


def run_buy_scan_phase(
    ctx: Any,
    *,
    settings: Any,
    state: Any,
    api_budget_state: Any,
    scheduler_state: Any,
    cycle_id: Any,
    cycle_budget: Any,
    order_type: Any,
    note: Any,
    resolve_buy_universe_symbols: Any,
    log_buy_universe_source: Any,
    get_korean_now: Any,
    _api_budget_backoff_active: Any,
    _api_budget_backoff_remaining_seconds: Any,
    _api_budget_transient_backoff_active: Any,
    _api_budget_request_window_size: Any,
    _api_budget_can_quote: Any,
    _api_budget_can_request: Any,
    _api_budget_min_wait_for_request_slot: Any,
    _api_budget_note_rate_limit: Any,
    _cap_buy_scan_deep_eval_symbols_for_api_budget: Any,
    _select_buy_scan_profile: Any,
    _build_buy_scan_layered_universe: Any,
    _build_buy_scan_pre_gating: Any,
    _build_buy_scan_shallow_plan: Any,
    _build_buy_scan_deep_eval_symbols: Any,
    _apply_buy_runtime_guards_to_scan_results: Any,
    _record_cycle_action: Any,
    _log_engine_event: Any,
    _print_cycle_conclusion: Any,
    _print_buy_pre_gating_summary: Any,
    _print_buy_scan_stage_summary: Any,
    _print_buy_runtime_filter_summary: Any,
    _print_buy_strategy: Any,
    _print_buy_score_summary: Any,
    scan_target_symbols: Any,
    get_last_scan_diagnostics: Any,
    get_adaptive_pacing_summary: Any,
    select_top_candidate: Any,
    select_top_analysis_result: Any,
    serialize_selection_details: Any,
    buy_scan_uses_separate_quote_account: Any,
    build_universe_console_lines: Any,
    build_scan_console_lines: Any,
    log_order_event: Any,
    mark_buy_blocked: Any,
    resolve_mock_buy_price_floor_krw: Any,
) -> bool:
    if ctx.rate_limit_triggered and ctx.rate_limit_source == "sell_watch":
        _sw_drain_now = get_korean_now()
        _sw_drain_seconds = _api_budget_backoff_remaining_seconds(api_budget_state, now=_sw_drain_now)
        if _sw_drain_seconds > 0:
            print(
                f"[info] BUY scan entry: draining sell_watch rate-limit backoff"
                f" | wait={round(_sw_drain_seconds * 1000)}ms"
            )
            time.sleep(_sw_drain_seconds)
            ctx.sell_watch_backoff_drain_ms = round(_sw_drain_seconds * 1000, 1)
            # Backoff served — fall through to buy_scan

    if not ctx.buy_scan_due:
        scheduler_decision = str(scheduler_state.get("decision") or "")
        if ctx.buy_scan_skipped_reason == "previous_scan_running":
            hold_reason = "이전 BUY scan이 아직 종료되지 않아 새 BUY scan을 시작하지 않습니다."
            skip_action = "skipped_buy_scan_previous_running"
            ctx.buy_status_text = "skipped(previous_scan_running)"
        elif ctx.buy_scan_skipped_reason == "cycle_budget_low":
            hold_reason = "남은 cycle hard budget이 부족해 BUY scan을 건너뜁니다."
            skip_action = "skipped_buy_scan_cycle_budget_low"
            ctx.buy_status_text = "skipped(cycle_budget_low)"
        elif scheduler_decision == "SELL_PRIORITY_DEFER_BUY_BUDGET":
            hold_reason = "API budget 부족으로 BUY scan을 다음 tick으로 미룹니다."
            skip_action = "skipped_buy_scan_budget_limited"
            ctx.buy_scan_skipped_reason = "api_budget_limited"
            ctx.buy_status_text = "skipped(api_budget_limited)"
        elif scheduler_decision == "API_BACKOFF_WAIT":
            hold_reason = "직전 rate limit backoff 중이라 BUY scan을 잠시 미룹니다."
            skip_action = "skipped_buy_scan_budget_limited"
            ctx.buy_scan_skipped_reason = "api_backoff"
            ctx.buy_status_text = "skipped(api_backoff)"
        else:
            hold_reason = "BUY scan 주기 대기"
            skip_action = "skipped_buy_scan_cadence"
            ctx.buy_scan_skipped_reason = "cadence_not_reached"
            ctx.buy_status_text = "skipped(cadence)"
        print(f"[info] buy cadence: skipped | reason={hold_reason}")
        print(f"[info] {hold_reason}")
        _print_cycle_conclusion(
            side="HOLD",
            display_name="-",
            reason=hold_reason,
            planned_qty=0,
        )
        _record_cycle_action(
            state,
            action="HOLD_BUY_SCAN_WAIT",
            reason=hold_reason,
            selected_symbol=None,
        )
        _log_engine_event(
            action=skip_action,
            reason=hold_reason,
            cycle_id=cycle_id,
            market_open=ctx.market_open,
            market_session=ctx.session_status.session,
        )
        return True

    if cycle_budget.should_skip_stage(min_remaining_seconds=3.0):
        ctx.buy_scan_skipped_reason = "cycle_budget_low"
        ctx.buy_status_text = "skipped(cycle_budget_low)"
        ctx.cycle_budget_exceeded = cycle_budget.exceeded
        ctx.budget_exceeded_stage = "before_buy_scan"
        _print_cycle_conclusion(
            side="HOLD",
            display_name="-",
            reason="남은 cycle hard budget이 부족해 BUY scan을 생략합니다.",
            planned_qty=0,
        )
        _record_cycle_action(
            state,
            action="HOLD_CYCLE_BUDGET_BUY_SCAN",
            reason="남은 cycle hard budget이 부족해 BUY scan을 생략했습니다.",
            selected_symbol=None,
        )
        _log_engine_event(
            action="skipped_buy_scan_cycle_budget_low",
            reason="cycle_budget_low",
            cycle_id=cycle_id,
            market_open=ctx.market_open,
            market_session=ctx.session_status.session,
        )
        return True

    for line in build_universe_console_lines(settings):
        print(line)
    print()
    print("[info] buy cadence: due | BUY scan running")
    if ctx.buy_quote_prefetch_snapshot_info is not None:
        live_snapshot_symbols = ctx.buy_quote_prefetch_symbols
        snapshot_info = ctx.buy_quote_prefetch_snapshot_info
    else:
        live_snapshot_symbols, snapshot_info = resolve_buy_universe_symbols()
    log_buy_universe_source(snapshot_info)
    ctx.requested_buy_symbols = tuple(live_snapshot_symbols or settings.target_symbols)
    ctx.selection_details["live_snapshot"] = snapshot_info
    held_symbols = tuple(
        position.symbol
        for position in getattr(ctx.portfolio_snapshot, "held_positions", ())
        if int(getattr(position, "holding_qty", 0) or 0) > 0
    )
    ctx.buy_scan_profile_state = _select_buy_scan_profile(state, settings)
    ctx.buy_scan_layered_universe = _build_buy_scan_layered_universe(
        state=state,
        settings=settings,
        raw_symbols=ctx.requested_buy_symbols,
        excluded_symbols=held_symbols if settings.run_mode == "trade" else (),
    )
    layered_symbols = tuple(ctx.buy_scan_layered_universe.get("selected_symbols") or ())
    ctx.buy_pre_gating = _build_buy_scan_pre_gating(
        candidate_symbols=layered_symbols,
        state=state,
        settings=ctx.effective_buy_settings,
        portfolio_snapshot=ctx.portfolio_snapshot,
        daily_pnl_brake_state=ctx.daily_pnl_brake_state,
        regime_state=ctx.regime_state,
        mode=settings.run_mode,
    )
    normalized_pre_gating = _normalize_pre_gating_payload(
        {
        "requested_count": len(layered_symbols),
        "allowed_count": len(tuple(ctx.buy_pre_gating.get("allowed_symbols") or ())),
        "early_reject_count": int(ctx.buy_pre_gating.get("early_reject_count", 0) or 0),
        "reason_counts": dict(ctx.buy_pre_gating.get("reason_counts") or {}),
        "rejected": list(ctx.buy_pre_gating.get("rejected") or []),
        "scan_allowed": bool(ctx.buy_pre_gating.get("scan_allowed", True)),
        "scan_block_reason": ctx.buy_pre_gating.get("scan_block_reason"),
        }
    )
    ctx.buy_scan_shallow_plan = _build_buy_scan_shallow_plan(
        state=state,
        settings=settings,
        profile=str(ctx.buy_scan_profile_state.get("profile") or "momentum"),
        symbols=tuple(ctx.buy_pre_gating.get("allowed_symbols") or ()),
        layer_by_symbol=dict(ctx.buy_scan_layered_universe.get("layer_by_symbol") or {}),
    )
    buy_scan_deep_eval_symbols = _build_buy_scan_deep_eval_symbols(
        shallow_plan=ctx.buy_scan_shallow_plan
    )
    ctx.buy_scan_shallow_plan["deep_eval_symbols"] = buy_scan_deep_eval_symbols
    ctx.buy_scan_shallow_plan["deep_eval_budget_cap"] = {
        "budget_cap_applied": False,
        "budget_cap_reasons": [],
        "quote_budget_cap_applied": False,
        "request_budget_cap_applied": False,
        "quote_budget_remaining_before_scan": None,
        "request_budget_remaining_before_scan": None,
        "execution_request_reserve": 0,
        "min_scan_request_floor": 0,
        "request_budget_floor_applied": False,
        "execution_request_reserve_relaxed_by": 0,
        "request_budget_available_for_scan": None,
        "quote_budget_original_count": len(buy_scan_deep_eval_symbols),
        "budget_capped_count": len(buy_scan_deep_eval_symbols),
    }
    ctx.buy_scan_shallow_plan["core_rescue_deep_eval_priority_applied"] = (
        buy_scan_deep_eval_symbols
        != tuple(ctx.buy_scan_shallow_plan.get("shortlist_symbols") or ())
    )
    ctx.selection_details["pre_gating"] = normalized_pre_gating
    ctx.selection_details["staged_scan"] = {
        "profile": ctx.buy_scan_profile_state.get("profile"),
        "rotation_enabled": bool(ctx.buy_scan_profile_state.get("rotation_enabled")),
        "raw_universe_count": len(ctx.requested_buy_symbols),
        "layered_universe_count": len(layered_symbols),
        "excluded_holding_count": int(ctx.buy_scan_layered_universe.get("excluded_count", 0) or 0),
        "core_count": int(ctx.buy_scan_layered_universe.get("core_count", 0) or 0),
        "rotating_count": int(ctx.buy_scan_layered_universe.get("rotating_count", 0) or 0),
        "exploration_count": int(ctx.buy_scan_layered_universe.get("exploration_count", 0) or 0),
        "selected_core_count": int(ctx.buy_scan_layered_universe.get("selected_core_count", 0) or 0),
        "selected_rotating_count": int(ctx.buy_scan_layered_universe.get("selected_rotating_count", 0) or 0),
        "selected_exploration_count": int(ctx.buy_scan_layered_universe.get("selected_exploration_count", 0) or 0),
        "pre_gating_count": int(normalized_pre_gating.get("early_reject_count", 0) or 0),
        "shallow_ranked_count": int(ctx.buy_scan_shallow_plan.get("ranked_count", 0) or 0),
        "deep_eval_limit": int(ctx.buy_scan_shallow_plan.get("deep_eval_limit", 0) or 0),
        "exploration_quota_used": int(ctx.buy_scan_shallow_plan.get("exploration_quota_used", 0) or 0),
        "core_rescue_applied": bool(ctx.buy_scan_shallow_plan.get("core_rescue_applied", False)),
        "core_rescue_selected_symbol": ctx.buy_scan_shallow_plan.get("core_rescue_selected_symbol"),
        "core_rescue_selected_score": ctx.buy_scan_shallow_plan.get("core_rescue_selected_score"),
        "core_rescue_replaced_symbol": ctx.buy_scan_shallow_plan.get("core_rescue_replaced_symbol"),
        "core_rescue_reason": ctx.buy_scan_shallow_plan.get("core_rescue_reason"),
        "core_rescue_deep_eval_priority_applied": bool(
            ctx.buy_scan_shallow_plan.get("core_rescue_deep_eval_priority_applied", False)
        ),
        "layered_symbols_preview": {
            "core": list((ctx.buy_scan_layered_universe.get("preview") or {}).get("core", []) or []),
            "rotating": list((ctx.buy_scan_layered_universe.get("preview") or {}).get("rotating", []) or []),
            "exploration": list((ctx.buy_scan_layered_universe.get("preview") or {}).get("exploration", []) or []),
        },
        "shallow_shortlist_preview": list(ctx.buy_scan_shallow_plan.get("shortlist_preview") or []),
        "deep_eval_symbols": list(buy_scan_deep_eval_symbols),
    }
    _print_buy_pre_gating_summary(
        pre_gating=ctx.buy_pre_gating,
        cycle_id=cycle_id,
        market_open=ctx.market_open,
        market_session=ctx.session_status.session,
    )
    _print_buy_scan_stage_summary(
        profile_state=ctx.buy_scan_profile_state,
        layered_universe=ctx.buy_scan_layered_universe,
        pre_gating=normalized_pre_gating,
        shallow_plan=ctx.buy_scan_shallow_plan,
    )
    if not ctx.buy_pre_gating.get("scan_allowed", True):
        hold_reason = str(
            ctx.buy_pre_gating.get("scan_block_reason")
            or "BUY 사전 게이트에서 전체 스캔이 차단되었습니다."
        )
        ctx.buy_scan_requested_count = int(ctx.buy_scan_shallow_plan.get("deep_eval_limit", 0) or 0)
        ctx.buy_scan_skipped_reason = "pre_gated_scan_blocked"
        ctx.buy_status_text = "skipped(pre_gated)"
        _print_cycle_conclusion(
            side="HOLD",
            display_name="-",
            reason=hold_reason,
            planned_qty=0,
        )
        _record_cycle_action(
            state,
            action="HOLD_BUY_PRE_GATED",
            reason=hold_reason,
            selected_symbol=None,
        )
        return True
    effective_scan_symbols = tuple(ctx.buy_pre_gating.get("allowed_symbols") or ())
    ctx.buy_scan_requested_count = int(ctx.buy_scan_shallow_plan.get("deep_eval_limit", 0) or 0)
    if not effective_scan_symbols:
        hold_reason = "모든 BUY 유니버스가 사전 게이트에서 차단되어 deep BUY scoring을 생략합니다."
        ctx.buy_scan_skipped_reason = "pre_gated_all_symbols"
        ctx.buy_status_text = "skipped(pre_gated)"
        _print_cycle_conclusion(
            side="HOLD",
            display_name="-",
            reason=hold_reason,
            planned_qty=0,
        )
        _record_cycle_action(
            state,
            action="HOLD_BUY_PRE_GATED",
            reason=hold_reason,
            selected_symbol=None,
        )
        return True
    shortlist_symbols = tuple(ctx.buy_scan_shallow_plan.get("shortlist_symbols") or ())
    deep_eval_symbols = tuple(ctx.buy_scan_shallow_plan.get("deep_eval_symbols") or ())
    if not shortlist_symbols:
        hold_reason = "shallow ranking 결과 deep evaluation 대상이 없어 이번 BUY scan을 생략합니다."
        ctx.buy_scan_requested_count = 0
        ctx.buy_scan_skipped_reason = "shallow_shortlist_empty"
        ctx.buy_status_text = "skipped(shallow_shortlist_empty)"
        _print_cycle_conclusion(
            side="HOLD",
            display_name="-",
            reason=hold_reason,
            planned_qty=0,
        )
        _record_cycle_action(
            state,
            action="HOLD_BUY_SCAN_WAIT",
            reason=hold_reason,
            selected_symbol=None,
        )
        return True

    scan_request_at = get_korean_now()
    min_buy_scan_request_floor = (
        min(3, len(deep_eval_symbols))
        if settings.confirm_buy == "YES"
        and ctx.market_open
        and not _api_budget_backoff_active(api_budget_state, now=scan_request_at)
        and not _api_budget_transient_backoff_active(api_budget_state, now=scan_request_at)
        and int(api_budget_state.get("rate_limit_hits", 0) or 0) <= 0
        else 0
    )
    ctx.buy_scan_separate_quote_lane = buy_scan_uses_separate_quote_account(settings)
    if ctx.buy_scan_separate_quote_lane:
        deep_eval_budget_cap = {
            "budget_cap_applied": False,
            "budget_cap_reasons": [],
            "quote_budget_cap_applied": False,
            "request_budget_cap_applied": False,
            "quote_budget_remaining_before_scan": None,
            "request_budget_remaining_before_scan": None,
            "execution_request_reserve": (
                2 if settings.confirm_buy == "YES" and ctx.market_open else 0
            ),
            "min_scan_request_floor": min_buy_scan_request_floor,
            "request_budget_floor_applied": False,
            "execution_request_reserve_relaxed_by": 0,
            "request_budget_available_for_scan": None,
            "quote_budget_original_count": len(deep_eval_symbols),
            "budget_capped_count": len(deep_eval_symbols),
            "separate_quote_lane_budget_bypass": True,
        }
        print(
            "[info] BUY deep-eval execution budget cap bypassed"
            " | reason=separate live quote lane"
            f" | symbols={len(deep_eval_symbols)}"
        )
    else:
        deep_eval_symbols, deep_eval_budget_cap = _cap_buy_scan_deep_eval_symbols_for_api_budget(
            symbols=deep_eval_symbols,
            api_budget_state=api_budget_state,
            now=scan_request_at,
            execution_request_reserve=(
                2 if settings.confirm_buy == "YES" and ctx.market_open else 0
            ),
            min_scan_request_floor=min_buy_scan_request_floor,
        )
    ctx.buy_scan_requested_count = len(deep_eval_symbols)
    ctx.buy_scan_shallow_plan["deep_eval_symbols"] = deep_eval_symbols
    ctx.buy_scan_shallow_plan["deep_eval_budget_cap"] = deep_eval_budget_cap
    staged_scan_details = ctx.selection_details.get("staged_scan")
    if isinstance(staged_scan_details, dict):
        staged_scan_details["deep_eval_symbols"] = list(deep_eval_symbols)
        staged_scan_details["deep_eval_budget_cap"] = deep_eval_budget_cap
    if deep_eval_budget_cap.get("budget_cap_applied"):
        print(
            "[info] BUY deep-eval capped by API budget"
            f" | reasons={','.join(list(deep_eval_budget_cap.get('budget_cap_reasons') or [])) or '-'}"
            f" | {deep_eval_budget_cap.get('budget_capped_count')}"
            f"/{deep_eval_budget_cap.get('quote_budget_original_count')}"
            f" | remaining_quotes={deep_eval_budget_cap.get('quote_budget_remaining_before_scan')}"
            f" | remaining_requests={deep_eval_budget_cap.get('request_budget_remaining_before_scan')}"
            f" | execution_reserve={deep_eval_budget_cap.get('execution_request_reserve')}"
            f" | scan_floor={deep_eval_budget_cap.get('min_scan_request_floor')}"
        )
    if not deep_eval_symbols:
        hold_reason = "API budget 부족으로 BUY deep-eval 대상이 없어 이번 BUY scan을 생략합니다."
        ctx.buy_scan_skipped_reason = "api_budget_limited"
        ctx.buy_status_text = "skipped(api_budget_limited)"
        _print_cycle_conclusion(
            side="HOLD",
            display_name="-",
            reason=hold_reason,
            planned_qty=0,
        )
        _record_cycle_action(
            state,
            action="HOLD_API_BUDGET_BUY_SCAN",
            reason=hold_reason,
            selected_symbol=None,
        )
        _log_engine_event(
            action="skipped_buy_scan_budget_limited",
            reason=hold_reason,
            cycle_id=cycle_id,
            market_open=ctx.market_open,
            market_session=ctx.session_status.session,
        )
        return True

    buy_scan_started_perf = time.perf_counter()
    ctx.request_window_size_before_buy_scan = _api_budget_request_window_size(
        api_budget_state,
        now=scan_request_at,
    )
    print(
        f"[info] request window size before buy_scan={ctx.request_window_size_before_buy_scan}"
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
    if ctx.buy_scan_separate_quote_lane:
        print(
            "[info] BUY scan execution-lane request guard bypassed"
            " | reason=separate live quote lane"
        )
    else:
        pre_buy_wait_seconds = _api_budget_min_wait_for_request_slot(
            api_budget_state,
            now=scan_request_at,
            request_cost=1,
        )
        if pre_buy_wait_seconds > 0:
            ctx.buy_scan_guard_wait_ms += pre_buy_wait_seconds * 1000.0
            ctx.throttle_guard_triggered = True
            ctx.throttle_guard_reason = "buy_scan_request_window"
            print(
                f"[info] throttle guard triggered before buy_scan | wait={int(round(pre_buy_wait_seconds * 1000))}ms"
            )
            time.sleep(pre_buy_wait_seconds)
            scan_request_at = get_korean_now()
        if not _api_budget_can_quote(api_budget_state, quote_cost=1):
            ctx.buy_scan_skipped_reason = "api_budget_limited"
            ctx.buy_status_text = "skipped(api_budget_limited)"
            print("[info] BUY scan을 건너뜁니다: quote 예산 부족")
            print("[info] buy cadence: due | buy scan skipped: api budget limited")
            _print_cycle_conclusion(
                side="HOLD",
                display_name="-",
                reason="API budget 부족으로 BUY scan을 다음 tick으로 미룹니다.",
                planned_qty=0,
            )
            _record_cycle_action(
                state,
                action="HOLD_API_BUDGET_BUY_SCAN",
                reason="API budget 부족으로 BUY scan을 다음 tick으로 미뤘습니다.",
                selected_symbol=None,
            )
            _log_engine_event(
                action="skipped_buy_scan_budget_limited",
                reason="API budget 부족으로 BUY scan을 다음 tick으로 미뤘습니다.",
                cycle_id=cycle_id,
                market_open=ctx.market_open,
                market_session=ctx.session_status.session,
            )
            return True
        if not _api_budget_can_request(api_budget_state, now=scan_request_at):
            ctx.buy_scan_skipped_reason = "api_request_budget_limited"
            ctx.buy_status_text = "skipped(api_request_budget_limited)"
            print("[info] BUY scan을 건너뜁니다: request 예산 부족")
            print("[info] buy cadence: due | buy scan skipped: api budget limited")
            _print_cycle_conclusion(
                side="HOLD",
                display_name="-",
                reason="API request 예산 부족으로 BUY scan을 다음 tick으로 미룹니다.",
                planned_qty=0,
            )
            _record_cycle_action(
                state,
                action="HOLD_API_BUDGET_BUY_SCAN",
                reason="API request 예산 부족으로 BUY scan을 다음 tick으로 미뤘습니다.",
                selected_symbol=None,
            )
            _log_engine_event(
                action="skipped_buy_scan_budget_limited",
                reason="API request 예산 부족으로 BUY scan을 다음 tick으로 미뤘습니다.",
                cycle_id=cycle_id,
                market_open=ctx.market_open,
                market_session=ctx.session_status.session,
            )
            return True

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
                deep_eval_symbols = restrict_symbols_to_prefetched(
                    deep_eval_symbols,
                    ctx.buy_quote_prefetch_price_data_by_symbol,
                )
                ctx.buy_scan_requested_count = len(deep_eval_symbols)
                staged_scan_details = ctx.selection_details.get("staged_scan")
                if isinstance(staged_scan_details, dict):
                    staged_scan_details["deep_eval_symbols"] = list(deep_eval_symbols)
            if ctx.buy_quote_prefetch_deadline_hit and not deep_eval_symbols:
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
                ctx.backoff_applied_seconds = int(
                    _api_budget_backoff_remaining_seconds(
                        api_budget_state, now=get_korean_now()
                    )
                )
                note(
                    "DEGRADED",
                    "rate limit detected at buy_scan prefetch"
                    f" | symbol={metrics['rate_limit_symbol'] or '-'}"
                    f" | backoff applied: {ctx.backoff_applied_seconds}s",
                )
        elif prefetch_join.timed_out:
            ctx.buy_quote_prefetch_deadline_hit = True
            ctx.buy_quote_prefetch_worker_detached = prefetch_join.worker_detached
            ctx.buy_quote_prefetch_cleanup_nonblocking = prefetch_join.cleanup_nonblocking
            ctx.buy_quote_prefetch_future_done = prefetch_join.future_done
            ctx.buy_scan_guard_released = prefetch_join.guard_released
            ctx.buy_scan_guard_release_reason = prefetch_join.guard_release_reason
            ctx.buy_scan_skipped_reason = "quote_prefetch_timeout"
            ctx.buy_status_text = "skipped(quote_prefetch_timeout)"
            deep_eval_symbols = ()
            ctx.buy_scan_requested_count = 0
            note(
                "DEGRADED",
                "BUY quote prefetch did not finish before the bounded join timeout.",
            )
        elif prefetch_join.status == "failed":
            ctx.buy_quote_prefetch_cleanup_nonblocking = prefetch_join.cleanup_nonblocking
            ctx.buy_quote_prefetch_future_done = prefetch_join.future_done
            ctx.buy_scan_guard_released = prefetch_join.guard_released
            ctx.buy_scan_guard_release_reason = prefetch_join.guard_release_reason
            ctx.buy_scan_skipped_reason = "quote_prefetch_failed"
            ctx.buy_status_text = "skipped(quote_prefetch_failed)"
            deep_eval_symbols = ()
            ctx.buy_scan_requested_count = 0
            note(
                "DEGRADED",
                f"BUY quote prefetch failed; BUY scan skipped safely: {prefetch_join.error}",
            )
        ctx.buy_quote_prefetch_future = None

    if ctx.buy_scan_skipped_reason in {
        "quote_prefetch_timeout",
        "quote_prefetch_failed",
        "quote_prefetch_deadline",
    } and not deep_eval_symbols:
        _print_cycle_conclusion(
            side="HOLD",
            display_name="-",
            reason="BUY quote prefetch 결과가 부족해 이번 BUY scan을 안전하게 생략합니다.",
            planned_qty=0,
        )
        _record_cycle_action(
            state,
            action="HOLD_BUY_QUOTE_PREFETCH",
            reason="BUY quote prefetch 결과 부족으로 BUY scan을 생략했습니다.",
            selected_symbol=None,
        )
        _log_engine_event(
            action="skipped_buy_scan_quote_prefetch",
            reason=ctx.buy_scan_skipped_reason,
            cycle_id=cycle_id,
            market_open=ctx.market_open,
            market_session=ctx.session_status.session,
        )
        return True

    # F4 (E4): drop symbols quarantined earlier today (3+ consecutive malformed
    # quotes). Such a symbol can't be scored or bought anyway, so this only stops
    # wasted quote calls and per-cycle warn spam — no order-behaviour change.
    try:
        _live_symbols, _dead_excluded = filter_dead_symbols(list(deep_eval_symbols), state)
        if _dead_excluded:
            deep_eval_symbols = tuple(_live_symbols)
    except Exception:
        pass

    buy_scan_metrics_before = get_request_metrics_summary()
    ctx.raw_scan_results = scan_target_symbols(
        settings=settings,
        token=ctx.token,
        portfolio_snapshot=ctx.portfolio_snapshot,
        symbols=deep_eval_symbols,
        layer_by_symbol=dict(ctx.buy_scan_layered_universe.get("layer_by_symbol") or {}),
        price_data_by_symbol=ctx.buy_quote_prefetch_price_data_by_symbol,
        allow_inline_quote_fetch=not ctx.buy_scan_separate_quote_lane,
    )
    scan_diagnostics = get_last_scan_diagnostics()
    # F4: track consecutive missing-field skips; quarantine + warn once on the
    # cycle a symbol crosses the threshold (defensive — never breaks the scan).
    try:
        _quarantine = update_dead_symbol_tracking(
            state,
            parse_skipped_symbols=scan_diagnostics.get("parse_error_skipped_symbols") or [],
            scanned_symbols=list(deep_eval_symbols),
            today=get_korean_now().date().isoformat(),
        )
        for _dead_symbol in _quarantine["newly_quarantined"]:
            print(
                f"[warn] symbol {_dead_symbol} auto-quarantined for today after "
                f"{DEAD_SYMBOL_MISS_THRESHOLD} consecutive parse-skip cycles; "
                "excluded from further scans today."
            )
    except Exception:
        pass
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
        ctx.buy_scan_skipped_reason = "rate_limit_detected"
        if ctx.buy_scan_evaluated_count == 0:
            ctx.buy_status_text = "skipped(rate_limit)"
        note(
            "DEGRADED",
            f"rate limit detected at buy_scan | backoff applied: {ctx.backoff_applied_seconds}s",
        )
        _log_engine_event(
            action="rate_limit_detected_buy_scan",
            reason=str(scan_diagnostics.get("rate_limit_message") or "BUY scan quote loop에서 EGW00201을 감지했습니다."),
            cycle_id=cycle_id,
            market_open=ctx.market_open,
            market_session=ctx.session_status.session,
        )
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
    ctx.buy_scan_evaluated_count = len(ctx.raw_scan_results)
    ctx.buy_status_text = f"evaluated({ctx.buy_scan_evaluated_count}/{ctx.buy_scan_requested_count})"
    if ctx.rate_limit_triggered and ctx.rate_limit_source == "buy_scan" and ctx.buy_scan_evaluated_count == 0:
        ctx.buy_status_text = "skipped(rate_limit)"
    if ctx.buy_scan_requested_count > 0 and ctx.buy_scan_evaluated_count == 0 and ctx.buy_scan_skipped_reason is None:
        interrupted_reason = str(scan_diagnostics.get("interrupted_reason") or "").strip()
        if interrupted_reason:
            ctx.buy_scan_skipped_reason = interrupted_reason
    ctx.buy_scan_top_k_count = min(
        settings.buy_scan_top_k_candidates,
        ctx.buy_scan_evaluated_count,
    )
    for result in ctx.raw_scan_results:
        ctx.observed_market_snapshots[result.symbol] = result.market_snapshot
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
    ctx.top_analysis_result = select_top_analysis_result(ctx.scan_results)
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
        float(ctx.buy_scan_guard_wait_ms) + float(ctx.buy_scan_throttle_total_sleep_ms),
        1,
    )
    ctx.buy_scan_throttle_immediate_pass_count = int(
        scan_diagnostics.get("throttle_immediate_pass_count", 0) or 0
    )
    ctx.buy_scan_sample_symbols = list(scan_diagnostics.get("sample_symbols", []) or [])
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

    selection_logging_started_perf = time.perf_counter()
    existing_pre_gating = _normalize_pre_gating_payload(
        ctx.selection_details.get("pre_gating")
    )
    existing_staged_scan = dict(ctx.selection_details.get("staged_scan") or {})
    ctx.selection_details = serialize_selection_details(
        selected_result=ctx.selected_candidate,
        results=ctx.scan_results,
    )
    if existing_pre_gating:
        ctx.selection_details["pre_gating"] = existing_pre_gating
    if existing_staged_scan:
        ctx.selection_details["staged_scan"] = existing_staged_scan
    ctx.selection_details["requested_universe_count"] = ctx.buy_scan_requested_count
    ctx.selection_details["evaluated_count"] = len(ctx.scan_results)
    ctx.selection_details["top_candidate_limit"] = settings.buy_scan_top_k_candidates
    ctx.selection_details["budget_limited"] = len(ctx.scan_results) < ctx.buy_scan_requested_count
    ctx.buy_scan_logging_ms += (time.perf_counter() - selection_logging_started_perf) * 1000
    ctx.timing_summary["buy_scan"] = _phase_timing_summary(
        elapsed_ms=(time.perf_counter() - buy_scan_started_perf) * 1000,
        request_delta=buy_scan_delta,
    )

    if settings.run_mode == "scan_only":
        if ctx.selected_candidate is not None:
            _print_cycle_conclusion(
                side="BUY",
                display_name=ctx.selected_candidate.display_name,
                reason=ctx.selection_details["selection_reason"],
            )
        _print_scan_only_notice()
        _record_cycle_action(
            state,
            action="SCAN_ONLY",
            reason="scan_only 모드라 주문 검토를 생략했습니다.",
            selected_symbol=ctx.selected_candidate.symbol if ctx.selected_candidate else None,
        )
        return True

    if ctx.selected_candidate is None:
        if ctx.top_analysis_result is None:
            if _should_downgrade_empty_buy_scan_to_backoff(
                rate_limit_triggered=ctx.rate_limit_triggered,
                rate_limit_source=ctx.rate_limit_source,
                buy_scan_evaluated_count=ctx.buy_scan_evaluated_count,
                scan_results_count=len(ctx.scan_results),
                buy_scan_skipped_reason=ctx.buy_scan_skipped_reason,
                rate_limit_partial_stop=ctx.rate_limit_partial_stop,
            ):
                hold_reason = _build_buy_scan_rate_limit_degraded_reason(
                    backoff_applied_seconds=ctx.backoff_applied_seconds,
                    rate_limit_partial_stop_symbol=ctx.rate_limit_partial_stop_symbol,
                    rate_limit_partial_completed_count=ctx.rate_limit_partial_completed_count,
                    rate_limit_partial_remaining_count=ctx.rate_limit_partial_remaining_count,
                )
                ctx.buy_scan_skipped_reason = ctx.buy_scan_skipped_reason or "rate_limit_detected"
                ctx.buy_status_text = "skipped(rate_limit)"
                ctx.selection_details["selection_reason"] = hold_reason
                ctx.selection_details["buy_scan_degraded_reason"] = "rate_limit"
                ctx.selection_details["rate_limit_partial_stop"] = ctx.rate_limit_partial_stop
                ctx.selection_details["rate_limit_partial_stop_symbol"] = (
                    ctx.rate_limit_partial_stop_symbol
                )
                ctx.selection_details["rate_limit_partial_completed_count"] = (
                    ctx.rate_limit_partial_completed_count
                )
                ctx.selection_details["rate_limit_partial_remaining_count"] = (
                    ctx.rate_limit_partial_remaining_count
                )
                print(f"[info] {hold_reason}")
                _print_cycle_conclusion(
                    side="HOLD",
                    display_name="-",
                    reason=hold_reason,
                    planned_qty=0,
                )
                _record_cycle_action(
                    state,
                    action="HOLD_API_BACKOFF",
                    reason=hold_reason,
                    selected_symbol=None,
                )
                _log_engine_event(
                    action="skipped_buy_scan_rate_limit",
                    reason=hold_reason,
                    cycle_id=cycle_id,
                    market_open=ctx.market_open,
                    market_session=ctx.session_status.session,
                )
                return True
            if _is_benign_empty_buy_scan(
                buy_scan_evaluated_count=ctx.buy_scan_evaluated_count,
                scan_results_count=len(ctx.scan_results),
                buy_scan_skipped_reason=ctx.buy_scan_skipped_reason,
                buy_scan_partial_budget=ctx.buy_scan_partial_budget,
            ):
                benign_reason = (
                    str(ctx.buy_scan_skipped_reason or "").strip()
                    or ("partial_budget" if ctx.buy_scan_partial_budget else "empty_buy_scan")
                )
                hold_reason = (
                    "BUY scan이 후보를 만들지 않았지만 안전한 skip 상태로 "
                    f"분류되어 이번 cycle은 HOLD 처리합니다. (reason={benign_reason})"
                )
                ctx.buy_scan_skipped_reason = benign_reason
                ctx.buy_status_text = f"skipped({benign_reason})"
                ctx.selection_details["selection_reason"] = hold_reason
                ctx.selection_details["buy_scan_degraded_reason"] = benign_reason
                print(f"[info] {hold_reason}")
                _print_cycle_conclusion(
                    side="HOLD",
                    display_name="-",
                    reason=hold_reason,
                    planned_qty=0,
                )
                _record_cycle_action(
                    state,
                    action="HOLD_BUY_SCAN_SKIPPED",
                    reason=hold_reason,
                    selected_symbol=None,
                )
                _log_engine_event(
                    action="skipped_buy_scan_empty",
                    reason=hold_reason,
                    cycle_id=cycle_id,
                    market_open=ctx.market_open,
                    market_session=ctx.session_status.session,
                )
                return True
            raise RuntimeError("분석 대상 종목이 없어 스캔 결과를 만들지 못했습니다.")

        buy_analysis_block = _build_buy_analysis_block_context(ctx.top_analysis_result)

        _print_buy_strategy(
            ctx.top_analysis_result.strategy_result,
            display_label=ctx.top_analysis_result.display_name,
            candidate=ctx.top_analysis_result.candidate,
        )
        _print_buy_score_summary(ctx.top_analysis_result)
        _print_cycle_conclusion(
            side="HOLD",
            display_name=ctx.top_analysis_result.display_name,
            reason=buy_analysis_block["reason"],
            planned_qty=0,
        )

        log_order_event(
            symbol=ctx.top_analysis_result.symbol,
            qty=settings.qty,
            order_type=order_type,
            confirm_buy=settings.confirm_buy,
            market_open=ctx.market_open,
            cycle_id=cycle_id,
            action=buy_analysis_block["action"],
            result="skipped",
            reason=buy_analysis_block["reason"],
            raw_response={
                "strategy_details": ctx.top_analysis_result.strategy_result.to_log_payload(),
                "selection_details": ctx.selection_details,
                "score_summary": ctx.top_analysis_result.score_summary,
                "score_highlights": list(ctx.top_analysis_result.score_highlights),
                "score_penalties": list(ctx.top_analysis_result.score_penalties),
                "expected_fee_krw": ctx.top_analysis_result.expected_fee_krw,
                "expected_tax_krw": ctx.top_analysis_result.expected_tax_krw,
                "expected_slippage_krw": ctx.top_analysis_result.expected_slippage_krw,
                "expected_total_cost_krw": ctx.top_analysis_result.expected_total_cost_krw,
                "expected_cost_bps": ctx.top_analysis_result.expected_cost_bps,
                "cost_quality_score": ctx.top_analysis_result.cost_quality_score,
                "expected_cost_penalty": ctx.top_analysis_result.expected_cost_penalty,
                "net_edge_bps": ctx.top_analysis_result.net_edge_bps,
                "cost_block_reason": ctx.top_analysis_result.cost_block_reason,
            },
        )
        mark_buy_blocked(state, ctx.top_analysis_result.symbol)
        _record_cycle_action(
            state,
            action=buy_analysis_block["cycle_action"],
            reason=buy_analysis_block["reason"],
            order_side=None,
            symbol=ctx.top_analysis_result.symbol,
            qty=0,
            selected_symbol=ctx.top_analysis_result.symbol,
        )
        return True

    return False
