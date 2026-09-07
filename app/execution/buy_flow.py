"""BUY execution flow.

Extracted during Stage 3b-1.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from app.auth.token import ApiHttpError, get_request_metrics_summary
from app.core.error_classification import (
    looks_like_buy_untradable_response,
    looks_like_rate_limit_error,
    rate_limit_source_from_response_body,
)
from app.core.formatters import format_bps, format_krw, format_qty
from app.core.market_session import (
    build_market_session_console_lines,
    get_korean_market_session,
)
from app.core.order_log import (
    OrderLogReadError,
    build_price_capture,
    count_today_buy_order_submissions,
    log_order_event,
    summarize_order_reason,
)
from app.core.runtime_budget import (
    api_budget_backoff_remaining_seconds,
    api_budget_can_request,
    api_budget_register_request,
    api_budget_register_requests,
)
from app.core.time_utils import get_korean_now
from app.domestic_stock.balance import is_symbol_already_holding
from app.domestic_stock.order import buy_market
from app.domestic_stock.orderable import inquire_orderable_cash
from app.execution import calculate_position_sizing
from app.execution.order_guard import (
    build_buy_attempt_signature,
    evaluate_buy_order_guard,
    evaluate_rebalance_sell_guard,
)
from app.execution.rebalance import (
    build_position_sizing_block_context,
    build_quality_rebalance_preview,
    build_rebalance_candidate,
    calculate_rebalance_sell_sizing,
    estimate_rebalance_concentration_preview,
    position_sizing_requires_rebalance,
    print_quality_rebalance_preview,
    print_rebalance_preview,
    print_rebalance_skip,
)
from app.execution.schema import ExecutionSnapshot, build_execution_snapshot
from app.math_models.sizing import (
    apply_math_overlay_to_limits,
    build_math_sizing_overlay,
)
from app.notifications.main_runtime_hooks import market_session_status_payload
from app.reporting.runtime_snapshots import (
    print_cycle_conclusion,
    record_cycle_action,
)
from app.risk.guards import (
    RiskGuardInput,
    build_risk_guard_console_lines,
    build_risk_guard_skipped_console_lines,
    evaluate_buy_risk_guards,
    serialize_risk_evaluation_for_log,
)
from app.runtime_state import (
    add_recent_order,
    mark_bought_symbol,
    mark_buy_attempt,
    mark_buy_blocked,
    mark_buy_cooldown_blocked,
    mark_buy_untradable_symbol,
    mark_reentry_blocked,
    record_buy_attempt_signature,
    record_reentry_decision,
)
from app.scanner import build_buy_strategy_summary
from app.strategy.reentry import evaluate_reentry_eligibility


@dataclass(frozen=True)
class BuyFlowResult:
    execution_snapshot: object | None = None
    position_sizing: object | None = None
    sell_position_sizing: object | None = None
    buy_risk_guard_payload: object | None = None
    sell_risk_guard_payload: object | None = None
    rebalance_buy_preview: object | None = None
    quality_rebalance_preview: object | None = None
    active_sell_analysis: object | None = None
    rebalance_selection_ms: float | None = None
    execution_tail_backoff_drain_ms: float = 0.0
    rate_limit_source: str | None = None


def _request_metrics_delta(
    before: dict[str, object] | None,
    after: dict[str, object] | None,
) -> dict[str, object]:
    before_categories = (before or {}).get("categories") or {}
    after_categories = (after or {}).get("categories") or {}
    categories: dict[str, dict[str, float | int]] = {}
    for category in ("quote", "balance", "orderable", "order", "token", "other"):
        before_bucket = before_categories.get(category) or {}
        after_bucket = after_categories.get(category) or {}
        categories[category] = {
            "count": max(
                0,
                int(after_bucket.get("count", 0)) - int(before_bucket.get("count", 0)),
            ),
            "elapsed_ms": round(
                max(
                    0.0,
                    float(after_bucket.get("elapsed_ms", 0.0))
                    - float(before_bucket.get("elapsed_ms", 0.0)),
                ),
                1,
            ),
        }
    return {
        "total_requests": max(
            0,
            int((after or {}).get("total_requests", 0))
            - int((before or {}).get("total_requests", 0)),
        ),
        "total_elapsed_ms": round(
            max(
                0.0,
                float((after or {}).get("total_elapsed_ms", 0.0))
                - float((before or {}).get("total_elapsed_ms", 0.0)),
            ),
            1,
        ),
        "categories": categories,
    }


def _phase_timing_summary(
    *,
    elapsed_ms: float,
    request_delta: dict[str, object] | None = None,
) -> dict[str, float]:
    network_ms = float((request_delta or {}).get("total_elapsed_ms", 0.0))
    total_ms = round(max(0.0, float(elapsed_ms)), 1)
    network_ms = round(max(0.0, network_ms), 1)
    local_ms = round(max(0.0, total_ms - network_ms), 1)
    return {
        "elapsed_ms": total_ms,
        "network_ms": network_ms,
        "local_ms": local_ms,
    }


def print_buy_strategy(decision, *, display_label: str, candidate: bool) -> None:
    status = "candidate" if candidate else "reject"
    print("=== 매수 전략 판단 ===")
    print(
        f"{display_label} | B: {build_buy_strategy_summary(decision)} | "
        f"{decision.passed_count}/{decision.enabled_count} | {status}"
    )
    print("최종 매수 판단: " + ("통과" if candidate else "차단"))
    print()


def print_buy_score_summary(result) -> None:
    print("=== 매수 스코어 요약 ===")
    print(f"score={result.score:.2f}")
    print(
        "핵심 가산 요인: "
        + (", ".join(result.score_highlights) if result.score_highlights else "없음")
    )
    print(
        "핵심 감점 요인: "
        + (", ".join(result.score_penalties) if result.score_penalties else "없음")
    )
    print(f"요약: {result.score_summary}")
    print(
        "비용 요약: "
        f"예상 총비용 {format_krw(result.expected_total_cost_krw)} | "
        f"cost {result.expected_cost_bps:.1f}bps | "
        f"net edge {result.net_edge_bps:.1f}bps"
    )
    print(
        "수학 요약: "
        f"{result.math_score_summary or 'summary unavailable'}"
    )
    if result.mean_reversion_summary:
        z_text = (
            "-"
            if result.mean_reversion_zscore is None
            else f"{float(result.mean_reversion_zscore or 0.0):.2f}"
        )
        print(
            "평균회귀: "
            f"{result.mean_reversion_summary} | z={z_text}"
        )
    if result.portfolio_risk_summary:
        corr_text = (
            "-"
            if result.portfolio_avg_correlation is None
            else f"{float(result.portfolio_avg_correlation or 0.0):.2f}"
        )
        var_text = (
            "-"
            if result.variance_increase_estimate is None
            else f"{float(result.variance_increase_estimate or 0.0):.3f}"
        )
        print(
            "포트폴리오 위험: "
            f"{result.portfolio_risk_summary} | avg corr={corr_text} | var+={var_text}"
        )
    print()


def print_buy_orderable_preview(execution_snapshot, position_sizing) -> None:
    details = position_sizing.details
    print("=== 주문 가능 조회 ===")
    print(
        f"주문가능현금(주문가능조회): {format_krw(execution_snapshot.orderable_cash)}"
    )
    print(f"현재가 기준 가능 수량: {format_qty(execution_snapshot.orderable_qty)}")
    print(f"추천 매수 수량: {format_qty(position_sizing.recommended_qty)}")
    print(
        f"추천 매수 금액: {format_krw(position_sizing.recommended_notional_krw)}"
    )
    print(f"예상 매수 수수료: {format_krw(details['estimated_buy_fee_krw'])}")
    print(
        f"예상 진입 슬리피지: {format_krw(details['estimated_buy_slippage_krw'])}"
    )
    print(f"예상 진입 총비용: {format_krw(details['estimated_entry_cost_krw'])}")
    print(
        f"예상 왕복 비용: {format_krw(details['estimated_round_trip_cost_krw'])}"
    )
    print(
        f"비용 포함 손익분기 상승률: {format_bps(float(details['estimated_break_even_bps']))}"
    )
    if details.get("effective_math_multiplier") is not None:
        print(
            "수학 보정 multiplier="
            f"{float(details.get('effective_math_multiplier', 1.0) or 1.0):.2f}x"
        )
        print(
            "수학 보정 요약: "
            f"{details.get('math_sizing_summary') or '기본 수량 유지'}"
        )
    if details.get("budget_rescue_applied"):
        print(
            "최소 1주 rescue: "
            f"{details.get('budget_rescue_reason') or '적용'}"
        )
    print()


def build_preview_orderable_output_from_portfolio(
    *,
    portfolio_snapshot,
    current_price: int,
) -> dict[str, str]:
    cash = max(int(getattr(portfolio_snapshot, "cash_orderable", 0) or 0), 0)
    price = int(current_price or 0)
    orderable_qty = cash // price if price > 0 else 0
    return {
        "ord_psbl_cash": str(cash),
        "nrcvb_buy_qty": str(orderable_qty),
    }


def build_math_sizing_context(*, candidate, settings) -> tuple[dict[str, object], dict[str, object]]:
    math_overlay = build_math_sizing_overlay(candidate)
    math_limits = apply_math_overlay_to_limits(
        max_budget_per_trade_krw=settings.buy_max_budget_per_trade_krw,
        max_account_exposure_pct=settings.buy_max_account_exposure_pct,
        max_qty_per_trade=settings.buy_max_qty_per_trade,
        overlay=math_overlay,
    )
    return math_overlay, math_limits


def build_rebalance_buy_preview(
    *,
    selected_candidate,
    sell_analysis,
    position_sizing,
    execution_snapshot,
    portfolio_snapshot,
    sell_sizing,
    settings,
):
    math_overlay, math_limits = build_math_sizing_context(
        candidate=selected_candidate,
        settings=settings,
    )
    estimated_cash_after_sell = (
        execution_snapshot.orderable_cash
        + int(sell_sizing.details.get("estimated_net_proceeds_krw", 0))
    )
    synthetic_orderable_qty = 0
    if execution_snapshot.current_price > 0:
        synthetic_orderable_qty = estimated_cash_after_sell // execution_snapshot.current_price

    synthetic_execution_snapshot = ExecutionSnapshot(
        symbol=execution_snapshot.symbol,
        orderable_cash=estimated_cash_after_sell,
        orderable_qty=synthetic_orderable_qty,
        current_price=execution_snapshot.current_price,
        expected_notional_krw=execution_snapshot.expected_notional_krw,
    )
    synthetic_portfolio_snapshot = replace(
        portfolio_snapshot,
        cash_total=portfolio_snapshot.cash_total + int(sell_sizing.details.get("estimated_net_proceeds_krw", 0)),
        cash_orderable=portfolio_snapshot.cash_orderable + int(sell_sizing.details.get("estimated_net_proceeds_krw", 0)),
        cash_next_day=portfolio_snapshot.cash_next_day + int(sell_sizing.details.get("estimated_net_proceeds_krw", 0)),
    )
    reevaluated_position_sizing = calculate_position_sizing(
        execution_snapshot=synthetic_execution_snapshot,
        portfolio_snapshot=synthetic_portfolio_snapshot,
        max_budget_per_trade_krw=int(math_limits["max_budget_per_trade_krw"]),
        max_account_exposure_pct=float(math_limits["max_account_exposure_pct"]),
        max_qty_per_trade=int(math_limits["max_qty_per_trade"]),
        settings=settings,
    )
    reevaluated_position_sizing.details.update(
        {
            **math_overlay,
            "math_score_summary": selected_candidate.math_score_summary,
            "mean_reversion_summary": selected_candidate.mean_reversion_summary,
            "mean_reversion_zscore": selected_candidate.mean_reversion_zscore,
            "portfolio_risk_summary": selected_candidate.portfolio_risk_summary,
            "portfolio_avg_correlation": selected_candidate.portfolio_avg_correlation,
            "portfolio_max_correlation": selected_candidate.portfolio_max_correlation,
            "variance_increase_estimate": selected_candidate.variance_increase_estimate,
            "pre_math_max_budget_per_trade_krw": settings.buy_max_budget_per_trade_krw,
            "pre_math_max_account_exposure_pct": settings.buy_max_account_exposure_pct,
            "pre_math_max_qty_per_trade": settings.buy_max_qty_per_trade,
            "math_effective_max_budget_per_trade_krw": int(
                math_limits["max_budget_per_trade_krw"]
            ),
            "math_effective_max_account_exposure_pct": float(
                math_limits["max_account_exposure_pct"]
            ),
            "math_effective_max_qty_per_trade": int(math_limits["max_qty_per_trade"]),
        }
    )
    return {
        "estimated_cash_after_sell": estimated_cash_after_sell,
        "synthetic_execution_snapshot": synthetic_execution_snapshot,
        "position_sizing": reevaluated_position_sizing,
        "math_overlay": math_overlay,
        "current_recommended_qty": position_sizing.recommended_qty,
        "next_cycle_buyable_qty": int(synthetic_orderable_qty),
        "concentration_preview": estimate_rebalance_concentration_preview(
            portfolio_snapshot=portfolio_snapshot,
            sell_symbol=sell_analysis.symbol,
            replacement_symbol=selected_candidate.symbol,
            replacement_market_value_krw=reevaluated_position_sizing.recommended_notional_krw,
            settings=settings,
        ),
        "next_action": "BUY"
        if reevaluated_position_sizing.recommended_qty > 0
        else "HOLD",
        "next_reason": (
            "리밸런싱 매도 체결 시 다음 사이클에서 BUY 재검토가 가능합니다."
            if reevaluated_position_sizing.recommended_qty > 0
            else "리밸런싱 매도 후에도 다음 사이클 BUY 추천 수량이 0주로 예상됩니다."
        ),
    }


def print_rebalance_buy_preview(
    *,
    selected_candidate,
    rebalance_buy_preview: dict[str, object],
) -> None:
    preview_sizing = rebalance_buy_preview["position_sizing"]
    preview_execution_snapshot = rebalance_buy_preview["synthetic_execution_snapshot"]
    details = preview_sizing.details

    print("=== 리밸런싱 후 BUY 재평가 미리보기 ===")
    print(
        f"예상 리밸런싱 후 주문가능현금: "
        f"{format_krw(int(rebalance_buy_preview['estimated_cash_after_sell']))}"
    )
    print(f"재평가 매수 후보: {selected_candidate.display_name}")
    if rebalance_buy_preview.get("buy_expected_total_cost_krw") is not None:
        print(
            "재평가 비용 요약: "
            f"{format_krw(int(rebalance_buy_preview['buy_expected_total_cost_krw']))} | "
            f"cost {format_bps(float(rebalance_buy_preview.get('buy_expected_cost_bps', 0.0) or 0.0))} | "
            f"net edge {format_bps(float(rebalance_buy_preview.get('buy_net_edge_bps', 0.0) or 0.0))}"
        )
    print(
        f"재평가 가능 수량(현금기준): {format_qty(preview_execution_snapshot.orderable_qty)}"
    )
    print(
        f"다음 사이클 예상 BUY 가능 수량: {format_qty(int(rebalance_buy_preview.get('next_cycle_buyable_qty', 0) or 0))}"
    )
    print(f"재평가 추천 수량: {format_qty(preview_sizing.recommended_qty)}")
    print(
        f"재평가 추천 매수 금액: {format_krw(preview_sizing.recommended_notional_krw)}"
    )
    print(f"예상 진입 총비용: {format_krw(details['estimated_entry_cost_krw'])}")
    print(
        f"비용 포함 손익분기 상승률: {format_bps(float(details['estimated_break_even_bps']))}"
    )
    concentration_preview = rebalance_buy_preview.get("concentration_preview") or {}
    if concentration_preview:
        before = concentration_preview.get("before") or {}
        after = concentration_preview.get("after") or {}
        print(
            "재평가 집중도: "
            f"top1 {float(before.get('top1_weight_pct', 0.0) or 0.0):.2f}% → {float(after.get('top1_weight_pct', 0.0) or 0.0):.2f}% | "
            f"top3 {float(before.get('top3_weight_pct', 0.0) or 0.0):.2f}% → {float(after.get('top3_weight_pct', 0.0) or 0.0):.2f}%"
        )
        print(f"집중도 해석: {str(concentration_preview.get('comment') or '-')}")
    print(f"다음 사이클 예상 동작: {rebalance_buy_preview['next_action']}")
    print(f"다음 사이클 예상 사유: {rebalance_buy_preview['next_reason']}")
    print()


def run_buy_order_flow(
    *,
    state: dict,
    settings,
    effective_buy_settings,
    token: str,
    portfolio_snapshot,
    selected_candidate,
    scan_results,
    sell_analysis_results,
    selection_details: dict[str, object],
    regime_state: dict[str, object] | None,
    daily_pnl_brake_state: dict[str, object] | None,
    market_open: bool,
    session_status,
    order_type: str,
    cycle_id: str,
    sell_check_due: bool,
    sell_watch_partial: bool,
    sell_watch_partial_reason: str | None,
    api_budget_state: dict[str, object],
    timing_summary: dict[str, object],
    rate_limit_source: str | None = None,
    run_sell_order_flow: Callable[..., object] | None = None,
    send_order_slack_notification: Callable[..., None] | None = None,
    wait_for_execution_request_budget: Callable[..., float] | None = None,
    flow_context: dict[str, object] | None = None,
) -> BuyFlowResult:
    if run_sell_order_flow is None:
        raise ValueError("run_sell_order_flow callback is required")
    if send_order_slack_notification is None:
        send_order_slack_notification = lambda **_kwargs: None
    if wait_for_execution_request_budget is None:
        wait_for_execution_request_budget = lambda *_args, **_kwargs: 0.0
    flow_context = flow_context if flow_context is not None else {}
    execution_snapshot = flow_context.get("execution_snapshot")
    position_sizing = flow_context.get("position_sizing")
    sell_position_sizing = flow_context.get("sell_position_sizing")
    buy_risk_guard_payload = flow_context.get("buy_risk_guard_payload")
    sell_risk_guard_payload = flow_context.get("sell_risk_guard_payload")
    rebalance_buy_preview = flow_context.get("rebalance_buy_preview")
    quality_rebalance_preview = flow_context.get("quality_rebalance_preview")
    active_sell_analysis = flow_context.get("active_sell_analysis")
    rebalance_selection_ms = flow_context.get("rebalance_selection_ms")
    execution_tail_backoff_drain_ms = float(
        flow_context.get("execution_tail_backoff_drain_ms", 0.0) or 0.0
    )

    def _sync_flow_context() -> None:
        flow_context.update(
            {
                "execution_snapshot": execution_snapshot,
                "position_sizing": position_sizing,
                "sell_position_sizing": sell_position_sizing,
                "buy_risk_guard_payload": buy_risk_guard_payload,
                "sell_risk_guard_payload": sell_risk_guard_payload,
                "rebalance_buy_preview": rebalance_buy_preview,
                "quality_rebalance_preview": quality_rebalance_preview,
                "active_sell_analysis": active_sell_analysis,
                "rebalance_selection_ms": rebalance_selection_ms,
                "execution_tail_backoff_drain_ms": execution_tail_backoff_drain_ms,
                "rate_limit_source": rate_limit_source,
            }
        )

    def _finish() -> BuyFlowResult:
        _sync_flow_context()
        return BuyFlowResult(
            execution_snapshot=execution_snapshot,
            position_sizing=position_sizing,
            sell_position_sizing=sell_position_sizing,
            buy_risk_guard_payload=buy_risk_guard_payload,
            sell_risk_guard_payload=sell_risk_guard_payload,
            rebalance_buy_preview=rebalance_buy_preview,
            quality_rebalance_preview=quality_rebalance_preview,
            active_sell_analysis=active_sell_analysis,
            rebalance_selection_ms=rebalance_selection_ms,
            execution_tail_backoff_drain_ms=execution_tail_backoff_drain_ms,
            rate_limit_source=rate_limit_source,
        )
    symbol = selected_candidate.symbol
    snapshot = selected_candidate.market_snapshot
    strategy_decision = selected_candidate.strategy_result

    print_buy_strategy(
        strategy_decision,
        display_label=selected_candidate.display_name,
        candidate=selected_candidate.candidate,
    )
    print_buy_score_summary(selected_candidate)

    print("=== 현재가 조회 ===")
    print(f"종목코드: {snapshot.symbol}")
    print(f"현재가: {format_krw(snapshot.current_price)}")
    print(f"등락률: {snapshot.prev_day_change_pct:.2f}%")
    print()
    strategy_details = strategy_decision.to_log_payload()

    base_log_context = {
        "symbol": symbol,
        "order_type": order_type,
        "confirm_buy": settings.confirm_buy,
        "market_open": market_open,
        "cycle_id": cycle_id,
    }
    current_position = (
        portfolio_snapshot.get_position(symbol)
        if portfolio_snapshot is not None
        else None
    )
    selected_reentry_decision = evaluate_reentry_eligibility(
        symbol=symbol,
        runtime_state=state,
        settings=effective_buy_settings,
        regime_state=regime_state,
        daily_pnl_brake_state=daily_pnl_brake_state,
        current_snapshot=snapshot,
        residual_position_qty=int(getattr(current_position, "holding_qty", 0) or 0),
        candidate=selected_candidate,
        now=get_korean_now(),
    )
    record_reentry_decision(
        state,
        symbol=symbol,
        reentry_state=selected_reentry_decision.state,
        reason=selected_reentry_decision.reason,
    )
    selection_details["selected_reentry_decision"] = selected_reentry_decision.to_log_payload()
    buy_execution_tail_budget = {
        "follow_up_request_reserve": (
            1 if settings.confirm_buy == "YES" and market_open else 0
        ),
        "orderable_wait_ms": 0.0,
        "order_submit_wait_ms": 0.0,
    }
    selection_details["buy_execution_tail_budget"] = buy_execution_tail_budget

    if daily_pnl_brake_state and daily_pnl_brake_state.get("buy_paused"):
        pause_reason = str(
            daily_pnl_brake_state.get("pause_reason")
            or daily_pnl_brake_state.get("reason")
            or "일중 손실 브레이크가 발동해 신규 BUY를 중단합니다."
        )
        print_cycle_conclusion(
            side="HOLD",
            display_name=selected_candidate.display_name,
            reason=pause_reason,
            planned_qty=0,
        )
        log_order_event(
            **{**base_log_context, "qty": 0},
            action=str(daily_pnl_brake_state.get("action") or "blocked_daily_pnl_pause"),
            result="skipped",
            reason=pause_reason,
            raw_response={
                "strategy_details": strategy_details,
                "selection_details": selection_details,
                "daily_pnl_brake_state": daily_pnl_brake_state,
            },
        )
        mark_buy_blocked(state, symbol)
        record_cycle_action(
            state,
            action=(
                "BUY_BLOCKED_DAILY_PNL_HARD_STOP"
                if daily_pnl_brake_state.get("action") == "blocked_daily_pnl_hard_stop"
                else "BUY_BLOCKED_DAILY_PNL_PAUSE"
            ),
            reason=pause_reason,
            order_side="BUY",
            symbol=symbol,
            qty=0,
            selected_symbol=symbol,
        )
        return _finish()

    if is_symbol_already_holding(portfolio_snapshot, symbol):
        holding_reason = (
            selected_reentry_decision.reason
            if selected_reentry_decision.state == "blocked_residual_position"
            else "이미 보유 중이라 주문을 보내지 않았습니다."
        )
        holding_action = (
            "blocked_buy_residual_position"
            if selected_reentry_decision.state == "blocked_residual_position"
            else "blocked_already_holding"
        )
        holding_cycle_action = (
            "BUY_BLOCKED_RESIDUAL_POSITION"
            if selected_reentry_decision.state == "blocked_residual_position"
            else "BUY_BLOCKED_ALREADY_HOLDING"
        )
        print(holding_reason)
        log_order_event(
            **{**base_log_context, "qty": settings.qty},
            action=holding_action,
            result="skipped",
            reason=holding_reason,
            raw_response={
                "strategy_details": strategy_details,
                "selection_details": selection_details,
                "reentry_decision": selected_reentry_decision.to_log_payload(),
            },
        )
        mark_buy_blocked(state, symbol)
        record_cycle_action(
            state,
            action=holding_cycle_action,
            reason=holding_reason,
            order_side="BUY",
            symbol=symbol,
            qty=settings.qty,
            selected_symbol=symbol,
        )
        return _finish()

    if not market_open:
        for line in build_risk_guard_skipped_console_lines(
            enabled=settings.buy_enable_risk_guards,
            skip_reason=f"{session_status.reason} 리스크 가드를 평가하지 않았습니다.",
        ):
            print(line)
        print()
        print(
            f"현재는 주문 가능 세션이 아니므로 주문가능조회와 주문을 보내지 않습니다. "
            f"({session_status.session})"
        )
        log_order_event(
            **{**base_log_context, "qty": 0},
            action=session_status.buy_block_action or "blocked_market_closed",
            result="skipped",
            reason=session_status.reason,
            raw_response={
                "strategy_details": strategy_details,
                "selection_details": selection_details,
                "reentry_decision": selected_reentry_decision.to_log_payload(),
            },
        )
        if settings.buy_block_on_blocked_preview:
            mark_buy_attempt(state, symbol)
            add_recent_order(
                state,
                side="BUY",
                symbol=symbol,
                qty=0,
                action=session_status.buy_block_action or "blocked_market_closed",
            )
        mark_buy_blocked(state, symbol)
        record_cycle_action(
            state,
            action="BUY_BLOCKED_ORDER_WINDOW",
            reason=session_status.reason,
            order_side="BUY",
            symbol=symbol,
            qty=0,
            selected_symbol=symbol,
        )
        return _finish()

    order_preview_started_perf = time.perf_counter()
    order_preview_metrics_before = get_request_metrics_summary()
    if settings.confirm_buy != "YES":
        selection_details["orderable_lookup_source"] = "portfolio_snapshot_preview"
        orderable_data = {
            "rt_cd": "0",
            "output": build_preview_orderable_output_from_portfolio(
                portfolio_snapshot=portfolio_snapshot,
                current_price=snapshot.current_price,
            ),
        }
    else:
        selection_details["orderable_lookup_source"] = "kis_orderable_api"
        # buy_scan에서 rate limit이 발생했을 경우 backoff가 아직 남아있을 수 있습니다.
        # orderable 호출 전에 남은 backoff를 소진해 즉시 실패를 방지합니다.
        _drain_now = get_korean_now()
        _backoff_drain_seconds = api_budget_backoff_remaining_seconds(api_budget_state, now=_drain_now)
        if _backoff_drain_seconds > 0:
            print(
                f"[info] BUY execution tail waiting for API backoff drain"
                f" | wait={round(_backoff_drain_seconds * 1000)}ms"
            )
            time.sleep(_backoff_drain_seconds)
            execution_tail_backoff_drain_ms = round(_backoff_drain_seconds * 1000, 1)
            flow_context["execution_tail_backoff_drain_ms"] = execution_tail_backoff_drain_ms
            timing_summary["execution_tail_backoff_drain_ms"] = execution_tail_backoff_drain_ms
        # 현재가/잔고 조회 직후 바로 이어지는 호출이라 초당 제한을 조금 더 보수적으로 맞춥니다.
        # orderable 직후에는 거의 무조건 order_submit이 따라오므로 2-request burst로
        # 예약해 KIS 1초 window가 한 번 더 비어 있도록 강제합니다.
        buy_execution_tail_budget["orderable_wait_ms"] = wait_for_execution_request_budget(
            api_budget_state,
            now=get_korean_now(),
            phase="orderable_lookup",
            request_cost=2,
            request_reserve=int(
                buy_execution_tail_budget.get("follow_up_request_reserve", 0) or 0
            ),
        )
        orderable_request_at = get_korean_now()
        if not api_budget_can_request(api_budget_state, now=orderable_request_at):
            raise RuntimeError("주문가능조회 전 API request 예산이 부족합니다.")
        api_budget_register_request(api_budget_state, now=orderable_request_at)
        orderable_data = inquire_orderable_cash(
            symbol=symbol,
            price=str(snapshot.current_price),
            token=token,
        )
        if orderable_data.get("rt_cd") != "0":
            rate_limit_source = (
                rate_limit_source_from_response_body(
                    orderable_data,
                    source="orderable",
                )
                or rate_limit_source
            )
            raise RuntimeError(f"주문가능금액 조회 실패: {orderable_data}")

    execution_snapshot = build_execution_snapshot(
        symbol=symbol,
        current_price=snapshot.current_price,
        qty=settings.qty,
        orderable_output=orderable_data["output"],
    )
    flow_context["execution_snapshot"] = execution_snapshot
    math_overlay, math_limits = build_math_sizing_context(
        candidate=selected_candidate,
        settings=effective_buy_settings,
    )
    position_sizing = calculate_position_sizing(
        execution_snapshot=execution_snapshot,
        portfolio_snapshot=portfolio_snapshot,
        max_budget_per_trade_krw=int(math_limits["max_budget_per_trade_krw"]),
        max_account_exposure_pct=float(math_limits["max_account_exposure_pct"]),
        max_qty_per_trade=int(math_limits["max_qty_per_trade"]),
        settings=effective_buy_settings,
        allow_min_one_share_budget_rescue=True,
    )
    flow_context["position_sizing"] = position_sizing
    position_sizing.details.update(
        {
            **math_overlay,
            "math_score_summary": selected_candidate.math_score_summary,
            "mean_reversion_summary": selected_candidate.mean_reversion_summary,
            "mean_reversion_zscore": selected_candidate.mean_reversion_zscore,
            "portfolio_risk_summary": selected_candidate.portfolio_risk_summary,
            "portfolio_avg_correlation": selected_candidate.portfolio_avg_correlation,
            "portfolio_max_correlation": selected_candidate.portfolio_max_correlation,
            "variance_increase_estimate": selected_candidate.variance_increase_estimate,
            "pre_math_max_budget_per_trade_krw": effective_buy_settings.buy_max_budget_per_trade_krw,
            "pre_math_max_account_exposure_pct": effective_buy_settings.buy_max_account_exposure_pct,
            "pre_math_max_qty_per_trade": effective_buy_settings.buy_max_qty_per_trade,
            "math_effective_max_budget_per_trade_krw": int(
                math_limits["max_budget_per_trade_krw"]
            ),
            "math_effective_max_account_exposure_pct": float(
                math_limits["max_account_exposure_pct"]
            ),
            "math_effective_max_qty_per_trade": int(math_limits["max_qty_per_trade"]),
        }
    )
    recommended_qty = position_sizing.recommended_qty
    sized_log_context = {**base_log_context, "qty": recommended_qty}
    rebalance_note_log_context = {
        key: value for key, value in sized_log_context.items() if key != "order_type"
    }
    planned_execution_snapshot = build_execution_snapshot(
        symbol=symbol,
        current_price=snapshot.current_price,
        qty=max(recommended_qty, 0),
        orderable_output=orderable_data["output"],
    )
    position_sizing_block = build_position_sizing_block_context(position_sizing)
    order_preview_metrics_after = get_request_metrics_summary()
    timing_summary["order_preview"] = _phase_timing_summary(
        elapsed_ms=(time.perf_counter() - order_preview_started_perf) * 1000,
        request_delta=_request_metrics_delta(
            order_preview_metrics_before,
            order_preview_metrics_after,
        ),
    )

    if (
        settings.enable_rebalance_sell
        and sell_check_due
        and position_sizing_requires_rebalance(position_sizing)
        and portfolio_snapshot.held_positions
    ):
        try:
            buy_submission_count = count_today_buy_order_submissions(strict=True)
            buy_submission_limit_reached = (
                buy_submission_count >= settings.buy_daily_max_order_submissions
            )
            rebalance_skip_reason = "오늘 BUY 주문 제출 한도를 이미 사용해 rebalance를 검토하지 않습니다."
        except OrderLogReadError:
            buy_submission_limit_reached = True
            rebalance_skip_reason = "주문 로그를 신뢰할 수 없어 rebalance 검토를 차단합니다."
        if buy_submission_limit_reached:
            log_order_event(
                **rebalance_note_log_context,
                order_type="rebalance",
                action="rebalance_skipped_limit",
                result="skipped",
                reason=rebalance_skip_reason,
                raw_response={
                    "position_sizing": position_sizing.details,
                    "selection_details": selection_details,
                },
            )
            print_rebalance_skip(rebalance_skip_reason)
        else:
            rebalance_guard = evaluate_rebalance_sell_guard(
                state=state,
                max_submissions_per_day=settings.rebalance_sell_max_submissions_per_day,
            )
            rebalance_selection_started_perf = time.perf_counter()
            rebalance_evaluation = build_rebalance_candidate(
                sell_analysis_results=sell_analysis_results,
                selected_candidate=selected_candidate,
                scan_results=scan_results,
                portfolio_snapshot=portfolio_snapshot,
                settings=settings,
            )
            rebalance_selection_ms = (
                time.perf_counter() - rebalance_selection_started_perf
            ) * 1000
            if not rebalance_guard.allowed:
                log_order_event(
                    **rebalance_note_log_context,
                    order_type="rebalance",
                    action="rebalance_skipped_limit",
                    result="skipped",
                    reason="rebalance 일일 횟수 제한으로 검토하지 않습니다.",
                    raw_response={
                        "position_sizing": position_sizing.details,
                        "selection_details": selection_details,
                    },
                )
                print_rebalance_skip(
                    "rebalance 일일 횟수 제한으로 검토하지 않습니다."
                )
            else:
                rebalance_candidate = rebalance_evaluation["candidate"]
                if rebalance_candidate is None:
                    rebalance_skip_reason = str(rebalance_evaluation["reason"])
                    rebalance_reason_code = str(
                        rebalance_evaluation.get("reason_code") or ""
                    )
                    _reason_code_to_action = {
                        "sell_watch_incomplete": "rebalance_deferred_sell_watch_incomplete",
                        "no_candidate": "rebalance_skipped_no_candidate",
                        "blocked_score_delta": "rebalance_skipped_score_delta",
                        "blocked_net_edge": "rebalance_skipped_net_edge",
                        "blocked_concentration": "rebalance_skipped_concentration",
                        "blocked_profit_buffer": "rebalance_skipped_profit_buffer",
                        "blocked_other": "rebalance_skipped_no_candidate",
                    }
                    rebalance_skip_action = _reason_code_to_action.get(
                        rebalance_reason_code,
                        "rebalance_skipped_no_candidate",
                    )
                    rebalance_diagnostics = {
                        "reason_code": rebalance_reason_code or None,
                        "unevaluated_holding_count": rebalance_evaluation.get(
                            "unevaluated_holding_count"
                        ),
                        "unevaluated_holding_symbols_sample": rebalance_evaluation.get(
                            "unevaluated_holding_symbols_sample"
                        ),
                        "sell_watch_partial": sell_watch_partial,
                        "sell_watch_partial_reason": sell_watch_partial_reason,
                        "sell_analysis_evaluated_count": len(sell_analysis_results),
                        "held_position_count": len(portfolio_snapshot.held_positions),
                    }
                    log_order_event(
                        **rebalance_note_log_context,
                        order_type="rebalance",
                        action=rebalance_skip_action,
                        result="skipped",
                        reason=rebalance_skip_reason,
                        raw_response={
                            "position_sizing": position_sizing.details,
                            "selection_details": selection_details,
                            "rebalance_diagnostics": rebalance_diagnostics,
                        },
                    )
                    print_rebalance_skip(str(rebalance_evaluation["reason"]))
                else:
                    weakest_analysis = rebalance_candidate["analysis"]
                    active_sell_analysis = weakest_analysis
                    flow_context["active_sell_analysis"] = active_sell_analysis
                    sell_position_sizing = calculate_rebalance_sell_sizing(
                        weakest_analysis=weakest_analysis,
                        position_sizing=position_sizing,
                        execution_snapshot=execution_snapshot,
                        settings=settings,
                    )
                    rebalance_log_context = {
                        "symbol": weakest_analysis.symbol,
                        "qty": sell_position_sizing.recommended_sell_qty,
                        "order_type": "market_sell",
                        "confirm_buy": settings.confirm_buy,
                        "market_open": market_open,
                        "cycle_id": cycle_id,
                    }
                    rebalance_raw_response = {
                        "sell_strategy_details": weakest_analysis.sell_decision.to_log_payload(),
                        "buy_strategy_details": weakest_analysis.buy_strategy_result.to_log_payload(),
                        "sell_position_sizing": sell_position_sizing.details,
                        "trigger": "rebalance",
                        "recommended_sell_qty": sell_position_sizing.recommended_sell_qty,
                        "sell_plan": {
                            "current_price_krw": weakest_analysis.market_snapshot.current_price,
                            "qty": sell_position_sizing.recommended_sell_qty,
                            "notional_krw": sell_position_sizing.recommended_notional_krw,
                            "estimated_sell_fee_krw": sell_position_sizing.details["estimated_sell_fee_krw"],
                            "estimated_sell_tax_krw": sell_position_sizing.details["estimated_sell_tax_krw"],
                            "estimated_sell_slippage_krw": sell_position_sizing.details["estimated_sell_slippage_krw"],
                            "estimated_net_proceeds_krw": sell_position_sizing.details["estimated_net_proceeds_krw"],
                        },
                        "rebalance_plan": {
                            "buy_symbol": selected_candidate.symbol,
                            "buy_name": selected_candidate.name,
                            "buy_score": selected_candidate.score,
                            "buy_score_summary": selected_candidate.score_summary,
                            "buy_score_highlights": list(selected_candidate.score_highlights),
                            "buy_score_penalties": list(selected_candidate.score_penalties),
                            "buy_expected_total_cost_krw": selected_candidate.expected_total_cost_krw,
                            "buy_expected_cost_bps": selected_candidate.expected_cost_bps,
                            "buy_net_edge_bps": selected_candidate.net_edge_bps,
                            "buy_cost_block_reason": selected_candidate.cost_block_reason,
                            "sell_symbol": weakest_analysis.symbol,
                            "sell_name": weakest_analysis.name,
                            "cash_insufficient_reason": position_sizing_block["reason"],
                            "sell_holding_score": rebalance_candidate["current_holding_score"],
                            "sell_holding_quality_score": rebalance_candidate["holding_quality_score"],
                            "sell_replaceability_score": rebalance_candidate["replaceability_score"],
                            "sell_replacement_pressure_score": rebalance_candidate["replacement_pressure_score"],
                            "sell_trend_break_penalty": rebalance_candidate["trend_break_penalty"],
                            "sell_momentum_decay_penalty": rebalance_candidate["momentum_decay_penalty"],
                            "score_delta": rebalance_candidate["score_delta"],
                            "sell_net_pnl_bps": rebalance_candidate["net_pnl_bps"],
                            "selection_reason": rebalance_candidate["selection_reason"],
                        },
                    }
                    log_order_event(
                        **rebalance_note_log_context,
                        order_type="rebalance",
                        action="rebalance_considered",
                        result="skipped",
                        reason=position_sizing_block["reason"],
                        raw_response=rebalance_raw_response,
                    )
                    rebalance_buy_preview = build_rebalance_buy_preview(
                        selected_candidate=selected_candidate,
                        sell_analysis=weakest_analysis,
                        position_sizing=position_sizing,
                        execution_snapshot=execution_snapshot,
                        portfolio_snapshot=portfolio_snapshot,
                        sell_sizing=sell_position_sizing,
                        settings=effective_buy_settings,
                    )
                    rebalance_buy_preview["selection_reason"] = str(
                        rebalance_candidate["selection_reason"]
                    )
                    rebalance_buy_preview["cash_insufficient_reason"] = position_sizing_block["reason"]
                    rebalance_buy_preview["buy_score_summary"] = selected_candidate.score_summary
                    rebalance_buy_preview["buy_score_highlights"] = list(
                        selected_candidate.score_highlights
                    )
                    rebalance_buy_preview["buy_score_penalties"] = list(
                        selected_candidate.score_penalties
                    )
                    rebalance_buy_preview["buy_expected_total_cost_krw"] = (
                        selected_candidate.expected_total_cost_krw
                    )
                    rebalance_buy_preview["buy_expected_cost_bps"] = (
                        selected_candidate.expected_cost_bps
                    )
                    rebalance_buy_preview["buy_net_edge_bps"] = (
                        selected_candidate.net_edge_bps
                    )
                    rebalance_buy_preview["preview_type"] = "cash_insufficient"
                    rebalance_buy_preview["status"] = "selected"
                    rebalance_buy_preview["current_weakest_candidates"] = list(
                        rebalance_evaluation.get("current_weakest_candidates") or []
                    )
                    rebalance_buy_preview["replacement_candidates"] = list(
                        rebalance_evaluation.get("replacement_candidates") or []
                    )
                    rebalance_buy_preview["selected_pair"] = {
                        "sell_symbol": weakest_analysis.symbol,
                        "sell_display_name": weakest_analysis.display_name,
                        "buy_symbol": selected_candidate.symbol,
                        "buy_display_name": selected_candidate.display_name,
                        "score_delta": float(rebalance_candidate["score_delta"]),
                        "cost_adjusted_delta": float(rebalance_candidate["cost_adjusted_delta"]),
                        "quality_optimizer_score": float(rebalance_candidate["quality_optimizer_score"]),
                        "expected_cash_unlock_krw": int(rebalance_candidate["expected_cash_unlock_krw"]),
                        "next_cycle_buyable_qty": int(rebalance_candidate["next_cycle_buyable_qty"]),
                        "selection_reason": str(rebalance_candidate["selection_reason"]),
                        "concentration_before": (
                            (rebalance_candidate.get("concentration_preview") or {}).get("before") or {}
                        ),
                        "concentration_after": (
                            (rebalance_candidate.get("concentration_preview") or {}).get("after") or {}
                        ),
                        "concentration_comment": str(
                            (rebalance_candidate.get("concentration_preview") or {}).get("comment") or ""
                        ),
                    }
                    rebalance_raw_response["rebalance_buy_preview"] = {
                        "estimated_cash_after_sell_krw": rebalance_buy_preview["estimated_cash_after_sell"],
                        "next_cycle_expected_action": rebalance_buy_preview["next_action"],
                        "next_cycle_reason": rebalance_buy_preview["next_reason"],
                        "selection_reason": rebalance_buy_preview["selection_reason"],
                        "cash_insufficient_reason": rebalance_buy_preview["cash_insufficient_reason"],
                        "buy_score_summary": rebalance_buy_preview["buy_score_summary"],
                        "buy_score_highlights": rebalance_buy_preview["buy_score_highlights"],
                        "buy_score_penalties": rebalance_buy_preview["buy_score_penalties"],
                        "position_sizing": rebalance_buy_preview["position_sizing"].details,
                        "preview_type": rebalance_buy_preview["preview_type"],
                        "status": rebalance_buy_preview["status"],
                        "current_weakest_candidates": rebalance_buy_preview["current_weakest_candidates"],
                        "replacement_candidates": rebalance_buy_preview["replacement_candidates"],
                        "selected_pair": rebalance_buy_preview["selected_pair"],
                    }
                    print_rebalance_preview(
                        sell_analysis=weakest_analysis,
                        sell_sizing=sell_position_sizing,
                        buy_candidate=selected_candidate,
                        score_delta=float(rebalance_candidate["score_delta"]),
                        current_holding_score=float(rebalance_candidate["current_holding_score"]),
                        holding_quality_score=float(rebalance_candidate["holding_quality_score"]),
                        replaceability_score=float(rebalance_candidate["replaceability_score"]),
                        cost_adjusted_delta=float(rebalance_candidate["cost_adjusted_delta"]),
                        quality_optimizer_score=float(rebalance_candidate["quality_optimizer_score"]),
                        net_pnl_bps=float(rebalance_candidate["net_pnl_bps"]),
                        expected_cash_unlock_krw=int(rebalance_candidate["expected_cash_unlock_krw"]),
                        concentration_preview=rebalance_candidate.get("concentration_preview"),
                        selection_reason=str(rebalance_candidate["selection_reason"]),
                        consideration_reason=position_sizing_block["reason"],
                    )
                    log_order_event(
                        **rebalance_note_log_context,
                        order_type="rebalance",
                        action="rebalance_sell_preview",
                        result="skipped",
                        reason=str(rebalance_candidate["selection_reason"]),
                        raw_response=rebalance_raw_response,
                    )
                    rebalance_flow_context: dict[str, object] = {"risk_guard_payload": None}
                    run_sell_order_flow(
                        state=state,
                        settings=settings,
                        token=token,
                        portfolio_snapshot=portfolio_snapshot,
                        analysis=weakest_analysis,
                        sell_sizing=sell_position_sizing,
                        sell_log_context=rebalance_log_context,
                        sell_raw_response=rebalance_raw_response,
                        market_open=market_open,
                        session_status=session_status,
                        cycle_reason="rebalance",
                        cycle_action_label="리밸런싱 매도",
                        is_rebalance=True,
                        flow_context=rebalance_flow_context,
                        api_budget_state=api_budget_state,
                    )
                    sell_risk_guard_payload = rebalance_flow_context.get("risk_guard_payload")
                    flow_context["sell_risk_guard_payload"] = sell_risk_guard_payload
                    print_rebalance_buy_preview(
                        selected_candidate=selected_candidate,
                        rebalance_buy_preview=rebalance_buy_preview,
                    )
                    print("리밸런싱 매도를 수행했으므로 신규 매수는 다음 사이클에서 재평가합니다.")
                    return _finish()
    elif settings.enable_rebalance_sell and position_sizing_requires_rebalance(position_sizing):
        log_order_event(
            **rebalance_note_log_context,
            order_type="rebalance",
            action="rebalance_skipped_no_candidate",
            result="skipped",
            reason="weakest holding이 없어 rebalance를 검토하지 않습니다.",
            raw_response={
                "position_sizing": position_sizing.details,
                "selection_details": selection_details,
            },
        )
        print_rebalance_skip("weakest holding이 없어 rebalance를 검토하지 않습니다.")
    elif settings.enable_rebalance_sell:
        if recommended_qty > 0:
            if settings.enable_quality_rebalance_preview and sell_analysis_results:
                quality_rebalance_preview = build_quality_rebalance_preview(
                    sell_analysis_results=sell_analysis_results,
                    scan_results=scan_results,
                    portfolio_snapshot=portfolio_snapshot,
                    settings=settings,
                )
                flow_context["quality_rebalance_preview"] = quality_rebalance_preview
                rebalance_buy_preview = quality_rebalance_preview
                flow_context["rebalance_buy_preview"] = rebalance_buy_preview
                print_quality_rebalance_preview(preview=quality_rebalance_preview)
                quality_action = (
                    "rebalance_quality_preview"
                    if str(quality_rebalance_preview.get("status") or "") == "preview"
                    else (
                        "rebalance_skipped_concentration"
                        if "집중도 한도" in str(quality_rebalance_preview.get("reason") or "")
                        else "rebalance_skipped_net_edge"
                        if "비용 반영 순우위" in str(quality_rebalance_preview.get("reason") or "")
                        else "rebalance_skipped_no_candidate"
                    )
                )
                log_order_event(
                    **rebalance_note_log_context,
                    order_type="rebalance",
                    action=quality_action,
                    result="skipped",
                    reason=str(quality_rebalance_preview.get("reason") or "quality rebalance preview"),
                    raw_response={
                        "selection_details": selection_details,
                        "rebalance_preview": quality_rebalance_preview,
                        "score_summary": selected_candidate.score_summary,
                        "score_highlights": list(selected_candidate.score_highlights),
                        "score_penalties": list(selected_candidate.score_penalties),
                    },
                )
            else:
                print_rebalance_skip(
                    "신규 BUY 추천 수량이 0주가 아니어서 rebalance를 검토하지 않습니다."
                )
        else:
            print_rebalance_skip(position_sizing_block["rebalance_skip_reason"])

    if recommended_qty <= 0:
        print_buy_orderable_preview(execution_snapshot, position_sizing)
        print(f"주문 중단: {position_sizing.reason}")
        # Task 3: detailed cause logging for trade_budget_limited and other block reasons
        _block_code = position_sizing_block.get("code", "")
        _sizing_details = position_sizing.details
        _budget_block_detail: dict[str, object] = {
            "block_reason_code": _block_code,
            "block_reason_label": position_sizing_block.get("label", ""),
            "max_affordable_qty": int(_sizing_details.get("max_affordable_qty", 0) or 0),
            "budget_limited_qty": int(_sizing_details.get("budget_limited_qty", 0) or 0),
            "exposure_limited_qty": int(_sizing_details.get("exposure_limited_qty", 0) or 0),
            "max_qty_limited_qty": int(_sizing_details.get("max_qty_limited_qty", 0) or 0),
            "max_budget_per_trade_krw": int(_sizing_details.get("max_budget_per_trade_krw", 0) or 0),
            "exposure_budget_krw": int(_sizing_details.get("exposure_budget_krw", 0) or 0),
            "orderable_cash_krw": int(_sizing_details.get("orderable_cash_krw", 0) or 0),
            "current_price_krw": int(_sizing_details.get("current_price_krw", 0) or 0),
            "math_effective_max_budget_per_trade_krw": int(
                _sizing_details.get("math_effective_max_budget_per_trade_krw", 0) or 0
            ),
            "budget_rescue_applied": bool(_sizing_details.get("budget_rescue_applied", False)),
        }
        if _block_code == "trade_budget_limited":
            _min_cost_1share = int(_sizing_details.get("current_price_krw", 0) or 0)
            _budget_block_detail["diagnosis"] = (
                f"1주당 가격={_min_cost_1share:,}원 > "
                f"1회매수예산={int(_sizing_details.get('max_budget_per_trade_krw', 0) or 0):,}원"
                if _min_cost_1share > int(_sizing_details.get("max_budget_per_trade_krw", 0) or 0)
                else f"예산={int(_sizing_details.get('max_budget_per_trade_krw', 0) or 0):,}원 "
                f"현금={int(_sizing_details.get('orderable_cash_krw', 0) or 0):,}원"
            )
            print(
                f"[info] trade_budget_limited detail: "
                f"max_budget={int(_sizing_details.get('max_budget_per_trade_krw', 0) or 0):,}원 | "
                f"math_budget={int(_sizing_details.get('math_effective_max_budget_per_trade_krw', 0) or 0):,}원 | "
                f"price={int(_sizing_details.get('current_price_krw', 0) or 0):,}원 | "
                f"cash={int(_sizing_details.get('orderable_cash_krw', 0) or 0):,}원 | "
                f"budget_rescue={bool(_sizing_details.get('budget_rescue_applied', False))}"
            )
        log_order_event(
            **sized_log_context,
            action=position_sizing_block["action"],
            result="skipped",
            reason=position_sizing.reason,
            raw_response={
                "strategy_details": strategy_details,
                "selection_details": selection_details,
                "position_sizing": position_sizing.details,
                "cash_insufficient_reason": position_sizing_block["reason"],
                "budget_block_detail": _budget_block_detail,
            },
        )
        mark_buy_blocked(state, symbol)
        record_cycle_action(
            state,
            action=position_sizing_block["cycle_action"],
            reason=position_sizing.reason,
            order_side="BUY",
            symbol=symbol,
            qty=0,
            selected_symbol=symbol,
        )
        return _finish()

    buy_guard = evaluate_buy_order_guard(
        state=state,
        symbol=symbol,
        qty=recommended_qty,
        block_rebuy_symbols_bought_today=effective_buy_settings.block_rebuy_symbols_bought_today,
        allow_one_buy_per_symbol_per_day=effective_buy_settings.allow_one_buy_per_symbol_per_day,
        rebuy_cooldown_minutes=effective_buy_settings.rebuy_cooldown_minutes,
        same_symbol_max_buys_per_day=effective_buy_settings.same_symbol_max_buys_per_day,
        blocked_cooldown_minutes=(
            effective_buy_settings.buy_blocked_cooldown_minutes
            if effective_buy_settings.enable_buy_cooldown
            else 0
        ),
        order_cooldown_minutes=effective_buy_settings.order_cooldown_minutes,
        reentry_decision=selected_reentry_decision,
    )
    if not buy_guard.allowed:
        print_buy_orderable_preview(execution_snapshot, position_sizing)
        if buy_guard.is_cooldown:
            print("주문 가드: BUY cooldown skip")
            print_cycle_conclusion(
                side="HOLD",
                display_name=selected_candidate.display_name,
                reason="BUY cooldown guard 적용",
                planned_qty=0,
            )
        print(f"매수 중단: {buy_guard.reason}")
        log_order_event(
            **sized_log_context,
            action=buy_guard.action or "blocked_buy_duplicate_guard",
            result="skipped",
            reason=buy_guard.reason,
            raw_response={
                "strategy_details": strategy_details,
                "selection_details": selection_details,
                "position_sizing": position_sizing.details,
                "reentry_decision": selected_reentry_decision.to_log_payload(),
            },
        )
        mark_buy_blocked(state, symbol)
        if buy_guard.is_cooldown:
            mark_buy_cooldown_blocked(state, symbol)
        if buy_guard.action in {
            "blocked_buy_reentry_cooldown",
            "blocked_buy_same_day_stop_loss_reentry",
            "blocked_buy_recent_stop_loss",
            "blocked_buy_churn_risk",
            "blocked_buy_reentry_hard_guard",
            "blocked_buy_residual_position",
            "blocked_buy_reentry_state",
        }:
            mark_reentry_blocked(state, symbol)
        record_cycle_action(
            state,
            action=(
                "BUY_BLOCKED_RESIDUAL_POSITION"
                if buy_guard.action == "blocked_buy_residual_position"
                else "BUY_BLOCKED_SAME_DAY_STOP_LOSS_REENTRY"
                if buy_guard.action == "blocked_buy_same_day_stop_loss_reentry"
                else "BUY_BLOCKED_RECENT_STOP_LOSS"
                if buy_guard.action == "blocked_buy_recent_stop_loss"
                else "BUY_BLOCKED_REENTRY_HARD_GUARD"
                if buy_guard.action == "blocked_buy_reentry_hard_guard"
                else "BUY_BLOCKED_REENTRY_CHURN"
                if buy_guard.action == "blocked_buy_churn_risk"
                else "BUY_SKIPPED_COOLDOWN"
                if buy_guard.is_cooldown
                else (
                    "BUY_BLOCKED_SAME_SYMBOL_DAILY_LIMIT"
                    if buy_guard.action == "blocked_buy_same_symbol_daily_limit"
                    else "BUY_BLOCKED_DUPLICATE_GUARD"
                )
            ),
            reason=buy_guard.reason,
            order_side="BUY",
            symbol=symbol,
            qty=recommended_qty,
            selected_symbol=symbol,
        )
        return _finish()

    print_buy_orderable_preview(execution_snapshot, position_sizing)

    print("=== 매수 주문 미리보기 ===")
    print(f"주문 종목: {selected_candidate.display_name}")
    print(f"주문 수량: {format_qty(recommended_qty)}")
    print("주문 방식: 시장가")
    print(f"CONFIRM_BUY={settings.confirm_buy}")
    print(
        f"예상 매수 수수료: {format_krw(position_sizing.details['estimated_buy_fee_krw'])}"
    )
    print(
        f"예상 진입 슬리피지: {format_krw(position_sizing.details['estimated_buy_slippage_krw'])}"
    )
    print(
        f"예상 진입 총비용: {format_krw(position_sizing.details['estimated_entry_cost_krw'])}"
    )
    print(
        f"예상 왕복 비용: {format_krw(position_sizing.details['estimated_round_trip_cost_krw'])}"
    )
    print(
        f"비용 포함 손익분기 상승률: {format_bps(float(position_sizing.details['estimated_break_even_bps']))}"
    )
    print()
    print_cycle_conclusion(
        side="BUY",
        display_name=selected_candidate.display_name,
        reason=selection_details["selection_reason"],
        planned_qty=recommended_qty,
    )

    if settings.confirm_buy != "YES":
        for line in build_risk_guard_skipped_console_lines(
            enabled=settings.buy_enable_risk_guards,
            skip_reason="CONFIRM_BUY가 YES가 아니어서 리스크 가드를 평가하지 않았습니다.",
        ):
            print(line)
        print()
        print("실주문은 실행하지 않았습니다. .env에서 CONFIRM_BUY=YES 로 바꾸면 매수 검토를 주문으로 보냅니다.")
        log_order_event(
            **sized_log_context,
            action="blocked_confirm_buy_off",
            result="skipped",
            reason="CONFIRM_BUY가 YES가 아니어서 주문을 보내지 않았습니다.",
            raw_response={
                "strategy_details": strategy_details,
                "selection_details": selection_details,
                "position_sizing": position_sizing.details,
            },
        )
        if settings.buy_block_on_blocked_preview:
            mark_buy_attempt(state, symbol)
            add_recent_order(
                state,
                side="BUY",
                symbol=symbol,
                qty=recommended_qty,
                action="blocked_confirm_buy_off",
            )
        mark_buy_blocked(state, symbol)
        record_cycle_action(
            state,
            action="BUY_PREVIEW",
            reason="CONFIRM_BUY가 YES가 아니어서 주문을 보내지 않았습니다.",
            order_side="BUY",
            symbol=symbol,
            qty=recommended_qty,
            selected_symbol=symbol,
        )
        return _finish()

    if not market_open:
        for line in build_risk_guard_skipped_console_lines(
            enabled=settings.buy_enable_risk_guards,
            skip_reason=f"{session_status.reason} 리스크 가드를 평가하지 않았습니다.",
        ):
            print(line)
        print()
        print(
            f"현재는 주문 가능 세션이 아니므로 주문을 보내지 않습니다. "
            f"({session_status.session})"
        )
        log_order_event(
            **sized_log_context,
            action=session_status.buy_block_action or "blocked_market_closed",
            result="skipped",
            reason=session_status.reason,
            raw_response={
                "strategy_details": strategy_details,
                "selection_details": selection_details,
                "position_sizing": position_sizing.details,
            },
        )
        if settings.buy_block_on_blocked_preview:
            mark_buy_attempt(state, symbol)
            add_recent_order(
                state,
                side="BUY",
                symbol=symbol,
                qty=recommended_qty,
                action=session_status.buy_block_action or "blocked_market_closed",
            )
        mark_buy_blocked(state, symbol)
        record_cycle_action(
            state,
            action="BUY_BLOCKED_ORDER_WINDOW",
            reason=session_status.reason,
            order_side="BUY",
            symbol=symbol,
            qty=recommended_qty,
            selected_symbol=symbol,
        )
        return _finish()

    risk_guard_input = RiskGuardInput(
        market_snapshot=snapshot,
        portfolio_snapshot=portfolio_snapshot,
        execution_snapshot=planned_execution_snapshot,
        side="BUY",
        enabled=effective_buy_settings.buy_enable_risk_guards,
        daily_max_order_submissions=effective_buy_settings.buy_daily_max_order_submissions,
        daily_max_notional_krw=settings.buy_daily_max_notional_krw,
    )
    risk_guard_decision = evaluate_buy_risk_guards(guard_input=risk_guard_input)
    buy_risk_guard_payload = serialize_risk_evaluation_for_log(risk_guard_decision)
    flow_context["buy_risk_guard_payload"] = buy_risk_guard_payload
    for line in build_risk_guard_console_lines(risk_guard_decision):
        print(line)
    print()
    if not risk_guard_decision.allowed:
        print(f"리스크 차단: {risk_guard_decision.reason}")
        log_order_event(
            **sized_log_context,
            action=risk_guard_decision.action or "blocked_risk_guard",
            result="skipped",
            reason=risk_guard_decision.reason,
            raw_response={
                "strategy_details": strategy_details,
                "selection_details": selection_details,
                "position_sizing": position_sizing.details,
                "risk_guard": serialize_risk_evaluation_for_log(
                    risk_guard_decision
                ),
            },
        )
        mark_buy_blocked(state, symbol)
        record_cycle_action(
            state,
            action="BUY_BLOCKED_RISK_GUARD",
            reason=risk_guard_decision.reason,
            order_side="BUY",
            symbol=symbol,
            qty=recommended_qty,
            selected_symbol=symbol,
        )
        return _finish()

    order_session_status = get_korean_market_session()
    if not order_session_status.order_allowed:
        print("주문 직전 시장 상태를 재확인했습니다.")
        for line in build_market_session_console_lines(order_session_status):
            print(line)
        print(
            f"현재는 주문 가능 세션이 아니므로 주문을 보내지 않습니다. "
            f"({order_session_status.session})"
        )
        log_order_event(
            **sized_log_context,
            action=order_session_status.buy_block_action or "blocked_market_closed",
            result="skipped",
            reason=f"주문 직전 재확인: {order_session_status.reason}",
            raw_response={
                "strategy_details": strategy_details,
                "selection_details": selection_details,
                "position_sizing": position_sizing.details,
                "risk_guard": serialize_risk_evaluation_for_log(
                    risk_guard_decision
                ),
                "order_session_recheck": market_session_status_payload(
                    order_session_status
                ),
            },
        )
        if settings.buy_block_on_blocked_preview:
            mark_buy_attempt(state, symbol)
            add_recent_order(
                state,
                side="BUY",
                symbol=symbol,
                qty=recommended_qty,
                action=order_session_status.buy_block_action
                or "blocked_market_closed",
            )
        mark_buy_blocked(state, symbol)
        record_cycle_action(
            state,
            action="BUY_BLOCKED_ORDER_WINDOW",
            reason=f"주문 직전 재확인: {order_session_status.reason}",
            order_side="BUY",
            symbol=symbol,
            qty=recommended_qty,
            selected_symbol=symbol,
        )
        return _finish()

    failure_already_logged = False

    try:
        if execution_snapshot.orderable_qty < recommended_qty:
            raise RuntimeError("주문 가능 수량이 부족합니다.")

        # W2 price capture: decision/reference price + best quote at submit.
        # No re-quote is fetched before buy_market, so quote_at_submit == reference
        # today; both are stamped additively for forward-compat. NOTE: this is the
        # submit-side half only — real slippage also needs the fill price, which the
        # order ack (order_response: odno, no fill) does not carry; that is deferred
        # to a fill-reconciliation source (W4 / live-shadow), joinable by odno.
        # Isolated so a recording failure can never affect the order path.
        try:
            buy_price_capture = build_price_capture(
                reference_price_krw=planned_execution_snapshot.current_price,
                quote_at_submit=execution_snapshot.current_price,
            )
        except Exception:
            buy_price_capture = {}

        buy_submitted_reason = "주문 API 호출 직전입니다."
        log_order_event(
            **sized_log_context,
            action="order_submitted",
            result="success",
            reason=buy_submitted_reason,
            raw_response={
                "strategy_details": strategy_details,
                "selection_details": selection_details,
                "position_sizing": position_sizing.details,
                "risk_guard": serialize_risk_evaluation_for_log(
                    risk_guard_decision
                ),
                "order_plan": {
                    "current_price_krw": planned_execution_snapshot.current_price,
                    "qty": recommended_qty,
                    "notional_krw": planned_execution_snapshot.expected_notional_krw,
                },
                **buy_price_capture,
            },
        )
        mark_buy_attempt(state, symbol)
        record_buy_attempt_signature(
            state,
            signature=build_buy_attempt_signature(symbol=symbol, qty=recommended_qty),
        )
        add_recent_order(
            state,
            side="BUY",
            symbol=symbol,
            qty=recommended_qty,
            action="order_submitted",
        )
        send_order_slack_notification(
            side="BUY",
            action="order_submitted",
            symbol=symbol,
            qty=recommended_qty,
            status="submitted",
            reason=buy_submitted_reason,
            submitted_price_krw=planned_execution_snapshot.current_price,
        )

        # order_submit은 KIS 측 inquire-order 후속 조회까지 고려해
        # 2-request burst를 reserve 합니다. 단일 호출이지만 바로 뒤 sleep(1.0)
        # + order confirmation 흐름이 동일 window 안에 겹치는 일이 잦아
        # KIS EGW00201을 유발해 왔습니다.
        buy_execution_tail_budget["order_submit_wait_ms"] = (
            wait_for_execution_request_budget(
                api_budget_state,
                now=get_korean_now(),
                phase="order_submit",
                request_cost=2,
                request_reserve=0,
            )
        )
        order_submit_at = get_korean_now()
        if not api_budget_can_request(
            api_budget_state,
            now=order_submit_at,
            request_cost=2,
        ):
            raise RuntimeError("주문 제출 직전 API request 예산이 부족합니다.")
        api_budget_register_requests(
            api_budget_state,
            now=order_submit_at,
            request_count=2,
        )
        time.sleep(1.0)
        order_result = buy_market(symbol=symbol, qty=recommended_qty, token=token)

        if order_result.get("rt_cd") != "0":
            buy_untradable_detected = looks_like_buy_untradable_response(order_result)
            rate_limit_source = (
                rate_limit_source_from_response_body(
                    order_result,
                    source="buy_order",
                )
                or rate_limit_source
            )
            buy_failure_reason = summarize_order_reason(
                order_result,
                "주문 응답 rt_cd가 0이 아니어서 실패로 처리했습니다.",
            )
            log_order_event(
                **sized_log_context,
                action="order_failed",
                result="failed",
                reason=buy_failure_reason,
                raw_response={
                    "strategy_details": strategy_details,
                    "selection_details": selection_details,
                    "position_sizing": position_sizing.details,
                    "order_response": order_result,
                    "buy_untradable_detected": buy_untradable_detected,
                },
            )
            add_recent_order(
                state,
                side="BUY",
                symbol=symbol,
                qty=recommended_qty,
                action="order_failed",
            )
            if buy_untradable_detected:
                mark_buy_untradable_symbol(state, symbol)
            failure_already_logged = True
            record_cycle_action(
                state,
                action="BUY_ORDER_FAILED",
                reason=buy_failure_reason,
                order_side="BUY",
                symbol=symbol,
                qty=recommended_qty,
                selected_symbol=symbol,
            )
            send_order_slack_notification(
                side="BUY",
                action="order_failed",
                symbol=symbol,
                qty=recommended_qty,
                status="failed",
                reason=buy_failure_reason,
                submitted_price_krw=planned_execution_snapshot.current_price,
            )
            _sync_flow_context()
            raise RuntimeError(f"주문 실패: {order_result}")

        buy_success_reason = summarize_order_reason(order_result, "모의 주문이 성공했습니다.")
        log_order_event(
            **sized_log_context,
            action="order_succeeded",
            result="success",
            reason=buy_success_reason,
            raw_response={
                "strategy_details": strategy_details,
                "selection_details": selection_details,
                "position_sizing": position_sizing.details,
                "order_response": order_result,
                **buy_price_capture,
            },
        )
        mark_bought_symbol(state, symbol)

        print("=== 주문 결과 ===")
        print(order_result)
        record_cycle_action(
            state,
            action="BUY_ORDER_SUCCEEDED",
            reason="모의 주문이 성공했습니다.",
            order_side="BUY",
            symbol=symbol,
            qty=recommended_qty,
            selected_symbol=symbol,
        )
        send_order_slack_notification(
            side="BUY",
            action="order_succeeded",
            symbol=symbol,
            qty=recommended_qty,
            status="succeeded",
            reason=buy_success_reason,
            submitted_price_krw=planned_execution_snapshot.current_price,
        )
    except ApiHttpError as exc:
        if looks_like_rate_limit_error(exc):
            # Mark source here so the outer except handler uses it instead of "unknown".
            # Do NOT call _api_budget_note_rate_limit here — the outer handler will call
            # it exactly once when it catches the re-raised exception, preventing a double
            # increment of rate_limit_hits.
            rate_limit_source = "buy_order"
            flow_context["rate_limit_source"] = rate_limit_source
        buy_untradable_detected = looks_like_buy_untradable_response(exc.data)
        buy_failure_reason = summarize_order_reason(exc.data, str(exc))
        log_order_event(
            **sized_log_context,
            action="order_failed",
            result="failed",
            reason=buy_failure_reason,
            raw_response={
                "strategy_details": strategy_details,
                "selection_details": selection_details,
                "position_sizing": position_sizing.details,
                "order_response": exc.data,
                "buy_untradable_detected": buy_untradable_detected,
            },
        )
        add_recent_order(
            state,
            side="BUY",
            symbol=symbol,
            qty=recommended_qty,
            action="order_failed",
        )
        if buy_untradable_detected:
            mark_buy_untradable_symbol(state, symbol)
        record_cycle_action(
            state,
            action="BUY_ORDER_FAILED",
            reason=buy_failure_reason,
            order_side="BUY",
            symbol=symbol,
            qty=recommended_qty,
            selected_symbol=symbol,
        )
        send_order_slack_notification(
            side="BUY",
            action="order_failed",
            symbol=symbol,
            qty=recommended_qty,
            status="failed",
            reason=buy_failure_reason,
            submitted_price_krw=planned_execution_snapshot.current_price,
        )
        _sync_flow_context()
        raise
    except Exception as exc:
        buy_untradable_detected = looks_like_buy_untradable_response(str(exc))
        buy_failure_reason = summarize_order_reason(str(exc), str(exc))
        if not failure_already_logged:
            log_order_event(
                **sized_log_context,
                action="order_failed",
                result="failed",
                reason=buy_failure_reason,
                raw_response={
                    "strategy_details": strategy_details,
                    "selection_details": selection_details,
                    "position_sizing": position_sizing.details,
                    "order_response": str(exc),
                    "buy_untradable_detected": buy_untradable_detected,
                },
            )
            add_recent_order(
                state,
                side="BUY",
                symbol=symbol,
                qty=recommended_qty,
                action="order_failed",
            )
        if buy_untradable_detected:
            mark_buy_untradable_symbol(state, symbol)
        record_cycle_action(
            state,
            action="BUY_ORDER_FAILED",
            reason=str(exc),
            order_side="BUY",
            symbol=symbol,
            qty=recommended_qty,
            selected_symbol=symbol,
        )
        if not failure_already_logged:
            send_order_slack_notification(
                side="BUY",
                action="order_failed",
                symbol=symbol,
                qty=recommended_qty,
                status="failed",
                reason=buy_failure_reason,
                submitted_price_krw=planned_execution_snapshot.current_price,
            )
        _sync_flow_context()
        raise

    return _finish()
