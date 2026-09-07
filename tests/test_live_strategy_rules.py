from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.market_data.schema import MarketSnapshot
from app.portfolio.schema import PortfolioPosition, PortfolioSnapshot
from app.strategy.buy_decision import (
    BuyDecision,
    evaluate_buy_decision,
    evaluate_live_consensus_rank,
    evaluate_near_intraday_high,
    evaluate_open_reclaim,
)
from app.strategy.schema import BuyDecisionSummary, StrategyEvaluationResult
from app.strategy.sell_decision import (
    evaluate_intraday_peak_reversal,
    evaluate_range_breakdown,
    evaluate_sell_decision,
)


class LiveStrategyRuleTests(unittest.TestCase):
    def test_buy_decision_adds_live_rank_rules_and_boosts_required_count(self) -> None:
        snapshot = MarketSnapshot(
            symbol="005930",
            current_price=100_000,
            open_price=101_000,
            low_price=99_000,
            prev_day_change_pct=-0.5,
            live_snapshot_available=True,
            live_snapshot_updated_at="2026-04-17T10:32:06+09:00",
            live_snapshot_combined_rank=1,
            live_volume_rank=2,
            live_fluctuation_rank=None,
            live_volume_power_rank=1,
            live_ranked_source_count=2,
        )

        decision = evaluate_buy_decision(
            snapshot=snapshot,
            symbol="005930",
            qty=1,
            enable_intraday_pullback=True,
            enable_rebound_from_low=True,
            enable_controlled_down_day=True,
            enable_gap_down_open=True,
            enable_range_recovery=True,
            enable_live_volume_rank=True,
            enable_live_volume_power_rank=True,
            rebound_from_low_pct=0.01,
            controlled_down_day_min=-6.0,
            controlled_down_day_max=0.0,
            gap_down_open_min_pct=0.3,
            gap_down_open_max_pct=5.0,
            range_recovery_min_ratio=0.2,
            required_pass_count=3,
        )

        rule_by_name = {result.strategy_name: result for result in decision.rule_results}
        self.assertTrue(rule_by_name["live_volume_rank"].passed)
        self.assertTrue(rule_by_name["live_volume_power_rank"].passed)
        self.assertEqual(decision.required_pass_count, 4)
        self.assertTrue(decision.should_attempt_buy)

    def test_sell_decision_triggers_live_power_breakdown(self) -> None:
        snapshot = MarketSnapshot(
            symbol="005930",
            current_price=101_000,
            open_price=103_000,
            low_price=100_500,
            prev_day_change_pct=-1.2,
            live_snapshot_available=True,
            live_snapshot_updated_at="2026-04-17T10:32:06+09:00",
            live_snapshot_combined_rank=None,
            live_volume_rank=None,
            live_fluctuation_rank=None,
            live_volume_power_rank=None,
            live_ranked_source_count=0,
        )
        portfolio_snapshot = PortfolioSnapshot(
            positions=(
                PortfolioPosition(
                    symbol="005930",
                    name="삼성전자",
                    holding_qty=10,
                    average_cost=100_000,
                    current_price=101_000,
                    market_value=1_010_000,
                    gross_pnl=10_000,
                    gross_pnl_pct=1.0,
                    has_position=True,
                ),
            ),
            cash_total=10_000_000,
            cash_orderable=10_000_000,
            cash_next_day=10_000_000,
            total_evaluation_amount=1_010_000,
        )
        buy_strategy_result = BuyDecision(
            summary=BuyDecisionSummary(
                should_attempt_buy=False,
                passed_count=1,
                enabled_count=7,
                required_pass_count=4,
                final_reason="weak holding",
                passed_strategy_names=("intraday_pullback",),
                total_count=7,
            ),
            evaluation_results=(
                StrategyEvaluationResult(
                    strategy_name="intraday_pullback",
                    enabled=True,
                    passed=True,
                    reason="pass",
                ),
            ),
        )
        settings = SimpleNamespace(
            use_cost_aware_pnl=True,
            buy_fee_bps=0.0,
            sell_fee_bps=0.0,
            sell_tax_bps=0.0,
            buy_slippage_bps=0.0,
            sell_slippage_bps=0.0,
        )

        decision = evaluate_sell_decision(
            snapshot=snapshot,
            portfolio_snapshot=portfolio_snapshot,
            symbol="005930",
            buy_strategy_result=buy_strategy_result,
            enabled=True,
            stop_loss_pct=-3.0,
            take_profit_pct=3.0,
            trailing_stop_pct=1.5,
            enable_live_leadership_loss=True,
            enable_live_power_breakdown=True,
            settings=settings,
        )

        self.assertTrue(decision.should_attempt_sell)
        self.assertEqual(decision.triggered_rule_name, "live_power_breakdown")
        rule_by_name = {result.rule_name: result for result in decision.rule_results}
        self.assertTrue(rule_by_name["live_leadership_loss"].passed)
        self.assertTrue(rule_by_name["live_power_breakdown"].passed)


