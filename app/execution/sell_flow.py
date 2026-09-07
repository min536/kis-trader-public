"""SELL execution flow.

Extracted from app.main Stage 3a-1.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.auth.token import ApiHttpError
from app.core.error_classification import (
    looks_like_no_position_sell_response,
)
from app.core.market_session import (
    build_market_session_console_lines,
    get_korean_market_session,
)
from app.core.order_log import (
    build_price_capture,
    log_order_event,
    summarize_order_reason,
)
from app.core.runtime_budget import (
    api_budget_can_request,
    api_budget_register_requests,
)
from app.core.time_utils import get_korean_now
from app.domestic_stock.order import sell_market
from app.execution.order_guard import (
    build_sell_cooldown_key,
    OrderGuardResult,
    evaluate_sell_order_guard,
)
from app.execution.schema import ExecutionSnapshot
from app.notifications.main_runtime_hooks import (
    market_session_status_payload,
)
from app.reporting.runtime_snapshots import (
    print_cycle_conclusion,
    record_cycle_action,
)
from app.risk.guards import (
    RiskGuardInput,
    build_risk_guard_console_lines,
    evaluate_sell_risk_guards,
    serialize_risk_evaluation_for_log,
)
from app.runtime_state import (
    add_recent_order,
    mark_rebalance_sell_submission,
    mark_sell_blocked,
    mark_sell_cooldown_blocked,
    mark_sell_intent_submitted,
    mark_sell_trigger,
    mark_sold_symbol,
    record_symbol_exit,
    resolve_sell_intent,
)
from app.scanner.runtime_scan import resolve_sell_exit_reason
from app.strategy.sell_decision import SellAnalysisResult


def _with_sell_guard_details(
    raw_response: dict,
    sell_guard: OrderGuardResult,
) -> dict:
    """Attach the guard's arithmetic to a blocked-SELL order-log record.

    A bare `blocked_sell_no_sellable_residual` label cannot be triaged after the
    fact — the reader needs holding_qty vs pending_intent_qty and the submit
    stamp to tell an in-flight reservation from a stranded one.
    """
    if not sell_guard.details:
        return raw_response
    return {**raw_response, "sell_guard": dict(sell_guard.details)}


def _build_sell_execution_snapshot(
    *,
    symbol: str,
    current_price: int,
    holding_qty: int,
    qty: int,
) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        symbol=symbol,
        orderable_cash=0,
        orderable_qty=holding_qty,
        current_price=current_price,
        expected_notional_krw=current_price * qty,
    )


def run_sell_order_flow(
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
    print_sell_preview: Callable[..., None] | None = None,
    send_order_slack_notification: Callable[..., None] | None = None,
    wait_for_execution_request_budget: Callable[..., float] | None = None,
) -> bool:
    sell_guard = evaluate_sell_order_guard(
        state=state,
        symbol=analysis.symbol,
        holding_qty=analysis.holding_qty,
        qty=sell_sizing.recommended_sell_qty,
        block_resell_symbols_sold_today=settings.block_resell_symbols_sold_today,
        allow_one_sell_trigger_per_symbol_per_day=settings.allow_one_sell_trigger_per_symbol_per_day,
        blocked_cooldown_minutes=(
            settings.sell_blocked_cooldown_minutes
            if settings.enable_sell_cooldown
            else 0
        ),
        order_cooldown_minutes=settings.order_cooldown_minutes,
        trigger=sell_sizing.sell_trigger,
    )

    if sell_sizing.recommended_sell_qty <= 0:
        print_cycle_conclusion(
            side="HOLD",
            display_name=analysis.display_name,
            reason="추천 매도 수량이 0주입니다.",
            planned_qty=0,
        )
        log_order_event(
            **sell_log_context,
            action="blocked_sell_position_sizing",
            result="skipped",
            reason="추천 매도 수량이 0주라 매도 주문을 보내지 않았습니다.",
            raw_response=sell_raw_response,
        )
        mark_sell_blocked(state, analysis.symbol)
        record_cycle_action(
            state,
            action="SELL_BLOCKED_POSITION_SIZING",
            reason="추천 매도 수량이 0주라 매도 주문을 보내지 않았습니다.",
            order_side="SELL",
            symbol=analysis.symbol,
            qty=0,
            selected_symbol=analysis.symbol,
        )
        return True

    if not sell_guard.allowed and sell_guard.is_cooldown:
        print("주문 가드: SELL cooldown skip")
        print_cycle_conclusion(
            side="HOLD",
            display_name=analysis.display_name,
            reason="SELL cooldown guard 적용",
            planned_qty=0,
        )
        log_order_event(
            **sell_log_context,
            action=sell_guard.action or "blocked_sell_cooldown",
            result="skipped",
            reason=sell_guard.reason,
            raw_response=_with_sell_guard_details(sell_raw_response, sell_guard),
        )
        mark_sell_cooldown_blocked(state, analysis.symbol)
        record_cycle_action(
            state,
            action="SELL_SKIPPED_COOLDOWN",
            reason=sell_guard.reason,
            order_side="SELL",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            selected_symbol=analysis.symbol,
        )
        return True

    if not sell_guard.allowed:
        print(f"매도 중단: {sell_guard.reason}")
        print_cycle_conclusion(
            side="HOLD",
            display_name=analysis.display_name,
            reason="SELL 주문 가드 적용",
            planned_qty=0,
        )
        log_order_event(
            **sell_log_context,
            action=sell_guard.action or "blocked_sell_duplicate_guard",
            result="skipped",
            reason=sell_guard.reason,
            raw_response=_with_sell_guard_details(sell_raw_response, sell_guard),
        )
        mark_sell_blocked(state, analysis.symbol)
        record_cycle_action(
            state,
            action="SELL_BLOCKED_DUPLICATE_GUARD",
            reason=sell_guard.reason,
            order_side="SELL",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            selected_symbol=analysis.symbol,
        )
        return True

    sell_risk_input = RiskGuardInput(
        market_snapshot=analysis.market_snapshot,
        portfolio_snapshot=portfolio_snapshot,
        execution_snapshot=_build_sell_execution_snapshot(
            symbol=analysis.symbol,
            current_price=analysis.market_snapshot.current_price,
            holding_qty=analysis.holding_qty,
            qty=sell_sizing.recommended_sell_qty,
        ),
        side="SELL",
        enabled=settings.buy_enable_risk_guards,
        daily_max_order_submissions=settings.sell_daily_max_order_submissions,
        daily_max_notional_krw=settings.sell_daily_max_notional_krw,
    )
    sell_risk_decision = evaluate_sell_risk_guards(guard_input=sell_risk_input)
    sell_risk_payload = serialize_risk_evaluation_for_log(sell_risk_decision)
    if flow_context is not None:
        flow_context["risk_guard_payload"] = sell_risk_payload
    for line in build_risk_guard_console_lines(sell_risk_decision):
        print(line)
    print()
    if not sell_risk_decision.allowed:
        print(f"리스크 차단: {sell_risk_decision.reason}")
        log_order_event(
            **sell_log_context,
            action=sell_risk_decision.action or "blocked_sell_risk_guard",
            result="skipped",
            reason=sell_risk_decision.reason,
            raw_response={
                **sell_raw_response,
                "risk_guard": sell_risk_payload,
            },
        )
        mark_sell_blocked(state, analysis.symbol)
        record_cycle_action(
            state,
            action="SELL_BLOCKED_RISK_GUARD",
            reason=sell_risk_decision.reason,
            order_side="SELL",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            selected_symbol=analysis.symbol,
        )
        return True

    if print_sell_preview is not None:
        print_sell_preview(analysis, sell_sizing)
    print_cycle_conclusion(
        side="SELL",
        display_name=analysis.display_name,
        reason=cycle_reason,
        planned_qty=sell_sizing.recommended_sell_qty,
    )

    if not market_open:
        print(
            f"현재는 주문 가능 세션이 아니므로 매도 검토를 주문으로 보내지 않습니다. "
            f"({session_status.session})"
        )
        log_order_event(
            **sell_log_context,
            action=session_status.sell_block_action or "blocked_sell_market_closed",
            result="skipped",
            reason=session_status.reason,
            raw_response=sell_raw_response,
        )
        mark_sell_blocked(state, analysis.symbol)
        record_cycle_action(
            state,
            action=f"SELL_BLOCKED_{session_status.session}",
            reason=session_status.reason,
            order_side="SELL",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            selected_symbol=analysis.symbol,
        )
        return True

    if settings.confirm_buy != "YES":
        print("CONFIRM_BUY가 YES가 아니므로 매도 주문은 보내지 않고 미리보기까지만 진행합니다.")
        log_order_event(
            **sell_log_context,
            action="sell_preview_only",
            result="success",
            reason="CONFIRM_BUY가 YES가 아니어서 매도 주문을 보내지 않았습니다.",
            raw_response=sell_raw_response,
        )
        mark_sell_trigger(state, analysis.symbol, record_symbol=False)
        add_recent_order(
            state,
            side="SELL",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            action="sell_preview_only",
        )
        record_cycle_action(
            state,
            action="SELL_PREVIEW",
            reason="CONFIRM_BUY가 YES가 아니어서 매도 주문을 보내지 않았습니다.",
            order_side="SELL",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            selected_symbol=analysis.symbol,
        )
        return True

    order_session_status = get_korean_market_session()
    if not order_session_status.order_allowed:
        print("주문 직전 시장 상태를 재확인했습니다.")
        for line in build_market_session_console_lines(order_session_status):
            print(line)
        print(
            f"현재는 주문 가능 세션이 아니므로 매도 주문을 보내지 않습니다. "
            f"({order_session_status.session})"
        )
        log_order_event(
            **sell_log_context,
            action=(
                order_session_status.sell_block_action
                or "blocked_sell_market_closed"
            ),
            result="skipped",
            reason=f"주문 직전 재확인: {order_session_status.reason}",
            raw_response={
                **sell_raw_response,
                "risk_guard": sell_risk_payload,
                "order_session_recheck": market_session_status_payload(
                    order_session_status
                ),
            },
        )
        mark_sell_blocked(state, analysis.symbol)
        record_cycle_action(
            state,
            action=f"SELL_BLOCKED_{order_session_status.session}",
            reason=f"주문 직전 재확인: {order_session_status.reason}",
            order_side="SELL",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            selected_symbol=analysis.symbol,
        )
        return True

    _notify: Callable[..., None] = send_order_slack_notification or (lambda **kw: None)

    failure_already_logged = False
    try:
        # W2 price capture: decision/reference price + best quote at submit.
        # Sell uses a single market snapshot, so quote_at_submit == reference today;
        # both are stamped additively for forward-compat. NOTE: submit-side half only
        # — real slippage also needs the fill price, which the order ack (order_response:
        # odno, no fill) does not carry; deferred to a fill-reconciliation source
        # (W4 / live-shadow), joinable by odno.
        # Isolated so a recording failure can never affect the order path.
        try:
            sell_price_capture = build_price_capture(
                reference_price_krw=analysis.market_snapshot.current_price,
                quote_at_submit=analysis.market_snapshot.current_price,
            )
        except Exception:
            sell_price_capture = {}

        sell_submitted_reason = f"{cycle_action_label} API 호출 직전입니다."
        log_order_event(
            **sell_log_context,
            action="sell_order_submitted",
            result="success",
            reason=sell_submitted_reason,
            raw_response={
                **sell_raw_response,
                "risk_guard": sell_risk_payload,
                **sell_price_capture,
            },
        )
        mark_sell_trigger(
            state,
            analysis.symbol,
            signature=build_sell_cooldown_key(
                symbol=analysis.symbol,
                trigger=sell_sizing.sell_trigger,
            ),
        )
        mark_sell_intent_submitted(
            state,
            analysis.symbol,
            sell_sizing.recommended_sell_qty,
            trigger=sell_sizing.sell_trigger,
        )
        add_recent_order(
            state,
            side="SELL",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            action="sell_order_submitted",
        )
        _notify(
            side="SELL",
            action="sell_order_submitted",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            status="submitted",
            reason=sell_submitted_reason,
            submitted_price_krw=analysis.market_snapshot.current_price,
        )
        if api_budget_state is not None:
            _wait = wait_for_execution_request_budget or (lambda *a, **kw: 0.0)
            sell_order_wait_ms = _wait(
                api_budget_state,
                now=get_korean_now(),
                phase="sell_order_submit",
                request_cost=2,
                request_reserve=0,
            )
            if flow_context is not None:
                flow_context["sell_order_submit_wait_ms"] = sell_order_wait_ms
            sell_order_submit_at = get_korean_now()
            if not api_budget_can_request(
                api_budget_state,
                now=sell_order_submit_at,
                request_cost=2,
            ):
                raise RuntimeError("매도 주문 제출 직전 API request 예산이 부족합니다.")
            api_budget_register_requests(
                api_budget_state,
                now=sell_order_submit_at,
                request_count=2,
            )
        time.sleep(1.0)
        sell_result = sell_market(
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            token=token,
        )
        if sell_result.get("rt_cd") != "0":
            sell_failure_reason = summarize_order_reason(
                sell_result,
                "매도 주문 응답 rt_cd가 0이 아니어서 실패로 처리했습니다.",
            )
            _sell_fail_response: dict = {**sell_raw_response, "order_response": sell_result}
            if looks_like_no_position_sell_response(sell_result):
                _sell_fail_response["failure_category"] = "no_position_on_sell"
            log_order_event(
                **sell_log_context,
                action="sell_order_failed",
                result="failed",
                reason=sell_failure_reason,
                raw_response=_sell_fail_response,
            )
            add_recent_order(
                state,
                side="SELL",
                symbol=analysis.symbol,
                qty=sell_sizing.recommended_sell_qty,
                action="sell_order_failed",
            )
            failure_already_logged = True
            record_cycle_action(
                state,
                action="SELL_ORDER_FAILED",
                reason=sell_failure_reason,
                order_side="SELL",
                symbol=analysis.symbol,
                qty=sell_sizing.recommended_sell_qty,
                selected_symbol=analysis.symbol,
            )
            _notify(
                side="SELL",
                action="sell_order_failed",
                symbol=analysis.symbol,
                qty=sell_sizing.recommended_sell_qty,
                status="failed",
                reason=sell_failure_reason,
                submitted_price_krw=analysis.market_snapshot.current_price,
            )
            raise RuntimeError(f"매도 주문 실패: {sell_result}")

        sell_success_reason = summarize_order_reason(sell_result, f"{cycle_action_label}가 성공했습니다.")
        log_order_event(
            **sell_log_context,
            action="sell_order_succeeded",
            result="success",
            reason=sell_success_reason,
            raw_response={
                **sell_raw_response,
                "order_response": sell_result,
                **sell_price_capture,
            },
        )
        add_recent_order(
            state,
            side="SELL",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            action="sell_order_succeeded",
        )
        exit_was_full_close = sell_sizing.recommended_sell_qty >= analysis.holding_qty
        record_symbol_exit(
            state,
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            exit_reason=resolve_sell_exit_reason(
                analysis=analysis,
                cycle_reason=cycle_reason,
                is_rebalance=is_rebalance,
            ),
            exit_price=analysis.market_snapshot.current_price,
            was_full_close=exit_was_full_close,
            trigger_context={
                "cycle_reason": cycle_reason,
                "triggered_rule_name": analysis.sell_decision.triggered_rule_name,
                "net_pnl_pct": analysis.sell_decision.details.get("net_pnl_pct"),
                "holding_qty_before": analysis.holding_qty,
                "sell_qty": sell_sizing.recommended_sell_qty,
            },
        )
        if exit_was_full_close:
            mark_sold_symbol(state, analysis.symbol)
        if is_rebalance:
            mark_rebalance_sell_submission(state)
        print("=== 매도 주문 결과 ===")
        print(sell_result)
        record_cycle_action(
            state,
            action="SELL_ORDER_SUCCEEDED",
            reason=f"{cycle_action_label}가 성공했습니다.",
            order_side="SELL",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            selected_symbol=analysis.symbol,
        )
        _notify(
            side="SELL",
            action="sell_order_succeeded",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            status="succeeded",
            reason=sell_success_reason,
            submitted_price_krw=analysis.market_snapshot.current_price,
        )
        return True
    except ApiHttpError as exc:
        resolve_sell_intent(
            state,
            analysis.symbol,
            sell_sizing.recommended_sell_qty,
        )
        sell_failure_reason = summarize_order_reason(exc.data, str(exc))
        _api_exc_response: dict = {**sell_raw_response, "order_response": exc.data}
        if looks_like_no_position_sell_response(exc.data) or looks_like_no_position_sell_response(str(exc)):
            _api_exc_response["failure_category"] = "no_position_on_sell"
        log_order_event(
            **sell_log_context,
            action="sell_order_failed",
            result="failed",
            reason=sell_failure_reason,
            raw_response=_api_exc_response,
        )
        add_recent_order(
            state,
            side="SELL",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            action="sell_order_failed",
        )
        record_cycle_action(
            state,
            action="SELL_ORDER_FAILED",
            reason=sell_failure_reason,
            order_side="SELL",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            selected_symbol=analysis.symbol,
        )
        _notify(
            side="SELL",
            action="sell_order_failed",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            status="failed",
            reason=sell_failure_reason,
            submitted_price_krw=analysis.market_snapshot.current_price,
        )
        raise
    except Exception as exc:
        resolve_sell_intent(
            state,
            analysis.symbol,
            sell_sizing.recommended_sell_qty,
        )
        sell_failure_reason = summarize_order_reason(str(exc), str(exc))
        if not failure_already_logged:
            _generic_exc_response: dict = {**sell_raw_response, "order_response": str(exc)}
            if looks_like_no_position_sell_response(str(exc)):
                _generic_exc_response["failure_category"] = "no_position_on_sell"
            log_order_event(
                **sell_log_context,
                action="sell_order_failed",
                result="failed",
                reason=sell_failure_reason,
                raw_response=_generic_exc_response,
            )
            add_recent_order(
                state,
                side="SELL",
                symbol=analysis.symbol,
                qty=sell_sizing.recommended_sell_qty,
                action="sell_order_failed",
            )
        record_cycle_action(
            state,
            action="SELL_ORDER_FAILED",
            reason=str(exc),
            order_side="SELL",
            symbol=analysis.symbol,
            qty=sell_sizing.recommended_sell_qty,
            selected_symbol=analysis.symbol,
        )
        if not failure_already_logged:
            _notify(
                side="SELL",
                action="sell_order_failed",
                symbol=analysis.symbol,
                qty=sell_sizing.recommended_sell_qty,
                status="failed",
                reason=sell_failure_reason,
                submitted_price_krw=analysis.market_snapshot.current_price,
            )
        raise
