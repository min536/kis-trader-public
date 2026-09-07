"""Tests for app.research.gate2.shadow_report (G4)."""

import unittest

from app.gate2 import schema
from app.research.gate2 import shadow_report


def _rows():
    return [
        {
            "symbol": "AAA",
            "score_v1": 0.90,
            "passed_count": 5,
            "score_components": {
                "intraday_pullback_strength_score": 80.0,
                "live_volume_rank_strength_score": 100.0,
                "trend_alignment_score": 0.35,
            },
        },
        {
            "symbol": "BBB",
            "score_v1": 0.95,
            "passed_count": 5,
            "score_components": {
                "rebound_from_low_strength_score": 100.0,
                "cost_quality_score": 0.35,
            },
        },
        {
            "symbol": "CCC",
            "score_v1": 0.50,
            "passed_count": 3,
            "score_components": {
                "controlled_down_strength_score": 40.0,
            },
        },
        {
            "symbol": "DDD",
            "score_v1": 0.99,
            "passed_count": 2,
            "score_components": {},
        },
    ]


_MISSING_AAA = (
    "rebound_strength_score",
    "controlled_down_quality_score",
    "gap_down_quality_score",
    "range_recovery_score",
    "volume_power_score",
    "macd_momentum_score",
    "cost_quality_score",
    "mean_reversion_score",
    "price_velocity_score",
    "volatility_risk_score",
    "portfolio_diversification_score",
    "liquidity_score",
)
_MISSING_BBB = (
    "pullback_strength_score",
    "controlled_down_quality_score",
    "gap_down_quality_score",
    "range_recovery_score",
    "volume_rank_score",
    "volume_power_score",
    "trend_alignment_score",
    "macd_momentum_score",
    "mean_reversion_score",
    "price_velocity_score",
    "volatility_risk_score",
    "portfolio_diversification_score",
    "liquidity_score",
)
_MISSING_CCC = (
    "pullback_strength_score",
    "rebound_strength_score",
    "gap_down_quality_score",
    "range_recovery_score",
    "volume_rank_score",
    "volume_power_score",
    "trend_alignment_score",
    "macd_momentum_score",
    "cost_quality_score",
    "mean_reversion_score",
    "price_velocity_score",
    "volatility_risk_score",
    "portfolio_diversification_score",
    "liquidity_score",
)
_MISSING_DDD = (
    "pullback_strength_score",
    "rebound_strength_score",
    "controlled_down_quality_score",
    "gap_down_quality_score",
    "range_recovery_score",
    "volume_rank_score",
    "volume_power_score",
    "trend_alignment_score",
    "macd_momentum_score",
    "cost_quality_score",
    "mean_reversion_score",
    "price_velocity_score",
    "volatility_risk_score",
    "portfolio_diversification_score",
    "liquidity_score",
)


class BuildShadowComparisonTest(unittest.TestCase):
    def test_full_comparison_rows(self):
        # score_v2 values below are weight-normalized (2026-07-08 score_v2 fix:
        # final_score = sum(score*weight) / sum(weight), keeping the 0-100
        # scale regardless of weights' magnitude — see test_gate2_score_v2.py).
        artifact = schema.default_artifact()
        comp = shadow_report.build_shadow_comparison(_rows(), artifact)
        self.assertEqual(len(comp), 4)
        for row, expected_v2 in zip(
            comp,
            [21.3953488372093, 13.953488372093023, 3.7209302325581395, 0.0],
        ):
            self.assertAlmostEqual(row["score_v2"], expected_v2, places=6)
        by_symbol = {row["symbol"]: row for row in comp}
        self.assertEqual(
            {s: {k: v for k, v in row.items() if k != "score_v2"} for s, row in by_symbol.items()},
            {
                "AAA": {
                    "symbol": "AAA",
                    "score_v1": 0.90,
                    "rank_v1": 2,
                    "rank_v2": 1,
                    "rank_delta": -1,
                    "would_pass_v2": False,
                    "missing": _MISSING_AAA,
                },
                "BBB": {
                    "symbol": "BBB",
                    "score_v1": 0.95,
                    "rank_v1": 1,
                    "rank_v2": 2,
                    "rank_delta": 1,
                    "would_pass_v2": False,
                    "missing": _MISSING_BBB,
                },
                "CCC": {
                    "symbol": "CCC",
                    "score_v1": 0.50,
                    "rank_v1": 3,
                    "rank_v2": 3,
                    "rank_delta": 0,
                    "would_pass_v2": False,
                    "missing": _MISSING_CCC,
                },
                "DDD": {
                    "symbol": "DDD",
                    "score_v1": 0.99,
                    "rank_v1": 4,
                    "rank_v2": 4,
                    "rank_delta": 0,
                    "would_pass_v2": False,
                    "missing": _MISSING_DDD,
                },
            },
        )


class SummarizeShadowComparisonTest(unittest.TestCase):
    def test_summary_full_dict(self):
        artifact = schema.default_artifact()
        comp = shadow_report.build_shadow_comparison(_rows(), artifact)
        summary = shadow_report.summarize_shadow_comparison(comp)
        self.assertEqual(
            summary,
            {
                # rank1 v1 == BBB, rank1 v2 == AAA -> disagree
                "top1_agreement": False,
                # top3 v1 {BBB, AAA, CCC} vs top3 v2 {AAA, BBB, CCC} -> 3
                "top3_overlap": 3,
                # post-fix (weight-normalized score_v2): AAA=21.4/BBB=13.95/
                # CCC=3.72/DDD=0.0 — none clear threshold 60 on this sparse
                # synthetic fixture (most of the 15 dims are missing here).
                "v2_pass_count": 0,
                # (|-1| + |1| + 0 + 0) / 4 == 0.5
                "mean_abs_rank_delta": 0.5,
            },
        )


class BuildShadowConsoleLinesTest(unittest.TestCase):
    def test_console_lines_literal(self):
        artifact = schema.default_artifact()
        comp = shadow_report.build_shadow_comparison(_rows(), artifact)
        summary = shadow_report.summarize_shadow_comparison(comp)
        lines = shadow_report.build_shadow_console_lines(comp, summary)
        self.assertEqual(
            lines,
            [
                "shadow score_v1 vs score_v2 comparison (n=4)",
                "AAA v1=0.90 v2=21.40 rank 2->1 (d=-1) pass=N missing=12",
                "BBB v1=0.95 v2=13.95 rank 1->2 (d=+1) pass=N missing=13",
                "CCC v1=0.50 v2=3.72 rank 3->3 (d=+0) pass=N missing=14",
                "DDD v1=0.99 v2=0.00 rank 4->4 (d=+0) pass=N missing=15",
                "top1_agreement=False top3_overlap=3 v2_pass=0 "
                "mean_abs_rank_delta=0.50",
            ],
        )


if __name__ == "__main__":
    unittest.main()
