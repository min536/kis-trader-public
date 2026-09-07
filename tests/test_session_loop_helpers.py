from __future__ import annotations

import io
import unittest
from datetime import datetime, timedelta
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

from app.core.time_utils import KOREA_TZ
from app.runtime.session_loop import (
    build_cycle_id,
    build_scheduler_tick_decision,
    compute_next_tick_sleep_seconds,
    is_due,
    note_rate_limit_backoff,
    parse_hhmm_window,
    print_engine_schedule_state,
    within_hhmm_window,
)


class SessionLoopTimingHelperTests(unittest.TestCase):
    def test_is_due_when_never_run(self) -> None:
        now = datetime(2026, 5, 29, 10, 0, tzinfo=KOREA_TZ)

        self.assertTrue(
            is_due(last_run_at=None, interval_seconds=60, now=now)
        )

    def test_is_due_after_interval_elapsed(self) -> None:
        now = datetime(2026, 5, 29, 10, 0, tzinfo=KOREA_TZ)

        self.assertTrue(
            is_due(
                last_run_at=now - timedelta(seconds=60),
                interval_seconds=60,
                now=now,
            )
        )

    def test_is_not_due_before_interval_elapsed(self) -> None:
        now = datetime(2026, 5, 29, 10, 0, tzinfo=KOREA_TZ)

        self.assertFalse(
            is_due(
                last_run_at=now - timedelta(seconds=59),
                interval_seconds=60,
                now=now,
            )
        )

    def test_next_tick_sleep_subtracts_elapsed_cycle_time(self) -> None:
        started = datetime(2026, 5, 29, 10, 0, tzinfo=KOREA_TZ)
        completed = started + timedelta(seconds=11)

        self.assertEqual(
            compute_next_tick_sleep_seconds(
                tick_started_at=started,
                completed_at=completed,
                base_tick_seconds=30,
            ),
            19.0,
        )

    def test_next_tick_sleep_is_zero_when_cycle_overruns_target(self) -> None:
        started = datetime(2026, 5, 29, 10, 0, tzinfo=KOREA_TZ)
        completed = started + timedelta(seconds=35)

        self.assertEqual(
            compute_next_tick_sleep_seconds(
                tick_started_at=started,
                completed_at=completed,
                base_tick_seconds=30,
            ),
            0.0,
        )

    def test_parse_hhmm_window(self) -> None:
        self.assertEqual(parse_hhmm_window("11:00-13:30"), (660, 810))
        self.assertEqual(parse_hhmm_window(" 09:05 - 10:10 "), (545, 610))

    def test_parse_hhmm_window_returns_none_for_invalid_text(self) -> None:
        self.assertIsNone(parse_hhmm_window(""))
        self.assertIsNone(parse_hhmm_window("not-a-window"))
        self.assertIsNone(parse_hhmm_window("09:00-nope"))

    def test_within_hhmm_window_same_day(self) -> None:
        self.assertTrue(
            within_hhmm_window(
                now=datetime(2026, 5, 29, 11, 30, tzinfo=KOREA_TZ),
                window_text="11:00-13:00",
            )
        )
        self.assertFalse(
            within_hhmm_window(
                now=datetime(2026, 5, 29, 13, 0, tzinfo=KOREA_TZ),
                window_text="11:00-13:00",
            )
        )

    def test_within_hhmm_window_wraps_midnight(self) -> None:
        self.assertTrue(
            within_hhmm_window(
                now=datetime(2026, 5, 29, 23, 30, tzinfo=KOREA_TZ),
                window_text="22:00-02:00",
            )
        )
        self.assertTrue(
            within_hhmm_window(
                now=datetime(2026, 5, 30, 1, 30, tzinfo=KOREA_TZ),
                window_text="22:00-02:00",
            )
        )
        self.assertFalse(
            within_hhmm_window(
                now=datetime(2026, 5, 30, 2, 0, tzinfo=KOREA_TZ),
                window_text="22:00-02:00",
            )
        )


