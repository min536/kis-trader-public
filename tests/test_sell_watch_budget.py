import unittest

from app.core.sell_watch_budget import (
    build_sell_watch_budget_plan,
    compute_effective_sell_check_interval_seconds,
    should_update_sell_watch_partial_memory,
)


class SellWatchBudgetTests(unittest.TestCase):
    def test_interval_stretches_after_sell_watch_partial(self) -> None:
        self.assertEqual(
            compute_effective_sell_check_interval_seconds(
                base_interval_seconds=5,
                total_holdings=5,
                recent_partial=True,
                last_rate_limit_source=None,
            ),
            15,
        )

    def test_interval_stretches_more_after_sell_watch_rate_limit(self) -> None:
        self.assertEqual(
            compute_effective_sell_check_interval_seconds(
                base_interval_seconds=5,
                total_holdings=5,
                recent_partial=True,
                last_rate_limit_source="sell_watch",
            ),
            30,
        )

    def test_interval_stretches_more_after_repeated_sell_watch_partial(self) -> None:
        self.assertEqual(
            compute_effective_sell_check_interval_seconds(
                base_interval_seconds=5,
                total_holdings=5,
                recent_partial=True,
                last_rate_limit_source=None,
                recent_partial_streak=2,
            ),
            20,
        )

    def test_budget_plan_caps_sell_watch_when_buy_reserve_and_partial_pressure_exist(self) -> None:
        plan = build_sell_watch_budget_plan(
            total_holdings=6,
            remaining_requests=5,
            remaining_quotes=4,
            request_window_size=1,
            soft_request_limit=6,
            buy_scan_due=True,
            buy_scan_request_reserve=2,
            buy_scan_quote_reserve=1,
            recent_partial=True,
            last_rate_limit_source=None,
            rate_limit_hits=0,
        )

        self.assertEqual(plan.pressure_level, "partial_recent")
        self.assertEqual(plan.max_evaluations, 3)

    def test_budget_plan_caps_more_after_repeated_partial_pressure(self) -> None:
        plan = build_sell_watch_budget_plan(
            total_holdings=6,
            remaining_requests=6,
            remaining_quotes=6,
            request_window_size=1,
            soft_request_limit=6,
            buy_scan_due=True,
            buy_scan_request_reserve=0,
            buy_scan_quote_reserve=0,
            recent_partial=True,
            last_rate_limit_source=None,
            rate_limit_hits=0,
            recent_partial_streak=2,
        )

        self.assertEqual(plan.pressure_level, "partial_recent")
        self.assertEqual(plan.max_evaluations, 2)
        self.assertIn("반복", plan.reason)

    def test_partial_memory_updates_only_for_open_sell_watch_with_holdings(self) -> None:
        self.assertTrue(
            should_update_sell_watch_partial_memory(
                sell_check_due=True,
                market_open=True,
                holding_count=1,
            )
        )
        self.assertFalse(
            should_update_sell_watch_partial_memory(
                sell_check_due=False,
                market_open=True,
                holding_count=1,
            )
        )
        self.assertFalse(
            should_update_sell_watch_partial_memory(
                sell_check_due=True,
                market_open=False,
                holding_count=1,
            )
        )
        self.assertFalse(
            should_update_sell_watch_partial_memory(
                sell_check_due=True,
                market_open=True,
                holding_count=0,
            )
        )

    def test_budget_plan_caps_to_single_evaluation_after_sell_watch_rate_limit(self) -> None:
        plan = build_sell_watch_budget_plan(
            total_holdings=6,
            remaining_requests=5,
            remaining_quotes=5,
            request_window_size=0,
            soft_request_limit=6,
            buy_scan_due=True,
            buy_scan_request_reserve=1,
            buy_scan_quote_reserve=1,
            recent_partial=True,
            last_rate_limit_source="sell_watch",
            rate_limit_hits=1,
        )

        self.assertEqual(plan.pressure_level, "rate_limit_recent")
        self.assertEqual(plan.max_evaluations, 1)

    def test_budget_plan_preserves_sell_order_execution_reserve(self) -> None:
        plan = build_sell_watch_budget_plan(
            total_holdings=6,
            remaining_requests=3,
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

        self.assertEqual(plan.pressure_level, "normal")
        self.assertEqual(plan.max_evaluations, 1)

    def test_budget_plan_relaxes_execution_reserve_for_minimum_sell_watch(self) -> None:
        plan = build_sell_watch_budget_plan(
            total_holdings=6,
            remaining_requests=2,
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

        self.assertEqual(plan.pressure_level, "execution_reserve_relaxed")
        self.assertEqual(plan.max_evaluations, 1)
        self.assertIn("최소 1개", plan.reason)

    def test_budget_plan_skips_when_no_request_can_be_spent_on_sell_watch(self) -> None:
        plan = build_sell_watch_budget_plan(
            total_holdings=6,
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

        self.assertEqual(plan.pressure_level, "execution_reserve_exhausted")
        self.assertEqual(plan.max_evaluations, 0)

    def test_budget_plan_does_not_relax_buy_scan_reserve(self) -> None:
        plan = build_sell_watch_budget_plan(
            total_holdings=6,
            remaining_requests=3,
            remaining_quotes=6,
            request_window_size=0,
            soft_request_limit=5,
            buy_scan_due=True,
            buy_scan_request_reserve=3,
            buy_scan_quote_reserve=0,
            execution_request_reserve=0,
            recent_partial=False,
            last_rate_limit_source=None,
            rate_limit_hits=0,
        )

        self.assertEqual(plan.pressure_level, "buy_scan_reserve_exhausted")
        self.assertEqual(plan.max_evaluations, 0)

    def test_budget_plan_allows_full_scan_when_headroom_is_ample(self) -> None:
        plan = build_sell_watch_budget_plan(
            total_holdings=2,
            remaining_requests=6,
            remaining_quotes=6,
            request_window_size=0,
            soft_request_limit=6,
            buy_scan_due=False,
            buy_scan_request_reserve=0,
            buy_scan_quote_reserve=0,
            recent_partial=False,
            last_rate_limit_source=None,
            rate_limit_hits=0,
        )

        self.assertEqual(plan.pressure_level, "normal")
        self.assertEqual(plan.max_evaluations, 2)


if __name__ == "__main__":
    unittest.main()
