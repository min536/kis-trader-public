"""Sell selftest / sell-test-mode console flows.

Test-mode-only paths: the guard selftest replays a canned stop-loss
scenario through the sell order guard twice (expecting the second pass
to cooldown-skip), and the sell test cycle prints a full sell decision
preview without ever submitting an order. Bodies moved verbatim from
app/main.py (R1 core slimming).
"""

from __future__ import annotations

from app.core.order_log import log_order_event
from app.execution import calculate_sell_position_sizing
from app.execution.order_guard import (
    build_sell_cooldown_key,
    evaluate_sell_order_guard,
)
from app.core.formatters import format_krw, format_qty
from app.core.market_session import (
    build_market_session_console_lines,
    get_korean_market_session,
)
from app.reporting.console import print_sell_decision, print_sell_preview
from app.reporting.runtime_snapshots import (
    print_cycle_conclusion,
    print_last_action,
    record_cycle_action,
)
from app.runtime_state import record_sell_attempt_signature
from app.strategy.sell_decision import build_sell_analysis
from app.strategy.sell_test_scenarios import build_sell_test_scenario


def sell_test_active(settings) -> bool:
    return settings.enable_sell_test_scenarios and settings.sell_test_mode != "off"


def print_test_mode(settings) -> None:
    if not sell_test_active(settings) and not settings.enable_sell_guard_selftest:
        return

    print("=== 테스트 모드 ===")
    if sell_test_active(settings):
        print(f"sell_test_mode={settings.sell_test_mode}")
    if settings.enable_sell_guard_selftest:
        print("sell_guard_selftest=on")
    print("실제 주문은 전송하지 않습니다.")
    print()