class SchedulerTickDecisionTests(unittest.TestCase):
    def _now(self) -> datetime:
        return datetime(2026, 5, 29, 10, 0, tzinfo=KOREA_TZ)

    def test_sell_and_buy_due(self) -> None:
        decision = build_scheduler_tick_decision(
            sell_check_due=True,
            buy_scan_due=True,
            api_budget_state={},
            now=self._now(),
        )

        self.assertEqual(decision["decision"], "SELL_PRIORITY_WITH_BUY")
        self.assertTrue(decision["sell_check_due"])
        self.assertTrue(decision["buy_scan_due"])
        self.assertFalse(decision["skip_cycle"])

    def test_transient_backoff_skips_whole_cycle(self) -> None:
        now = self._now()
        decision = build_scheduler_tick_decision(
            sell_check_due=True,
            buy_scan_due=True,
            api_budget_state={"transient_error_until": now + timedelta(seconds=30)},
            now=now,
        )

        self.assertEqual(decision["decision"], "API_TRANSIENT_BACKOFF_WAIT")
        self.assertFalse(decision["sell_check_due"])
        self.assertFalse(decision["buy_scan_due"])
        self.assertTrue(decision["skip_cycle"])

    def test_rate_limit_backoff_preserves_due_sell_watch_and_defers_buy(self) -> None:
        now = self._now()
        decision = build_scheduler_tick_decision(
            sell_check_due=True,
            buy_scan_due=True,
            api_budget_state={"backoff_until": now + timedelta(seconds=30)},
            now=now,
        )

        self.assertEqual(decision["decision"], "API_BACKOFF_WAIT")
        self.assertTrue(decision["sell_check_due"])
        self.assertFalse(decision["buy_scan_due"])
        self.assertFalse(decision["skip_cycle"])

    def test_balance_rate_limit_backoff_skips_whole_cycle_even_when_sell_is_due(self) -> None:
        now = self._now()
        decision = build_scheduler_tick_decision(
            sell_check_due=True,
            buy_scan_due=True,
            api_budget_state={
                "backoff_until": now + timedelta(seconds=30),
                "last_rate_limit_source": "balance",
            },
            now=now,
        )

        self.assertEqual(decision["decision"], "API_BACKOFF_WAIT")
        self.assertFalse(decision["sell_check_due"])
        self.assertFalse(decision["buy_scan_due"])
        self.assertTrue(decision["skip_cycle"])

    def test_rate_limit_backoff_skips_when_only_buy_is_due(self) -> None:
        now = self._now()
        decision = build_scheduler_tick_decision(
            sell_check_due=False,
            buy_scan_due=True,
            api_budget_state={"backoff_until": now + timedelta(seconds=30)},
            now=now,
        )

        self.assertEqual(decision["decision"], "API_BACKOFF_WAIT")
        self.assertFalse(decision["sell_check_due"])
        self.assertFalse(decision["buy_scan_due"])
        self.assertTrue(decision["skip_cycle"])

    def test_quote_budget_defers_buy_when_sell_is_due(self) -> None:
        decision = build_scheduler_tick_decision(
            sell_check_due=True,
            buy_scan_due=True,
            api_budget_state={
                "quotes_used_this_tick": 2,
                "soft_max_quotes_per_tick": 2,
            },
            now=self._now(),
        )

        self.assertEqual(decision["decision"], "SELL_PRIORITY_DEFER_BUY_BUDGET")
        self.assertTrue(decision["sell_check_due"])
        self.assertFalse(decision["buy_scan_due"])
        self.assertFalse(decision["skip_cycle"])


