"""Buy-scan funnel reason normalization and summary printing helpers.

Extracted from app.main Stage 2e-1 through Stage 2e-3.
"""

from __future__ import annotations

from app.strategy.reentry import normalize_exit_reason
from app.strategy.sell_decision import SellAnalysisResult


def resolve_sell_exit_reason(
    *,
    analysis: SellAnalysisResult,
    cycle_reason: str,
    is_rebalance: bool,
) -> str:
    if is_rebalance:
        return "rebalance_sell"
    normalized_cycle_reason = normalize_exit_reason(cycle_reason)
    if normalized_cycle_reason != "unknown":
        return normalized_cycle_reason
    normalized_trigger = normalize_exit_reason(analysis.sell_decision.triggered_rule_name)
    if normalized_trigger != "unknown":
        return normalized_trigger
    return "unknown"


def print_buy_pre_gating_summary(
    *,
    pre_gating: dict[str, object],
    cycle_id: str,
    market_open: bool,
    market_session: str | None,
    log_engine_event=None,
) -> None:
    requested_symbols = tuple(pre_gating.get("requested_symbols") or ())
    allowed_symbols = tuple(pre_gating.get("allowed_symbols") or ())
    rejected = list(pre_gating.get("rejected") or [])
    reason_counts = dict(pre_gating.get("reason_counts") or {})
    reason_text = "n/a"
    print("=== BUY 사전 게이트 ===")
    print(
        f"requested={len(requested_symbols)} | allowed={len(allowed_symbols)} | "
        f"early_rejected={len(rejected)}"
    )
    if not pre_gating.get("scan_allowed", True):
        reason = str(pre_gating.get("scan_block_reason") or "BUY scan 전체 차단")
        print(f"scan blocked: {reason}")
        if log_engine_event is not None:
            log_engine_event(
                action="buy_scan_pre_gated",
                reason=reason,
                cycle_id=cycle_id,
                market_open=market_open,
                market_session=market_session,
            )
        print()
        return
    if reason_counts:
        reason_text = ", ".join(
            f"{key}={count}" for key, count in sorted(reason_counts.items())
        )
        print(f"early reject reasons: {reason_text}")
    if rejected:
        sample = ", ".join(
            f"{item.get('symbol')}({item.get('reason_code')})" for item in rejected[:5]
        )
        print(f"sample rejected: {sample}")
    reentry_block_reason_counts = dict(pre_gating.get("reentry_block_reason_counts") or {})
    if reentry_block_reason_counts:
        print(
            "re-entry summary: "
            f"allowed={int(pre_gating.get('reentry_allowed_count', 0) or 0)} | "
            f"blocked={int(pre_gating.get('reentry_blocked_count', 0) or 0)} | "
            + ", ".join(
                f"{key}={value}"
                for key, value in sorted(reentry_block_reason_counts.items())
            )
        )
    if rejected and log_engine_event is not None:
        log_engine_event(
            action="buy_scan_pre_gated",
            reason=(
                f"BUY pre-gating으로 {len(rejected)}개 종목을 deep scoring 전에 제외했습니다. "
                f"사유: {reason_text if reason_counts else 'n/a'}"
            ),
            cycle_id=cycle_id,
            market_open=market_open,
            market_session=market_session,
        )
    print()


