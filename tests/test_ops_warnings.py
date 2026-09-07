"""Operator warnings for OrderGate detached-handler blocks (plan §S5-③)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from app.notifications.ops_warnings import send_detached_handler_warning


class SendDetachedHandlerWarningTests(unittest.TestCase):
    def test_sends_order_gate_blocked_event_with_handler_identity(self) -> None:
        notifier = mock.Mock()
        notifier.send.return_value = SimpleNamespace(status="sent")

        sent = send_detached_handler_warning(
            reason="detached order handler still running: SELL:000660",
            blocked_count=2,
            notifier=notifier,
        )

        self.assertTrue(sent)
        notifier.send.assert_called_once()
        args, kwargs = notifier.send.call_args
        self.assertEqual(args[0], "order_gate_blocked")
        self.assertIn("SELL:000660", args[1])
        self.assertEqual(kwargs.get("details", {}).get("blocked_count"), 2)

    def test_notifier_failure_is_swallowed_and_returns_false(self) -> None:
        notifier = mock.Mock()
        notifier.send.side_effect = RuntimeError("slack down")

        sent = send_detached_handler_warning(
            reason="detached order handler still running: BUY:005930",
            blocked_count=1,
            notifier=notifier,
        )

        self.assertFalse(sent)


if __name__ == "__main__":
    unittest.main()


class OrderGateBlockedEventRoutingTests(unittest.TestCase):
    def test_event_type_routes_to_bottlenecks_channel(self) -> None:
        from app.notifications.slack import (
            BOTTLENECKS_CHANNEL_ENV,
            EVENT_CHANNEL_ENV_BY_TYPE,
        )

        self.assertEqual(
            EVENT_CHANNEL_ENV_BY_TYPE.get("order_gate_blocked"),
            BOTTLENECKS_CHANNEL_ENV,
        )
