from __future__ import annotations

import inspect
import unittest
from unittest import mock

from app import main as main_module
from app.execution import buy_flow as buy_flow_module
from app.execution import sell_flow as sell_flow_module
from app.notifications.order_events import build_order_slack_notification_payload


class MainSlackOrderHookTests(unittest.TestCase):
    def test_order_action_mapping_uses_operator_routed_event_types(self) -> None:
        self.assertEqual(
            main_module._slack_event_type_for_order_action("order_submitted"),
            "order_submitted",
        )
        self.assertEqual(
            main_module._slack_event_type_for_order_action("sell_order_submitted"),
            "order_submitted",
        )
        self.assertEqual(
            main_module._slack_event_type_for_order_action("order_succeeded"),
            "order_accepted",
        )
        self.assertEqual(
            main_module._slack_event_type_for_order_action("sell_order_succeeded"),
            "order_accepted",
        )
        self.assertEqual(
            main_module._slack_event_type_for_order_action("order_failed"),
            "order_rejected",
        )
        self.assertEqual(
            main_module._slack_event_type_for_order_action("sell_order_failed"),
            "order_rejected",
        )

    def test_order_slack_payload_helper_preserves_details_shape(self) -> None:
        payload = build_order_slack_notification_payload(
            side="buy",
            action="order_failed",
            symbol="005930",
            qty=3,
            status="failed",
            reason="mock failure",
            submitted_price_krw=70000,
        )

        self.assertEqual(payload.message, "BUY order failed")
        self.assertEqual(payload.symbol, "005930")
        self.assertEqual(payload.details["side"], "BUY")
        self.assertEqual(payload.details["quantity"], 3)
        self.assertEqual(payload.details["status"], "failed")
        self.assertEqual(payload.details["reason"], "mock failure")
        self.assertEqual(payload.details["submitted_price_krw"], 70000)

    def test_order_slack_notification_swallows_notifier_errors(self) -> None:
        calls: list[tuple[object, ...]] = []

        class FailingNotifier:
            def send(self, *args: object, **kwargs: object) -> None:
                calls.append((args, kwargs))
                raise RuntimeError("slack down")

        with mock.patch.object(
            main_module,
            "_get_slack_notifier",
            return_value=FailingNotifier(),
        ):
            main_module._send_order_slack_notification(
                side="BUY",
                action="order_failed",
                symbol="005930",
                qty=3,
                status="failed",
                reason="mock failure",
                submitted_price_krw=70000,
            )

        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args[0], "order_rejected")
        self.assertEqual(args[1], "BUY order failed")
        self.assertEqual(kwargs["symbol"], "005930")
        self.assertEqual(kwargs["details"]["side"], "BUY")
        self.assertEqual(kwargs["details"]["quantity"], 3)
        self.assertEqual(kwargs["details"]["status"], "failed")
        self.assertEqual(kwargs["details"]["reason"], "mock failure")
        self.assertEqual(kwargs["details"]["submitted_price_krw"], 70000)

    def test_submitted_order_slack_notification_suppressed_by_default(self) -> None:
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch.object(
                main_module,
                "_get_slack_notifier",
                side_effect=AssertionError("submitted Slack notification should be suppressed"),
            ),
        ):
            main_module._send_order_slack_notification(
                side="BUY",
                action="order_submitted",
                symbol="005930",
                qty=3,
                status="submitted",
                submitted_price_krw=70000,
            )

    def test_submitted_order_slack_notification_enabled_by_setting(self) -> None:
        calls: list[tuple[object, ...]] = []

        class CapturingNotifier:
            def send(self, *args: object, **kwargs: object) -> None:
                calls.append((args, kwargs))

        with (
            mock.patch.dict(
                "os.environ",
                {"SLACK_NOTIFY_ORDER_SUBMITTED": "true"},
                clear=True,
            ),
            mock.patch.object(
                main_module,
                "_get_slack_notifier",
                return_value=CapturingNotifier(),
            ),
        ):
            main_module._send_order_slack_notification(
                side="SELL",
                action="sell_order_submitted",
                symbol="005930",
                qty=2,
                status="submitted",
                submitted_price_krw=70000,
            )

        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args[0], "order_submitted")
        self.assertEqual(args[1], "SELL order submitted")
        self.assertEqual(kwargs["symbol"], "005930")
        self.assertEqual(kwargs["details"]["side"], "SELL")
        self.assertEqual(kwargs["details"]["quantity"], 2)
        self.assertEqual(kwargs["details"]["status"], "submitted")

    def test_terminal_order_slack_notifications_still_send_by_default(self) -> None:
        calls: list[tuple[object, ...]] = []

        class CapturingNotifier:
            def send(self, *args: object, **kwargs: object) -> None:
                calls.append((args, kwargs))

        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch.object(
                main_module,
                "_get_slack_notifier",
                return_value=CapturingNotifier(),
            ),
        ):
            main_module._send_order_slack_notification(
                side="BUY",
                action="order_succeeded",
                symbol="005930",
                qty=3,
                status="accepted",
                submitted_price_krw=70000,
            )
            main_module._send_order_slack_notification(
                side="SELL",
                action="sell_order_failed",
                symbol="035720",
                qty=1,
                status="failed",
                reason="mock failure",
            )

        self.assertEqual([call[0][0] for call in calls], ["order_accepted", "order_rejected"])

    def test_buy_and_sell_submit_hooks_are_near_existing_log_points(self) -> None:
        buy_source = inspect.getsource(buy_flow_module.run_buy_order_flow)
        buy_log = buy_source.index('action="order_submitted"')
        buy_hook = buy_source.index(
            'send_order_slack_notification(\n            side="BUY"',
            buy_log,
        )
        buy_order_call = buy_source.index("order_result = buy_market", buy_log)

        sell_source = inspect.getsource(sell_flow_module.run_sell_order_flow)
        sell_log = sell_source.index('action="sell_order_submitted"')
        sell_hook = sell_source.index(
            '_notify(\n            side="SELL"',
            sell_log,
        )
        sell_order_call = sell_source.index("sell_result = sell_market", sell_log)

        self.assertLess(buy_log, buy_hook)
        self.assertLess(buy_hook, buy_order_call)
        self.assertLess(sell_log, sell_hook)
        self.assertLess(sell_hook, sell_order_call)

    def test_buy_and_sell_success_failure_hooks_are_near_existing_log_points(self) -> None:
        def assert_hook_after_log(
            source: str,
            *,
            action: str,
            side: str,
            start: int = 0,
        ) -> None:
            log_marker = source.index(f'action="{action}"', start)
            hook_marker = source.index("send_order_slack_notification(", log_marker)
            hook_block = source[hook_marker : hook_marker + 360]

            self.assertIn(f'side="{side}"', hook_block)
            self.assertIn(f'action="{action}"', hook_block)
            self.assertLess(log_marker, hook_marker)

        buy_source = inspect.getsource(buy_flow_module.run_buy_order_flow)
        buy_start = buy_source.index("symbol = selected_candidate.symbol")
        assert_hook_after_log(
            buy_source,
            action="order_failed",
            side="BUY",
            start=buy_start,
        )
        assert_hook_after_log(
            buy_source,
            action="order_succeeded",
            side="BUY",
            start=buy_start,
        )

        sell_source = inspect.getsource(sell_flow_module.run_sell_order_flow)

        def assert_sell_hook_after_log(
            source: str,
            *,
            action: str,
            side: str,
            start: int = 0,
        ) -> None:
            log_marker = source.index(f'action="{action}"', start)
            hook_marker = source.index("_notify(", log_marker)
            hook_block = source[hook_marker : hook_marker + 360]

            self.assertIn(f'side="{side}"', hook_block)
            self.assertIn(f'action="{action}"', hook_block)
            self.assertLess(log_marker, hook_marker)

        assert_sell_hook_after_log(sell_source, action="sell_order_failed", side="SELL")
        assert_sell_hook_after_log(sell_source, action="sell_order_succeeded", side="SELL")

    def test_runtime_status_snapshot_has_start_session_and_final_writes(self) -> None:
        source = inspect.getsource(main_module.run_cycle)
        start_cycle = source.index("start_cycle(state")
        start_write = source.index("_write_slack_runtime_status_snapshot(", start_cycle)
        # Slice G (docs/main_run_cycle_slimming_plan_20260703.md §3, 국면 6): the
        # session 判定 block — including the ``state["last_market_session"]`` set and
        # its following ``_write_slack_runtime_status_snapshot`` — moved into
        # run_session_gate. run_cycle now shows the start-write, then the gate call,
        # then the finalize call. The session snapshot ordering is asserted against
        # the session_gate module source below.
        session_gate_call = source.index("run_session_gate(", start_write)
        # Slice F: the final daily-summary snapshot write lives in finalize_cycle,
        # which run_cycle invokes from its finally block — the terminal anchor here.
        finalize_call = source.index("finalize_cycle(", session_gate_call)

        self.assertLess(start_cycle, start_write)
        self.assertLess(start_write, session_gate_call)
        self.assertLess(session_gate_call, finalize_call)

    def test_session_gate_source_writes_snapshot_after_session_set(self) -> None:
        from app.runtime.cycle_phases import session_gate as session_gate_module

        source = inspect.getsource(session_gate_module.run_session_gate)
        session_set = source.index('state["last_market_session"] = ctx.session_status.session')
        session_write = source.index("_write_slack_runtime_status_snapshot(", session_set)

        self.assertLess(session_set, session_write)

    def test_finalize_cycle_source_writes_final_snapshot_after_daily_summary(self) -> None:
        from app.runtime.cycle_phases import finalize as finalize_module

        source = inspect.getsource(finalize_module.finalize_cycle)
        daily_summary = source.index("daily_summary = build_daily_summary()")
        final_write = source.index("_write_slack_runtime_status_snapshot(", daily_summary)

        self.assertLess(daily_summary, final_write)

    def test_runtime_status_snapshot_helper_swallows_writer_errors(self) -> None:
        with mock.patch.object(
            main_module,
            "write_slack_status_snapshot",
            side_effect=OSError("disk full"),
        ):
            result = main_module._write_slack_runtime_status_snapshot(
                runtime_state={"last_market_session": "REGULAR"},
                session_status="REGULAR",
            )

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
