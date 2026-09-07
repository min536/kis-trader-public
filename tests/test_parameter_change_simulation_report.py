import unittest

from app.tools.parameter_change_simulation_report import (
    estimate_budget_effects,
    estimate_core_effects,
)


class ParameterChangeSimulationReportTests(unittest.TestCase):
    def test_estimate_budget_effects_reduces_pressure_under_larger_sell_interval(self) -> None:
        payload = estimate_budget_effects(
            cycles=59,
            sell_partial=46,
            rate_limit_triggered=45,
            buy_skip=9,
            sell_watch_rl_source=36,
            sell_interval_current=15,
            sell_interval_suggested=22,
            request_reserve_delta=1,
            quote_reserve_delta=1,
        )

        self.assertLess(payload["estimated_sell_partial"], 46)
        self.assertLess(payload["estimated_rate_limit_triggered"], 45)
        self.assertLessEqual(payload["estimated_buy_skip"], 9)

    def test_estimate_core_effects_uses_threshold_scenario(self) -> None:
        core = {
            "threshold_simulation": {
                "available": True,
                "current_hits": 0,
                "scenarios": [
                    {
                        "threshold": 3,
                        "would_meet_threshold_count": 0,
                        "delta_vs_current_threshold": 0,
                        "flipped_row_count": 0,
                        "flipped_symbols": {},
                    },
                    {
                        "threshold": 2,
                        "would_meet_threshold_count": 3,
                        "delta_vs_current_threshold": 3,
                        "flipped_row_count": 3,
                        "flipped_symbols": {"000270": 2, "035420": 1},
                    },
                ],
            }
        }

        payload = estimate_core_effects(
            core=core,
            current_threshold=3,
            suggested_threshold=2,
        )

        self.assertTrue(payload["available"])
        self.assertEqual(payload["estimated_additional_hits"], 3)
        self.assertEqual(payload["flipped_symbols"], {"000270": 2, "035420": 1})


if __name__ == "__main__":
    unittest.main()
