import unittest

from app.core.sell_watch_budget import build_sell_watch_budget_plan
from app.core.sell_watch_cursor import (
    build_sell_watch_cursor_plan,
    retry_anchor_to_restore_after_empty_budget,
)


class SellWatchCursorTests(unittest.TestCase):
    def test_retry_anchor_reorders_from_retry_symbol(self) -> None:
        symbols = ("005930", "000660", "035420")

        plan = build_sell_watch_cursor_plan(
            symbols=symbols,
            next_start_index=0,
            retry_symbol="000660",
            risk_first_override=False,
        )

        self.assertEqual(plan.start_index, 1)
        self.assertEqual(
            [symbols[index] for index in plan.ordered_indices],
            ["000660", "035420", "005930"],
        )
        self.assertTrue(plan.used_retry_anchor)
        self.assertTrue(plan.clear_retry_symbol)

    def test_empty_execution_reserve_budget_restores_consumed_retry_anchor(self) -> None:
        symbols = ("005930", "000660", "035420")
        cursor_plan = build_sell_watch_cursor_plan(
            symbols=symbols,
            next_start_index=0,
            retry_symbol="000660",
            risk_first_override=False,
        )
        budget_plan = build_sell_watch_budget_plan(
            total_holdings=len(symbols),
            remaining_requests=0,
            remaining_quotes=6,
            request_window_size=0,
            soft_request_limit=5,
            buy_scan_due=False,
            buy_scan_request_reserve=0,
            buy_scan_quote_reserve=0,
            execution_request_reserve=2,
            recent_partial=False,
            last_rate_limit_source=None,
            rate_limit_hits=0,
        )

        restored = retry_anchor_to_restore_after_empty_budget(
            budget_limit=budget_plan.max_evaluations,
            retry_symbol=cursor_plan.retry_symbol,
            retry_anchor_consumed=cursor_plan.used_retry_anchor,
        )

        self.assertEqual(budget_plan.pressure_level, "execution_reserve_exhausted")
        self.assertEqual(budget_plan.max_evaluations, 0)
        self.assertEqual(restored, "000660")

    def test_non_empty_budget_does_not_restore_consumed_retry_anchor(self) -> None:
        restored = retry_anchor_to_restore_after_empty_budget(
            budget_limit=1,
            retry_symbol="000660",
            retry_anchor_consumed=True,
        )

        self.assertIsNone(restored)

    def test_empty_budget_does_not_restore_stale_retry_anchor(self) -> None:
        restored = retry_anchor_to_restore_after_empty_budget(
            budget_limit=0,
            retry_symbol="111111",
            retry_anchor_consumed=False,
        )

        self.assertIsNone(restored)

    def test_stale_retry_anchor_clears_and_keeps_cursor_rotation(self) -> None:
        symbols = ("005930", "000660", "035420")

        plan = build_sell_watch_cursor_plan(
            symbols=symbols,
            next_start_index=2,
            retry_symbol="111111",
            risk_first_override=False,
        )

        self.assertEqual(plan.start_index, 2)
        self.assertEqual(
            [symbols[index] for index in plan.ordered_indices],
            ["035420", "005930", "000660"],
        )
        self.assertFalse(plan.used_retry_anchor)
        self.assertTrue(plan.clear_retry_symbol)

    def test_risk_first_override_starts_from_first_symbol_without_retry(self) -> None:
        symbols = ("005930", "000660", "035420")

        plan = build_sell_watch_cursor_plan(
            symbols=symbols,
            next_start_index=2,
            retry_symbol=None,
            risk_first_override=True,
        )

        self.assertEqual(plan.start_index, 0)
        self.assertEqual([symbols[index] for index in plan.ordered_indices], list(symbols))
        self.assertFalse(plan.used_retry_anchor)
        self.assertFalse(plan.clear_retry_symbol)


if __name__ == "__main__":
    unittest.main()
