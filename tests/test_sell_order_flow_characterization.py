from __future__ import annotations

import unittest
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from app import main as main_module
from app.auth.token import ApiHttpError
from app.execution import sell_flow as sell_flow_module
from app.core.market_session import MarketSessionStatus
from app.core.time_utils import KOREA_TZ
from app.execution.order_guard import build_sell_cooldown_key
from app.execution.sell_position_sizing import SellPositionSizingResult
from app.market_data.schema import MarketSnapshot
from app.portfolio.schema import PortfolioPosition, PortfolioSnapshot
from app.strategy.sell_decision import SellAnalysisResult, SellDecisionResult


FIXED_NOW = datetime(2026, 5, 27, 10, 15, tzinfo=KOREA_TZ)


def _settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "block_resell_symbols_sold_today": True,
        "allow_one_sell_trigger_per_symbol_per_day": False,
        "enable_sell_cooldown": True,
        "sell_blocked_cooldown_minutes": 30,
        "order_cooldown_minutes": 0,
        "buy_enable_risk_guards": False,
        "sell_daily_max_order_submissions": 99,
        "sell_daily_max_notional_krw": 999_999_999,
        "confirm_buy": "YES",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _state() -> dict[str, object]:
    return {
        "last_cycle_id": "cycle-test",
        "recent_orders": [],
        "sell_triggered_symbols_today": [],
        "symbols_sold_today": [],
        "sell_blocked_symbols_today": [],
        "blocked_sell_symbols_today": [],
        "sell_cooldown_blocked_symbols_today": [],
        "pending_sell_intents_by_symbol": {},
        "last_exit_reason_by_symbol": {},
        "last_exit_at_by_symbol": {},
        "last_exit_price_by_symbol": {},
        "last_exit_qty_by_symbol": {},
        "last_exit_was_full_close_by_symbol": {},
        "last_exit_trigger_context_by_symbol": {},
        "symbols_bought_today": [],
        "buy_attempted_symbols_today": [],
        "buy_blocked_symbols_today": [],
        "blocked_buy_symbols_today": [],
    }


def _session(
    *,
    allowed: bool = True,
    session: str = "REGULAR",
    sell_block_action: str | None = None,
) -> MarketSessionStatus:
    return MarketSessionStatus(
        session=session,
        order_allowed=allowed,
        reason="regular market" if allowed else "market closed",
        buy_block_action=None if allowed else "blocked_market_closed",
        sell_block_action=sell_block_action,
    )


def _analysis(
    *,
    symbol: str = "005930",
    holding_qty: int = 3,
    current_price: int = 80_000,
    trigger: str = "take_profit",
) -> SellAnalysisResult:
    snapshot = MarketSnapshot(
        symbol=symbol,
        current_price=current_price,
        open_price=79_000,
        low_price=78_500,
        prev_day_change_pct=1.2,
    )
    sell_decision = SellDecisionResult(
        should_attempt_sell=True,
        reason=f"{trigger} signal",
        details={"net_pnl_pct": 4.2},
        triggered_rule_name=trigger,
        rule_results=(),
    )
    buy_strategy_result = SimpleNamespace(
        to_log_payload=lambda: {"should_attempt_buy": False}
    )
    return SellAnalysisResult(
        symbol=symbol,
        name="TestCo",
        market_snapshot=snapshot,
        buy_strategy_result=buy_strategy_result,
        sell_decision=sell_decision,
        holding_qty=holding_qty,
        average_cost=70_000,
    )


def _sizing(
    *,
    qty: int = 3,
    current_price: int = 80_000,
    trigger: str = "take_profit",
) -> SellPositionSizingResult:
    notional = qty * current_price
    return SellPositionSizingResult(
        recommended_sell_qty=qty,
        sell_reason=f"{trigger} sizing",
        sell_trigger=trigger,
        sell_fraction=1.0,
        available_holding_qty=qty,
        recommended_notional_krw=notional,
        details={
            "trigger": trigger,
            "available_holding_qty": qty,
            "sell_fraction": 1.0,
            "recommended_sell_qty": qty,
            "recommended_notional_krw": notional,
            "estimated_sell_fee_krw": 10,
            "estimated_sell_tax_krw": 20,
            "estimated_sell_slippage_krw": 30,
            "estimated_net_proceeds_krw": max(0, notional - 60),
        },
    )


