from __future__ import annotations

import unittest

from app.market_data.schema import MarketSnapshot
from app.scanner.service import build_candidate_reason, calculate_selection_score
from app.strategy.buy_decision import evaluate_buy_decision


class SelectionScoreStrengthTests(unittest.TestCase):
    def test_near_miss_rules_contribute_soft_signal_quality(self) -> None:
        snapshot = MarketSnapshot(
            symbol="005930",
            current_price=10_040,
            open_price=10_100,
            low_price=10_000,
            prev_day_change_pct=-0.5,
        )
        decision = evaluate_buy_decision(
            snapshot=snapshot,
            symbol="005930",
            qty=1,
            enable_intraday_pullback=True,
            enable_rebound_from_low=True,
            enable_controlled_down_day=True,
            enable_gap_down_open=False,
            enable_range_recovery=True,
            enable_live_volume_rank=False,
            enable_live_volume_power_rank=False,
            rebound_from_low_pct=0.005,
            controlled_down_day_min=-6.0,
            controlled_down_day_max=8.0,
            gap_down_open_min_pct=0.3,
            gap_down_open_max_pct=5.0,
            range_recovery_min_ratio=0.5,
            required_pass_count=3,
        )

        rule_by_name = {result.strategy_name: result for result in decision.rule_results}
        self.assertTrue(rule_by_name["intraday_pullback"].passed)
        self.assertFalse(rule_by_name["rebound_from_low"].passed)
        self.assertTrue(rule_by_name["controlled_down_day"].passed)
        self.assertFalse(rule_by_name["range_recovery"].passed)
        self.assertEqual(decision.passed_count, 2)

        score, components = calculate_selection_score(
            snapshot=snapshot,
            strategy_result=decision,
            rebound_from_low_pct=0.005,
            controlled_down_day_min=-6.0,
            controlled_down_day_max=8.0,
            gap_down_open_min_pct=0.3,
            gap_down_open_max_pct=5.0,
            range_recovery_min_ratio=0.5,
        )

        self.assertEqual(components["rebound_from_low_strength_score"], 80.0)
        self.assertEqual(components["range_recovery_strength_score"], 80.0)
        self.assertEqual(components["gap_down_open_strength_score"], 0.0)
        self.assertEqual(components["soft_passed_count_base"], 3.6)
        self.assertGreater(score, float(decision.passed_count))

    def test_candidate_reason_uses_soft_signal_quality_count(self) -> None:
        reason = build_candidate_reason(
            passed_count=2,
            min_passed_count=3,
            signal_quality_count=3.6,
            score=3.2,
            min_score=3.1,
            net_profit_buffer_bps=12.0,
            min_net_profit_buffer_bps=8.0,
            passes_profit_buffer=True,
            use_cost_aware_pnl=True,
            expected_cost_bps=18.0,
            expected_cost_block_bps=40.0,
            net_edge_bps=9.0,
            min_net_edge_bps=5.0,
            cost_block_reason=None,
        )

        self.assertIn("signal_quality 3.60개 상당", reason)
        self.assertIn("후보로 선정", reason)


if __name__ == "__main__":
    unittest.main()
