from __future__ import annotations

import unittest
from datetime import datetime

from app import main as main_module


class BuyScanBudgetCapTests(unittest.TestCase):
    def test_deep_eval_symbols_are_capped_by_remaining_quote_budget(self) -> None:
        capped, metadata = main_module._cap_buy_scan_deep_eval_symbols_for_api_budget(
            symbols=("005930", "000660", "035420", "051910"),
            api_budget_state={
                "quotes_used_this_tick": 3,
                "soft_max_quotes_per_tick": 5,
                "recent_requests": [],
                "soft_max_requests_per_second": 5,
            },
            now=datetime(2026, 4, 25, 10, 0, 0),
        )

        self.assertEqual(capped, ("005930", "000660"))
        self.assertTrue(metadata["budget_cap_applied"])
        self.assertEqual(metadata["budget_cap_reasons"], ["quote_budget"])
        self.assertTrue(metadata["quote_budget_cap_applied"])
        self.assertFalse(metadata["request_budget_cap_applied"])
        self.assertEqual(metadata["quote_budget_remaining_before_scan"], 2)
        self.assertEqual(metadata["request_budget_remaining_before_scan"], 5)
        self.assertEqual(metadata["quote_budget_original_count"], 4)
        self.assertEqual(metadata["budget_capped_count"], 2)

    def test_deep_eval_symbols_remain_when_quote_budget_is_sufficient(self) -> None:
        capped, metadata = main_module._cap_buy_scan_deep_eval_symbols_for_api_budget(
            symbols=("005930", "000660"),
            api_budget_state={
                "quotes_used_this_tick": 1,
                "soft_max_quotes_per_tick": 5,
                "recent_requests": [],
                "soft_max_requests_per_second": 5,
            },
            now=datetime(2026, 4, 25, 10, 0, 0),
        )

        self.assertEqual(capped, ("005930", "000660"))
        self.assertFalse(metadata["budget_cap_applied"])
        self.assertEqual(metadata["budget_cap_reasons"], [])
        self.assertEqual(metadata["quote_budget_remaining_before_scan"], 4)

    def test_deep_eval_symbols_are_capped_by_remaining_request_budget(self) -> None:
        now = datetime(2026, 4, 25, 10, 0, 0)
        capped, metadata = main_module._cap_buy_scan_deep_eval_symbols_for_api_budget(
            symbols=("005930", "000660", "035420"),
            api_budget_state={
                "quotes_used_this_tick": 0,
                "soft_max_quotes_per_tick": 5,
                "recent_requests": [now, now],
                "soft_max_requests_per_second": 3,
            },
            now=now,
        )

        self.assertEqual(capped, ("005930",))
        self.assertTrue(metadata["budget_cap_applied"])
        self.assertEqual(metadata["budget_cap_reasons"], ["request_budget"])
        self.assertFalse(metadata["quote_budget_cap_applied"])
        self.assertTrue(metadata["request_budget_cap_applied"])
        self.assertEqual(metadata["request_budget_remaining_before_scan"], 1)

    def test_deep_eval_cap_reports_both_budget_reasons(self) -> None:
        now = datetime(2026, 4, 25, 10, 0, 0)
        capped, metadata = main_module._cap_buy_scan_deep_eval_symbols_for_api_budget(
            symbols=("005930", "000660", "035420"),
            api_budget_state={
                "quotes_used_this_tick": 4,
                "soft_max_quotes_per_tick": 5,
                "recent_requests": [now, now],
                "soft_max_requests_per_second": 3,
            },
            now=now,
        )

        self.assertEqual(capped, ("005930",))
        self.assertEqual(metadata["budget_cap_reasons"], ["quote_budget", "request_budget"])

    def test_execution_request_reserve_reduces_deep_eval_request_budget(self) -> None:
        now = datetime(2026, 4, 25, 10, 0, 0)
        capped, metadata = main_module._cap_buy_scan_deep_eval_symbols_for_api_budget(
            symbols=("005930", "000660", "035420", "051910"),
            api_budget_state={
                "quotes_used_this_tick": 0,
                "soft_max_quotes_per_tick": 10,
                "recent_requests": [],
                "soft_max_requests_per_second": 5,
            },
            now=now,
            execution_request_reserve=2,
        )

        self.assertEqual(capped, ("005930", "000660", "035420"))
        self.assertTrue(metadata["budget_cap_applied"])
        self.assertEqual(metadata["budget_cap_reasons"], ["request_budget"])
        self.assertEqual(metadata["request_budget_remaining_before_scan"], 5)
        self.assertEqual(metadata["execution_request_reserve"], 2)
        self.assertEqual(metadata["request_budget_available_for_scan"], 3)
        self.assertFalse(metadata["request_budget_floor_applied"])

    def test_scan_floor_relaxes_execution_reserve_for_buy_deep_eval(self) -> None:
        now = datetime(2026, 4, 25, 10, 0, 0)
        capped, metadata = main_module._cap_buy_scan_deep_eval_symbols_for_api_budget(
            symbols=("005930", "000660", "035420", "051910"),
            api_budget_state={
                "quotes_used_this_tick": 0,
                "soft_max_quotes_per_tick": 10,
                "recent_requests": [],
                "soft_max_requests_per_second": 4,
            },
            now=now,
            execution_request_reserve=2,
            min_scan_request_floor=3,
        )

        self.assertEqual(capped, ("005930", "000660", "035420"))
        self.assertTrue(metadata["budget_cap_applied"])
        self.assertEqual(metadata["budget_cap_reasons"], ["request_budget"])
        self.assertEqual(metadata["request_budget_remaining_before_scan"], 4)
        self.assertEqual(metadata["execution_request_reserve"], 2)
        self.assertEqual(metadata["min_scan_request_floor"], 3)
        self.assertTrue(metadata["request_budget_floor_applied"])
        self.assertEqual(metadata["execution_request_reserve_relaxed_by"], 1)
        self.assertEqual(metadata["request_budget_available_for_scan"], 3)


if __name__ == "__main__":
    unittest.main()
