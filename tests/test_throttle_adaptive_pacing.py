from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from app.core import throttle as throttle_module


class ThrottleAdaptivePacingTests(unittest.TestCase):
    def setUp(self) -> None:
        throttle_module._REQUEST_TIMESTAMPS.clear()
        throttle_module._QUOTE_TIMESTAMPS.clear()
        throttle_module._LAST_CATEGORY_TS["request"] = 0.0
        throttle_module._LAST_CATEGORY_TS["quote"] = 0.0
        throttle_module._LAST_GLOBAL_TS = 0.0
        throttle_module._ADAPTIVE_PENALTY_UNTIL = 0.0
        throttle_module._ADAPTIVE_PENALTY_EXTRA_DELAY_SECONDS = 0.0
        throttle_module._LAST_RATE_LIMIT_SOURCE = None
        throttle_module.reset_throttle_metrics()

    def test_adaptive_pacing_applies_to_non_quote_requests(self) -> None:
        settings = SimpleNamespace(
            api_soft_max_requests_per_second=100,
            api_soft_max_quotes_per_tick=100,
        )
        sleep_calls: list[float] = []

        with (
            mock.patch.object(throttle_module, "get_settings", return_value=settings),
            mock.patch.object(
                throttle_module,
                "get_adaptive_pacing_summary",
                return_value={
                    "active": True,
                    "extra_delay_ms": 120.0,
                    "remaining_ms": 6000.0,
                },
            ),
            mock.patch.object(throttle_module.time, "perf_counter", side_effect=[100.0, 100.12]),
            mock.patch.object(throttle_module.time, "sleep", side_effect=sleep_calls.append),
        ):
            result = throttle_module.throttle(
                minimum_interval=0.0,
                category="order",
            )

        self.assertEqual(result["category"], "request")
        self.assertEqual(sleep_calls, [0.12])

    def test_adaptive_pacing_still_applies_to_quote_requests(self) -> None:
        settings = SimpleNamespace(
            api_soft_max_requests_per_second=100,
            api_soft_max_quotes_per_tick=100,
        )
        sleep_calls: list[float] = []

        with (
            mock.patch.object(throttle_module, "get_settings", return_value=settings),
            mock.patch.object(
                throttle_module,
                "get_adaptive_pacing_summary",
                return_value={
                    "active": True,
                    "extra_delay_ms": 120.0,
                    "remaining_ms": 6000.0,
                },
            ),
            mock.patch.object(throttle_module.time, "perf_counter", side_effect=[100.0, 100.12]),
            mock.patch.object(throttle_module.time, "sleep", side_effect=sleep_calls.append),
        ):
            result = throttle_module.throttle(
                minimum_interval=0.0,
                category="quote",
            )

        self.assertEqual(result["category"], "quote")
        self.assertEqual(sleep_calls, [0.12])

    def test_request_floor_defaults_to_conservative_spacing(self) -> None:
        settings = SimpleNamespace(
            api_soft_max_requests_per_second=100,
            api_soft_max_quotes_per_tick=100,
        )
        throttle_module._LAST_CATEGORY_TS["request"] = 100.0
        sleep_calls: list[float] = []

        with (
            mock.patch.object(throttle_module, "get_settings", return_value=settings),
            mock.patch.object(
                throttle_module,
                "get_adaptive_pacing_summary",
                return_value={
                    "active": False,
                    "extra_delay_ms": 0.0,
                    "remaining_ms": 0.0,
                },
            ),
            mock.patch.object(throttle_module.time, "perf_counter", side_effect=[100.10, 100.80]),
            mock.patch.object(throttle_module.time, "sleep", side_effect=sleep_calls.append),
        ):
            result = throttle_module.throttle(category="request")

        self.assertEqual(result["category"], "request")
        self.assertAlmostEqual(sleep_calls[0], 0.70)
        self.assertEqual(result["sleep_ms"], 700.0)

    def test_request_floor_can_be_tightened_by_settings(self) -> None:
        settings = SimpleNamespace(
            api_soft_max_requests_per_second=100,
            api_soft_max_quotes_per_tick=100,
            api_min_inter_request_seconds=0.8,
        )
        throttle_module._LAST_CATEGORY_TS["request"] = 100.0
        sleep_calls: list[float] = []

        with (
            mock.patch.object(throttle_module, "get_settings", return_value=settings),
            mock.patch.object(
                throttle_module,
                "get_adaptive_pacing_summary",
                return_value={
                    "active": False,
                    "extra_delay_ms": 0.0,
                    "remaining_ms": 0.0,
                },
            ),
            mock.patch.object(throttle_module.time, "perf_counter", side_effect=[100.20, 100.80]),
            mock.patch.object(throttle_module.time, "sleep", side_effect=sleep_calls.append),
        ):
            result = throttle_module.throttle(category="request")

        self.assertEqual(result["category"], "request")
        self.assertAlmostEqual(sleep_calls[0], 0.60)
        self.assertEqual(result["sleep_ms"], 600.0)

    def test_request_floor_applies_across_categories(self) -> None:
        settings = SimpleNamespace(
            api_soft_max_requests_per_second=100,
            api_soft_max_quotes_per_tick=100,
            api_min_inter_request_seconds=0.8,
        )
        throttle_module._LAST_CATEGORY_TS["request"] = 90.0
        throttle_module._LAST_CATEGORY_TS["quote"] = 100.0
        throttle_module._LAST_GLOBAL_TS = 100.0
        sleep_calls: list[float] = []

        with (
            mock.patch.object(throttle_module, "get_settings", return_value=settings),
            mock.patch.object(
                throttle_module,
                "get_adaptive_pacing_summary",
                return_value={
                    "active": False,
                    "extra_delay_ms": 0.0,
                    "remaining_ms": 0.0,
                },
            ),
            mock.patch.object(throttle_module.time, "perf_counter", side_effect=[100.20, 100.80]),
            mock.patch.object(throttle_module.time, "sleep", side_effect=sleep_calls.append),
        ):
            result = throttle_module.throttle(category="request")

        self.assertEqual(result["category"], "request")
        self.assertAlmostEqual(sleep_calls[0], 0.60)
        self.assertEqual(result["sleep_ms"], 600.0)

    def test_note_rate_limit_records_last_source_for_diagnostics(self) -> None:
        with mock.patch.object(throttle_module.time, "perf_counter", return_value=100.0):
            throttle_module.note_rate_limit(source="buy_scan")

            summary = throttle_module.get_adaptive_pacing_summary()

        self.assertTrue(summary["active"])
        self.assertEqual(summary["last_rate_limit_source"], "buy_scan")


if __name__ == "__main__":
    unittest.main()
