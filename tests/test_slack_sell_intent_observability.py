"""Slack surfaces stranded SELL intents and counts the lockout as a bottleneck.

docs/todo_20260710.md §A-4 (T1) + the "Slack 연동 보강" note, and §B N2.
"""
from __future__ import annotations

import unittest
from datetime import timedelta

from app.core.time_utils import get_korean_now
from app.notifications.runtime_status_snapshot import (
    _BOTTLENECK_ACTION_KEYS,
    _BOTTLENECK_LABELS,
    build_slack_status_snapshot,
    render_pending_intent_line,
)


class SellLockoutBottleneckTests(unittest.TestCase):
    def test_sell_lockout_actions_are_tracked_bottlenecks(self) -> None:
        for key in (
            "blocked_sell_no_sellable_residual",
            "blocked_sell_duplicate_pending_intent",
        ):
            self.assertIn(key, _BOTTLENECK_ACTION_KEYS)
            self.assertIn(key, _BOTTLENECK_LABELS)

    def test_every_bottleneck_key_has_a_label(self) -> None:
        self.assertEqual(
            sorted(_BOTTLENECK_ACTION_KEYS), sorted(_BOTTLENECK_LABELS)
        )


class PendingIntentSnapshotTests(unittest.TestCase):
    def _state(self, pending: dict) -> dict:
        return {
            "pending_sell_intents_by_symbol": pending,
            "broker_last_synced_positions_by_symbol": {},
        }

    def test_snapshot_reports_pending_intent_count_and_oldest_age(self) -> None:
        old = (get_korean_now() - timedelta(minutes=42)).isoformat()
        recent = (get_korean_now() - timedelta(minutes=2)).isoformat()
        snapshot = build_slack_status_snapshot(
            runtime_state=self._state(
                {
                    "023530": {"qty": 18, "submitted_at": old, "trigger": "stop_loss"},
                    "082740": {"qty": 36, "submitted_at": recent},
                }
            )
        )

        intents = snapshot["pending_sell_intents"]
        self.assertEqual(intents["count"], 2)
        self.assertEqual(intents["oldest_symbol"], "023530")
        self.assertGreaterEqual(intents["oldest_age_minutes"], 41)

    def test_snapshot_with_no_pending_intents_is_zeroed(self) -> None:
        snapshot = build_slack_status_snapshot(runtime_state=self._state({}))
        intents = snapshot["pending_sell_intents"]
        self.assertEqual(intents["count"], 0)
        self.assertEqual(intents["oldest_symbol"], "")
        self.assertIsNone(intents["oldest_age_minutes"])

    def test_status_line_shows_stranded_intent(self) -> None:
        old = (get_korean_now() - timedelta(minutes=42)).isoformat()
        snapshot = build_slack_status_snapshot(
            runtime_state=self._state(
                {"023530": {"qty": 18, "submitted_at": old, "trigger": "stop_loss"}}
            )
        )
        line = render_pending_intent_line(snapshot)
        self.assertIn("023530", line)
        self.assertIn("SELL intent", line)

    def test_status_line_is_empty_when_nothing_is_pending(self) -> None:
        snapshot = build_slack_status_snapshot(runtime_state=self._state({}))
        self.assertEqual(render_pending_intent_line(snapshot), "")


if __name__ == "__main__":
    unittest.main()
