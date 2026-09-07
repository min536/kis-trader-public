from __future__ import annotations

import unittest
from types import SimpleNamespace

from app import main as main_module
from app.strategy import sell_decision as sell_decision_module
from app.strategy.sell_decision import SellAnalysisResult
from app.strategy.sell_test_scenarios import build_sell_test_scenario


def _make_settings() -> SimpleNamespace:
    return SimpleNamespace(
        buy_rule_enable_intraday_pullback=True,
        buy_rule_enable_rebound_from_low=True,
        buy_rule_enable_controlled_down_day=True,
        buy_rule_enable_gap_down_open=False,
        buy_rule_enable_range_recovery=False,
        buy_rule_enable_live_volume_rank=False,
        buy_rule_enable_live_volume_power_rank=False,
        buy_rule_rebound_from_low_pct=1.0,
        buy_rule_controlled_down_day_min=-3.0,
        buy_rule_controlled_down_day_max=-0.3,
        buy_rule_gap_down_open_min_pct=-4.0,
        buy_rule_gap_down_open_max_pct=-0.5,
        buy_rule_range_recovery_min_ratio=0.5,
        buy_rule_required_pass_count=2,
        sell_enable=True,
        sell_stop_loss_pct=-2.0,
        sell_take_profit_pct=3.0,
        sell_trailing_stop_pct=1.5,
        sell_rule_enable_live_leadership_loss=False,
        sell_rule_enable_live_power_breakdown=False,
        use_cost_aware_pnl=False,
        buy_fee_bps=1.5,
        buy_slippage_bps=0.0,
        sell_fee_bps=1.5,
        sell_tax_bps=15.0,
        sell_slippage_bps=0.0,
    )


class BuildSellAnalysisTests(unittest.TestCase):
    def test_stop_loss_scenario_composes_analysis(self) -> None:
        scenario = build_sell_test_scenario("stop_loss")
        assert scenario is not None
        settings = _make_settings()

        analysis = sell_decision_module.build_sell_analysis(
            symbol=scenario.symbol,
            holding_qty=scenario.holding_qty,
            average_cost=scenario.average_cost,
            market_snapshot=scenario.market_snapshot,
            portfolio_snapshot=scenario.portfolio_snapshot,
            settings=settings,
        )

        self.assertIsInstance(analysis, SellAnalysisResult)
        self.assertEqual(analysis.symbol, scenario.symbol)
        self.assertEqual(analysis.holding_qty, scenario.holding_qty)
        self.assertEqual(analysis.average_cost, scenario.average_cost)
        self.assertIs(analysis.market_snapshot, scenario.market_snapshot)
        self.assertTrue(analysis.sell_decision.should_attempt_sell)

    def test_unheld_symbol_falls_back_to_symbol_name_lookup(self) -> None:
        scenario = build_sell_test_scenario("stop_loss")
        assert scenario is not None
        settings = _make_settings()

        analysis = sell_decision_module.build_sell_analysis(
            symbol="000000",
            holding_qty=0,
            average_cost=0,
            market_snapshot=scenario.market_snapshot,
            portfolio_snapshot=scenario.portfolio_snapshot,
            settings=settings,
        )

        self.assertEqual(analysis.symbol, "000000")
        self.assertIsNone(analysis.name)
        self.assertEqual(analysis.display_name, "000000")
        self.assertFalse(analysis.sell_decision.should_attempt_sell)


class MainAliasSeamTests(unittest.TestCase):
    """app.main re-exports the builder under its legacy underscore name."""

    def test_main_exposes_build_sell_analysis(self) -> None:
        self.assertIs(
            main_module._build_sell_analysis,
            sell_decision_module.build_sell_analysis,
        )


if __name__ == "__main__":
    unittest.main()
