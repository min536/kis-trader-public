import unittest

from app.tools.preopen_operator_brief import build_parameter_nudges


class PreopenOperatorBriefTests(unittest.TestCase):
    def test_build_parameter_nudges_surfaces_budget_and_core_knobs(self) -> None:
        class _Settings:
            sell_check_interval_seconds = 5
            api_buy_scan_min_request_reserve = 1
            api_buy_scan_min_quote_reserve = 1
            buy_rule_required_pass_count_core = 3

        health = {
            "sell_watch_partial_count": 46,
            "rate_limit_triggered_count": 45,
            "buy_scan_rate_limit_skip_count": 9,
            "rate_limit_sources": {"sell_watch": 36},
        }
        budget = {"meta": {"cycles_analyzed": 59}}
        core = {
            "rule_contrast_interpretation": {
                "verdict": "threshold_only",
                "gap_to_threshold": 1,
            },
            "rule_lift_opportunities": {
                "near_threshold_row_count": 3,
            },
            "symbol_failure_diagnosis": {
                "symbols": [
                    {
                        "symbol": "035420",
                        "verdict": "threshold_only",
                        "top_missing_rules": [
                            {"rule": "intraday_pullback"},
                            {"rule": "controlled_down_day"},
                        ],
                    }
                ]
            },
        }

        nudges = build_parameter_nudges(
            settings=_Settings(),
            health=health,
            budget=budget,
            core=core,
        )

        parameters = [item["parameter"] for item in nudges]
        self.assertIn("SELL_CHECK_INTERVAL_SECONDS", parameters)
        self.assertIn("API_BUY_SCAN_MIN_REQUEST_RESERVE", parameters)
        self.assertIn("API_BUY_SCAN_MIN_QUOTE_RESERVE", parameters)
        self.assertIn("BUY_RULE_REQUIRED_PASS_COUNT_CORE", parameters)


if __name__ == "__main__":
    unittest.main()
