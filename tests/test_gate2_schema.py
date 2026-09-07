"""Tests for app.gate2.schema (G1; research shim removed 2026-07-03, eabecd7)."""

import tempfile
import unittest
from pathlib import Path

from app.gate2 import schema


class ConditionScoreNamesTest(unittest.TestCase):
    def test_condition_score_names_exact_literal(self):
        self.assertEqual(
            schema.CONDITION_SCORE_NAMES,
            (
                "pullback_strength_score",
                "rebound_strength_score",
                "controlled_down_quality_score",
                "gap_down_quality_score",
                "range_recovery_score",
                "trend_alignment_score",
                "macd_momentum_score",
                "volume_rank_score",
                "volume_power_score",
                "cost_quality_score",
                "mean_reversion_score",
                "price_velocity_score",
                "liquidity_score",
                "volatility_risk_score",
                "portfolio_diversification_score",
            ),
        )


class DefaultArtifactTest(unittest.TestCase):
    def test_default_artifact_literal_pins(self):
        artifact = schema.default_artifact()
        self.assertEqual(artifact.version, "score_v2_w0")
        self.assertEqual(artifact.buy_threshold, 60.0)
        self.assertEqual(
            artifact.weights,
            {
                "pullback_strength_score": 1.0,
                "rebound_strength_score": 1.0,
                "controlled_down_quality_score": 1.0,
                "gap_down_quality_score": 1.0,
                "range_recovery_score": 1.0,
                "volume_rank_score": 1.0,
                "volume_power_score": 1.0,
                "trend_alignment_score": 0.5,
                "macd_momentum_score": 0.5,
                "cost_quality_score": 0.5,
                "mean_reversion_score": 0.5,
                "price_velocity_score": 0.5,
                "volatility_risk_score": 0.5,
                "portfolio_diversification_score": 0.5,
                "liquidity_score": 0.25,
            },
        )
        self.assertEqual(
            artifact.normalization_caps,
            {
                "trend_alignment": 0.35,
                "macd_momentum": 0.20,
                "cost_quality": 0.35,
                "mean_reversion": 0.30,
                "velocity_bonus": 0.20,
                "velocity_penalty": 0.20,
                "diversification_bonus": 0.15,
                "portfolio_correlation_penalty": 0.37,
                "variance_increase_penalty": 0.50,
            },
        )
        # weights cover exactly the 15 condition score names
        self.assertEqual(set(artifact.weights), set(schema.CONDITION_SCORE_NAMES))


class ValidateArtifactTest(unittest.TestCase):
    def test_default_artifact_validates(self):
        schema.validate_artifact(schema.default_artifact())

    def _mutate(self, **changes):
        base = schema.default_artifact()
        return schema.ScoreV2Artifact(
            version=changes.get("version", base.version),
            weights=changes.get("weights", dict(base.weights)),
            buy_threshold=changes.get("buy_threshold", base.buy_threshold),
            normalization_caps=changes.get(
                "normalization_caps", dict(base.normalization_caps)
            ),
        )

    def test_validate_violations_raise(self):
        base = schema.default_artifact()

        missing = dict(base.weights)
        del missing["liquidity_score"]

        extra = dict(base.weights)
        extra["unexpected_score"] = 0.5

        weight_too_high = dict(base.weights)
        weight_too_high["pullback_strength_score"] = 1.5

        weight_negative = dict(base.weights)
        weight_negative["pullback_strength_score"] = -0.1

        cap_zero = dict(base.normalization_caps)
        cap_zero["trend_alignment"] = 0.0

        cap_negative = dict(base.normalization_caps)
        cap_negative["trend_alignment"] = -0.1

        cases = {
            "missing_key": self._mutate(weights=missing),
            "extra_key": self._mutate(weights=extra),
            "weight_above_one": self._mutate(weights=weight_too_high),
            "weight_below_zero": self._mutate(weights=weight_negative),
            "threshold_above_100": self._mutate(buy_threshold=100.1),
            "threshold_below_0": self._mutate(buy_threshold=-1.0),
            "cap_zero": self._mutate(normalization_caps=cap_zero),
            "cap_negative": self._mutate(normalization_caps=cap_negative),
        }
        for label, artifact in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    schema.validate_artifact(artifact)


class RoundTripTest(unittest.TestCase):
    def test_save_load_round_trip(self):
        artifact = schema.default_artifact()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.json"
            schema.save_artifact(artifact, path)
            loaded = schema.load_artifact(path)
        self.assertEqual(loaded, artifact)
        self.assertEqual(loaded.version, "score_v2_w0")
        self.assertEqual(loaded.weights, artifact.weights)
        self.assertEqual(loaded.normalization_caps, artifact.normalization_caps)
        self.assertEqual(loaded.buy_threshold, 60.0)


if __name__ == "__main__":
    unittest.main()
