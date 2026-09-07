from __future__ import annotations

import unittest

from app.tools.recommend_score_guards import build_recommendation_summary


class RecommendScoreGuardsTests(unittest.TestCase):
    def test_build_recommendation_summary_derives_overall_and_core_overrides(self) -> None:
        rows = [
            {
                "deep_evaluated": True,
                "selection_bucket": "core",
                "executed": False,
                "final_candidate": True,
                "rejection_reason": None,
                "score_deep": 4.01,
                "passed_count_deep": 3,
            },
            {
                "deep_evaluated": True,
                "selection_bucket": "core",
                "executed": False,
                "final_candidate": False,
                "rejection_reason": "score_below_threshold",
                "score_deep": 3.15,
                "passed_count_deep": 2,
            },
            {
                "deep_evaluated": True,
                "selection_bucket": "rotating",
                "executed": True,
                "final_candidate": True,
                "rejection_reason": None,
                "score_deep": 3.42,
                "passed_count_deep": 3,
            },
            {
                "deep_evaluated": True,
                "selection_bucket": "rotating",
                "executed": False,
                "final_candidate": False,
                "rejection_reason": "score_below_threshold",
                "score_deep": 3.25,
                "passed_count_deep": 2,
            },
        ]

        summary = build_recommendation_summary(rows)
        overrides = summary["recommended_settings_overrides"]

        self.assertEqual(overrides["buy_rule_required_pass_count"], 3)
        self.assertEqual(overrides["buy_min_score"], 3.3)
        self.assertEqual(overrides["buy_rule_required_pass_count_core"], 3)
        self.assertEqual(overrides["buy_min_score_core"], 3.2)

    def test_missing_positive_or_negative_rows_yields_no_recommendation(self) -> None:
        rows = [
            {
                "deep_evaluated": True,
                "selection_bucket": "rotating",
                "executed": False,
                "final_candidate": False,
                "rejection_reason": "score_below_threshold",
                "score_deep": 2.8,
                "passed_count_deep": 2,
            }
        ]

        summary = build_recommendation_summary(rows)
        self.assertEqual(summary["recommended_settings_overrides"], {})


if __name__ == "__main__":
    unittest.main()
