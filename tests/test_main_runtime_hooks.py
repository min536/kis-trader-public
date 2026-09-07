"""Tests for app.notifications.main_runtime_hooks helper functions.

These tests verify the extracted helpers without importing or touching app.main.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.market_data.quality_sentinel import evaluate_market_data_quality
from app.market_data.schema import MarketSnapshot
from app.notifications.main_runtime_hooks import (
    build_market_data_quality_alert_text,
    market_session_status_payload,
    run_market_data_quality_sentinel,
    slack_event_type_for_order_action,
    slack_session_status_text,
)


def _bad_snapshot(symbol: str = "005930") -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol, current_price=0, open_price=69_000,
        low_price=68_000, high_price=71_000, prev_day_change_pct=1.5,
    )

_NOW_EPOCH = 1_710_000_000.0


def _fresh_updated_at(age_seconds: float) -> str:
    return datetime.fromtimestamp(_NOW_EPOCH - age_seconds, tz=timezone.utc).isoformat()


class SlackEventTypeForOrderActionTests(unittest.TestCase):
    # ── buy-side mappings ──────────────────────────────────────────────
    def test_order_submitted(self) -> None:
        self.assertEqual(
            slack_event_type_for_order_action("order_submitted"),
            "order_submitted",
        )

    def test_order_succeeded(self) -> None:
        self.assertEqual(
            slack_event_type_for_order_action("order_succeeded"),
            "order_accepted",
        )

    def test_order_failed(self) -> None:
        self.assertEqual(
            slack_event_type_for_order_action("order_failed"),
            "order_rejected",
        )

    # ── sell-side mappings ─────────────────────────────────────────────
    def test_sell_order_submitted(self) -> None:
        self.assertEqual(
            slack_event_type_for_order_action("sell_order_submitted"),
            "order_submitted",
        )

    def test_sell_order_succeeded(self) -> None:
        self.assertEqual(
            slack_event_type_for_order_action("sell_order_succeeded"),
            "order_accepted",
        )

    def test_sell_order_failed(self) -> None:
        self.assertEqual(
            slack_event_type_for_order_action("sell_order_failed"),
            "order_rejected",
        )

    # ── unknown / empty / None-like ────────────────────────────────────
    def test_unknown_action_returns_none(self) -> None:
        self.assertIsNone(slack_event_type_for_order_action("unknown_action"))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(slack_event_type_for_order_action(""))

    def test_none_returns_none(self) -> None:
        self.assertIsNone(slack_event_type_for_order_action(None))  # type: ignore[arg-type]

    def test_whitespace_only_returns_none(self) -> None:
        self.assertIsNone(slack_event_type_for_order_action("   "))


class SlackSessionStatusTextTests(unittest.TestCase):
    def test_object_with_session_attribute(self) -> None:
        status = SimpleNamespace(session="REGULAR")
        self.assertEqual(slack_session_status_text(status), "REGULAR")

    def test_object_without_session_attribute_uses_str(self) -> None:
        # When there is no .session attribute, the function falls back to
        # str(session_status) itself.
        self.assertEqual(slack_session_status_text("PRE_MARKET"), "PRE_MARKET")

    def test_empty_session_attribute_returns_none(self) -> None:
        status = SimpleNamespace(session="")
        self.assertIsNone(slack_session_status_text(status))

    def test_none_value_returns_none(self) -> None:
        self.assertIsNone(slack_session_status_text(None))

    def test_whitespace_session_returns_none(self) -> None:
        status = SimpleNamespace(session="   ")
        self.assertIsNone(slack_session_status_text(status))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(slack_session_status_text(""))


class MarketSessionStatusPayloadTests(unittest.TestCase):
    def test_payload_keys_match_status_attributes(self) -> None:
        status = SimpleNamespace(
            session="REGULAR",
            order_allowed=True,
            reason="market_open",
            buy_block_action="none",
            sell_block_action="none",
        )
        payload = market_session_status_payload(status)

        self.assertEqual(payload["session"], "REGULAR")
        self.assertTrue(payload["order_allowed"])
        self.assertEqual(payload["reason"], "market_open")
        self.assertEqual(payload["buy_block_action"], "none")
        self.assertEqual(payload["sell_block_action"], "none")

    def test_order_allowed_is_coerced_to_bool(self) -> None:
        status = SimpleNamespace(
            session="CLOSED",
            order_allowed=0,
            reason="market_closed",
            buy_block_action="block",
            sell_block_action="block",
        )
        payload = market_session_status_payload(status)
        self.assertIs(payload["order_allowed"], False)

    def test_payload_contains_all_required_keys(self) -> None:
        status = SimpleNamespace(
            session="PRE_MARKET",
            order_allowed=False,
            reason="not_yet",
            buy_block_action="wait",
            sell_block_action="wait",
        )
        payload = market_session_status_payload(status)
        expected_keys = {
            "session",
            "order_allowed",
            "reason",
            "buy_block_action",
            "sell_block_action",
        }
        self.assertEqual(set(payload.keys()), expected_keys)


class BuildMarketDataQualityAlertTextTests(unittest.TestCase):
    def _report(self, snapshots, *, updated_at):
        return evaluate_market_data_quality(
            snapshots,
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=updated_at,
        )

    def test_warn_report_produces_alert_text(self) -> None:
        bad = MarketSnapshot(
            symbol="005930", current_price=0, open_price=69_000,
            low_price=68_000, high_price=71_000, prev_day_change_pct=1.5,
        )
        report = self._report([bad], updated_at=_fresh_updated_at(30))
        text = build_market_data_quality_alert_text(report)
        self.assertIsNotNone(text)
        self.assertIn("005930", text)
        self.assertIn("WARN", text)

    def test_ok_report_produces_no_text(self) -> None:
        good = MarketSnapshot(
            symbol="005930", current_price=70_000, open_price=69_000,
            low_price=68_000, high_price=71_000, prev_day_change_pct=1.5,
        )
        report = self._report([good], updated_at=_fresh_updated_at(30))
        self.assertIsNone(build_market_data_quality_alert_text(report))


class RunMarketDataQualitySentinelTests(unittest.TestCase):
    def test_writes_artifact_but_skips_alert_when_flag_off(self) -> None:
        sent: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_path = Path(temp_dir) / "market_data_quality.json"
            report = run_market_data_quality_sentinel(
                [_bad_snapshot()],
                now_epoch=_NOW_EPOCH,
                refresh_interval_seconds=180,
                snapshot_updated_at=_fresh_updated_at(30),
                env={},
                artifact_path=artifact_path,
                alert_sender=sent.append,
            )
            self.assertEqual(report.status, "warn")
            self.assertTrue(artifact_path.exists())
            self.assertEqual(sent, [])

    def test_sends_alert_when_flag_on_and_warn(self) -> None:
        sent: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_path = Path(temp_dir) / "market_data_quality.json"
            run_market_data_quality_sentinel(
                [_bad_snapshot()],
                now_epoch=_NOW_EPOCH,
                refresh_interval_seconds=180,
                snapshot_updated_at=_fresh_updated_at(30),
                env={"MARKET_DATA_QUALITY_ALERTS_ENABLED": "1"},
                artifact_path=artifact_path,
                alert_sender=sent.append,
            )
            self.assertEqual(len(sent), 1)
            self.assertIn("005930", sent[0])

    def test_never_raises_when_alert_sender_fails(self) -> None:
        def boom(_text: str) -> None:
            raise RuntimeError("alert channel down")

        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_path = Path(temp_dir) / "market_data_quality.json"
            # Must not propagate the alert failure into the trade cycle.
            run_market_data_quality_sentinel(
                [_bad_snapshot()],
                now_epoch=_NOW_EPOCH,
                refresh_interval_seconds=180,
                snapshot_updated_at=_fresh_updated_at(30),
                env={"MARKET_DATA_QUALITY_ALERTS_ENABLED": "1"},
                artifact_path=artifact_path,
                alert_sender=boom,
            )
            self.assertTrue(artifact_path.exists())


if __name__ == "__main__":
    unittest.main()