def normalize_pre_gating_payload(
    pre_gating: dict[str, object] | None,
) -> dict[str, object]:
    payload = pre_gating if isinstance(pre_gating, dict) else {}
    rejected = list(payload.get("rejected") or [])
    normalized_rejected: list[dict[str, str]] = []
    reasons_by_symbol: dict[str, str] = {}
    reentry_state_by_symbol = dict(payload.get("reentry_state_by_symbol") or {})
    last_exit_reason_by_symbol = dict(payload.get("last_exit_reason_by_symbol") or {})
    residual_position_present_by_symbol = dict(
        payload.get("residual_position_present_by_symbol") or {}
    )
    reentry_block_reason_counts = dict(payload.get("reentry_block_reason_counts") or {})
    for item in rejected:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip()
        if not symbol:
            continue
        reason_code = str(item.get("reason_code") or item.get("reason") or "").strip()
        if not reason_code:
            reason_code = "unknown"
        normalized_item = {
            "symbol": symbol,
            "reason_code": reason_code,
            "reason": str(item.get("reason") or reason_code),
            "reentry_state": str(item.get("reentry_state") or reason_code),
            "exit_reason": (str(item.get("exit_reason") or "").strip() or None),
            "residual_position_present": bool(item.get("residual_position_present")),
            "diagnostics": list(item.get("diagnostics") or []),
        }
        normalized_rejected.append(normalized_item)
        reasons_by_symbol[symbol] = reason_code
    return {
        "stage": "buy_pre_gating",
        "requested_count": int(payload.get("requested_count", 0) or 0),
        "allowed_count": int(payload.get("allowed_count", 0) or 0),
        "early_reject_count": int(payload.get("early_reject_count", 0) or 0),
        "reason_counts": dict(payload.get("reason_counts") or {}),
        "rejected": normalized_rejected,
        "rejected_symbols": [item["symbol"] for item in normalized_rejected],
        "reasons_by_symbol": reasons_by_symbol,
        "reentry_state_by_symbol": {
            str(symbol).strip(): str(value).strip()
            for symbol, value in reentry_state_by_symbol.items()
            if str(symbol).strip()
        },
        "last_exit_reason_by_symbol": {
            str(symbol).strip(): str(value).strip()
            for symbol, value in last_exit_reason_by_symbol.items()
            if str(symbol).strip() and str(value).strip()
        },
        "residual_position_present_by_symbol": {
            str(symbol).strip(): bool(value)
            for symbol, value in residual_position_present_by_symbol.items()
            if str(symbol).strip()
        },
        "reentry_block_reason_counts": {
            str(reason).strip(): int(count or 0)
            for reason, count in reentry_block_reason_counts.items()
            if str(reason).strip()
        },
        "reentry_allowed_count": int(payload.get("reentry_allowed_count", 0) or 0),
        "reentry_blocked_count": int(payload.get("reentry_blocked_count", 0) or 0),
        "scan_allowed": bool(payload.get("scan_allowed", True)),
        "scan_block_reason": payload.get("scan_block_reason"),
    }


