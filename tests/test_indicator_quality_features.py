from __future__ import annotations

import unittest
from unittest import mock

from app.market_data.schema import MarketSnapshot
from app.math_models.indicator_quality import build_indicator_quality_features


def _history_from_prices(prices: list[float]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, price in enumerate(prices):
        rows.append(
            {
                "timestamp": f"2026-05-07T09:{index:02d}:00+09:00",
                "price": price,
                "open_price": price * 1.002,
                "low_price": price * 0.995,
            }
        )
    return rows


class IndicatorQualityFeatureTests(unittest.TestCase):
    def test_builds_five_soft_indicator_scores_from_price_history(self) -> None:
        prices = [
            100.00,
            100.20,
            100.10,
            100.30,
            100.20,
            100.10,
            100.15,
            100.20,
            100.18,
            100.17,
            100.20,
            100.25,
            100.30,
            100.35,
            100.30,
            100.25,
            100.28,
            100.32,
            100.35,
        ]
        snapshot = MarketSnapshot(
            symbol="005930",
            current_price=100,
            open_price=101,
            low_price=99,
            prev_day_change_pct=-0.4,
        )
        score_components = {
            "rebound_from_low_strength_score": 80.0,
            "range_recovery_strength_score": 80.0,
            "rebound_from_low_bonus": 0.1,
            "range_recovery_bonus": 0.1,
        }

        with mock.patch(
            "app.math_models.indicator_quality.get_recent_symbol_price_history",
            return_value=_history_from_prices(prices),
        ):
            features = build_indicator_quality_features(
                symbol="005930",
                snapshot=snapshot,
                score_components=score_components,
            )

        self.assertTrue(features["data_sufficient"])
        self.assertGreater(features["rsi_recovery_score"], 0.0)
        self.assertGreater(features["bollinger_rebound_score"], 0.0)
        self.assertGreater(features["sma_support_score"], 0.0)
        self.assertGreater(features["atrp_balance_score"], 0.0)
        self.assertIn("bollinger_squeeze_score", features)

    def test_penalizes_overheated_rsi_and_high_atrp(self) -> None:
        prices = [100 + index * 2 for index in range(19)]
        snapshot = MarketSnapshot(
            symbol="005930",
            current_price=180,
            open_price=130,
            low_price=80,
            prev_day_change_pct=7.0,
        )

        with mock.patch(
            "app.math_models.indicator_quality.get_recent_symbol_price_history",
            return_value=_history_from_prices(prices),
        ):
            features = build_indicator_quality_features(
                symbol="005930",
                snapshot=snapshot,
                score_components={},
            )

        self.assertGreater(features["rsi_overheat_penalty"], 0.0)
        self.assertGreater(features["atrp_volatility_penalty"], 0.0)

    def test_returns_neutral_shadow_payload_when_history_is_insufficient(self) -> None:
        snapshot = MarketSnapshot(
            symbol="005930",
            current_price=100,
            open_price=101,
            low_price=99,
            prev_day_change_pct=0.0,
        )

        with mock.patch(
            "app.math_models.indicator_quality.get_recent_symbol_price_history",
            return_value=_history_from_prices([100.0, 100.2, 100.1]),
        ):
            features = build_indicator_quality_features(
                symbol="005930",
                snapshot=snapshot,
                score_components={"rebound_from_low_strength_score": 100.0},
            )

        self.assertFalse(features["data_sufficient"])
        self.assertEqual(features["observation_count"], 4)
        self.assertEqual(features["rsi_recovery_score"], 0.0)
        self.assertEqual(features["atrp_volatility_penalty"], 0.0)


if __name__ == "__main__":
    unittest.main()
