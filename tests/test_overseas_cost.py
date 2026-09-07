"""Tests for app/overseas_runtime/cost.py (US fee model)."""
import unittest


class TestEstimateOverseasCostBuy(unittest.TestCase):
    """Test 7: buy side — sec_fee and taf are zero, commission correct."""

    def test_buy_side_no_sec_fee_no_taf(self) -> None:
        from app.overseas_runtime.cost import estimate_overseas_cost

        result = estimate_overseas_cost(notional_usd=1000.0, qty=10, side="buy")
        self.assertEqual(result["sec_fee_usd"], 0.0)
        self.assertEqual(result["taf_usd"], 0.0)
        # commission = 1000 * 25 / 10000 = 2.5
        self.assertAlmostEqual(result["commission_usd"], 2.5, places=4)


class TestEstimateOverseasCostInvalidSide(unittest.TestCase):
    """Test 10: invalid side raises ValueError."""

    def test_invalid_side_raises_value_error(self) -> None:
        from app.overseas_runtime.cost import estimate_overseas_cost

        with self.assertRaises(ValueError):
            estimate_overseas_cost(notional_usd=1000.0, qty=10, side="hold")


class TestEstimateOverseasCostTafCap(unittest.TestCase):
    """Test 9: TAF is capped at taf_max for a huge qty."""

    def test_taf_capped_at_taf_max(self) -> None:
        from app.overseas_runtime.cost import OverseasCostModel, estimate_overseas_cost

        model = OverseasCostModel()
        # 100_000 shares * 0.000166 = 16.6, which exceeds taf_max=8.30
        result = estimate_overseas_cost(
            notional_usd=5_000_000.0, qty=100_000, side="sell", model=model
        )
        self.assertAlmostEqual(result["taf_usd"], model.taf_max, places=4)


class TestEstimateOverseasCostSell(unittest.TestCase):
    """Test 8: sell side — sec_fee and taf are > 0, total correct."""

    def test_sell_side_has_sec_fee_and_taf(self) -> None:
        from app.overseas_runtime.cost import OverseasCostModel, estimate_overseas_cost

        model = OverseasCostModel()
        result = estimate_overseas_cost(notional_usd=1000.0, qty=10, side="sell", model=model)
        self.assertGreater(result["sec_fee_usd"], 0.0)
        self.assertGreater(result["taf_usd"], 0.0)
        expected_total = round(
            result["commission_usd"] + result["sec_fee_usd"] + result["taf_usd"], 4
        )
        self.assertAlmostEqual(result["total_cost_usd"], expected_total, places=4)


if __name__ == "__main__":
    unittest.main()
