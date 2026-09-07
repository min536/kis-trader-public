"""Tests for app.gate2.adapter (G3; research shim removed 2026-07-03, eabecd7)."""

import unittest

from app.gate2 import adapter, schema


class ConditionScoresFromV1Test(unittest.TestCase):
    def setUp(self):
        self.caps = schema.default_artifact().normalization_caps

    def test_rich_input_full_mapping(self):
        components = {
            "intraday_pullback_strength_score": 80.0,
            "rebound_from_low_strength_score": 60.0,
            "controlled_down_strength_score": 100.0,
            "gap_down_open_strength_score": 0.0,
            "range_recovery_strength_score": 50.0,
            "live_volume_rank_strength_score": 100.0,
            "live_volume_power_rank_strength_score": 25.0,
            "trend_alignment_score": 0.35,
            "macd_momentum_score": 0.10,
            "cost_quality_score": 0.175,
            "mean_reversion_bonus": 0.15,
            "velocity_bonus": 0.20,
            "velocity_penalty": 0.0,
            "diversification_bonus": 0.15,
            "portfolio_correlation_penalty": 0.0,
            "variance_increase_penalty": 0.0,
        }
        result = adapter.condition_scores_from_v1(components, self.caps)
        self.assertEqual(
            result,
            {
                "pullback_strength_score": 80.0,
                "rebound_strength_score": 60.0,
                "controlled_down_quality_score": 100.0,
                "gap_down_quality_score": 0.0,
                "range_recovery_score": 50.0,
                "trend_alignment_score": 100.0,
                "macd_momentum_score": 50.0,
                "volume_rank_score": 100.0,
                "volume_power_score": 25.0,
                "cost_quality_score": 50.0,
                "mean_reversion_score": 50.0,
                "price_velocity_score": 100.0,
                "liquidity_score": None,
                "volatility_risk_score": 100.0,
                "portfolio_diversification_score": 100.0,
            },
        )


    def test_partial_input_missing_keys_yield_none(self):
        components = {
            "intraday_pullback_strength_score": 50.0,
            "trend_alignment_score": 0.175,  # clip(0.175/0.35*100) == 50
            "velocity_bonus": 0.10,  # penalty absent -> 0
        }
        result = adapter.condition_scores_from_v1(components, self.caps)
        self.assertEqual(
            result,
            {
                "pullback_strength_score": 50.0,
                "rebound_strength_score": None,
                "controlled_down_quality_score": None,
                "gap_down_quality_score": None,
                "range_recovery_score": None,
                "trend_alignment_score": 50.0,
                "macd_momentum_score": None,
                "volume_rank_score": None,
                "volume_power_score": None,
                "cost_quality_score": None,
                "mean_reversion_score": None,
                # price_velocity = clip(50 + (0.10/0.20 - 0)*50) == 75
                "price_velocity_score": 75.0,
                "liquidity_score": None,
                "volatility_risk_score": None,
                "portfolio_diversification_score": None,
            },
        )


    def test_all_missing_input_yields_all_none(self):
        result = adapter.condition_scores_from_v1({}, self.caps)
        self.assertEqual(
            result,
            {
                "pullback_strength_score": None,
                "rebound_strength_score": None,
                "controlled_down_quality_score": None,
                "gap_down_quality_score": None,
                "range_recovery_score": None,
                "trend_alignment_score": None,
                "macd_momentum_score": None,
                "volume_rank_score": None,
                "volume_power_score": None,
                "cost_quality_score": None,
                "mean_reversion_score": None,
                "price_velocity_score": None,
                "liquidity_score": None,
                "volatility_risk_score": None,
                "portfolio_diversification_score": None,
            },
        )


    def test_clip_boundaries(self):
        components = {
            "intraday_pullback_strength_score": 150.0,  # clip -> 100
            "gap_down_open_strength_score": -20.0,  # clip -> 0
            "trend_alignment_score": 0.70,  # 0.70/0.35*100 = 200 -> 100
            "variance_increase_penalty": 1.0,  # 100 - clip(200) = 0
        }
        result = adapter.condition_scores_from_v1(components, self.caps)
        self.assertEqual(result["pullback_strength_score"], 100.0)
        self.assertEqual(result["gap_down_quality_score"], 0.0)
        self.assertEqual(result["trend_alignment_score"], 100.0)
        self.assertEqual(result["volatility_risk_score"], 0.0)


    def test_bipolar_neutral_is_fifty(self):
        # Equal/zero bonus and penalty -> neutral 50.
        components = {
            "velocity_bonus": 0.0,
            "velocity_penalty": 0.0,
            "diversification_bonus": 0.0,
            "portfolio_correlation_penalty": 0.0,
        }
        result = adapter.condition_scores_from_v1(components, self.caps)
        self.assertEqual(result["price_velocity_score"], 50.0)
        self.assertEqual(result["portfolio_diversification_score"], 50.0)


    def test_bipolar_full_penalty_clips_to_zero(self):
        # Penalty at cap with no bonus -> 50 - 50 = 0 (lower clip boundary).
        components = {
            "velocity_penalty": self.caps["velocity_penalty"],
            "portfolio_correlation_penalty": self.caps[
                "portfolio_correlation_penalty"
            ],
        }
        result = adapter.condition_scores_from_v1(components, self.caps)
        self.assertEqual(result["price_velocity_score"], 0.0)
        self.assertEqual(result["portfolio_diversification_score"], 0.0)


if __name__ == "__main__":
    unittest.main()
