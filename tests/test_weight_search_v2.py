"""Tests for the W2 weight_search extensions (daily windows, objective_v2,
random candidates).

Kept separate from ``tests/test_gate2_weight_search.py`` (W1) so the additive
W2 surface does not collide with the existing test names/fixtures.
"""

import unittest

from app.gate2 import schema
from app.research.gate2 import weight_search


def _rec(symbol, ts, scores, fwd):
    return weight_search.EvaluationRecord(
        symbol=symbol, ts=ts, scores=scores, forward_return_bps=fwd
    )


class BuildDailyWalkForwardWindowsTest(unittest.TestCase):
    def test_windows_over_date_part_no_train_test_overlap(self):
        # Two intraday ts per date; window builder must collapse to the
        # DATE part (YYYY-MM-DD) and roll fixed train/test day windows.
        dates = ["2023-01-02", "2023-01-03", "2023-01-04",
                 "2023-01-05", "2023-01-06"]
        records = []
        for d in dates:
            records.append(_rec("AAA", f"{d} 09:01:00", {}, 0.0))
            records.append(_rec("AAA", f"{d} 09:02:00", {}, 0.0))
        windows = weight_search.build_daily_walk_forward_windows(
            records, train_days=2, test_days=2
        )
        # step defaults to test_days (=2): [d0,d1|d2,d3] then step 2 -> start d2
        # -> [d2,d3|d4,?] incomplete test -> dropped. One complete window.
        self.assertEqual(
            windows,
            [
                weight_search.WalkForwardWindow(
                    train_ts=("2023-01-02", "2023-01-03"),
                    test_ts=("2023-01-04", "2023-01-05"),
                ),
            ],
        )
        w = windows[0]
        self.assertEqual(
            set(w.train_ts) & set(w.test_ts), set(),
            "train/test date sets must not overlap",
        )

    def test_step_days_one_rolls_by_single_day(self):
        dates = ["2023-01-02", "2023-01-03", "2023-01-04",
                 "2023-01-05", "2023-01-06"]
        records = [_rec("AAA", f"{d} 10:00:00", {}, 0.0) for d in dates]
        windows = weight_search.build_daily_walk_forward_windows(
            records, train_days=2, test_days=1, step_days=1
        )
        self.assertEqual(
            windows,
            [
                weight_search.WalkForwardWindow(
                    train_ts=("2023-01-02", "2023-01-03"),
                    test_ts=("2023-01-04",),
                ),
                weight_search.WalkForwardWindow(
                    train_ts=("2023-01-03", "2023-01-04"),
                    test_ts=("2023-01-05",),
                ),
                weight_search.WalkForwardWindow(
                    train_ts=("2023-01-04", "2023-01-05"),
                    test_ts=("2023-01-06",),
                ),
            ],
        )

    def test_duplicate_dates_collapse_and_sort(self):
        raw = ["2023-02-03 09:05:00", "2023-02-01 15:20:00",
               "2023-02-02 11:00:00", "2023-02-03 09:06:00",
               "2023-02-01 09:01:00"]
        records = [_rec("AAA", ts, {}, 0.0) for ts in raw]
        windows = weight_search.build_daily_walk_forward_windows(
            records, train_days=1, test_days=1, step_days=1
        )
        self.assertEqual(
            windows,
            [
                weight_search.WalkForwardWindow(
                    train_ts=("2023-02-01",), test_ts=("2023-02-02",)
                ),
                weight_search.WalkForwardWindow(
                    train_ts=("2023-02-02",), test_ts=("2023-02-03",)
                ),
            ],
        )

    def test_size_and_step_below_one_raise(self):
        records = [_rec("AAA", f"2023-01-0{i} 10:00:00", {}, 0.0)
                   for i in range(1, 6)]
        cases = {
            "train_days": {"train_days": 0, "test_days": 1, "step_days": 1},
            "test_days": {"train_days": 1, "test_days": 0, "step_days": 1},
            "step_days": {"train_days": 1, "test_days": 1, "step_days": 0},
        }
        for label, kwargs in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    weight_search.build_daily_walk_forward_windows(
                        records, **kwargs
                    )


