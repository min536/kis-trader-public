"""S5(O1): order_log_integrity fail-closed 차단 시 세션-dedup 운영자 알림.

docs/order_log_line_limit_design_20260707.md §O1. account.blocked 원샷 알림
(app/notifications/account_alerts.py)의 관용구를 미러.
"""

from __future__ import annotations

import unittest

from app.notifications.account_alerts import (
    maybe_emit_order_log_integrity_alert,
    reset_order_log_integrity_latch,
)


class OrderLogIntegrityAlertTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_order_log_integrity_latch()

    def tearDown(self) -> None:
        reset_order_log_integrity_latch()

    def test_emits_once_then_latches(self) -> None:
        calls: list[tuple] = []

        def notify(event_type, message, **kwargs):
            calls.append((event_type, message, kwargs))

        first = maybe_emit_order_log_integrity_alert(
            error_code="order_log_too_large",
            side="BUY",
            account_signature="sig1",
            notify=notify,
        )
        second = maybe_emit_order_log_integrity_alert(
            error_code="order_log_too_large",
            side="BUY",
            account_signature="sig1",
            notify=notify,
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "order_log.untrusted")
        self.assertIn("order_log_too_large", calls[0][1])

    def test_distinct_account_signatures_each_emit(self) -> None:
        calls: list = []

        def notify(*args, **kwargs):
            calls.append(args)

        maybe_emit_order_log_integrity_alert(
            error_code="x", side="BUY", account_signature="a", notify=notify
        )
        maybe_emit_order_log_integrity_alert(
            error_code="x", side="BUY", account_signature="b", notify=notify
        )
        self.assertEqual(len(calls), 2)

    def test_notifier_exception_is_swallowed_and_not_latched(self) -> None:
        def boom(*args, **kwargs):
            raise RuntimeError("net down")

        result = maybe_emit_order_log_integrity_alert(
            error_code="x", side="BUY", account_signature="a", notify=boom
        )
        self.assertFalse(result)

        # 실패가 latch를 걸지 않았으므로, 이후 정상 notify는 여전히 발화한다.
        calls: list = []
        ok = maybe_emit_order_log_integrity_alert(
            error_code="x",
            side="BUY",
            account_signature="a",
            notify=lambda *a, **k: calls.append(a),
        )
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
