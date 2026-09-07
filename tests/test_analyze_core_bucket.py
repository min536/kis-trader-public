import unittest

from app.tools.analyze_core_bucket import (
    _rule_lift_opportunities,
    _symbol_failure_diagnosis,
    _threshold_simulation,
)


class AnalyzeCoreBucketDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            {
                "symbol": "0001",
                "symbol_name": "Alpha",
                "cycle_id": "c1",
                "passed_count_deep": 2,
                "score_deep": 3.2,
                "rejection_reason": "passed_count_insufficient",
                "strategy_pass_pattern": "P F P F F",
            },
            {
                "symbol": "0001",
                "symbol_name": "Alpha",
                "cycle_id": "c2",
                "passed_count_deep": 2,
                "score_deep": 2.8,
                "rejection_reason": "passed_count_insufficient",
                "strategy_pass_pattern": "P F F F P",
            },
            {
                "symbol": "0002",
                "symbol_name": "Beta",
                "cycle_id": "c3",
                "passed_count_deep": 1,
                "score_deep": 1.4,
                "rejection_reason": "passed_count_insufficient",
                "strategy_pass_pattern": "P F F F F",
            },
            {
                "symbol": "0003",
                "symbol_name": "Gamma",
                "cycle_id": "c4",
                "passed_count_deep": 2,
                "score_deep": 3.6,
                "rejection_reason": "passed_count_insufficient",
                "strategy_pass_pattern": "F P P F P",
            },
        ]
        self.non_core_strategy_hit_rate = {
            "per_strategy": {
                "intraday_pullback": {"pass_count": 3, "total": 3},
                "rebound_from_low": {"pass_count": 3, "total": 3},
                "controlled_down_day": {"pass_count": 1, "total": 3},
                "gap_down_open": {"pass_count": 0, "total": 3},
                "range_recovery": {"pass_count": 3, "total": 3},
            }
        }

    def test_threshold_simulation_counts_flipped_rows_when_threshold_lowers(self) -> None:
        result = _threshold_simulation(self.rows, 3)

        self.assertTrue(result["available"])
        self.assertEqual(result["current_required_pass_count"], 3)
        self.assertEqual(result["current_hits"], 0)
        scenarios = {entry["threshold"]: entry for entry in result["scenarios"]}
        self.assertEqual(scenarios[3]["flipped_row_count"], 0)
        self.assertEqual(scenarios[2]["flipped_row_count"], 3)
        self.assertEqual(scenarios[2]["flipped_symbols"], {"0001": 2, "0003": 1})
        self.assertEqual(scenarios[1]["would_meet_threshold_count"], 4)

    def test_rule_lift_opportunities_ranks_missing_rules_for_near_threshold_rows(self) -> None:
        result = _rule_lift_opportunities(
            self.rows,
            3,
            non_core_shr=self.non_core_strategy_hit_rate,
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["near_threshold_row_count"], 3)
        ranked = {entry["rule"]: entry for entry in result["ranked_rules"]}
        self.assertEqual(ranked["rebound_from_low"]["near_threshold_row_count"], 2)
        self.assertEqual(ranked["range_recovery"]["near_threshold_row_count"], 1)
        self.assertEqual(ranked["controlled_down_day"]["symbols"], ["0001"])
        self.assertEqual(result["ranked_rules"][0]["rule"], "rebound_from_low")

    def test_symbol_failure_diagnosis_splits_threshold_only_and_structural_gap(self) -> None:
        result = _symbol_failure_diagnosis(
            self.rows,
            3,
            structural_gap_rules=["rebound_from_low", "range_recovery"],
            non_core_shr=self.non_core_strategy_hit_rate,
        )

        self.assertTrue(result["available"])
        by_symbol = {entry["symbol"]: entry for entry in result["symbols"]}
        self.assertEqual(by_symbol["0001"]["verdict"], "structural_gap")
        self.assertEqual(by_symbol["0001"]["one_rule_short_count"], 2)
        self.assertEqual(by_symbol["0002"]["verdict"], "structural_gap")
        self.assertEqual(by_symbol["0003"]["verdict"], "threshold_only")


if __name__ == "__main__":
    unittest.main()
