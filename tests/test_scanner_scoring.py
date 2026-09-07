from __future__ import annotations

import unittest
from types import SimpleNamespace

import app.scanner.service as origin
from app.market_data.schema import MarketSnapshot
from app.scanner import scoring
from app.strategy.buy_decision import evaluate_buy_decision
from app.strategy.schema import StrategyEvaluationResult


class ScannerScoringPinTests(unittest.TestCase):
    def test_calculate_selection_score_is_reexported_identity(self) -> None:
        self.assertIs(origin.calculate_selection_score, scoring.calculate_selection_score)

    def test_effective_buy_min_score_is_reexported_identity(self) -> None:
        self.assertIs(origin._effective_buy_min_score, scoring._effective_buy_min_score)

    def test_effective_buy_min_passed_count_is_reexported_identity(self) -> None:
        self.assertIs(
            origin._effective_buy_min_passed_count,
            scoring._effective_buy_min_passed_count,
        )

    def test_clamp_is_reexported_identity(self) -> None:
        self.assertIs(origin._clamp, scoring._clamp)

    def test_percent_score_is_reexported_identity(self) -> None:
        self.assertIs(origin._percent_score, scoring._percent_score)

    def test_threshold_strength_is_reexported_identity(self) -> None:
        self.assertIs(origin._threshold_strength, scoring._threshold_strength)

    def test_range_strength_is_reexported_identity(self) -> None:
        self.assertIs(origin._range_strength, scoring._range_strength)

    def test_rule_equivalent_count_is_reexported_identity(self) -> None:
        self.assertIs(origin._rule_equivalent_count, scoring._rule_equivalent_count)

    def test_score_component_labels_is_reexported_identity(self) -> None:
        self.assertIs(origin._score_component_labels, scoring._score_component_labels)

    def test_summarize_score_breakdown_is_reexported_identity(self) -> None:
        self.assertIs(origin.summarize_score_breakdown, scoring.summarize_score_breakdown)

    def test_build_analysis_sort_key_is_reexported_identity(self) -> None:
        self.assertIs(origin.build_analysis_sort_key, scoring.build_analysis_sort_key)


class ClampTests(unittest.TestCase):
    def test_clamp_below_lower_returns_lower(self) -> None:
        self.assertEqual(scoring._clamp(-0.5), 0.0)

    def test_clamp_above_upper_returns_upper(self) -> None:
        self.assertEqual(scoring._clamp(1.5), 1.0)


class PercentScoreTests(unittest.TestCase):
    def test_percent_score_clamps_and_scales(self) -> None:
        self.assertEqual(scoring._percent_score(0.8), 80.0)

    def test_percent_score_above_one_caps_at_hundred(self) -> None:
        self.assertEqual(scoring._percent_score(1.5), 100.0)


class ThresholdStrengthTests(unittest.TestCase):
    def test_threshold_strength_partial(self) -> None:
        self.assertEqual(scoring._threshold_strength(0.4, 0.5), 80.0)

    def test_threshold_strength_nonpositive_full_score_returns_zero(self) -> None:
        self.assertEqual(scoring._threshold_strength(0.4, 0.0), 0.0)


class RangeStrengthTests(unittest.TestCase):
    def test_range_strength_inside_band_returns_full(self) -> None:
        self.assertEqual(
            scoring._range_strength(2.0, 1.0, 3.0, soft_margin=1.0),
            100.0,
        )

    def test_range_strength_below_band_decays_by_margin(self) -> None:
        self.assertEqual(
            scoring._range_strength(0.5, 1.0, 3.0, soft_margin=1.0),
            50.0,
        )

    def test_range_strength_above_band_decays_by_margin(self) -> None:
        self.assertEqual(
            scoring._range_strength(3.5, 1.0, 3.0, soft_margin=1.0),
            50.0,
        )

    def test_range_strength_degenerate_band_returns_zero(self) -> None:
        self.assertEqual(
            scoring._range_strength(2.0, 3.0, 3.0, soft_margin=1.0),
            0.0,
        )


class RuleEquivalentCountTests(unittest.TestCase):
    def test_disabled_rule_contributes_zero(self) -> None:
        result = StrategyEvaluationResult(
            strategy_name="rebound_from_low",
            enabled=False,
            passed=False,
            reason="disabled",
        )
        self.assertEqual(
            scoring._rule_equivalent_count(result=result, strength_score=80.0),
            0.0,
        )

    def test_passed_rule_contributes_one(self) -> None:
        result = StrategyEvaluationResult(
            strategy_name="rebound_from_low",
            enabled=True,
            passed=True,
            reason="passed",
        )
        self.assertEqual(
            scoring._rule_equivalent_count(result=result, strength_score=40.0),
            1.0,
        )

    def test_enabled_but_not_passed_uses_clamped_strength(self) -> None:
        result = StrategyEvaluationResult(
            strategy_name="rebound_from_low",
            enabled=True,
            passed=False,
            reason="near miss",
        )
        self.assertEqual(
            scoring._rule_equivalent_count(result=result, strength_score=80.0),
            0.8,
        )


class BuildAnalysisSortKeyTests(unittest.TestCase):
    def test_sort_key_tuple_negates_count_and_score(self) -> None:
        self.assertEqual(
            scoring.build_analysis_sort_key(passed_count=3, score=4.25, symbol="005930"),
            (-3, -4.25, "005930"),
        )