class ShadowBuyRuleTests(unittest.TestCase):
    """Shadow/test-only buy rule evaluators — not wired into live path."""

    def test_open_reclaim_passes_when_price_reclaims_open(self) -> None:
        snapshot = MarketSnapshot(
            symbol="035420",
            current_price=82_500,
            open_price=81_800,
            low_price=80_000,
            prev_day_change_pct=0.8,
        )
        result = evaluate_open_reclaim(
            snapshot=snapshot,
            min_reclaim_ratio=1.05,
            enabled=True,
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.strategy_name, "open_reclaim")

    def test_open_reclaim_fails_when_price_below_open(self) -> None:
        snapshot = MarketSnapshot(
            symbol="035420",
            current_price=81_000,
            open_price=81_800,
            low_price=80_000,
            prev_day_change_pct=0.8,
        )
        result = evaluate_open_reclaim(
            snapshot=snapshot,
            min_reclaim_ratio=1.05,
            enabled=True,
        )
        self.assertFalse(result.passed)

    def test_open_reclaim_disabled(self) -> None:
        snapshot = MarketSnapshot(
            symbol="035420",
            current_price=82_500,
            open_price=81_800,
            low_price=80_000,
            prev_day_change_pct=0.8,
        )
        result = evaluate_open_reclaim(
            snapshot=snapshot,
            min_reclaim_ratio=1.05,
            enabled=False,
        )
        self.assertFalse(result.passed)
        self.assertFalse(result.enabled)

    def test_open_reclaim_no_dip_below_open(self) -> None:
        snapshot = MarketSnapshot(
            symbol="035420",
            current_price=82_500,
            open_price=80_000,
            low_price=80_500,
            prev_day_change_pct=0.8,
        )
        result = evaluate_open_reclaim(
            snapshot=snapshot,
            min_reclaim_ratio=1.05,
            enabled=True,
        )
        self.assertFalse(result.passed)

    def test_near_intraday_high_passes_near_high(self) -> None:
        snapshot = MarketSnapshot(
            symbol="035420",
            current_price=82_500,
            open_price=81_800,
            low_price=80_000,
            prev_day_change_pct=0.8,
            high_price=82_700,
        )
        result = evaluate_near_intraday_high(
            snapshot=snapshot,
            max_gap_pct=0.4,
            enabled=True,
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.strategy_name, "near_intraday_high")

    def test_near_intraday_high_fails_when_far_from_high(self) -> None:
        snapshot = MarketSnapshot(
            symbol="035420",
            current_price=80_500,
            open_price=81_800,
            low_price=80_000,
            prev_day_change_pct=0.8,
            high_price=82_700,
        )
        result = evaluate_near_intraday_high(
            snapshot=snapshot,
            max_gap_pct=0.4,
            enabled=True,
        )
        self.assertFalse(result.passed)

    def test_near_intraday_high_disabled(self) -> None:
        snapshot = MarketSnapshot(
            symbol="035420",
            current_price=82_500,
            open_price=81_800,
            low_price=80_000,
            prev_day_change_pct=0.8,
            high_price=82_700,
        )
        result = evaluate_near_intraday_high(
            snapshot=snapshot,
            max_gap_pct=0.4,
            enabled=False,
        )
        self.assertFalse(result.passed)
        self.assertFalse(result.enabled)

    def test_live_consensus_rank_passes_with_enough_sources(self) -> None:
        snapshot = MarketSnapshot(
            symbol="005930",
            current_price=100_000,
            open_price=101_000,
            low_price=99_000,
            prev_day_change_pct=-0.5,
            live_snapshot_available=True,
            live_snapshot_updated_at="2026-04-17T10:32:06+09:00",
            live_snapshot_combined_rank=3,
            live_volume_rank=4,
            live_fluctuation_rank=5,
            live_volume_power_rank=2,
            live_ranked_source_count=3,
        )
        result = evaluate_live_consensus_rank(
            snapshot=snapshot,
            min_sources=2,
            enabled=True,
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.strategy_name, "live_consensus_rank")

    def test_live_consensus_rank_fails_insufficient_sources(self) -> None:
        snapshot = MarketSnapshot(
            symbol="005930",
            current_price=100_000,
            open_price=101_000,
            low_price=99_000,
            prev_day_change_pct=-0.5,
            live_snapshot_available=True,
            live_snapshot_updated_at="2026-04-17T10:32:06+09:00",
            live_snapshot_combined_rank=3,
            live_volume_rank=4,
            live_fluctuation_rank=None,
            live_volume_power_rank=None,
            live_ranked_source_count=1,
        )
        result = evaluate_live_consensus_rank(
            snapshot=snapshot,
            min_sources=2,
            enabled=True,
        )
        self.assertFalse(result.passed)

    def test_live_consensus_rank_disabled(self) -> None:
        snapshot = MarketSnapshot(
            symbol="005930",
            current_price=100_000,
            open_price=101_000,
            low_price=99_000,
            prev_day_change_pct=-0.5,
            live_snapshot_available=True,
            live_snapshot_updated_at="2026-04-17T10:32:06+09:00",
            live_snapshot_combined_rank=3,
            live_ranked_source_count=3,
        )
        result = evaluate_live_consensus_rank(
            snapshot=snapshot,
            min_sources=2,
            enabled=False,
        )
        self.assertFalse(result.passed)
        self.assertFalse(result.enabled)

    def test_live_consensus_rank_no_live_snapshot(self) -> None:
        snapshot = MarketSnapshot(
            symbol="005930",
            current_price=100_000,
            open_price=101_000,
            low_price=99_000,
            prev_day_change_pct=-0.5,
            live_snapshot_available=False,
        )
        result = evaluate_live_consensus_rank(
            snapshot=snapshot,
            min_sources=2,
            enabled=True,
        )
        self.assertFalse(result.passed)
        self.assertFalse(result.enabled)