def run_sell_guard_selftest(settings) -> None:
    scenario = build_sell_test_scenario("stop_loss")
    if scenario is None:
        return

    print("=== SELL 가드 자가검증 ===")
    print("첫 번째 동일 SELL 신호는 통과, 두 번째 동일 SELL 신호는 cooldown skip 이어야 합니다.")
    print()

    state: dict = {
        "recent_orders": [],
        "symbols_sold_today": [],
        "sell_triggered_symbols_today": [],
        "last_sell_attempt_signature": None,
        "last_sell_attempt_at": None,
    }
    position = scenario.portfolio_snapshot.held_positions[0]
    analysis = build_sell_analysis(
        symbol=position.symbol,
        holding_qty=position.holding_qty,
        average_cost=position.average_cost,
        market_snapshot=scenario.market_snapshot,
        portfolio_snapshot=scenario.portfolio_snapshot,
        settings=settings,
    )
    sell_sizing = calculate_sell_position_sizing(
        trigger=analysis.sell_decision.triggered_rule_name,
        holding_qty=analysis.holding_qty,
        current_price=analysis.market_snapshot.current_price,
        settings=settings,
    )
    sell_log_context = {
        "symbol": analysis.symbol,
        "qty": sell_sizing.recommended_sell_qty,
        "order_type": "market_sell",
        "confirm_buy": settings.confirm_buy,
        "market_open": False,
    }
    sell_raw_response = {
        "sell_strategy_details": analysis.sell_decision.to_log_payload(),
        "buy_strategy_details": analysis.buy_strategy_result.to_log_payload(),
        "sell_position_sizing": sell_sizing.details,
        "trigger": sell_sizing.sell_trigger,
        "recommended_sell_qty": sell_sizing.recommended_sell_qty,
        "sell_plan": {
            "current_price_krw": analysis.market_snapshot.current_price,
            "qty": sell_sizing.recommended_sell_qty,
            "notional_krw": sell_sizing.recommended_notional_krw,
            "estimated_sell_fee_krw": sell_sizing.details["estimated_sell_fee_krw"],
            "estimated_sell_tax_krw": sell_sizing.details["estimated_sell_tax_krw"],
            "estimated_sell_slippage_krw": sell_sizing.details[
                "estimated_sell_slippage_krw"
            ],
            "estimated_net_proceeds_krw": sell_sizing.details[
                "estimated_net_proceeds_krw"
            ],
        },
        "sell_guard_selftest": True,
    }
    signature = build_sell_cooldown_key(
        symbol=analysis.symbol,
        trigger=sell_sizing.sell_trigger,
    )

    first_guard = evaluate_sell_order_guard(
        state=state,
        symbol=analysis.symbol,
        holding_qty=analysis.holding_qty,
        qty=sell_sizing.recommended_sell_qty,
        block_resell_symbols_sold_today=settings.block_resell_symbols_sold_today,
        allow_one_sell_trigger_per_symbol_per_day=False,
        blocked_cooldown_minutes=settings.sell_blocked_cooldown_minutes,
        order_cooldown_minutes=settings.order_cooldown_minutes,
        trigger=analysis.sell_decision.triggered_rule_name,
    )
    print(
        f"1차 검증: {'PASS' if first_guard.allowed else 'FAIL'} | "
        f"symbol={analysis.display_name} | trigger={sell_sizing.sell_trigger} | "
        f"sell_qty={sell_sizing.recommended_sell_qty}"
    )

    record_sell_attempt_signature(state, signature=signature)

    second_guard = evaluate_sell_order_guard(
        state=state,
        symbol=analysis.symbol,
        holding_qty=analysis.holding_qty,
        qty=sell_sizing.recommended_sell_qty,
        block_resell_symbols_sold_today=settings.block_resell_symbols_sold_today,
        allow_one_sell_trigger_per_symbol_per_day=False,
        blocked_cooldown_minutes=settings.sell_blocked_cooldown_minutes,
        order_cooldown_minutes=settings.order_cooldown_minutes,
        trigger=analysis.sell_decision.triggered_rule_name,
        # The canned scenario is stop_loss, but this selftest exists to verify
        # the cooldown-skip branch; production stop_loss emergency bypass stays
        # covered by order_guard tests.
        emergency_triggers=frozenset(),
    )
    print(
        f"2차 검증: {'PASS' if second_guard.allowed else 'COOLDOWN_SKIP'} | "
        f"reason={second_guard.reason}"
    )
    if not second_guard.allowed and second_guard.is_cooldown:
        print("주문 가드: SELL cooldown skip")
        print_cycle_conclusion(
            side="HOLD",
            display_name=analysis.display_name,
            reason="SELL cooldown guard 적용",
            planned_qty=0,
        )
        log_order_event(
            **sell_log_context,
            action=second_guard.action or "blocked_sell_cooldown",
            result="skipped",
            reason=second_guard.reason,
            raw_response=sell_raw_response,
            environment="mock_test",
        )
        print_last_action("SELL_SKIPPED_COOLDOWN")
    print()