class GenerateRandomCandidatesSeedTest(unittest.TestCase):
    def test_same_seed_reproduces_identical_candidates(self):
        base = schema.default_artifact()
        a = weight_search.generate_random_candidates(base, n=5, seed=20260702)
        b = weight_search.generate_random_candidates(base, n=5, seed=20260702)
        self.assertEqual(len(a), 5)
        self.assertEqual(a, b)

    def test_different_seed_changes_candidates(self):
        base = schema.default_artifact()
        a = weight_search.generate_random_candidates(base, n=5, seed=1)
        b = weight_search.generate_random_candidates(base, n=5, seed=2)
        self.assertNotEqual(
            [c.weights for c in a], [c.weights for c in b]
        )

    def test_candidates_are_valid_and_within_bounds(self):
        base = schema.default_artifact()
        cands = weight_search.generate_random_candidates(
            base, n=8, seed=99, weight_low=0.0, weight_high=1.5,
            thresholds=(55, 60, 65),
        )
        for c in cands:
            with self.subTest(version=c.version):
                # non-frozen weights are clamped into the schema range [0, 1]
                self.assertIsNone(schema.validate_artifact(c))
                self.assertIn(c.buy_threshold, (55, 60, 65))
                # caps carried through from base unchanged
                self.assertEqual(
                    c.normalization_caps, base.normalization_caps
                )

    def test_version_strings_distinguishable_per_seed_and_index(self):
        base = schema.default_artifact()
        cands = weight_search.generate_random_candidates(base, n=3, seed=42)
        versions = [c.version for c in cands]
        self.assertEqual(len(set(versions)), 3)
        for i, v in enumerate(versions):
            self.assertEqual(v, f"w1-rand-42-{i}")

    def test_zero_candidates_returns_empty(self):
        base = schema.default_artifact()
        self.assertEqual(
            weight_search.generate_random_candidates(base, n=0, seed=1), []
        )


def _single_pullback_rec(ts, p, f):
    return _rec("AAA", ts, {"pullback_strength_score": p}, f)


def _pullback_artifact(threshold):
    # final_score == pullback value (single weight of 1.0), rest do not matter
    weights = {name: 0.0 for name in schema.CONDITION_SCORE_NAMES}
    weights["pullback_strength_score"] = 1.0
    return schema.ScoreV2Artifact(
        version="dd-cand",
        weights=weights,
        buy_threshold=threshold,
        normalization_caps={},
    )


class CandidateObjectiveV2Test(unittest.TestCase):
    def _selected_eval(self):
        # threshold 50: every record with pullback>=50 is selected, in order.
        # selected returns (order preserved): [10, -30, 5, -20, 15]
        records = [
            _single_pullback_rec("2023-01-02 09:01:00", 90.0, 10.0),
            _single_pullback_rec("2023-01-02 09:02:00", 90.0, -30.0),
            _single_pullback_rec("2023-01-02 09:03:00", 90.0, 5.0),
            _single_pullback_rec("2023-01-02 09:04:00", 90.0, -20.0),
            _single_pullback_rec("2023-01-02 09:05:00", 90.0, 15.0),
            # not selected (below threshold) -> excluded from the sequence
            _single_pullback_rec("2023-01-02 09:06:00", 10.0, 999.0),
        ]
        return weight_search.evaluate_candidate_v2(
            records, _pullback_artifact(50.0)
        )

    def test_evaluate_candidate_v2_preserves_selected_sequence(self):
        ev = self._selected_eval()
        self.assertEqual(ev.selected_returns, (10.0, -30.0, 5.0, -20.0, 15.0))
        self.assertEqual(ev.selected_count, 5)
        self.assertEqual(ev.total_return_bps, -20.0)
        self.assertEqual(ev.mean_selected_return_bps, -4.0)

    def test_mode_mean_matches_mean_selected(self):
        ev = self._selected_eval()
        self.assertEqual(
            weight_search.candidate_objective_v2(
                ev, mode="mean", min_selected=1
            ),
            -4.0,
        )

    def test_mode_total_sums_selected(self):
        ev = self._selected_eval()
        self.assertEqual(
            weight_search.candidate_objective_v2(
                ev, mode="total", min_selected=1
            ),
            -20.0,
        )

    def test_mode_dd_adjusted_hand_verified(self):
        # cumsum([10,-30,5,-20,15]) = [10,-20,-15,-35,-20]
        # running peak       = [10, 10, 10, 10, 10]
        # drawdown (peak-cum)= [ 0, 30, 25, 45, 30] -> max dd = 45
        # total = -20 ; dd_adjusted = -20 - 0.5*45 = -42.5
        ev = self._selected_eval()
        self.assertEqual(
            weight_search.candidate_objective_v2(
                ev, mode="dd_adjusted", dd_lambda=0.5, min_selected=1
            ),
            -42.5,
        )

    def test_dd_lambda_scales_penalty(self):
        ev = self._selected_eval()
        # lambda 0 -> equals total; lambda 1 -> total - maxdd = -20 - 45 = -65
        self.assertEqual(
            weight_search.candidate_objective_v2(
                ev, mode="dd_adjusted", dd_lambda=0.0, min_selected=1
            ),
            -20.0,
        )
        self.assertEqual(
            weight_search.candidate_objective_v2(
                ev, mode="dd_adjusted", dd_lambda=1.0, min_selected=1
            ),
            -65.0,
        )

    def test_monotonic_up_sequence_has_zero_drawdown(self):
        # all-positive selected returns -> cumsum monotonic up -> max dd 0
        records = [
            _single_pullback_rec("2023-01-02 09:01:00", 90.0, 4.0),
            _single_pullback_rec("2023-01-02 09:02:00", 90.0, 6.0),
            _single_pullback_rec("2023-01-02 09:03:00", 90.0, 10.0),
        ]
        ev = weight_search.evaluate_candidate_v2(
            records, _pullback_artifact(50.0)
        )
        # dd_adjusted == total when there is no drawdown
        self.assertEqual(
            weight_search.candidate_objective_v2(
                ev, mode="dd_adjusted", dd_lambda=0.5, min_selected=1
            ),
            20.0,
        )

    def test_below_min_selected_returns_negative_infinity(self):
        ev = self._selected_eval()  # selected_count == 5
        for mode in ("mean", "total", "dd_adjusted"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    weight_search.candidate_objective_v2(
                        ev, mode=mode, min_selected=6
                    ),
                    float("-inf"),
                )

    def test_default_min_selected_is_30(self):
        # 5 selected < default 30 -> -inf for every mode
        ev = self._selected_eval()
        for mode in ("mean", "total", "dd_adjusted"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    weight_search.candidate_objective_v2(ev, mode=mode),
                    float("-inf"),
                )

    def test_zero_selected_returns_negative_infinity(self):
        records = [_single_pullback_rec("2023-01-02 09:01:00", 10.0, 5.0)]
        ev = weight_search.evaluate_candidate_v2(
            records, _pullback_artifact(50.0)
        )
        self.assertEqual(ev.selected_count, 0)
        self.assertEqual(
            weight_search.candidate_objective_v2(
                ev, mode="dd_adjusted", min_selected=1
            ),
            float("-inf"),
        )

    def test_unknown_mode_raises(self):
        ev = self._selected_eval()
        with self.assertRaises(ValueError):
            weight_search.candidate_objective_v2(
                ev, mode="sharpe", min_selected=1
            )


