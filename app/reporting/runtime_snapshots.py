"""Runtime snapshot reporting helpers.

Extracted from app.main Stage 2f.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.core.formatters import format_qty
from app.core.order_log import log_order_event
from app.core.time_utils import get_korean_now
from app.runtime_state import runtime_state_summary, set_last_decision
from app.scanner.runtime_scan import (
    normalize_buy_funnel_reason as _normalize_buy_funnel_reason,
    normalize_pre_gating_payload as _normalize_pre_gating_payload,
    resolve_buy_candidate_rejection_reason as _resolve_buy_candidate_rejection_reason,
    resolve_buy_candidate_selection_outcome as _resolve_buy_candidate_selection_outcome,
)
from app.scanner.symbol_names import get_symbol_name
from app.strategy.core_shadow import build_core_shadow_fields


def build_buy_candidate_outcome_records(
    *,
    cycle_id: str,
    timestamp: str,
    market_session: str | None,
    run_mode: str,
    regime_state: dict[str, object] | None,
    daily_pnl_brake_state: dict[str, object] | None,
    portfolio_snapshot,
    requested_symbols: tuple[str, ...],
    layered_universe: dict[str, object],
    pre_gating: dict[str, object],
    shallow_plan: dict[str, object],
    raw_scan_results,
    scan_results,
    selected_candidate,
    runtime_state: dict[str, object],
) -> list[dict[str, Any]]:
    if not requested_symbols:
        return []

    layer_selected_symbols = set(tuple(layered_universe.get("selected_symbols") or ()))
    layer_by_symbol = dict(layered_universe.get("layer_by_symbol") or {})
    pre_gating_payload = _normalize_pre_gating_payload(pre_gating)
    reasons_by_symbol = dict(pre_gating_payload.get("reasons_by_symbol") or {})
    rejected_rows_by_symbol = {
        str(item.get("symbol") or "").strip(): item
        for item in list(pre_gating_payload.get("rejected") or [])
        if isinstance(item, dict) and str(item.get("symbol") or "").strip()
    }
    reentry_state_by_symbol = dict(pre_gating_payload.get("reentry_state_by_symbol") or {})
    last_exit_reason_by_symbol = dict(pre_gating_payload.get("last_exit_reason_by_symbol") or {})
    residual_position_present_by_symbol = dict(
        pre_gating_payload.get("residual_position_present_by_symbol") or {}
    )
    runtime_reentry_state_by_symbol = dict(runtime_state.get("last_reentry_state_by_symbol") or {})
    runtime_reentry_reason_by_symbol = dict(runtime_state.get("last_reentry_reason_by_symbol") or {})
    runtime_last_exit_reason_by_symbol = dict(runtime_state.get("last_exit_reason_by_symbol") or {})
    shortlist_symbols = set(tuple(shallow_plan.get("shortlist_symbols") or ()))
    shallow_candidates = tuple(shallow_plan.get("candidates") or ())
    shallow_by_symbol = {
        candidate.symbol: candidate for candidate in shallow_candidates if getattr(candidate, "symbol", None)
    }
    raw_by_symbol = {
        result.symbol: result for result in tuple(raw_scan_results or ()) if getattr(result, "symbol", None)
    }
    guarded_by_symbol = {
        result.symbol: result for result in tuple(scan_results or ()) if getattr(result, "symbol", None)
    }
    holdings_symbols = {
        str(position.symbol).strip()
        for position in getattr(portfolio_snapshot, "held_positions", ()) or ()
        if str(getattr(position, "symbol", "")).strip()
    }
    selected_symbol = (
        str(getattr(selected_candidate, "symbol", "") or "").strip() or None
    )
    final_action = str(runtime_state.get("last_action") or "").strip()
    final_reason = str(runtime_state.get("last_decision_reason") or "").strip()
    executed = final_action == "BUY_ORDER_SUCCEEDED"
    daily_pnl_state = str(
        (daily_pnl_brake_state or {}).get("action")
        or (daily_pnl_brake_state or {}).get("status")
        or runtime_state.get("current_brake_state")
        or "UNKNOWN"
    )
    regime = str(
        (regime_state or {}).get("current_regime")
        or runtime_state.get("current_regime")
        or "UNKNOWN"
    )

    records: list[dict[str, Any]] = []
    for symbol in requested_symbols:
        rejected_row = rejected_rows_by_symbol.get(symbol) or {}
        shallow_candidate = shallow_by_symbol.get(symbol)
        raw_result = raw_by_symbol.get(symbol)
        guarded_result = guarded_by_symbol.get(symbol)
        symbol_name = (
            getattr(shallow_candidate, "name", None)
            or getattr(raw_result, "name", None)
            or getattr(guarded_result, "name", None)
            or get_symbol_name(symbol)
        )
        layer_selected = symbol in layer_selected_symbols
        pre_gate_passed = layer_selected and symbol not in reasons_by_symbol and bool(
            pre_gating_payload.get("scan_allowed", True)
        )
        deep_evaluated = raw_result is not None
        final_candidate = selected_symbol == symbol
        rejection_reason = _resolve_buy_candidate_rejection_reason(
            symbol=symbol,
            pre_gating=pre_gating_payload,
            layer_selected=layer_selected,
            raw_result=raw_result,
            guarded_result=guarded_result,
            selected_symbol=selected_symbol,
            final_action=final_action,
            final_reason=final_reason,
        )

        stage_reached = "universe_layered_out"
        if layer_selected:
            stage_reached = "pre_gate_passed" if pre_gate_passed else "pre_gate_rejected"
        if shallow_candidate is not None:
            stage_reached = "shallow_ranked"
        if symbol in shortlist_symbols:
            stage_reached = "shallow_selected"
        if deep_evaluated:
            stage_reached = "deep_eval"
        if final_candidate:
            stage_reached = "final_candidate"
        if executed and final_candidate:
            stage_reached = "executed"
        residual_position_present = bool(
            residual_position_present_by_symbol.get(
                symbol,
                bool(rejected_row.get("residual_position_present")),
            )
        )
        if (
            rejection_reason == "residual_position_present"
            or str(rejected_row.get("reentry_state") or "").strip() == "blocked_residual_position"
        ):
            residual_position_present = True
        selection_outcome = _resolve_buy_candidate_selection_outcome(
            stage_reached=stage_reached,
            raw_result=raw_result,
            guarded_result=guarded_result,
            final_candidate=final_candidate,
            executed=executed,
        )
        runtime_reentry_state = (
            str(runtime_reentry_state_by_symbol.get(symbol) or "").strip()
            if final_candidate
            else ""
        )
        runtime_reentry_reason = (
            str(runtime_reentry_reason_by_symbol.get(symbol) or "").strip()
            if final_candidate
            else ""
        )
        runtime_last_exit_reason = (
            str(runtime_last_exit_reason_by_symbol.get(symbol) or "").strip()
            if final_candidate
            else ""
        )

        # Shadow evaluation — diagnostics only, no effect on live decisions.
        core_shadow = build_core_shadow_fields(
            raw_result=raw_result,
            selection_bucket=str(layer_by_symbol.get(symbol) or "unassigned"),
        )

        records.append(
            {
                "ts": timestamp,
                "cycle_id": cycle_id,
                "account_signature": str(runtime_state.get("account_signature") or "").strip() or None,
                "masked_account_display": str(runtime_state.get("masked_account_display") or "").strip() or None,
                "symbol": symbol,
                "symbol_name": symbol_name,
                "session": market_session,
                "run_mode": run_mode,
                "regime": regime,
                "stage_reached": stage_reached,
                "rejection_reason": rejection_reason,
                "selection_outcome": selection_outcome,
                "pre_gate_passed": bool(pre_gate_passed),
                "shallow_selected": bool(symbol in shortlist_symbols),
                "deep_evaluated": bool(deep_evaluated),
                "final_candidate": bool(final_candidate),
                "buy_signal": bool(final_candidate),
                "executed": bool(executed and final_candidate),
                "buy_signal_executed": bool(executed and final_candidate),
                "score_shallow": (
                    round(float(getattr(shallow_candidate, "shallow_score", 0.0) or 0.0), 4)
                    if shallow_candidate is not None
                    else None
                ),
                "score_deep": (
                    round(float(getattr(raw_result, "score", 0.0) or 0.0), 4)
                    if raw_result is not None
                    else None
                ),
                "passed_count_deep": (
                    int(getattr(raw_result, "passed_count", 0) or 0)
                    if raw_result is not None
                    else None
                ),
                "passes_profit_buffer": (
                    bool(getattr(raw_result, "passes_profit_buffer", True))
                    if raw_result is not None
                    else None
                ),
                "strategy_pass_pattern": (
                    str(getattr(raw_result, "passed_pattern", "") or "").strip() or None
                    if raw_result is not None
                    else None
                ),
                "selection_profile": str(
                    (shallow_plan.get("profile") or runtime_state.get("buy_scan_last_profile") or "").strip()
                    or None
                ),
                "selection_bucket": str(layer_by_symbol.get(symbol) or "unassigned"),
                "reentry_state": (
                    reentry_state_by_symbol.get(symbol)
                    or str(rejected_row.get("reentry_state") or "").strip()
                    or runtime_reentry_state
                    or None
                ),
                "reentry_reason": (
                    str(rejected_row.get("reason") or "").strip()
                    or runtime_reentry_reason
                    or None
                ),
                "last_exit_reason": (
                    last_exit_reason_by_symbol.get(symbol)
                    or str(rejected_row.get("exit_reason") or "").strip()
                    or runtime_last_exit_reason
                    or None
                ),
                "residual_position_present": residual_position_present,
                "daily_pnl_brake_state": daily_pnl_state,
                "already_holding": symbol in holdings_symbols,
                "cooldown_blocked": rejection_reason == "cooldown",
                "same_symbol_limit_blocked": rejection_reason == "same_symbol_daily_limit",
                "outcome_return_1h": None,
                "outcome_return_eod": None,
                "outcome_filled": False,
                # Shadow evaluation fields — diagnostics only.
                # core_shadow_evaluated is False for non-core / non-deep-eval rows.
                "core_shadow_evaluated": core_shadow["core_shadow_evaluated"],
                "core_shadow_trend_gate_passed": core_shadow["core_shadow_trend_gate_passed"],
                "core_shadow_passed_count": core_shadow["core_shadow_passed_count"],
                "core_shadow_pattern": core_shadow["core_shadow_pattern"],
                "core_shadow_passed": core_shadow["core_shadow_passed"],
            }
        )
    return records


def build_buy_cycle_funnel_stats(
    *,
    cycle_id: str,
    timestamp: str,
    market_session: str | None,
    run_mode: str,
    regime_state: dict[str, object] | None,
    requested_symbols: tuple[str, ...],
    layered_universe: dict[str, object],
    pre_gating: dict[str, object],
    shallow_plan: dict[str, object],
    raw_scan_results,
    selected_candidate,
    runtime_state: dict[str, object],
    sell_analysis_results,
    sell_evaluated_count: int,
    api_usage_summary: dict[str, object] | None,
    cycle_elapsed_ms: float | None,
    buy_risk_guard_payload: dict[str, object] | None = None,
) -> dict[str, Any]:
    pre_gating_payload = _normalize_pre_gating_payload(pre_gating)
    reason_counts_raw = dict(pre_gating_payload.get("reason_counts") or {})
    normalized_reason_counts: Counter[str] = Counter()
    for reason, count in reason_counts_raw.items():
        normalized_reason_counts[_normalize_buy_funnel_reason(reason) or "other"] += int(count or 0)

    executed_order_count = 1 if str(runtime_state.get("last_action") or "") == "BUY_ORDER_SUCCEEDED" else 0
    sell_triggered_count = sum(
        1
        for result in tuple(sell_analysis_results or ())
        if bool(getattr(getattr(result, "sell_decision", None), "should_attempt_sell", False))
    )
    regime = str(
        (regime_state or {}).get("current_regime")
        or runtime_state.get("current_regime")
        or "UNKNOWN"
    )
    layered_symbols = tuple(layered_universe.get("selected_symbols") or ())
    shortlist_symbols = tuple(shallow_plan.get("shortlist_symbols") or ())
    # Task 5: core/non-core candidate separation statistics
    layer_by_symbol = dict(layered_universe.get("layer_by_symbol") or {})
    _core_layered = sum(1 for s in layered_symbols if layer_by_symbol.get(s) == "core")
    _noncore_layered = len(layered_symbols) - _core_layered
    _core_shortlisted = sum(1 for s in shortlist_symbols if layer_by_symbol.get(s) == "core")
    _noncore_shortlisted = len(shortlist_symbols) - _core_shortlisted
    _raw_scan_list = tuple(raw_scan_results or ())
    _core_deep_eval = sum(
        1 for r in _raw_scan_list if layer_by_symbol.get(getattr(r, "symbol", "")) == "core"
    )
    _noncore_deep_eval = len(_raw_scan_list) - _core_deep_eval
    _selected_layer = layer_by_symbol.get(
        str(getattr(selected_candidate, "symbol", "") or "").strip(), ""
    ) if selected_candidate is not None else ""
    # Task 4: buy non-execution drop reason histogram (final_candidate → executed gap)
    _last_action = str(runtime_state.get("last_action") or "").strip()
    _last_reason = str(runtime_state.get("last_decision_reason") or "").strip()
    # record_cycle_action collapses every risk-guard block to a single
    # "BUY_BLOCKED_RISK_GUARD" action, so last_action can't distinguish an
    # order-log-integrity fail-closed block (which halts ALL buys until the log
    # is readable again) from an ordinary per-symbol guard. Inspect the guard
    # payload so this failure mode surfaces as its own funnel bucket instead of
    # being masked inside order_guard_blocked (docs/order_log_line_limit_design_
    # 20260707.md §O1 — today's incident was invisible for exactly this reason).
    _order_log_untrusted = False
    if isinstance(buy_risk_guard_payload, dict):
        _guard_results = buy_risk_guard_payload.get("guard_results")
        if isinstance(_guard_results, dict):
            _integrity = _guard_results.get("order_log_integrity")
            if isinstance(_integrity, dict) and _integrity.get("passed") is False:
                _order_log_untrusted = True
    _buy_non_execution_reason: str | None = None
    if selected_candidate is not None and not executed_order_count:
        # Classify why the final candidate didn't result in a buy
        _action_lower = _last_action.lower()
        if _order_log_untrusted:
            _buy_non_execution_reason = "order_log_untrusted"
        elif "guard" in _action_lower or "cooldown" in _action_lower or "rebuy" in _action_lower:
            _buy_non_execution_reason = "order_guard_blocked"
        elif "cash_insufficient" in _last_action or "CASH_INSUFFICIENT" in _last_action:
            _buy_non_execution_reason = "cash_insufficient"
        elif "trade_budget" in _last_action or "TRADE_BUDGET" in _last_action:
            _buy_non_execution_reason = "trade_budget_limited"
        elif "exposure" in _last_action.lower():
            _buy_non_execution_reason = "exposure_limited"
        elif "daily_pnl" in _last_action.lower() or "pnl_pause" in _last_action.lower():
            _buy_non_execution_reason = "daily_pnl_brake"
        elif "already_holding" in _last_action.lower() or "residual" in _last_action.lower():
            _buy_non_execution_reason = "already_holding"
        elif "order_failed" in _last_action.lower():
            _buy_non_execution_reason = "order_failed"
        elif "scan_only" in _last_action.lower() or run_mode == "scan_only":
            _buy_non_execution_reason = "scan_only_mode"
        elif not _last_action or _last_action in {"HOLD_NO_SIGNAL", "HOLD_BUY_SCAN_WAIT"}:
            _buy_non_execution_reason = "no_signal"
        else:
            _buy_non_execution_reason = _normalize_buy_funnel_reason(_last_action) or "other"
    funnel_summary = {
        "universe_size": len(requested_symbols),
        "layered_universe_size": len(layered_symbols),
        "layered_out_count": max(len(requested_symbols) - len(layered_symbols), 0),
        "pre_gate_passed": int(pre_gating_payload.get("allowed_count", 0) or 0),
        "pre_gate_rejected_total": int(pre_gating_payload.get("early_reject_count", 0) or 0),
        "pre_gate_rejection_counts": dict(normalized_reason_counts),
        "shallow_ranked_count": int(shallow_plan.get("ranked_count", 0) or 0),
        "shallow_shortlist_size": len(shortlist_symbols),
        "deep_eval_count": len(_raw_scan_list),
        "final_candidate_count": 1 if selected_candidate is not None else 0,
        "executed_order_count": executed_order_count,
        "reentry_allowed_count": int(pre_gating_payload.get("reentry_allowed_count", 0) or 0),
        "reentry_blocked_count": int(pre_gating_payload.get("reentry_blocked_count", 0) or 0),
        "reentry_block_reason_counts": dict(
            pre_gating_payload.get("reentry_block_reason_counts") or {}
        ),
        "sell_evaluated_count": int(sell_evaluated_count or 0),
        "sell_triggered_count": sell_triggered_count,
        "api_request_count": int((api_usage_summary or {}).get("total_requests", 0) or 0),
        "api_quote_request_count": int(
            ((api_usage_summary or {}).get("categories") or {}).get("quote", {}).get("count", 0)
            or 0
        ),
        "cycle_elapsed_ms": (
            round(float(cycle_elapsed_ms or 0.0), 1) if cycle_elapsed_ms is not None else None
        ),
        # Task 5: core/non-core breakdown at each funnel stage
        "core_layered_count": _core_layered,
        "noncore_layered_count": _noncore_layered,
        "core_shortlisted_count": _core_shortlisted,
        "noncore_shortlisted_count": _noncore_shortlisted,
        "core_deep_eval_count": _core_deep_eval,
        "noncore_deep_eval_count": _noncore_deep_eval,
        "selected_candidate_layer": _selected_layer or None,
        # Task 4: buy non-execution drop reason
        "buy_non_execution_reason": _buy_non_execution_reason,
    }
    return {
        "ts": timestamp,
        "cycle_id": cycle_id,
        "account_signature": str(runtime_state.get("account_signature") or "").strip() or None,
        "masked_account_display": str(runtime_state.get("masked_account_display") or "").strip() or None,
        "session": market_session,
        "run_mode": run_mode,
        "regime": regime,
        **funnel_summary,
        "pre_gate_rejected_cooldown": int(normalized_reason_counts.get("cooldown", 0)),
        "pre_gate_rejected_already_holding": int(
            normalized_reason_counts.get("already_holding", 0)
        ),
        "pre_gate_rejected_same_symbol_daily_limit": int(
            normalized_reason_counts.get("same_symbol_daily_limit", 0)
        ),
        "pre_gate_rejected_daily_pnl_brake": int(
            normalized_reason_counts.get("daily_pnl_brake", 0)
        ),
        "pre_gate_rejected_other": int(
            normalized_reason_counts.get("other", 0)
            + normalized_reason_counts.get("cash_insufficient", 0)
            + normalized_reason_counts.get("trade_budget_limited", 0)
            + normalized_reason_counts.get("exposure_limited", 0)
        ),
    }


def serialize_runtime_market_snapshot(snapshot) -> dict[str, object] | None:
    if snapshot is None:
        return None
    return {
        "symbol": str(getattr(snapshot, "symbol", "") or "").strip() or None,
        "current_price": int(getattr(snapshot, "current_price", 0) or 0),
        "open_price": int(getattr(snapshot, "open_price", 0) or 0),
        "low_price": int(getattr(snapshot, "low_price", 0) or 0),
        "prev_day_change_pct": float(getattr(snapshot, "prev_day_change_pct", 0.0) or 0.0),
        "observed_at": get_korean_now().isoformat(),
    }


def update_recent_market_snapshots(
    state: dict,
    observed_market_snapshots: dict[str, object],
) -> None:
    if not observed_market_snapshots:
        return
    stored = dict(state.get("recent_market_snapshots_by_symbol") or {})
    for symbol, snapshot in observed_market_snapshots.items():
        normalized_symbol = str(symbol or "").strip()
        if not normalized_symbol:
            continue
        payload = serialize_runtime_market_snapshot(snapshot)
        if payload is None:
            continue
        stored[normalized_symbol] = payload
    state["recent_market_snapshots_by_symbol"] = stored


def log_engine_event(
    *,
    action: str,
    reason: str,
    cycle_id: str,
    market_open: bool,
    market_session: str | None,
    error_type: str | None = None,
) -> None:
    raw_response: dict[str, Any] = {
        "engine_event": True,
        "market_session": market_session,
    }
    # F2-3 (E6): stamp the exception type on cycle_error records so a
    # message-less exception still records an actionable cause.
    if error_type is not None:
        raw_response["error_type"] = error_type
    log_order_event(
        symbol="",
        qty=0,
        order_type="engine_event",
        confirm_buy="-",
        market_open=market_open,
        cycle_id=cycle_id,
        action=action,
        result="skipped",
        reason=reason,
        raw_response=raw_response,
    )


def print_cycle_conclusion(
    *,
    side: str,
    display_name: str,
    reason: str,
    planned_qty: int | None = None,
) -> None:
    print("=== 이번 사이클 결론 ===")
    print(f"우선 실행 대상: {side}")
    print(f"선택 종목: {display_name}")
    print(f"사유: {reason}")
    if planned_qty is not None:
        print(f"예정 수량: {format_qty(planned_qty)}")
    print()


def print_last_action(action: str) -> None:
    print(f"last_action={action}")
    print()


def record_cycle_action(
    state: dict,
    *,
    action: str,
    reason: str,
    order_side: str | None = None,
    symbol: str | None = None,
    qty: int = 0,
    selected_symbol: str | None = None,
) -> None:
    set_last_decision(
        state,
        action=action,
        reason=reason,
        order_side=order_side,
        symbol=symbol,
        qty=qty,
        selected_symbol=selected_symbol,
    )
    print_last_action(action)


def print_runtime_state_summary(state: dict, *, state_write_ok: bool) -> None:
    summary = runtime_state_summary(state)
    print("=== 운영 상태 요약 ===")
    print(
        "계정 스코프: "
        f"{state.get('account_signature') or '-'} "
        f"({state.get('masked_account_display') or '-'})"
    )
    if state.get("account_scope_changed"):
        print(
            "계정 전환 감지: "
            f"{state.get('previous_account_signature') or '-'} -> {state.get('account_signature') or '-'}"
        )
    print(f"오늘 매수 완료 종목 수: {summary['buy_completed_count']}")
    print(f"오늘 매도 완료 종목 수: {summary['sell_completed_count']}")
    print(f"오늘 BUY 차단 종목 수: {summary['buy_blocked_count']}")
    print(f"오늘 SELL 차단 종목 수: {summary['sell_blocked_count']}")
    print(f"오늘 BUY cooldown 차단 종목 수: {summary['buy_cooldown_blocked_count']}")
    print(f"오늘 BUY 매매불가 차단 종목 수: {summary['buy_untradable_count']}")
    print(f"오늘 SELL cooldown 차단 종목 수: {summary['sell_cooldown_blocked_count']}")
    print(f"오늘 리밸런싱 매도 실행 수: {summary['rebalance_sell_submissions_count']}")
    print(f"최근 cycle 경고 수: {int(state.get('last_warning_count', 0) or 0)}")
    print(f"최근 cycle 오류 수: {int(state.get('last_error_count', 0) or 0)}")
    snapshot_status = (
        "N/A"
        if state.get("last_snapshot_write_ok") is None
        else ("OK" if state.get("last_snapshot_write_ok") else "WARN")
    )
    performance_status = (
        "N/A"
        if state.get("last_performance_write_ok") is None
        else ("OK" if state.get("last_performance_write_ok") else "WARN")
    )
    print(
        "snapshot/performance 저장 상태: "
        f"{snapshot_status} / {performance_status}"
    )
    print(f"상태 파일 기록: {'성공' if state_write_ok else '실패'}")