class ScoreComponentLabelsTests(unittest.TestCase):
    def test_labels_map_matches_expected_dict(self) -> None:
        self.assertEqual(
            scoring._score_component_labels(),
            {
                "trend_quality_score": "trend_quality",
                "momentum_quality_score": "momentum_quality",
                "price_efficiency_score": "price_efficiency",
                "mean_reversion_bonus": "mean_reversion",
                "diversification_bonus": "diversification",
                "cost_quality_score": "cost_quality",
                "intraday_pullback_bonus": "intraday_pullback",
                "rebound_from_low_bonus": "rebound_from_low",
                "controlled_down_bonus": "controlled_down_day",
                "gap_down_open_bonus": "gap_down_open",
                "range_recovery_bonus": "range_recovery",
                "overheat_penalty": "overheat_context",
                "overextension_penalty": "overextension",
                "pullback_exhaustion_penalty": "pullback_exhaustion",
                "portfolio_correlation_penalty": "portfolio_similarity",
                "variance_increase_penalty": "variance_increase",
                "expected_cost_penalty": "expected_cost",
                "hist_range_bonus": "hist_range_low",
                "velocity_bonus": "velocity_decel",
                "pullback_depth_bonus": "deep_pullback",
                "hist_range_penalty": "hist_range_high",
                "velocity_penalty": "velocity_freefall",
                "trend_alignment_score": "trend_alignment",
                "macd_momentum_score": "macd_momentum",
            },
        )


class SummarizeScoreBreakdownTests(unittest.TestCase):
    def test_top_positives_and_negatives_with_summary(self) -> None:
        components = {
            "trend_quality_score": 0.5,
            "momentum_quality_score": 0.3,
            "price_efficiency_score": 0.1,
            "intraday_pullback_bonus": 0.05,
            "overheat_penalty": 0.4,
            "variance_increase_penalty": 0.2,
        }
        positives, negatives, summary = scoring.summarize_score_breakdown(components)
        self.assertEqual(positives, ("trend_quality", "momentum_quality", "price_efficiency"))
        self.assertEqual(negatives, ("overheat_context", "variance_increase"))
        self.assertEqual(
            summary,
            "가산: trend_quality, momentum_quality, price_efficiency | 감점: overheat_context, variance_increase",
        )

    def test_empty_components_use_placeholder_text(self) -> None:
        positives, negatives, summary = scoring.summarize_score_breakdown({})
        self.assertEqual(positives, ())
        self.assertEqual(negatives, ())
        self.assertEqual(summary, "가산: 뚜렷한 가산 요인 없음 | 감점: 주요 감점 요인 없음")


class EffectiveBuyMinScoreTests(unittest.TestCase):
    def test_core_layer_uses_core_min_score(self) -> None:
        settings = SimpleNamespace(buy_min_score=3.1, buy_min_score_core=2.9)
        self.assertEqual(
            scoring._effective_buy_min_score(settings=settings, selection_layer="core"),
            2.9,
        )

    def test_non_core_layer_uses_default_min_score(self) -> None:
        settings = SimpleNamespace(buy_min_score=3.1, buy_min_score_core=2.9)
        self.assertEqual(
            scoring._effective_buy_min_score(settings=settings, selection_layer=""),
            3.1,
        )


class EffectiveBuyMinPassedCountTests(unittest.TestCase):
    def test_no_live_rule_keeps_base_count(self) -> None:
        settings = SimpleNamespace(buy_min_passed_count=2)
        strategy_result = SimpleNamespace(
            rule_results=(
                SimpleNamespace(enabled=True, strategy_name="intraday_pullback"),
                SimpleNamespace(enabled=True, strategy_name="rebound_from_low"),
            )
        )
        self.assertEqual(
            scoring._effective_buy_min_passed_count(
                settings=settings,
                strategy_result=strategy_result,
            ),
            2,
        )

    def test_enabled_live_rule_adds_one(self) -> None:
        settings = SimpleNamespace(buy_min_passed_count=2)
        strategy_result = SimpleNamespace(
            rule_results=(
                SimpleNamespace(enabled=True, strategy_name="intraday_pullback"),
                SimpleNamespace(enabled=True, strategy_name="live_volume_rank"),
            )
        )
        self.assertEqual(
            scoring._effective_buy_min_passed_count(
                settings=settings,
                strategy_result=strategy_result,
            ),
            3,
        )


class CalculateSelectionScoreTests(unittest.TestCase):
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

        score, components = scoring.calculate_selection_score(
            snapshot=snapshot,
            strategy_result=decision,
            rebound_from_low_pct=0.005,
            controlled_down_day_min=-6.0,
            controlled_down_day_max=8.0,
            gap_down_open_min_pct=0.3,
            gap_down_open_max_pct=5.0,
            range_recovery_min_ratio=0.5,
        )

        self.assertEqual(score, 4.94)
        self.assertEqual(
            components,
            {
                "passed_count_base": 2.0,
                "pullback_pct": 0.59,
                "rebound_pct": 0.4,
                "gap_down_open_pct": 0.0,
                "range_recovery_ratio": 0.4,
                "soft_passed_count_base": 3.6,
                "signal_quality_count": 3.6,
                "intraday_pullback_strength_score": 11.88,
                "rebound_from_low_strength_score": 80.0,
                "controlled_down_strength_score": 100.0,
                "gap_down_open_strength_score": 0.0,
                "range_recovery_strength_score": 80.0,
                "live_volume_rank_strength_score": 0.0,
                "live_volume_power_rank_strength_score": 0.0,
                "intraday_pullback_bonus": 0.06,
                "rebound_from_low_bonus": 0.0,
                "controlled_down_bonus": 0.2,
                "gap_down_open_bonus": 0.0,
                "range_recovery_bonus": 0.0,
                "trend_quality_score": 0.5,
                "momentum_quality_score": 0.31,
                "price_efficiency_score": 0.27,
                "gap_up_open_pct": 0.09,
                "overheat_penalty": 0.0,
                "pullback_exhaustion_penalty": 0.0,
            },
        )


if __name__ == "__main__":
    unittest.main()
