"""Tests for app.notifications.sanitize — secret/account redaction helpers.

R3-S1: sanitize cluster extracted verbatim from app.notifications.slack.
"""

from __future__ import annotations

import unittest

from app.notifications import sanitize


class SanitizeTextTests(unittest.TestCase):
    def test_sanitize_text_redacts_all_secret_shapes(self) -> None:
        env = {"KIS_APP_KEY": "fakekey-12345678"}

        self.assertEqual(sanitize.sanitize_text(None, env), "")
        self.assertEqual(sanitize.sanitize_text("plain message", env), "plain message")

        cases = (
            ("env-value", "key=fakekey-12345678 done", "fakekey-12345678"),
            ("slack-token", "slack xoxb-111-fakeslacktoken end", "xoxb-111-fakeslacktoken"),
            ("bearer", "Authorization: Bearer abcDEF123token", "abcDEF123token"),
            ("labeled-secret", "app_key: abcd1234", "abcd1234"),
            ("labeled-account", "account no: 123456-78", "123456-78"),
            ("korean-account", "계좌번호: 123456-01", "123456-01"),
        )
        for label, raw, leaked in cases:
            with self.subTest(label):
                rendered = sanitize.sanitize_text(raw, env)
                self.assertNotIn(leaked, rendered)
                self.assertIn("[REDACTED]", rendered)

    def test_sanitize_details_redacts_sensitive_keys_and_values(self) -> None:
        env = {"KIS_APP_KEY": "fakekey-12345678"}

        sanitized = sanitize._sanitize_details(
            {
                "reason": "limit hit",
                "app_key": "whatever-value",
                "계좌": "123456-01",
                "note": "uses fakekey-12345678 here",
            },
            env,
        )

        self.assertEqual(
            sanitized,
            {
                "reason": "limit hit",
                "app_key": "[REDACTED]",
                "계좌": "[REDACTED]",
                "note": "uses [REDACTED] here",
            },
        )
        self.assertEqual(sanitize._sanitize_details(None, env), {})

    def test_sensitive_env_names_track_slack_channel_constants(self) -> None:
        # sanitize.py keeps these as literals to avoid importing the slack
        # transport module; this pin breaks if the slack constants drift.
        from app.notifications import slack

        self.assertEqual(
            sanitize._SENSITIVE_ENV_NAMES[:8],
            (
                slack.SLACK_BOT_TOKEN_ENV,
                slack.OPERATOR_CHANNEL_ENV,
                slack.ORDERS_CHANNEL_ENV,
                slack.BOTTLENECKS_CHANNEL_ENV,
                slack.ACTIVATOR_CHANNEL_ENV,
                slack.BACK_TESTER_CHANNEL_ENV,
                slack.SUMMARY_CHANNEL_ENV,
                slack.PATH_FINDER_CHANNEL_ENV,
            ),
        )


if __name__ == "__main__":
    unittest.main()