def _portfolio(analysis: SellAnalysisResult) -> PortfolioSnapshot:
    position = PortfolioPosition(
        symbol=analysis.symbol,
        name=analysis.name,
        holding_qty=analysis.holding_qty,
        average_cost=analysis.average_cost,
        current_price=analysis.market_snapshot.current_price,
        market_value=analysis.holding_qty * analysis.market_snapshot.current_price,
        gross_pnl=analysis.holding_qty
        * (analysis.market_snapshot.current_price - analysis.average_cost),
        gross_pnl_pct=14.2,
        has_position=True,
    )
    return PortfolioSnapshot(
        positions=(position,),
        cash_total=0,
        cash_orderable=0,
        cash_next_day=0,
        total_evaluation_amount=position.market_value,
    )


def _flow_kwargs(
    *,
    state: dict[str, object] | None = None,
    settings: SimpleNamespace | None = None,
    analysis: SellAnalysisResult | None = None,
    sizing: SellPositionSizingResult | None = None,
    session_status: MarketSessionStatus | None = None,
    flow_context: dict[str, object] | None = None,
    api_budget_state: dict[str, object] | None = None,
) -> dict[str, object]:
    analysis = analysis or _analysis()
    sizing = sizing or _sizing(
        qty=analysis.holding_qty,
        current_price=analysis.market_snapshot.current_price,
        trigger=analysis.sell_decision.triggered_rule_name or "take_profit",
    )
    session_status = session_status or _session()
    return {
        "state": state if state is not None else _state(),
        "settings": settings or _settings(),
        "token": "token-test",
        "portfolio_snapshot": _portfolio(analysis),
        "analysis": analysis,
        "sell_sizing": sizing,
        "sell_log_context": {
            "symbol": analysis.symbol,
            "qty": sizing.recommended_sell_qty,
            "order_type": "market_sell",
            "confirm_buy": "YES",
            "market_open": True,
            "environment": "mock",
            "cycle_id": "cycle-test",
        },
        "sell_raw_response": {
            "source": "sell-flow-characterization",
            "sell_strategy": {"triggered_rule_name": sizing.sell_trigger},
        },
        "market_open": session_status.order_allowed,
        "session_status": session_status,
        "cycle_reason": "sell signal",
        "cycle_action_label": "SELL order",
        "flow_context": flow_context,
        "api_budget_state": api_budget_state,
    }


@contextmanager
def _patched_sell_flow(
    *,
    sell_result: dict[str, object] | BaseException | None = None,
    order_session: MarketSessionStatus | None = None,
    with_api_budget: bool = False,
):
    logs: list[dict[str, object]] = []
    slack: list[dict[str, object]] = []
    trace: list[tuple[str, str]] = []

    def log_order_event(**kwargs: object) -> bool:
        logs.append(kwargs)
        trace.append(("log", str(kwargs.get("action"))))
        return True

    def send_order_slack_notification(**kwargs: object) -> None:
        slack.append(kwargs)
        trace.append(("slack", str(kwargs.get("action"))))

    def sell_market(**kwargs: object) -> dict[str, object]:
        trace.append(("broker", "sell_market"))
        if isinstance(sell_result, BaseException):
            raise sell_result
        if sell_result is None:
            return {"rt_cd": "0", "msg_cd": "0000", "msg1": "accepted"}
        return sell_result

    risk_decision = SimpleNamespace(
        allowed=True,
        action=None,
        reason="risk ok",
        evaluated=True,
        details={},
        guard_results=(),
    )
    order_session = order_session or _session()
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(sell_flow_module, "log_order_event", side_effect=log_order_event)
        )
        stack.enter_context(
            mock.patch.object(
                main_module,
                "_send_order_slack_notification",
                side_effect=send_order_slack_notification,
            )
        )
        stack.enter_context(
            mock.patch.object(sell_flow_module, "sell_market", side_effect=sell_market)
        )
        stack.enter_context(
            mock.patch.object(sell_flow_module, "get_korean_market_session", return_value=order_session)
        )
        stack.enter_context(
            mock.patch.object(sell_flow_module, "evaluate_sell_risk_guards", return_value=risk_decision)
        )
        stack.enter_context(
            mock.patch.object(
                sell_flow_module,
                "serialize_risk_evaluation_for_log",
                return_value={"allowed": True, "reason": "risk ok"},
            )
        )
        stack.enter_context(
            mock.patch.object(sell_flow_module, "build_risk_guard_console_lines", return_value=[])
        )
        stack.enter_context(
            mock.patch.object(sell_flow_module, "build_market_session_console_lines", return_value=[])
        )
        stack.enter_context(
            mock.patch.object(main_module, "_print_sell_preview", return_value=None)
        )
        stack.enter_context(
            mock.patch.object(sell_flow_module, "print_cycle_conclusion", return_value=None)
        )
        stack.enter_context(mock.patch.object(sell_flow_module.time, "sleep", return_value=None))
        stack.enter_context(mock.patch.object(sell_flow_module, "get_korean_now", return_value=FIXED_NOW))
        if with_api_budget:

            def wait_for_budget(*args: object, **kwargs: object) -> float:
                trace.append(("budget", "wait_for_execution_request_budget"))
                return 12.3

            def can_request(*args: object, **kwargs: object) -> bool:
                trace.append(("budget", "api_budget_can_request"))
                return True

            def register_requests(*args: object, **kwargs: object) -> None:
                trace.append(("budget", "api_budget_register_requests"))

            stack.enter_context(
                mock.patch.object(
                    main_module,
                    "_wait_for_execution_request_budget",
                    side_effect=wait_for_budget,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    sell_flow_module,
                    "api_budget_can_request",
                    side_effect=can_request,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    sell_flow_module,
                    "api_budget_register_requests",
                    side_effect=register_requests,
                )
            )
        yield SimpleNamespace(logs=logs, slack=slack, trace=trace)