class EngineScheduleStateOutputTests(unittest.TestCase):
    def test_print_engine_schedule_state_includes_dynamic_runtime_context(self) -> None:
        settings = SimpleNamespace(
            sell_check_interval_seconds=20,
            buy_scan_interval_seconds=180,
            scan_symbols_max_per_cycle=36,
            buy_scan_deep_eval_limit=6,
            api_soft_max_requests_per_second=5,
            api_soft_max_quotes_per_tick=2,
        )
        now = datetime(2026, 5, 29, 10, 0, tzinfo=KOREA_TZ)
        last_sell = datetime(2026, 5, 29, 9, 59, tzinfo=KOREA_TZ)
        last_buy = datetime(2026, 5, 29, 9, 55, tzinfo=KOREA_TZ)
        buffer = io.StringIO()

        with (
            mock.patch("app.runtime.session_loop.get_korean_now", return_value=now),
            redirect_stdout(buffer),
        ):
            print_engine_schedule_state(
                settings=settings,
                sell_check_due=True,
                buy_scan_due=False,
                effective_sell_check_interval_seconds=30,
                effective_buy_scan_interval_seconds=240,
                last_sell_check_at=last_sell,
                last_buy_scan_at=last_buy,
                scheduler_decision="SELL_ONLY_DUE",
                runtime_rate_control={
                    "mode": "degraded",
                    "reason": "rate_limit_hits_10m=3",
                    "effective_scan_symbols_max_per_cycle": 16,
                    "effective_buy_scan_deep_eval_limit": 2,
                    "effective_sell_watch_max_holdings_per_tick": 4,
                },
                api_budget_state={
                    "recent_request_count": 1,
                    "quotes_used_this_tick": 2,
                    "backoff_remaining_seconds": 30,
                    "transient_backoff_remaining_seconds": 0,
                    "rate_limit_hits": 3,
                    "recent_rate_limit_hits_10m": 3,
                    "recent_transient_error_hits_10m": 0,
                    "consecutive_backoff_cycles": 4,
                },
            )

        output = buffer.getvalue()
        self.assertIn("=== 엔진 스케줄 상태 ===", output)
        self.assertIn("tick_at=2026-05-29T10:00:00+09:00", output)
        self.assertIn("SELL check due: YES (interval=20s -> 30s(dynamic))", output)
        self.assertIn("BUY scan due: NO (interval=180s -> 240s(dynamic))", output)
        self.assertIn("last_sell_check_at=2026-05-29T09:59:00+09:00", output)
        self.assertIn("last_buy_scan_at=2026-05-29T09:55:00+09:00", output)
        self.assertIn("scheduler decision=SELL_ONLY_DUE", output)
        self.assertIn(
            "runtime control=degraded | reason=rate_limit_hits_10m=3 | "
            "scan_max=16 | deep_eval=2 | sell_cap=4",
            output,
        )
        self.assertIn(
            "api_budget=requests:1/5, quotes:2/2, backoff_remaining:30s, "
            "transient_backoff:0s, rate_limit_hits:3, rate_limit_hits_10m:3, "
            "transient_hits_10m:0, backoff_cycles:4",
            output,
        )

    def test_print_engine_schedule_state_uses_base_intervals_and_empty_timestamps(self) -> None:
        settings = SimpleNamespace(
            sell_check_interval_seconds=20,
            buy_scan_interval_seconds=180,
            scan_symbols_max_per_cycle=36,
            buy_scan_deep_eval_limit=6,
            api_soft_max_requests_per_second=5,
            api_soft_max_quotes_per_tick=2,
        )
        now = datetime(2026, 5, 29, 10, 0, tzinfo=KOREA_TZ)
        buffer = io.StringIO()

        with (
            mock.patch("app.runtime.session_loop.get_korean_now", return_value=now),
            redirect_stdout(buffer),
        ):
            print_engine_schedule_state(
                settings=settings,
                sell_check_due=False,
                buy_scan_due=True,
            )

        output = buffer.getvalue()
        self.assertIn("SELL check due: NO (interval=20s)", output)
        self.assertIn("BUY scan due: YES (interval=180s)", output)
        self.assertIn("last_sell_check_at=-", output)
        self.assertIn("last_buy_scan_at=-", output)
        self.assertNotIn("scheduler decision=", output)
        self.assertNotIn("runtime control=", output)
        self.assertNotIn("api_budget=", output)


