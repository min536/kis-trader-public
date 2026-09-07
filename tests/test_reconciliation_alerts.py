"""R-c tests: reconciliation operator Slack notification.

Design: docs/manual_trade_reconciliation_design_20260704.md §4.
- slack.py registers ``reconciliation_drift`` → operator channel (3-line pattern).
- reconciliation_alerts.maybe_notify_manual_trade_suspects: operator alert for
  suspected_manual_* events; whole body in one try/except → return False (C5a).
"""
from __future__ import annotations

import unittest


class SlackReconciliationRoutingTests(unittest.TestCase):
    def test_reconciliation_drift_routes_to_operator_channel(self) -> None:
        from app.notifications.slack import (
            EVENT_CHANNEL_ENV_BY_TYPE,
            OPERATOR_CHANNEL_ENV,
            RECONCILIATION_EVENT_TYPE,
        )
        self.assertEqual(RECONCILIATION_EVENT_TYPE, "reconciliation_drift")
        self.assertEqual(
            EVENT_CHANNEL_ENV_BY_TYPE[RECONCILIATION_EVENT_TYPE],
            OPERATOR_CHANNEL_ENV,
        )

    def test_reconciliation_drift_resolves_to_operator_channel_env(self) -> None:
        # Parity with autotuner_proposal: an operator-only event type must
        # actually resolve to the operator channel (not empty → undelivered).
        from app.notifications.slack import (
            OPERATOR_CHANNEL_ENV,
            RECONCILIATION_EVENT_TYPE,
            resolve_channel_env_vars,
        )
        self.assertEqual(
            resolve_channel_env_vars(RECONCILIATION_EVENT_TYPE),
            (OPERATOR_CHANNEL_ENV,),
        )


def _report(events):
    return {"summary": "s", "events": list(events), "event_count": len(events)}


def _suspect_event(symbol, classification, expected, actual):
    return {
        "type": "unexpected_missing_position",
        "symbol": symbol,
        "expected_qty": expected,
        "actual_qty": actual,
        "reason": "-",
        "classification": classification,
        "detected_at": "2026-05-20T10:05:00+09:00",
    }


class MaybeNotifyManualTradeSuspectsTests(unittest.TestCase):
    def test_two_suspects_notify_once_with_two_lines(self) -> None:
        from app.notifications.reconciliation_alerts import (
            maybe_notify_manual_trade_suspects,
        )
        calls: list[tuple] = []

        def notify(event_type, message, *, symbol=None, details=None):
            calls.append((event_type, message, symbol, dict(details or {})))

        report = _report([
            _suspect_event("005930", "suspected_manual_sell", 10, 0),
            _suspect_event("000660", "suspected_manual_buy", 0, 3),
        ])
        fired = maybe_notify_manual_trade_suspects(report, notify=notify)

        self.assertTrue(fired)
        self.assertEqual(len(calls), 1)
        event_type, message, _symbol, _details = calls[0]
        self.assertEqual(event_type, "reconciliation_drift")
        self.assertIn("005930", message)
        self.assertIn("000660", message)

    def test_no_suspect_events_is_noop(self) -> None:
        from app.notifications.reconciliation_alerts import (
            maybe_notify_manual_trade_suspects,
        )
        calls = []

        def notify(*a, **kw):
            calls.append((a, kw))

        # Only an "unexplained" event -> not a manual-trade suspect.
        report = _report([
            {
                "type": "sell_intent_exceeds_actual_qty",
                "symbol": "005930",
                "expected_qty": 10,
                "actual_qty": 5,
                "reason": "-",
                "classification": "unexplained",
                "detected_at": "t",
            }
        ])
        fired = maybe_notify_manual_trade_suspects(report, notify=notify)
        self.assertFalse(fired)
        self.assertEqual(calls, [])

    def test_empty_events_is_noop(self) -> None:
        from app.notifications.reconciliation_alerts import (
            maybe_notify_manual_trade_suspects,
        )
        fired = maybe_notify_manual_trade_suspects(_report([]), notify=lambda *a, **k: None)
        self.assertFalse(fired)

    def test_notify_exception_swallowed_returns_false(self) -> None:
        from app.notifications.reconciliation_alerts import (
            maybe_notify_manual_trade_suspects,
        )

        def boom(*a, **kw):
            raise RuntimeError("slack down")

        report = _report([_suspect_event("005930", "suspected_manual_sell", 10, 0)])
        # Must not raise; whole body is guarded (C5a).
        fired = maybe_notify_manual_trade_suspects(report, notify=boom)
        self.assertFalse(fired)

    def test_malformed_report_swallowed_before_notify_c5a(self) -> None:
        from app.notifications.reconciliation_alerts import (
            maybe_notify_manual_trade_suspects,
        )
        called = []

        def notify(*a, **kw):
            called.append(1)

        # report is not a dict -> .get raises AttributeError inside the guarded
        # body, BEFORE any notify call. C5a: no exception, no notify, return False.
        fired = maybe_notify_manual_trade_suspects("not-a-report", notify=notify)
        self.assertFalse(fired)
        self.assertEqual(called, [])

        # Non-dict event entries are skipped, not raised on.
        fired2 = maybe_notify_manual_trade_suspects(
            {"events": ["garbage", None, 42]}, notify=notify
        )
        self.assertFalse(fired2)
        self.assertEqual(called, [])

    def test_no_broker_or_order_api_imported(self) -> None:
        import app.notifications.reconciliation_alerts as mod
        import inspect
        source = inspect.getsource(mod)
        for banned in ("app.execution", "app.broker", "kis_client", "place_order",
                       "submit_order", "inquire_balance"):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
