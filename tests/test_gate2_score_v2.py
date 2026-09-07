"""Tests for app.gate2.score_v2 (G2; research shim removed 2026-07-03, eabecd7)."""

import unittest

from app.gate2 import schema, score_v2


class WeightedGate2ScoreTest(unittest.TestCase):
    def test_final_score_is_weight_normalized_to_the_0_100_scale(self):
        """Bug fix (2026-07-08, V1 gate④ post-run finding): ``final_score`` must
        be a WEIGHTED AVERAGE (divided by ``sum(weights)``), not a raw weighted
        SUM. Real artifacts (default_artifact: weights sum to 10.75) combined
        with an unnormalized sum let ``final_score`` reach ~400-850 on real
        gate1-pass holdout records while ``buy_threshold`` is schema-capped to
        [0, 100] (schema.py validate_artifact) — every candidate cleared every
        offered threshold (empirically 100% pass rate), making the whole
        weighted-threshold mechanism non-discriminating for BOTH the KR gate2
        weight-search and the live ``app/overseas_stock/selection.py`` path
        (same ``score_v2_us_w0`` artifact shape). Two equal-weight full scores
        must average to that score, regardless of the weight magnitude."""
        result = score_v2.weighted_gate2_score(
            {"a": 100.0, "b": 100.0}, {"a": 1.0, "b": 1.0}
        )
        self.assertEqual(result.final_score, 100.0)
        result_scaled = score_v2.weighted_gate2_score(
            {"a": 100.0, "b": 100.0}, {"a": 5.0, "b": 5.0}
        )
        self.assertEqual(result_scaled.final_score, 100.0)

    def test_hand_calculated_full_contributions(self):
        scores = {"a": 50.0, "b": 100.0}
        weights = {"a": 1.0, "b": 0.5}
        result = score_v2.weighted_gate2_score(scores, weights)
        # contribution_i = (score_i * weight_i) / sum(weights) — normalized so
        # final_score stays a 0-100 weighted average regardless of weight scale.
        self.assertAlmostEqual(result.contributions["a"], 33.333333, places=5)
        self.assertAlmostEqual(result.contributions["b"], 33.333333, places=5)
        self.assertAlmostEqual(result.final_score, 66.666667, places=5)
        self.assertEqual(result.missing, ())


    def test_second_hand_calculated_case(self):
        scores = {"x": 80.0, "y": 20.0, "z": 100.0}
        weights = {"x": 0.5, "y": 1.0, "z": 0.25}
        result = score_v2.weighted_gate2_score(scores, weights)
        # raw contributions {40, 20, 25} / sum(weights)=1.75
        self.assertAlmostEqual(result.contributions["x"], 22.857143, places=5)
        self.assertAlmostEqual(result.contributions["y"], 11.428571, places=5)
        self.assertAlmostEqual(result.contributions["z"], 14.285714, places=5)
        self.assertAlmostEqual(result.final_score, 48.571429, places=5)
        self.assertEqual(result.missing, ())


    def test_none_and_missing_recorded_as_zero_contribution(self):
        # weights has 4 keys; scores omits "m" entirely and sets "n" to None
        weights = {"a": 1.0, "b": 0.5, "m": 1.0, "n": 0.5}
        scores = {"a": 50.0, "b": 100.0, "n": None}
        result = score_v2.weighted_gate2_score(scores, weights)
        # raw contributions {50, 50, 0, 0} / sum(weights)=3.0
        self.assertAlmostEqual(result.contributions["a"], 16.666667, places=5)
        self.assertAlmostEqual(result.contributions["b"], 16.666667, places=5)
        self.assertEqual(result.contributions["m"], 0.0)
        self.assertEqual(result.contributions["n"], 0.0)
        self.assertAlmostEqual(result.final_score, 33.333333, places=5)
        # missing follows weights iteration order
        self.assertEqual(result.missing, ("m", "n"))


    def test_validation_errors(self):
        weights = {"a": 1.0, "b": 0.5}
        cases = {
            "scores_not_subset": {"a": 50.0, "c": 10.0},
            "score_above_100": {"a": 100.1},
            "score_below_0": {"a": -0.1},
        }
        for label, scores in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    score_v2.weighted_gate2_score(scores, weights)


class PassesThresholdTest(unittest.TestCase):
    def _result(self, final):
        return score_v2.Gate2ScoreResult(
            final_score=final, contributions={}, missing=()
        )

    def test_threshold_boundary(self):
        artifact = schema.default_artifact()  # buy_threshold == 60.0
        cases = {
            59.99: False,
            60.0: True,
            60.01: True,
            100.0: True,
            0.0: False,
        }
        for final, expected in cases.items():
            with self.subTest(final=final):
                self.assertEqual(
                    score_v2.passes_threshold(self._result(final), artifact),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