class SellOrderFlowCharacterizationTests(unittest.TestCase):
    def test_success_path_logs_submitted_before_broker_then_success_and_state(self) -> None:
        state = _state()
        flow_context: dict[str, object] = {}
        kwargs = _flow_kwargs(
            state=state,
            flow_context=flow_context,
            api_budget_state={"request_timestamps": []},
        )

        with _patched_sell_flow(with_api_budget=True) as records:
            result = main_module._run_sell_order_flow(**kwargs)

        self.assertTrue(result)
        self.assertEqual([entry["action"] for entry in records.logs], [
            "sell_order_submitted",
            "sell_order_succeeded",
        ])
        self.assertEqual([entry["action"] for entry in records.slack], [
            "sell_order_submitted",
            "sell_order_succeeded",
        ])
        self.assertEqual(
            records.trace,
            [
                ("log", "sell_order_submitted"),
                ("slack", "sell_order_submitted"),
                ("budget", "wait_for_execution_request_budget"),
                ("budget", "api_budget_can_request"),
                ("budget", "api_budget_register_requests"),
                ("broker", "sell_market"),
                ("log", "sell_order_succeeded"),
                ("slack", "sell_order_succeeded"),
            ],
        )
        self.assertEqual(flow_context["sell_order_submit_wait_ms"], 12.3)
        self.assertEqual(state["last_action"], "SELL_ORDER_SUCCEEDED")
        self.assertEqual(state["last_final_action"], "SELL_ORDER_SUCCEEDED")
        self.assertEqual(state["last_order_side"], "SELL")
        self.assertEqual(state["last_sell_symbol"], "005930")
        self.assertEqual(state["last_sell_qty"], 3)
        self.assertEqual(state["sell_triggered_symbols_today"], ["005930"])
        self.assertEqual(state["symbols_sold_today"], ["005930"])
        self.assertEqual(
            state["last_sell_attempt_signature"],
            build_sell_cooldown_key(symbol="005930", trigger="take_profit"),
        )
        # `trigger` is recorded so the stale-intent TTL can apply the shorter
        # emergency ceiling to stop_loss exits (docs/todo_20260710.md §A-4 / T2).
        self.assertEqual(
            state["pending_sell_intents_by_symbol"],
            {"005930": {"qty": 3, "submitted_at": mock.ANY, "trigger": "take_profit"}},
        )
        self.assertEqual(state["symbols_bought_today"], [])
        self.assertEqual(state["buy_attempted_symbols_today"], [])
        self.assertEqual(state["buy_blocked_symbols_today"], [])
        self.assertEqual(state["blocked_buy_symbols_today"], [])
        self.assertEqual(
            [entry["action"] for entry in state["recent_orders"]],
            ["sell_order_submitted", "sell_order_succeeded"],
        )
        self.assertEqual(records.logs[1]["raw_response"]["order_response"]["rt_cd"], "0")

    def test_success_path_captures_reference_and_quote_prices(self) -> None:
        kwargs = _flow_kwargs(api_budget_state={"request_timestamps": []})

        with _patched_sell_flow(with_api_budget=True) as records:
            main_module._run_sell_order_flow(**kwargs)

        submitted = records.logs[0]["raw_response"]
        succeeded = records.logs[1]["raw_response"]
        for raw in (submitted, succeeded):
            self.assertEqual(raw["reference_price_krw"], 80000.0)
            self.assertEqual(raw["quote_at_submit"], 80000.0)

    def test_rt_cd_failure_logs_failure_not_success_and_not_mark_sold(self) -> None:
        state = _state()
        failure = {"rt_cd": "1", "msg_cd": "APBK9999", "msg1": "rejected"}
        kwargs = _flow_kwargs(state=state)

        with _patched_sell_flow(sell_result=failure) as records:
            with self.assertRaises(RuntimeError) as raised:
                main_module._run_sell_order_flow(**kwargs)

        self.assertIn("APBK9999", str(raised.exception))
        self.assertEqual([entry["action"] for entry in records.logs], [
            "sell_order_submitted",
            "sell_order_failed",
        ])
        self.assertEqual([entry["action"] for entry in records.slack], [
            "sell_order_submitted",
            "sell_order_failed",
        ])
        self.assertEqual(state["last_action"], "SELL_ORDER_FAILED")
        self.assertEqual(state["symbols_sold_today"], [])
        self.assertNotIn("005930", state["last_exit_reason_by_symbol"])
        self.assertNotIn("failure_category", records.logs[1]["raw_response"])
        self.assertEqual(records.logs[1]["raw_response"]["order_response"], failure)

    def test_no_position_rt_cd_failure_preserves_failure_category_and_response(self) -> None:
        state = _state()
        failure = {"rt_cd": "1", "msg_cd": "40240000", "msg1": "no position"}
        kwargs = _flow_kwargs(state=state)

        with _patched_sell_flow(sell_result=failure) as records:
            with self.assertRaises(RuntimeError) as raised:
                main_module._run_sell_order_flow(**kwargs)

        self.assertIn("40240000", str(raised.exception))
        raw_response = records.logs[1]["raw_response"]
        self.assertEqual(raw_response["failure_category"], "no_position_on_sell")
        self.assertEqual(raw_response["order_response"], failure)
        self.assertEqual(state["symbols_sold_today"], [])

    def test_api_http_error_logs_failure_and_reraises_without_success_mutation(self) -> None:
        state = _state()
        error_body = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "rate limit"}
        api_error = ApiHttpError("http failed", status_code=429, data=error_body)
        kwargs = _flow_kwargs(state=state)

        with _patched_sell_flow(sell_result=api_error) as records:
            with self.assertRaises(ApiHttpError):
                main_module._run_sell_order_flow(**kwargs)

        self.assertEqual([entry["action"] for entry in records.logs], [
            "sell_order_submitted",
            "sell_order_failed",
        ])
        self.assertEqual(records.logs[1]["raw_response"]["order_response"], error_body)
        self.assertNotIn("failure_category", records.logs[1]["raw_response"])
        self.assertEqual(records.slack[-1]["action"], "sell_order_failed")
        self.assertEqual(records.slack[-1]["status"], "failed")
        self.assertEqual(state["last_action"], "SELL_ORDER_FAILED")
        self.assertEqual(state["symbols_sold_today"], [])
        self.assertNotIn("005930", state["last_exit_reason_by_symbol"])

    def test_generic_exception_logs_failure_and_preserves_string_response(self) -> None:
        state = _state()
        kwargs = _flow_kwargs(state=state)

        with _patched_sell_flow(sell_result=RuntimeError("network down")) as records:
            with self.assertRaisesRegex(RuntimeError, "network down"):
                main_module._run_sell_order_flow(**kwargs)

        self.assertEqual([entry["action"] for entry in records.logs], [
            "sell_order_submitted",
            "sell_order_failed",
        ])
        self.assertEqual(records.logs[1]["raw_response"]["order_response"], "network down")
        self.assertEqual(records.slack[-1]["action"], "sell_order_failed")
        self.assertEqual(state["last_action"], "SELL_ORDER_FAILED")
        self.assertEqual(state["symbols_sold_today"], [])
        self.assertNotIn("005930", state["last_exit_reason_by_symbol"])

    def test_repeated_sell_cooldown_blocks_before_broker_and_uses_qty_free_key(self) -> None:
        state = _state()
        state["last_sell_attempt_signature"] = build_sell_cooldown_key(
            symbol="005930",
            trigger="take_profit",
        )
        state["last_sell_attempt_at"] = (FIXED_NOW - timedelta(minutes=5)).isoformat()
        analysis = _analysis(holding_qty=3, trigger="take_profit")
        sizing = _sizing(qty=1, trigger="take_profit")
        kwargs = _flow_kwargs(state=state, analysis=analysis, sizing=sizing)

        with (
            mock.patch("app.execution.order_guard.get_korean_now", return_value=FIXED_NOW),
            _patched_sell_flow() as records,
        ):
            result = main_module._run_sell_order_flow(**kwargs)

        self.assertTrue(result)
        self.assertEqual([entry["action"] for entry in records.logs], ["blocked_sell_cooldown"])
        self.assertEqual(records.slack, [])
        self.assertNotIn(("broker", "sell_market"), records.trace)
        self.assertEqual(state["sell_cooldown_blocked_symbols_today"], ["005930"])
        self.assertEqual(state["sell_blocked_symbols_today"], ["005930"])
        self.assertEqual(state["blocked_sell_symbols_today"], ["005930"])
        self.assertEqual(state["last_action"], "SELL_SKIPPED_COOLDOWN")
        self.assertEqual(state["symbols_sold_today"], [])

    def test_stop_loss_emergency_trigger_bypasses_repeated_sell_cooldown(self) -> None:
        state = _state()
        state["last_sell_attempt_signature"] = build_sell_cooldown_key(
            symbol="005930",
            trigger="stop_loss",
        )
        state["last_sell_attempt_at"] = (FIXED_NOW - timedelta(minutes=5)).isoformat()
        analysis = _analysis(holding_qty=3, trigger="stop_loss")
        sizing = _sizing(qty=3, trigger="stop_loss")
        kwargs = _flow_kwargs(state=state, analysis=analysis, sizing=sizing)

        with (
            mock.patch("app.execution.order_guard.get_korean_now", return_value=FIXED_NOW),
            _patched_sell_flow() as records,
        ):
            result = main_module._run_sell_order_flow(**kwargs)

        self.assertTrue(result)
        self.assertIn(("broker", "sell_market"), records.trace)
        self.assertEqual([entry["action"] for entry in records.logs], [
            "sell_order_submitted",
            "sell_order_succeeded",
        ])
        self.assertEqual(state["last_action"], "SELL_ORDER_SUCCEEDED")

    def test_order_session_recheck_blocks_before_submitted_log_and_broker(self) -> None:
        state = _state()
        kwargs = _flow_kwargs(state=state)
        recheck = _session(
            allowed=False,
            session="AFTER_MARKET",
            sell_block_action="blocked_sell_after_market",
        )

        with _patched_sell_flow(order_session=recheck) as records:
            result = main_module._run_sell_order_flow(**kwargs)

        self.assertTrue(result)
        self.assertEqual([entry["action"] for entry in records.logs], ["blocked_sell_after_market"])
        self.assertEqual(records.slack, [])
        self.assertNotIn(("broker", "sell_market"), records.trace)
        raw_response = records.logs[0]["raw_response"]
        self.assertEqual(raw_response["risk_guard"], {"allowed": True, "reason": "risk ok"})
        self.assertEqual(raw_response["order_session_recheck"]["session"], "AFTER_MARKET")
        self.assertEqual(state["last_action"], "SELL_BLOCKED_AFTER_MARKET")
        self.assertEqual(state["symbols_sold_today"], [])


if __name__ == "__main__":
    unittest.main()