class BuildRuntimeRateControlTests(unittest.TestCase):
    @staticmethod
    def _settings() -> SimpleNamespace:
        return SimpleNamespace(
            sell_check_interval_seconds=20,
            buy_scan_interval_seconds=180,
            scan_symbols_max_per_cycle=36,
            buy_scan_shallow_top_k=10,
            buy_scan_deep_eval_limit=6,
            adaptive_midday_enabled=True,
            adaptive_midday_window="11:00-13:00",
            adaptive_midday_buy_scan_interval_seconds=240,
            adaptive_midday_sell_check_interval_seconds=25,
            adaptive_midday_scan_symbols_max_per_cycle=24,
            adaptive_midday_buy_scan_deep_eval_limit=4,
            degraded_mode_enabled=True,
            degraded_mode_rate_limit_hits_in_10m=3,
            degraded_mode_consecutive_backoff_cycles=4,
            degraded_mode_duration_seconds=600,
            degraded_mode_buy_scan_interval_seconds=300,
            degraded_mode_sell_check_interval_seconds=30,
            degraded_mode_scan_symbols_max_per_cycle=16,
            degraded_mode_buy_scan_deep_eval_limit=2,
            degraded_mode_sell_watch_max_holdings_per_tick=4,
        )

    def test_build_runtime_rate_control_is_exposed_from_session_loop(self) -> None:
        from app.runtime.session_loop import build_runtime_rate_control

        now = datetime(2026, 4, 18, 14, 30, tzinfo=KOREA_TZ)
        api_budget_state = {
            "recent_rate_limit_hit_times": [],
            "consecutive_backoff_cycles": 0,
            "degraded_mode_until": None,
            "degraded_mode_reason": None,
            "backoff_until": None,
        }

        runtime = build_runtime_rate_control(
            settings=self._settings(),
            api_budget_state=api_budget_state,
            now=now,
        )

        self.assertEqual(runtime["mode"], "normal")
        self.assertEqual(runtime["effective_sell_check_interval_seconds"], 20)
        self.assertEqual(runtime["effective_buy_scan_interval_seconds"], 180)
        self.assertEqual(runtime["effective_buy_scan_shallow_top_k"], 10)
        self.assertEqual(runtime["effective_buy_scan_deep_eval_limit"], 6)
        self.assertIsNone(runtime["effective_sell_watch_max_holdings_per_tick"])


class NoteRateLimitBackoffTests(unittest.TestCase):
    def test_consecutive_hits_double_backoff_up_to_600s_cap(self) -> None:
        now = datetime(2026, 5, 27, 10, 0, 0)
        state: dict[str, object] = {}
        expected = [60, 120, 240, 480, 600, 600]

        with redirect_stdout(io.StringIO()), mock.patch(
            "app.runtime.session_loop.note_rate_limit"
        ), mock.patch("app.runtime.session_loop._record_bottleneck"):
            for index, expected_backoff in enumerate(expected, start=1):
                note_rate_limit_backoff(state, now=now, source="orderable")
                self.assertEqual(state["rate_limit_hits"], index)
                self.assertEqual(
                    state["backoff_until"],
                    now + timedelta(seconds=expected_backoff),
                )
                self.assertEqual(state["last_rate_limit_source"], "orderable")


class BuildCycleIdTests(unittest.TestCase):
    def test_cycle_id_format_timestamp_dash_8hex(self) -> None:
        import re

        cycle_id = build_cycle_id()

        self.assertRegex(cycle_id, r"^\d{8}T\d{6}-[0-9a-f]{8}$")


if __name__ == "__main__":
    unittest.main()
