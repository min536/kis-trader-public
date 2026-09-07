from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.core.costs import (
    LEGACY_BACKTEST_COST_PARAMS,
    canonical_backtest_cost_params,
)


class CostPolicySingleSourceTest(unittest.TestCase):
    def test_legacy_backtest_cost_params_match_prior_tool_literals(self) -> None:
        # Locks the value-preserving single source: shadow_watch.py and
        # run_proposal_backtest.py historically hardcoded exactly these.
        self.assertEqual(
            LEGACY_BACKTEST_COST_PARAMS,
            {
                "commission_rate": 0.00015,
                "tax_rate": 0.0023,
                "slippage": 0.001,
            },
        )


    def test_canonical_params_derive_from_settings_bps(self) -> None:
        settings = SimpleNamespace(
            buy_fee_bps=1.5,
            sell_fee_bps=1.5,
            sell_tax_bps=15.0,
            buy_slippage_bps=5.0,
            sell_slippage_bps=5.0,
        )
        params = canonical_backtest_cost_params(settings)
        with self.subTest(field="commission_rate"):
            self.assertAlmostEqual(params["commission_rate"], 0.00015, places=12)
        with self.subTest(field="tax_rate"):
            self.assertAlmostEqual(params["tax_rate"], 0.0015, places=12)
        with self.subTest(field="slippage"):
            self.assertAlmostEqual(params["slippage"], 0.0005, places=12)


    def test_canonical_diverges_from_legacy_on_tax_and_slippage(self) -> None:
        # Documents WHY migrating the tools is an operator gate: canonical is
        # cheaper (live policy), so proposal-backtest PnL would shift.
        settings = SimpleNamespace(
            sell_fee_bps=1.5, sell_tax_bps=15.0, sell_slippage_bps=5.0
        )
        canonical = canonical_backtest_cost_params(settings)
        with self.subTest(check="commission_matches"):
            self.assertAlmostEqual(
                canonical["commission_rate"],
                LEGACY_BACKTEST_COST_PARAMS["commission_rate"],
                places=12,
            )
        with self.subTest(check="tax_lower"):
            self.assertLess(
                canonical["tax_rate"], LEGACY_BACKTEST_COST_PARAMS["tax_rate"]
            )
        with self.subTest(check="slippage_lower"):
            self.assertLess(
                canonical["slippage"], LEGACY_BACKTEST_COST_PARAMS["slippage"]
            )


if __name__ == "__main__":
    unittest.main()
