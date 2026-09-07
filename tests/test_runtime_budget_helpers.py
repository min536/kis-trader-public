"""Tests for app.core.runtime_budget helper functions.

These tests verify the extracted budget helpers without importing or touching app.main.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.core.runtime_budget import (
    API_TRANSIENT_BASE_BACKOFF_SECONDS,
    API_TRANSIENT_BACKOFF_WINDOW_SECONDS,
    API_TRANSIENT_MAX_BACKOFF_SECONDS,
    api_budget_backoff_remaining_seconds,
    api_budget_min_wait_for_request_slot,
    api_budget_note_transient_api_error,
    api_budget_register_measured_extra_requests,
    api_budget_register_request,
    api_budget_register_requests,
    api_budget_transient_backoff_remaining_seconds,
    api_budget_update_rate_limit_recovery_state,
    api_budget_update_transient_recovery_state,
    build_api_budget_state,
    buy_scan_reserve_active,
    prune_recent_rate_limit_hits,
    prune_recent_transient_api_errors,
    request_metrics_delta,
    summarize_api_budget_state,
)


class ApiBackoffRemainingSecondsTests(unittest.TestCase):
    """Tests for api_budget_backoff_remaining_seconds."""

    def test_no_backoff_until_key(self) -> None:
        result = api_budget_backoff_remaining_seconds({}, now=datetime.now())
        self.assertEqual(result, 0.0)

    def test_backoff_until_is_none(self) -> None:
        result = api_budget_backoff_remaining_seconds(
            {"backoff_until": None}, now=datetime.now()
        )
        self.assertEqual(result, 0.0)

    def test_backoff_until_not_datetime(self) -> None:
        result = api_budget_backoff_remaining_seconds(
            {"backoff_until": "2026-05-09T12:00:00"}, now=datetime.now()
        )
        self.assertEqual(result, 0.0)

    def test_backoff_until_is_integer(self) -> None:
        result = api_budget_backoff_remaining_seconds(
            {"backoff_until": 12345}, now=datetime.now()
        )
        self.assertEqual(result, 0.0)

    def test_backoff_until_in_future_returns_positive(self) -> None:
        now = datetime(2026, 5, 9, 12, 0, 0)
        future = now + timedelta(seconds=30)
        result = api_budget_backoff_remaining_seconds(
            {"backoff_until": future}, now=now
        )
        self.assertAlmostEqual(result, 30.0, places=1)

    def test_backoff_until_in_past_returns_zero(self) -> None:
        now = datetime(2026, 5, 9, 12, 0, 0)
        past = now - timedelta(seconds=10)
        result = api_budget_backoff_remaining_seconds(
            {"backoff_until": past}, now=now
        )
        self.assertEqual(result, 0.0)

    def test_backoff_until_equals_now_returns_zero(self) -> None:
        now = datetime(2026, 5, 9, 12, 0, 0)
        result = api_budget_backoff_remaining_seconds(
            {"backoff_until": now}, now=now
        )
        self.assertEqual(result, 0.0)

    def test_semantics_max_zero_delta(self) -> None:
        """Verify that the result matches max(0.0, delta.total_seconds())."""
        now = datetime(2026, 5, 9, 12, 0, 0)
        future = now + timedelta(seconds=42.5)
        result = api_budget_backoff_remaining_seconds(
            {"backoff_until": future}, now=now
        )
        expected = max(0.0, (future - now).total_seconds())
        self.assertAlmostEqual(result, expected, places=6)


class ApiTransientBackoffRemainingSecondsTests(unittest.TestCase):
    """Tests for api_budget_transient_backoff_remaining_seconds."""

    def test_no_transient_error_until_key(self) -> None:
        result = api_budget_transient_backoff_remaining_seconds(
            {}, now=datetime.now()
        )
        self.assertEqual(result, 0.0)

    def test_transient_error_until_is_none(self) -> None:
        result = api_budget_transient_backoff_remaining_seconds(
            {"transient_error_until": None}, now=datetime.now()
        )
        self.assertEqual(result, 0.0)

    def test_transient_error_until_not_datetime(self) -> None:
        result = api_budget_transient_backoff_remaining_seconds(
            {"transient_error_until": "not-a-datetime"}, now=datetime.now()
        )
        self.assertEqual(result, 0.0)

    def test_transient_error_until_is_integer(self) -> None:
        result = api_budget_transient_backoff_remaining_seconds(
            {"transient_error_until": 9999}, now=datetime.now()
        )
        self.assertEqual(result, 0.0)

    def test_transient_until_in_future_returns_positive(self) -> None:
        now = datetime(2026, 5, 9, 14, 0, 0)
        future = now + timedelta(seconds=15)
        result = api_budget_transient_backoff_remaining_seconds(
            {"transient_error_until": future}, now=now
        )
        self.assertAlmostEqual(result, 15.0, places=1)

    def test_transient_until_in_past_returns_zero(self) -> None:
        now = datetime(2026, 5, 9, 14, 0, 0)
        past = now - timedelta(seconds=5)
        result = api_budget_transient_backoff_remaining_seconds(
            {"transient_error_until": past}, now=now
        )
        self.assertEqual(result, 0.0)

    def test_transient_until_equals_now_returns_zero(self) -> None:
        now = datetime(2026, 5, 9, 14, 0, 0)
        result = api_budget_transient_backoff_remaining_seconds(
            {"transient_error_until": now}, now=now
        )
        self.assertEqual(result, 0.0)

    def test_semantics_max_zero_delta(self) -> None:
        """Verify that the result matches max(0.0, delta.total_seconds())."""
        now = datetime(2026, 5, 9, 14, 0, 0)
        future = now + timedelta(seconds=99.9)
        result = api_budget_transient_backoff_remaining_seconds(
            {"transient_error_until": future}, now=now
        )
        expected = max(0.0, (future - now).total_seconds())
        self.assertAlmostEqual(result, expected, places=6)


class PruneRecentRateLimitHitsTests(unittest.TestCase):
    """Tests for prune_recent_rate_limit_hits."""

    def test_empty_state_returns_empty(self) -> None:
        state: dict = {}
        result = prune_recent_rate_limit_hits(state, now=datetime.now())
        self.assertEqual(result, [])

    def test_recent_hit_within_window_kept(self) -> None:
        now = datetime(2026, 5, 20, 12, 0, 0)
        recent = now - timedelta(seconds=100)
        state = {"recent_rate_limit_hit_times": [recent]}
        result = prune_recent_rate_limit_hits(state, now=now)
        self.assertEqual(result, [recent])

    def test_old_hit_outside_window_removed(self) -> None:
        now = datetime(2026, 5, 20, 12, 0, 0)
        old = now - timedelta(seconds=601)
        state = {"recent_rate_limit_hit_times": [old]}
        result = prune_recent_rate_limit_hits(state, now=now)
        self.assertEqual(result, [])

    def test_mutates_state_in_place(self) -> None:
        now = datetime(2026, 5, 20, 12, 0, 0)
        old = now - timedelta(seconds=700)
        recent = now - timedelta(seconds=10)
        state = {"recent_rate_limit_hit_times": [old, recent]}
        prune_recent_rate_limit_hits(state, now=now)
        self.assertEqual(state["recent_rate_limit_hit_times"], [recent])


class PruneRecentTransientApiErrorsTests(unittest.TestCase):
    """Tests for prune_recent_transient_api_errors."""

    def test_empty_state_returns_empty(self) -> None:
        state: dict = {}
        result = prune_recent_transient_api_errors(state, now=datetime.now())
        self.assertEqual(result, [])

    def test_recent_error_within_window_kept(self) -> None:
        now = datetime(2026, 5, 20, 12, 0, 0)
        recent = now - timedelta(seconds=API_TRANSIENT_BACKOFF_WINDOW_SECONDS - 1)
        state = {"recent_transient_error_times": [recent]}
        result = prune_recent_transient_api_errors(state, now=now)
        self.assertEqual(result, [recent])

    def test_old_error_outside_window_removed(self) -> None:
        now = datetime(2026, 5, 20, 12, 0, 0)
        old = now - timedelta(seconds=API_TRANSIENT_BACKOFF_WINDOW_SECONDS + 1)
        state = {"recent_transient_error_times": [old]}
        result = prune_recent_transient_api_errors(state, now=now)
        self.assertEqual(result, [])

    def test_mutates_state_in_place(self) -> None:
        now = datetime(2026, 5, 20, 12, 0, 0)
        old = now - timedelta(seconds=API_TRANSIENT_BACKOFF_WINDOW_SECONDS + 100)
        recent = now - timedelta(seconds=30)
        state = {"recent_transient_error_times": [old, recent]}
        prune_recent_transient_api_errors(state, now=now)
        self.assertEqual(state["recent_transient_error_times"], [recent])


class SummarizeApiBudgetStateTests(unittest.TestCase):
    """Tests for summarize_api_budget_state."""

    def _now(self) -> datetime:
        return datetime(2026, 5, 20, 12, 0, 0)

    def test_empty_state_returns_zero_fields(self) -> None:
        result = summarize_api_budget_state({}, now=self._now())
        self.assertEqual(result["recent_request_count"], 0)
        self.assertEqual(result["quotes_used_this_tick"], 0)
        self.assertEqual(result["backoff_remaining_seconds"], 0)
        self.assertEqual(result["transient_backoff_remaining_seconds"], 0)
        self.assertEqual(result["rate_limit_hits"], 0)
        self.assertIsNone(result["last_rate_limit_source"])
        self.assertEqual(result["recent_rate_limit_hits_10m"], 0)
        self.assertEqual(result["consecutive_backoff_cycles"], 0)
        self.assertEqual(result["transient_error_hits"], 0)
        self.assertIsNone(result["last_transient_error_source"])
        self.assertEqual(result["recent_transient_error_hits_10m"], 0)

    def test_active_backoff_shows_remaining(self) -> None:
        now = self._now()
        state = {"backoff_until": now + timedelta(seconds=45)}
        result = summarize_api_budget_state(state, now=now)
        self.assertEqual(result["backoff_remaining_seconds"], 45)

    def test_expired_backoff_shows_zero(self) -> None:
        now = self._now()
        state = {"backoff_until": now - timedelta(seconds=1)}
        result = summarize_api_budget_state(state, now=now)
        self.assertEqual(result["backoff_remaining_seconds"], 0)

    def test_active_transient_backoff_shows_remaining(self) -> None:
        now = self._now()
        state = {"transient_error_until": now + timedelta(seconds=20)}
        result = summarize_api_budget_state(state, now=now)
        self.assertEqual(result["transient_backoff_remaining_seconds"], 20)

    def test_recent_rate_limit_hits_counted(self) -> None:
        now = self._now()
        state = {
            "recent_rate_limit_hit_times": [
                now - timedelta(seconds=10),
                now - timedelta(seconds=20),
            ]
        }
        result = summarize_api_budget_state(state, now=now)
        self.assertEqual(result["recent_rate_limit_hits_10m"], 2)

    def test_old_rate_limit_hits_excluded(self) -> None:
        now = self._now()
        state = {"recent_rate_limit_hit_times": [now - timedelta(seconds=700)]}
        result = summarize_api_budget_state(state, now=now)
        self.assertEqual(result["recent_rate_limit_hits_10m"], 0)

    def test_recent_transient_error_hits_counted(self) -> None:
        now = self._now()
        state = {"recent_transient_error_times": [now - timedelta(seconds=30)]}
        result = summarize_api_budget_state(state, now=now)
        self.assertEqual(result["recent_transient_error_hits_10m"], 1)

    def test_last_rate_limit_source_blank_returns_none(self) -> None:
        result = summarize_api_budget_state(
            {"last_rate_limit_source": "  "}, now=self._now()
        )
        self.assertIsNone(result["last_rate_limit_source"])

    def test_last_rate_limit_source_nonempty_returned(self) -> None:
        result = summarize_api_budget_state(
            {"last_rate_limit_source": "buy_scan"}, now=self._now()
        )
        self.assertEqual(result["last_rate_limit_source"], "buy_scan")


class ApiUpdateRateLimitRecoveryStateTests(unittest.TestCase):
    """Tests for api_budget_update_rate_limit_recovery_state."""

    def test_source_present_records_source(self) -> None:
        now = datetime(2026, 5, 20, 12, 0, 0)
        state: dict = {"last_rate_limit_source": None, "rate_limit_hits": 3}
        api_budget_update_rate_limit_recovery_state(
            state, now=now, rate_limit_source="buy_scan"
        )
        self.assertEqual(state["last_rate_limit_source"], "buy_scan")
        self.assertEqual(state["rate_limit_hits"], 3)

    def test_no_source_no_backoff_clears_state(self) -> None:
        now = datetime(2026, 5, 20, 12, 0, 0)
        state: dict = {
            "last_rate_limit_source": "old_source",
            "rate_limit_hits": 5,
        }
        api_budget_update_rate_limit_recovery_state(
            state, now=now, rate_limit_source=None
        )
        self.assertIsNone(state["last_rate_limit_source"])
        self.assertEqual(state["rate_limit_hits"], 0)

    def test_no_source_with_active_backoff_preserves_state(self) -> None:
        now = datetime(2026, 5, 20, 12, 0, 0)
        state: dict = {
            "last_rate_limit_source": "old_source",
            "rate_limit_hits": 5,
            "backoff_until": now + timedelta(seconds=30),
        }
        api_budget_update_rate_limit_recovery_state(
            state, now=now, rate_limit_source=None
        )
        self.assertEqual(state["last_rate_limit_source"], "old_source")
        self.assertEqual(state["rate_limit_hits"], 5)


class ApiUpdateTransientRecoveryStateTests(unittest.TestCase):
    """Tests for api_budget_update_transient_recovery_state."""

    def test_source_present_records_source(self) -> None:
        now = datetime(2026, 5, 20, 12, 0, 0)
        state: dict = {"last_transient_error_source": None, "transient_error_hits": 2}
        api_budget_update_transient_recovery_state(
            state, now=now, transient_error_source="sell_watch"
        )
        self.assertEqual(state["last_transient_error_source"], "sell_watch")
        self.assertEqual(state["transient_error_hits"], 2)

    def test_no_source_no_backoff_clears_state(self) -> None:
        now = datetime(2026, 5, 20, 12, 0, 0)
        state: dict = {
            "last_transient_error_source": "old_source",
            "transient_error_hits": 4,
        }
        api_budget_update_transient_recovery_state(
            state, now=now, transient_error_source=None
        )
        self.assertIsNone(state["last_transient_error_source"])
        self.assertEqual(state["transient_error_hits"], 0)

    def test_no_source_with_active_transient_backoff_preserves_state(self) -> None:
        now = datetime(2026, 5, 20, 12, 0, 0)
        state: dict = {
            "last_transient_error_source": "old_source",
            "transient_error_hits": 4,
            "transient_error_until": now + timedelta(seconds=60),
        }
        api_budget_update_transient_recovery_state(
            state, now=now, transient_error_source=None
        )
        self.assertEqual(state["last_transient_error_source"], "old_source")
        self.assertEqual(state["transient_error_hits"], 4)


class ApiBudgetStateBuilderTests(unittest.TestCase):
    def test_build_api_budget_state_preserves_schema(self) -> None:
        settings = SimpleNamespace(
            api_soft_max_requests_per_second=5,
            api_soft_max_quotes_per_tick=7,
            api_backoff_seconds_on_rate_limit=90,
        )

        state = build_api_budget_state(settings)

        self.assertEqual(
            set(state),
            {
                "recent_requests",
                "quotes_used_this_tick",
                "backoff_until",
                "rate_limit_hits",
                "last_rate_limit_source",
                "recent_rate_limit_hit_times",
                "transient_error_until",
                "transient_error_hits",
                "last_transient_error_source",
                "recent_transient_error_times",
                "consecutive_backoff_cycles",
                "degraded_mode_until",
                "degraded_mode_reason",
                "soft_max_requests_per_second",
                "soft_max_quotes_per_tick",
                "backoff_seconds_on_rate_limit",
            },
        )
        self.assertEqual(state["soft_max_requests_per_second"], 5)
        self.assertEqual(state["soft_max_quotes_per_tick"], 7)
        self.assertEqual(state["backoff_seconds_on_rate_limit"], 90)


class ApiBudgetRegistrationTests(unittest.TestCase):
    def test_register_request_prunes_old_requests_and_adds_quote_cost(self) -> None:
        now = datetime(2026, 5, 27, 10, 0, 0)
        old = now - timedelta(seconds=2)
        state = {"recent_requests": [old], "quotes_used_this_tick": 1}

        api_budget_register_request(state, now=now, quote_cost=2)

        self.assertEqual(state["recent_requests"], [now])
        self.assertEqual(state["quotes_used_this_tick"], 3)

    def test_register_requests_charges_quote_cost_once(self) -> None:
        now = datetime(2026, 5, 27, 10, 0, 0)
        state = {"recent_requests": [], "quotes_used_this_tick": 0}

        api_budget_register_requests(
            state,
            now=now,
            request_count=3,
            quote_cost=2,
        )

        self.assertEqual(state["recent_requests"], [now, now, now])
        self.assertEqual(state["quotes_used_this_tick"], 2)

    def test_register_measured_extra_requests_only_adds_unregistered_delta(self) -> None:
        now = datetime(2026, 5, 27, 10, 0, 0)
        state = {"recent_requests": []}

        extra_count = api_budget_register_measured_extra_requests(
            state,
            request_delta={"categories": {"balance": {"count": 4}}},
            category="balance",
            already_registered_count=1,
            now=now,
        )

        self.assertEqual(extra_count, 3)
        self.assertEqual(state["recent_requests"], [now, now, now])


class ApiBudgetMinWaitTests(unittest.TestCase):
    def test_min_wait_returns_zero_when_request_slot_available(self) -> None:
        now = datetime(2026, 5, 27, 10, 0, 0)
        state = {"recent_requests": [], "soft_max_requests_per_second": 2}

        wait = api_budget_min_wait_for_request_slot(
            state,
            now=now,
            request_cost=1,
        )

        self.assertEqual(wait, 0.0)

    def test_min_wait_uses_overflow_pivot_plus_safety_margin(self) -> None:
        now = datetime(2026, 5, 27, 10, 0, 0, 500000)
        first = now - timedelta(seconds=0.8)
        second = now - timedelta(seconds=0.2)
        state = {
            "recent_requests": [first, second],
            "soft_max_requests_per_second": 2,
        }

        wait = api_budget_min_wait_for_request_slot(
            state,
            now=now,
            request_cost=1,
        )

        self.assertAlmostEqual(wait, 0.25, places=6)


class ApiBudgetTransientErrorTests(unittest.TestCase):
    def test_note_transient_api_error_sets_source_count_and_backoff(self) -> None:
        now = datetime(2026, 5, 27, 10, 0, 0)
        state = {"recent_transient_error_times": []}

        api_budget_note_transient_api_error(state, now=now, source="balance")

        self.assertEqual(state["recent_transient_error_times"], [now])
        self.assertEqual(state["last_transient_error_source"], "balance")
        self.assertEqual(state["transient_error_hits"], 1)
        self.assertEqual(
            state["transient_error_until"],
            now + timedelta(seconds=API_TRANSIENT_BASE_BACKOFF_SECONDS),
        )

    def test_note_transient_api_error_caps_exponential_backoff(self) -> None:
        now = datetime(2026, 5, 27, 10, 0, 0)
        recent_hits = [
            now - timedelta(seconds=5),
            now - timedelta(seconds=4),
            now - timedelta(seconds=3),
            now - timedelta(seconds=2),
            now - timedelta(seconds=1),
        ]
        state = {"recent_transient_error_times": recent_hits}

        api_budget_note_transient_api_error(state, now=now, source=None)

        self.assertEqual(state["transient_error_hits"], 6)
        self.assertIsNone(state["last_transient_error_source"])
        self.assertEqual(
            state["transient_error_until"],
            now + timedelta(seconds=API_TRANSIENT_MAX_BACKOFF_SECONDS),
        )


class RequestMetricsDeltaTests(unittest.TestCase):
    def test_clamps_negative_deltas_and_rounds_elapsed_ms(self) -> None:
        before = {
            "total_requests": 10,
            "total_elapsed_ms": 100.0,
            "categories": {
                "quote": {"count": 5, "elapsed_ms": 50.0},
                "order": {"count": 2, "elapsed_ms": 20.0},
            },
        }
        after = {
            "total_requests": 8,
            "total_elapsed_ms": 133.456,
            "categories": {
                "quote": {"count": 9, "elapsed_ms": 84.567},
                "order": {"count": 1, "elapsed_ms": 10.0},
            },
        }

        delta = request_metrics_delta(before, after)

        self.assertEqual(delta["total_requests"], 0)
        self.assertEqual(delta["total_elapsed_ms"], 33.5)
        self.assertEqual(delta["categories"]["quote"]["count"], 4)
        self.assertEqual(delta["categories"]["quote"]["elapsed_ms"], 34.6)
        self.assertEqual(delta["categories"]["order"]["count"], 0)
        self.assertEqual(delta["categories"]["order"]["elapsed_ms"], 0.0)
        for category in ("quote", "balance", "orderable", "order", "token", "other"):
            self.assertIn(category, delta["categories"])


class BuyScanReserveActiveTests(unittest.TestCase):
    def test_regular_session_with_positive_reserve_is_active(self) -> None:
        settings = SimpleNamespace(
            api_buy_scan_min_request_reserve=1,
            api_buy_scan_min_quote_reserve=0,
        )
        session_status = SimpleNamespace(session="REGULAR")

        self.assertTrue(
            buy_scan_reserve_active(
                settings=settings,
                session_status=session_status,
                buy_scan_due=True,
            )
        )

    def test_non_regular_or_not_due_is_inactive(self) -> None:
        settings = SimpleNamespace(
            api_buy_scan_min_request_reserve=1,
            api_buy_scan_min_quote_reserve=1,
        )
        regular = SimpleNamespace(session="REGULAR")
        premarket = SimpleNamespace(session="PREMARKET")

        self.assertFalse(
            buy_scan_reserve_active(
                settings=settings,
                session_status=regular,
                buy_scan_due=False,
            )
        )
        self.assertFalse(
            buy_scan_reserve_active(
                settings=settings,
                session_status=premarket,
                buy_scan_due=True,
            )
        )
        self.assertFalse(
            buy_scan_reserve_active(
                settings=settings,
                session_status=None,
                buy_scan_due=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
