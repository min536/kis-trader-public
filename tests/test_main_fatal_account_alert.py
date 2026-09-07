from __future__ import annotations

import unittest
from unittest import mock

from app import main as main_module
from app.notifications.account_alerts import reset_fatal_account_latch


class _CapturingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def send(self, event_type, message, *, symbol=None, details=None):
        self.calls.append((event_type, message, dict(details or {})))


class MainFatalAccountAlertTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_fatal_account_latch()
        self.addCleanup(reset_fatal_account_latch)

    def _fire(self, notifier, *, action="sell_order_failed", reason="x (40910000)"):
        with mock.patch.object(
            main_module, "_get_slack_notifier", return_value=notifier
        ):
            main_module._send_order_slack_notification(
                side="SELL",
                action=action,
                symbol="082740",
                qty=1,
                status="failed",
                reason=reason,
            )

    def test_fatal_account_failure_emits_exactly_one_account_blocked_event(self) -> None:
        notifier = _CapturingNotifier()
        self._fire(notifier)

        event_types = [call[0] for call in notifier.calls]
        # The normal order_rejected send plus exactly one account.blocked.
        self.assertIn("account.blocked", event_types)
        self.assertEqual(event_types.count("account.blocked"), 1)

    def test_second_fatal_failure_same_session_does_not_re_emit(self) -> None:
        notifier = _CapturingNotifier()
        self._fire(notifier)
        self._fire(notifier)

        event_types = [call[0] for call in notifier.calls]
        self.assertEqual(event_types.count("account.blocked"), 1)

    def test_non_fatal_failure_emits_no_account_blocked_event(self) -> None:
        notifier = _CapturingNotifier()
        self._fire(notifier, reason="일반 실패 (40600000)")

        event_types = [call[0] for call in notifier.calls]
        self.assertNotIn("account.blocked", event_types)

    def test_wrapper_swallows_classifier_exception(self) -> None:
        notifier = _CapturingNotifier()
        with (
            mock.patch.object(
                main_module,
                "classify_order_rejection",
                side_effect=RuntimeError("classifier boom"),
            ),
            mock.patch.object(
                main_module, "_get_slack_notifier", return_value=notifier
            ),
        ):
            # Must NOT raise.
            main_module._send_order_slack_notification(
                side="SELL",
                action="sell_order_failed",
                symbol="082740",
                qty=1,
                status="failed",
                reason="x (40910000)",
            )

    def test_wrapper_swallows_alert_emitter_exception(self) -> None:
        notifier = _CapturingNotifier()
        with (
            mock.patch.object(
                main_module,
                "maybe_emit_fatal_account_alert",
                side_effect=RuntimeError("alert boom"),
            ),
            mock.patch.object(
                main_module, "_get_slack_notifier", return_value=notifier
            ),
        ):
            # Must NOT raise.
            main_module._send_order_slack_notification(
                side="SELL",
                action="sell_order_failed",
                symbol="082740",
                qty=1,
                status="failed",
                reason="x (40910000)",
            )


if __name__ == "__main__":
    unittest.main()