class RandomCandidatesFrozenKeysTest(unittest.TestCase):
    _DEFAULT_FROZEN = (
        "portfolio_diversification_score",
        "volatility_risk_score",
    )

    def test_default_frozen_keys_equal_base_across_all_candidates(self):
        base = schema.default_artifact()
        cands = weight_search.generate_random_candidates(base, n=30, seed=7)
        for c in cands:
            for key in self._DEFAULT_FROZEN:
                with self.subTest(version=c.version, key=key):
                    self.assertEqual(c.weights[key], base.weights[key])

    def test_frozen_keys_stay_fixed_even_with_zero_high_range(self):
        # weight_high=0.0 would drive every *drawn* weight to 0.0; the frozen
        # dims must NOT be driven to 0 -- they keep base's (non-zero) values.
        base = schema.default_artifact()
        cands = weight_search.generate_random_candidates(
            base, n=10, seed=3, weight_low=0.0, weight_high=0.0
        )
        for c in cands:
            for key in self._DEFAULT_FROZEN:
                with self.subTest(version=c.version, key=key):
                    self.assertEqual(c.weights[key], base.weights[key])
                    self.assertGreater(c.weights[key], 0.0)
            # a non-frozen dim was driven to 0.0 by the zero-width range
            self.assertEqual(c.weights["pullback_strength_score"], 0.0)

    def test_custom_frozen_keys_are_honored(self):
        base = schema.default_artifact()
        frozen = ("liquidity_score", "cost_quality_score")
        cands = weight_search.generate_random_candidates(
            base, n=12, seed=11, frozen_keys=frozen
        )
        for c in cands:
            for key in frozen:
                with self.subTest(version=c.version, key=key):
                    self.assertEqual(c.weights[key], base.weights[key])
            # a key NOT in the custom frozen set is allowed to differ from base
            # for at least one candidate (i.e. it was actually randomized)
        self.assertTrue(
            any(
                c.weights["portfolio_diversification_score"]
                != base.weights["portfolio_diversification_score"]
                for c in cands
            ),
            "non-frozen dim should be randomized when not in frozen_keys",
        )

    def test_empty_frozen_keys_randomizes_every_dim(self):
        base = schema.default_artifact()
        cands = weight_search.generate_random_candidates(
            base, n=20, seed=5, frozen_keys=()
        )
        for key in self._DEFAULT_FROZEN:
            with self.subTest(key=key):
                self.assertTrue(
                    any(
                        c.weights[key] != base.weights[key] for c in cands
                    ),
                    f"{key} should vary when frozen_keys is empty",
                )


if __name__ == "__main__":
    unittest.main()
