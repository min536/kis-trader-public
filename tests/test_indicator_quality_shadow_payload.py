from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.market_data.schema import MarketSnapshot
from app.scanner.service import _build_feature_payload


class IndicatorQualityShadowPayloadTests(unittest.TestCase):
    def test_indicator_quality_features_are_reported_without_score_component_promotion(self) -> None:
        score_components = {
            "passed_count_base": 3.0,
            "signal_quality_count": 3.0,
            "trend_quality_score": 0.4,
            "momentum_quality_score": 0.3,
            "price_efficiency_score": 0.2,
        }
        indicator_quality_features = {
            "rsi_value": 45.0,
            "rsi_recovery_score": 0.18,
            "rsi_overheat_penalty": 0.0,
            "bollinger_rebound_score": 0.12,
            "sma_support_score": 0.18,
            "atrp_balance_score": 0.10,
            "data_sufficient": True,
            "observation_count": 30,
            "indicator_quality_summary": "RSI 회복권 / SMA 지지",
        }

        feature_map, feature_vector, feature_summaries, math_score_summary = (
            _build_feature_payload(
                snapshot=MarketSnapshot(
                    symbol="005930",
                    current_price=70_000,
                    open_price=69_500,
                    low_price=69_000,
                    prev_day_change_pct=-0.2,
                ),
                strategy_result=SimpleNamespace(passed_count=3, enabled_count=5),
                score_components=score_components,
                cost_metrics={
                    "expected_fee_krw": 10,
                    "expected_tax_krw": 20,
                    "expected_slippage_krw": 30,
                    "expected_total_cost_krw": 60,
                    "expected_cost_bps": 10.0,
                    "cost_quality_score": 0.2,
                    "expected_cost_penalty": 0.1,
                    "net_edge_bps": 12.0,
                },
                technical_features={"trend_alignment_score": 0.2},
                indicator_quality_features=indicator_quality_features,
                mean_reversion_features={"mean_reversion_bonus": 0.0},
                portfolio_risk_features={"diversification_bonus": 0.0},
                price_dynamics_features={"velocity_bonus": 0.0},
                final_score=3.2,
            )
        )

        self.assertNotIn("rsi_recovery_score", score_components)
        self.assertEqual(feature_map["final_score_components"]["final_score"], 3.2)
        self.assertEqual(
            feature_map["indicator_quality_features"]["rsi_recovery_score"],
            0.18,
        )
        self.assertEqual(
            feature_vector["indicator_quality_features.sma_support_score"],
            0.18,
        )
        self.assertIn("rsi_recovery_score", feature_summaries["indicator_quality_features"])
        self.assertIn("indicator_shadow=", math_score_summary)


if __name__ == "__main__":
    unittest.main()