class ShadowSellRuleTests(unittest.TestCase):
    """Shadow/test-only sell rule evaluators — not wired into live path."""

    def test_intraday_peak_reversal_passes_on_profit_drawdown_below_open(self) -> None:
        details = {
            "pnl_pct": 3.0,
            "effective_pnl_basis": "net",
            "drawdown_from_high_pct": 2.83,
            "current_price": 103_000,
            "open_price": 104_500,
            "high_price": 106_000,
        }
        result = evaluate_intraday_peak_reversal(
            details=details,
            min_profit_pct=1.2,
            min_drawdown_pct=1.5,
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.rule_name, "intraday_peak_reversal")

    def test_intraday_peak_reversal_fails_when_above_open(self) -> None:
        details = {
            "pnl_pct": 3.0,
            "effective_pnl_basis": "net",
            "drawdown_from_high_pct": 2.83,
            "current_price": 105_000,
            "open_price": 104_500,
            "high_price": 106_000,
        }
        result = evaluate_intraday_peak_reversal(
            details=details,
            min_profit_pct=1.2,
            min_drawdown_pct=1.5,
        )
        self.assertFalse(result.passed)

    def test_intraday_peak_reversal_fails_insufficient_profit(self) -> None:
        details = {
            "pnl_pct": 0.5,
            "effective_pnl_basis": "gross",
            "drawdown_from_high_pct": 2.83,
            "current_price": 103_000,
            "open_price": 104_500,
            "high_price": 106_000,
        }
        result = evaluate_intraday_peak_reversal(
            details=details,
            min_profit_pct=1.2,
            min_drawdown_pct=1.5,
        )
        self.assertFalse(result.passed)

    def test_intraday_peak_reversal_fails_no_high_price(self) -> None:
        details = {
            "pnl_pct": 3.0,
            "effective_pnl_basis": "net",
            "drawdown_from_high_pct": 2.83,
            "current_price": 103_000,
            "open_price": 104_500,
            "high_price": 0,
        }
        result = evaluate_intraday_peak_reversal(
            details=details,
            min_profit_pct=1.2,
            min_drawdown_pct=1.5,
        )
        self.assertFalse(result.passed)

    def test_range_breakdown_passes_weak_holding_in_lower_range(self) -> None:
        details = {
            "current_price": 99_200,
            "open_price": 101_500,
            "prev_day_change_pct": -1.4,
            "pnl_pct": -0.8,
            "range_position_ratio": 0.04,
            "buy_passed_count": 1,
            "buy_enabled_count": 6,
        }
        result = evaluate_range_breakdown(
            details=details,
            max_range_position_ratio=0.35,
            weak_profit_ceiling_pct=0.8,
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.rule_name, "range_breakdown")

    def test_range_breakdown_fails_when_above_open(self) -> None:
        details = {
            "current_price": 102_000,
            "open_price": 101_500,
            "prev_day_change_pct": -1.4,
            "pnl_pct": -0.8,
            "range_position_ratio": 0.04,
            "buy_passed_count": 1,
            "buy_enabled_count": 6,
        }
        result = evaluate_range_breakdown(
            details=details,
            max_range_position_ratio=0.35,
            weak_profit_ceiling_pct=0.8,
        )
        self.assertFalse(result.passed)

    def test_range_breakdown_fails_strong_holding_and_profit(self) -> None:
        details = {
            "current_price": 99_200,
            "open_price": 101_500,
            "prev_day_change_pct": -1.4,
            "pnl_pct": 5.0,
            "range_position_ratio": 0.04,
            "buy_passed_count": 5,
            "buy_enabled_count": 6,
        }
        result = evaluate_range_breakdown(
            details=details,
            max_range_position_ratio=0.35,
            weak_profit_ceiling_pct=0.8,
        )
        self.assertFalse(result.passed)

    def test_range_breakdown_passes_weak_profit_context(self) -> None:
        # Strong holding but weak profit — should still trigger
        details = {
            "current_price": 99_200,
            "open_price": 101_500,
            "prev_day_change_pct": -1.4,
            "pnl_pct": 0.3,
            "range_position_ratio": 0.10,
            "buy_passed_count": 5,
            "buy_enabled_count": 6,
        }
        result = evaluate_range_breakdown(
            details=details,
            max_range_position_ratio=0.35,
            weak_profit_ceiling_pct=0.8,
        )
        self.assertTrue(result.passed)


if __name__ == "__main__":
    unittest.main()
