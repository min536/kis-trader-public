"""Tests for app.notifications.slack_format — pure Slack text/payload builders.

R3-S2: formatter + alert-builder cluster extracted verbatim from
app.notifications.slack (transport stays there).
"""

from __future__ import annotations

import unittest

from app.notifications import slack_format

# Non-empty so sanitize_text uses this mapping instead of os.environ.
_ENV = {"UNUSED_SENTINEL": "1"}


class BuildOrderAlertTextTests(unittest.TestCase):
    def test_order_alert_text_covers_event_side_amount_and_reason(self) -> None:
        cases = (
            (
                "buy-submitted-with-total",
                dict(
                    event_type="order_submitted",
                    symbol="005930",
                    details={"side": "BUY", "quantity": "10", "submitted_price_krw": "70,000"},
                    env=_ENV,
                ),
                ["📤", "매수", "제출", "005930", "10주 × 70,000원 = 700,000원", "상태: 제출"],
                [],
            ),
            (
                "rejected-reason-with-code",
                dict(
                    event_type="order_rejected",
                    symbol="005930",
                    details={"side": "BUY", "quantity": "3", "reason": "한도 초과 (E1234)"},
                    env=_ENV,
                ),
                ["❌", "실패", "사유: 한도 초과 `(E1234)`"],
                [],
            ),
            (
                "boilerplate-reason-suppressed",
                dict(
                    event_type="order_accepted",
                    symbol="005930",
                    details={"side": "BUY", "quantity": "3", "reason": "주문 API 호출 직전"},
                    env=_ENV,
                ),
                ["✅", "접수"],
                ["메모:", "주문 API 호출 직전"],
            ),
            (
                "sell-side-inferred-from-action",
                dict(
                    event_type="order_submitted",
                    symbol="005930",
                    details={"action": "sell_order_submitted", "quantity": "5"},
                    env=_ENV,
                ),
                ["매도", "5주"],
                [],
            ),
        )
        for label, kwargs, expected_in, expected_not_in in cases:
            with self.subTest(label):
                text = slack_format._build_order_alert_text(**kwargs)
                self.assertIsNotNone(text)
                for fragment in expected_in:
                    self.assertIn(fragment, text)
                for fragment in expected_not_in:
                    self.assertNotIn(fragment, text)

        self.assertIsNone(
            slack_format._build_order_alert_text(
                event_type="order_unknown", symbol="005930", details={}, env=_ENV
            )
        )

    def test_rate_limit_alert_text_labels_source_metrics_and_action(self) -> None:
        text = slack_format._build_rate_limit_alert_text(
            message="rate limited",
            details={
                "source": "buy_scan",
                "hits": 3,
                "backoff_seconds": 45,
                "action": "defer_buy_scan",
            },
            env=_ENV,
        )

        self.assertIn("⚠️ *KIS rate-limit* | `buy_scan` 매수 스캔", text)
        self.assertIn("Backoff: `45s`", text)
        self.assertIn("Hits: `3`", text)
        self.assertIn("Action: 매수 스캔 보류", text)

        minimal = slack_format._build_rate_limit_alert_text(
            message="rate limited", details=None, env=_ENV
        )
        self.assertIn("unknown", minimal)

    def test_postrun_alert_payload_colors_status_and_compacts_body(self) -> None:
        payload = slack_format._build_postrun_alert_payload(
            channel="C123",
            message=(
                "EOD postrun failed\n"
                "Exit code: 2\n"
                "Report: /very/long/path/to/report_20260611.md\n"
            ),
            details={"status": "failed", "report": "/very/long/path/to/report_20260611.md"},
            env=_ENV,
        )

        self.assertEqual(
            payload,
            {
                "channel": "C123",
                "text": "EOD postrun failed",
                "unfurl_links": False,
                "unfurl_media": False,
                "attachments": [
                    {
                        "color": "#E53935",
                        "text": "Exit code: 2\nReport: report_20260611.md",
                        "mrkdwn_in": ["text"],
                    }
                ],
            },
        )

        complete = slack_format._build_postrun_alert_payload(
            channel="C123",
            message="EOD postrun complete\nDuration: 12s",
            details={"status": "complete"},
            env=_ENV,
        )
        self.assertEqual(complete["attachments"][0]["color"], "#36a64f")

    def test_slack_module_binds_canonical_slack_format_objects(self) -> None:
        # Facade pin: app.notifications.slack must re-export slack_format's
        # objects, not keep shadowing local copies of the moved cluster.
        from app.notifications import slack

        names = (
            "_get_tag_registry",
            "_truncate",
            "_format_krw",
            "_format_order_reason",
            "_format_order_quantity",
            "_format_symbol_with_name",
            "_compute_order_total",
            "_format_rate_limit_source",
            "_format_rate_limit_action",
            "_build_rate_limit_alert_text",
            "_build_postrun_alert_payload",
            "format_symbol_tag_summary",
            "_infer_order_side",
            "_build_order_alert_text",
        )
        for name in names:
            with self.subTest(name):
                self.assertIs(getattr(slack, name), getattr(slack_format, name))


if __name__ == "__main__":
    unittest.main()