def run_sell_test_cycle(settings, state: dict) -> None:
    sell_test_scenario = build_sell_test_scenario(settings.sell_test_mode)
    if sell_test_scenario is None:
        return

    session_status = get_korean_market_session()
    market_open = session_status.order_allowed
    portfolio_snapshot = sell_test_scenario.portfolio_snapshot
    position = portfolio_snapshot.held_positions[0]

    print("=== 매도 테스트 모드 ===")
    print(f"scenario={sell_test_scenario.mode}")
    print(sell_test_scenario.description)
    print()
    for line in build_market_session_console_lines(session_status):
        print(line)
    print()

    print("=== 보유 종목 ===")
    print(
        f"{position.symbol} | {format_qty(position.holding_qty)} | "
        f"평균단가 {format_krw(position.average_cost)}"
    )
    print()

    sell_analysis = build_sell_analysis(
        symbol=position.symbol,
        holding_qty=position.holding_qty,
        average_cost=position.average_cost,
        market_snapshot=sell_test_scenario.market_snapshot,
        portfolio_snapshot=portfolio_snapshot,
        settings=settings,
    )
    sell_analysis_results = (sell_analysis,)
    print_sell_decision(sell_analysis_results, settings=settings)

    if settings.run_mode == "scan_only":
        print_cycle_conclusion(
            side="SELL",
            display_name=sell_analysis.display_name,
            reason=sell_analysis.sell_decision.triggered_rule_name or "테스트 시나리오",
            planned_qty=calculate_sell_position_sizing(
                trigger=sell_analysis.sell_decision.triggered_rule_name,
                holding_qty=sell_analysis.holding_qty,
                current_price=sell_analysis.market_snapshot.current_price,
                settings=settings,
            ).recommended_sell_qty,
        )
        record_cycle_action(
            state,
            action="SELL_TEST_SCAN_ONLY",
            reason="scan_only 모드의 매도 테스트 시나리오를 출력했습니다.",
            order_side="SELL",
            symbol=sell_analysis.symbol,
            qty=0,
            selected_symbol=sell_analysis.symbol,
        )
        print("scan_only 모드에서는 매도 테스트 판단만 확인하고 종료합니다.")
        print()
        return

    sell_sizing = calculate_sell_position_sizing(
        trigger=sell_analysis.sell_decision.triggered_rule_name,
        holding_qty=sell_analysis.holding_qty,
        current_price=sell_analysis.market_snapshot.current_price,
        settings=settings,
    )
    sell_log_context = {
        "symbol": sell_analysis.symbol,
        "qty": sell_sizing.recommended_sell_qty,
        "order_type": "market_sell",
        "confirm_buy": settings.confirm_buy,
        "market_open": market_open,
    }
    sell_raw_response = {
        "sell_strategy_details": sell_analysis.sell_decision.to_log_payload(),
        "buy_strategy_details": sell_analysis.buy_strategy_result.to_log_payload(),
        "sell_position_sizing": sell_sizing.details,
        "trigger": sell_sizing.sell_trigger,
        "recommended_sell_qty": sell_sizing.recommended_sell_qty,
        "sell_test_mode": settings.sell_test_mode,
        "sell_plan": {
            "current_price_krw": sell_analysis.market_snapshot.current_price,
            "qty": sell_sizing.recommended_sell_qty,
            "notional_krw": sell_sizing.recommended_notional_krw,
            "estimated_sell_fee_krw": sell_sizing.details["estimated_sell_fee_krw"],
            "estimated_sell_tax_krw": sell_sizing.details["estimated_sell_tax_krw"],
            "estimated_sell_slippage_krw": sell_sizing.details[
                "estimated_sell_slippage_krw"
            ],
            "estimated_net_proceeds_krw": sell_sizing.details[
                "estimated_net_proceeds_krw"
            ],
        },
    }

    if sell_analysis.sell_decision.should_attempt_sell:
        print_sell_preview(sell_analysis, sell_sizing)
        print_cycle_conclusion(
            side="SELL",
            display_name=sell_analysis.display_name,
            reason=sell_sizing.sell_trigger or "-",
            planned_qty=sell_sizing.recommended_sell_qty,
        )
        if not market_open:
            print(f"현재는 주문 가능 세션이 아니므로 매도 검토를 주문으로 보내지 않습니다. ({session_status.session})")
            log_order_event(
                **sell_log_context,
                action=session_status.sell_block_action or "blocked_sell_market_closed",
                result="skipped",
                reason=session_status.reason,
                raw_response=sell_raw_response,
                environment="mock_test",
            )
            print_last_action(f"SELL_BLOCKED_{session_status.session}")
            return

        print("SELL_TEST_MODE 검증 경로이므로 실제 매도 주문은 보내지 않고 미리보기만 기록합니다.")
        log_order_event(
            **sell_log_context,
            action="sell_preview_only",
            result="success",
            reason=sell_sizing.sell_reason,
            raw_response=sell_raw_response,
            environment="mock_test",
        )
        print_last_action("SELL_TEST_PREVIEW_ONLY")
        return

    print("매도 테스트 결과: 현재 시나리오에서는 매도하지 않고 보유 유지입니다.")
    print()
    print_cycle_conclusion(
        side="HOLD",
        display_name=sell_analysis.display_name,
        reason="매도 조건 미충족",
        planned_qty=0,
    )
    log_order_event(
        **sell_log_context,
        action="blocked_sell_strategy_rejected",
        result="skipped",
        reason=sell_analysis.sell_decision.reason,
        raw_response=sell_raw_response,
        environment="mock_test",
    )
    print_last_action("SELL_BLOCKED_STRATEGY_REJECTED")