def normalize_buy_funnel_reason(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if "blocked_same_day_stop_loss_reentry" in lowered or "same_day_stop_loss_reentry" in lowered:
        return "same_day_stop_loss_reentry_blocked"
    if "blocked_residual_position" in lowered or "residual_position" in lowered:
        return "residual_position_present"
    if "excluded_symbol" in lowered or "buy_excluded_symbols" in lowered or "buy 제외" in text:
        return "excluded_symbol"
    if "untradable_today" in lowered or "untradable_symbol" in lowered or "매매불가" in text:
        return "untradable_symbol"
    if "blocked_recent_stop_loss" in lowered or "recent_stop_loss" in lowered:
        return "recent_stop_loss_no_fresh_setup"
    if "blocked_churn_risk" in lowered or "fresh setup" in lowered or "새로워 보이지" in text:
        return "profit_exit_but_setup_not_refreshed"
    if "blocked_hard_guard" in lowered:
        return "trade_mode_block"
    if "trade_mode" in lowered or "scan_only" in lowered:
        return "trade_mode_block"
    if lowered in {"reentry_cooldown", "cooldown"} or "cooldown" in lowered:
        return "cooldown"
    if "already_holding" in lowered or "already holding" in lowered or "이미 보유" in text:
        return "already_holding"
    if "same_symbol_daily_limit" in lowered or "동일 종목 일일 진입" in text:
        return "same_symbol_daily_limit"
    if "daily_pnl" in lowered or "손실 브레이크" in text or "daily pnl" in lowered:
        return "daily_pnl_brake"
    if "cash_insufficient" in lowered:
        return "cash_insufficient"
    if "현금 부족" in text:
        return "cash_insufficient"
    if "trade_budget_limited" in lowered:
        return "trade_budget_limited"
    if "예산" in text or "trade budget" in lowered:
        return "trade_budget_limited"
    if "exposure_limited" in lowered:
        return "exposure_limited"
    if "노출" in text or "exposure" in lowered:
        return "exposure_limited"
    if lowered in {"expected_cost_too_high", "cost_veto"} or "cost" in lowered:
        return "cost_veto"
    if lowered in {"net_edge_too_low", "net_edge_veto"} or "net edge" in lowered:
        return "net_edge_veto"
    if "포트폴리오" in text or "portfolio" in lowered:
        return "portfolio_fit_veto"
    if "전략" in text or "signal" in lowered or "rejected" in lowered:
        return "strategy_rejected"
    return "other"


def resolve_deep_eval_rejection_reason(result) -> str | None:
    if result is None:
        return None
    cost_block_reason = str(getattr(result, "cost_block_reason", "") or "").strip().lower()
    if cost_block_reason in {"expected_cost_too_high", "net_edge_too_low"}:
        return "cost_filter_blocked"
    if bool(getattr(result, "candidate", False)):
        return None
    final_reason = str(getattr(result, "final_reason", "") or "").strip()
    if (
        "score" in final_reason.lower()
        and "최소 기준" in final_reason
        and "못 미쳐" in final_reason
    ):
        return "score_below_threshold"
    if not bool(getattr(result, "passes_profit_buffer", True)):
        return "profit_buffer_insufficient"
    strategy_result = getattr(result, "strategy_result", None)
    if strategy_result is not None and not bool(
        getattr(strategy_result, "should_attempt_buy", False)
    ):
        return "passed_count_insufficient"
    return None


def resolve_buy_candidate_rejection_reason(
    *,
    symbol: str,
    pre_gating: dict[str, object],
    layer_selected: bool,
    raw_result,
    guarded_result,
    selected_symbol: str | None,
    final_action: str | None,
    final_reason: str | None,
) -> str | None:
    reasons_by_symbol = dict(pre_gating.get("reasons_by_symbol") or {})
    pre_gating_reason = normalize_buy_funnel_reason(reasons_by_symbol.get(symbol))
    if pre_gating_reason:
        return pre_gating_reason
    if layer_selected and not bool(pre_gating.get("scan_allowed", True)):
        blocked = normalize_buy_funnel_reason(
            pre_gating.get("scan_block_reason") or "daily_pnl_brake"
        )
        return blocked or "daily_pnl_brake"
    if raw_result is not None and symbol == selected_symbol:
        reason = normalize_buy_funnel_reason(final_reason)
        if reason and reason != "other":
            return reason
    result = guarded_result or raw_result
    if result is not None:
        deep_eval = resolve_deep_eval_rejection_reason(result)
        if deep_eval:
            return deep_eval
    return None


def resolve_buy_candidate_selection_outcome(
    *,
    stage_reached: str,
    raw_result,
    guarded_result,
    final_candidate: bool,
    executed: bool,
) -> str | None:
    if executed and final_candidate:
        return "executed"
    if stage_reached == "pre_gate_rejected":
        return "pre_gate_blocked"
    if stage_reached == "shallow_selected" and raw_result is None:
        return "shortlisted_not_deep_evaluated"
    if raw_result is None and guarded_result is None:
        return None
    result = guarded_result or raw_result
    if not bool(getattr(result, "candidate", False)):
        return "deep_eval_rejected"
    return "not_selected_after_deep_eval"


def print_buy_scan_stage_summary(
    *,
    profile_state: dict[str, object],
    layered_universe: dict[str, object],
    pre_gating: dict[str, object],
    shallow_plan: dict[str, object] | None,
) -> None:
    print("=== BUY staged scan ===")
    print(
        f"profile={profile_state.get('profile')} | "
        f"rotation={'YES' if profile_state.get('rotation_enabled') else 'NO'}"
    )
    print(
        "layers: "
        f"core={layered_universe.get('core_count', 0)} "
        f"(selected {layered_universe.get('selected_core_count', 0)}) | "
        f"rotating={layered_universe.get('rotating_count', 0)} "
        f"(selected {layered_universe.get('selected_rotating_count', 0)}) | "
        f"exploration={layered_universe.get('exploration_count', 0)} "
        f"(selected {layered_universe.get('selected_exploration_count', 0)})"
    )
    print(
        "pre-gating: "
        f"requested={pre_gating.get('requested_count', 0)} | "
        f"allowed={pre_gating.get('allowed_count', 0)} | "
        f"rejected={pre_gating.get('early_reject_count', 0)}"
    )
    if shallow_plan is not None:
        _shallow_cap_str = ""
        if shallow_plan.get("shallow_cap_applied"):
            _shallow_cap_str = (
                f" | cap={shallow_plan.get('shallow_cap_limit', 0)}"
                f"/{shallow_plan.get('shallow_cap_original_count', 0)}"
            )
        print(
            "shallow/deep: "
            f"ranked={shallow_plan.get('ranked_count', 0)}{_shallow_cap_str} | "
            f"shortlist={len(tuple(shallow_plan.get('shortlist_symbols') or ()))} | "
            f"deep_limit={shallow_plan.get('deep_eval_limit', 0)} | "
            f"exploration_quota_used={shallow_plan.get('exploration_quota_used', 0)}"
        )
        preview = list(shallow_plan.get("shortlist_preview") or [])
        if preview:
            preview_text = ", ".join(
                [
                    f"{item.get('symbol')}({item.get('layer')}, score={float(item.get('shallow_score', 0.0)):.2f})"
                    for item in preview[:5]
                    if isinstance(item, dict)
                ]
            )
            if preview_text:
                print(f"shortlist preview: {preview_text}")
        if shallow_plan.get("core_rescue_applied"):
            selected_symbol = str(shallow_plan.get("core_rescue_selected_symbol") or "?")
            replaced_symbol = str(shallow_plan.get("core_rescue_replaced_symbol") or "?")
            selected_score = shallow_plan.get("core_rescue_selected_score")
            score_text = (
                f"{float(selected_score):.2f}"
                if isinstance(selected_score, (int, float))
                else "?"
            )
            print(
                "core shortlist rescue: "
                f"{selected_symbol}(score={score_text}) <- {replaced_symbol}"
            )
        deep_eval_symbols = tuple(shallow_plan.get("deep_eval_symbols") or ())
        shortlist_symbols = tuple(shallow_plan.get("shortlist_symbols") or ())
        if deep_eval_symbols and deep_eval_symbols != shortlist_symbols:
            print("deep-eval order override: " + ", ".join(deep_eval_symbols))
    print()


def print_buy_runtime_filter_summary(
    *,
    before_results,
    after_results,
    cycle_id: str | None = None,
    market_open: bool = False,
    market_session: str | None = None,
    log_engine_event=None,
) -> None:
    before_candidate_count = sum(1 for result in before_results if result.candidate)
    after_candidate_count = sum(1 for result in after_results if result.candidate)
    excluded_messages: list[str] = []

    for before_result, after_result in zip(before_results, after_results):
        if before_result.candidate and not after_result.candidate:
            excluded_messages.append(
                f"{after_result.display_name} | 이유: {after_result.final_reason}"
            )
            action = None
            final_reason = str(after_result.final_reason or "")
            if (
                "same-day stop_loss" in final_reason.lower()
                or ("당일" in final_reason and "stop_loss" in final_reason.lower())
            ):
                action = "blocked_buy_same_day_stop_loss_reentry"
            elif "stop_loss" in final_reason:
                action = "blocked_buy_recent_stop_loss"
            elif "잔여 포지션" in final_reason:
                action = "blocked_buy_residual_position"
            elif "재진입 cooldown" in final_reason:
                action = "blocked_buy_reentry_cooldown"
            elif "동일 종목 일일 진입 횟수 제한" in final_reason:
                action = "blocked_buy_same_symbol_daily_limit"
            elif "fresh setup" in final_reason or "새로워 보이지" in final_reason:
                action = "blocked_buy_churn_risk"
            if action and cycle_id and log_engine_event is not None:
                log_engine_event(
                    action=action,
                    reason=after_result.final_reason,
                    cycle_id=cycle_id,
                    market_open=market_open,
                    market_session=market_session,
                )

    print("=== 유니버스 필터 ===")
    print(f"필터 적용 전 후보 수: {before_candidate_count}")
    print(f"필터 적용 후 후보 수: {after_candidate_count}")
    if excluded_messages:
        print("제외 종목:")
        for message in excluded_messages:
            print(message)
    else:
        print("제외된 종목 없음")
    print()
