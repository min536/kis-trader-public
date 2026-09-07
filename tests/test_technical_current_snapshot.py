from __future__ import annotations

import unittest
from unittest import mock

from app.market_data.schema import MarketSnapshot
from app.math_models.history import append_current_snapshot_observation
from app.math_models.technical import build_technical_features


class TechnicalCurrentSnapshotTests(unittest.TestCase):
    def _snapshot(self, *, symbol: str = "005930", price: int = 61_200) -> MarketSnapshot:
        return MarketSnapshot(
            symbol=symbol,
            current_price=price,
            open_price=60_000,
            low_price=59_800,
            prev_day_change_pct=1.2,
        )

    def test_append_current_snapshot_observation_adds_in_memory_price(self) -> None:
        history = [{"timestamp": "2026-04-24T09:00:00+09:00", "price": 60_000}]

        rows = append_current_snapshot_observation(
            history,
            symbol="005930",
            snapshot=self._snapshot(price=61_200),
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["price"], 61_200)
        self.assertEqual(rows[-1]["source"], "current_cycle")
        self.assertEqual(len(history), 1)

    def test_technical_features_use_current_snapshot_without_extra_api_call(self) -> None:
        history = [
            {"timestamp": f"2026-04-24T09:{minute:02d}:00+09:00", "price": 60_000 + minute * 10}
            for minute in range(11)
        ]

        with mock.patch(
            "app.math_models.technical.get_recent_symbol_price_history",
            return_value=history,
        ):
            features_without_current = build_technical_features("005930")
            features_with_current = build_technical_features(
                "005930",
                current_snapshot=self._snapshot(price=61_200),
            )

        self.assertFalse(features_without_current["history_usable"])
        self.assertEqual(features_without_current["history_observation_count"], 11)
        self.assertTrue(features_with_current["history_usable"])
        self.assertEqual(features_with_current["history_observation_count"], 12)

    def test_ignores_snapshot_for_different_symbol(self) -> None:
        history = [
            {"timestamp": f"2026-04-24T09:{minute:02d}:00+09:00", "price": 60_000 + minute * 10}
            for minute in range(11)
        ]

        with mock.patch(
            "app.math_models.technical.get_recent_symbol_price_history",
            return_value=history,
        ):
            features = build_technical_features(
                "005930",
                current_snapshot=self._snapshot(symbol="000660", price=120_000),
            )

        self.assertFalse(features["history_usable"])
        self.assertEqual(features["history_observation_count"], 11)


if __name__ == "__main__":
    unittest.main()
