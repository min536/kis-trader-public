"""Tests for app.research.gate2.weight_search (W1)."""

import unittest

from app.gate2 import schema
from app.research.gate2 import weight_search


def _rec(symbol, ts, scores, fwd):
    return weight_search.EvaluationRecord(
        symbol=symbol, ts=ts, scores=scores, forward_return_bps=fwd
    )


class BuildWalkForwardWindowsTest(unittest.TestCase):
    def test_rolling_windows_full_literal(self):
        # 7 unique timestamps, train=3 / test=2 / step default (=test_size=2)
        ts_list = ["t0", "t1", "t2", "t3", "t4", "t5", "t6"]
        records = [_rec("AAA", ts, {}, 0.0) for ts in ts_list]
        windows = weight_search.build_walk_forward_windows(
            records, train_size=3, test_size=2
        )
        self.assertEqual(
            windows,
            [
                weight_search.WalkForwardWindow(
                    train_ts=("t0", "t1", "t2"), test_ts=("t3", "t4")
                ),
                weight_search.WalkForwardWindow(
                    train_ts=("t2", "t3", "t4"), test_ts=("t5", "t6")
                ),
            ],
        )

    def test_duplicate_and_unsorted_ts_are_normalized(self):
        # Unsorted with duplicates; unique sorted -> [d1, d2, d3, d4, d5]
        raw = ["d3", "d1", "d2", "d3", "d5", "d4", "d1", "d2"]
        records = [_rec("AAA", ts, {}, 0.0) for ts in raw]
        windows = weight_search.build_walk_forward_windows(
            records, train_size=2, test_size=2, step=1
        )
        self.assertEqual(
            windows,
            [
                weight_search.WalkForwardWindow(
                    train_ts=("d1", "d2"), test_ts=("d3", "d4")
                ),
                weight_search.WalkForwardWindow(
                    train_ts=("d2", "d3"), test_ts=("d4", "d5")
                ),
            ],
        )

    def test_size_and_step_below_one_raise(self):
        records = [_rec("AAA", f"t{i}", {}, 0.0) for i in range(5)]
        cases = {
            "train_size": {"train_size": 0, "test_size": 1, "step": 1},
            "test_size": {"train_size": 1, "test_size": 0, "step": 1},
            "step": {"train_size": 1, "test_size": 1, "step": 0},
        }
        for label, kwargs in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    weight_search.build_walk_forward_windows(records, **kwargs)


def _artifact(version, weights, buy_threshold):
    return schema.ScoreV2Artifact(
        version=version,
        weights=weights,
        buy_threshold=buy_threshold,
        normalization_caps={},
    )


class EvaluateCandidateTest(unittest.TestCase):
    def test_hand_calculated_full_evaluation(self):
        # weights: final = (pullback*1.0 + rebound*0.5) / sum(weights=1.5)
        # — weight-normalized (2026-07-08 score_v2 fix); threshold 60
        weights = {
            "pullback_strength_score": 1.0,
            "rebound_strength_score": 0.5,
        }
        artifact = _artifact("cand_a", weights, 60.0)
        records = [
            # final = (80 + 50) / 1.5 = 86.67 >= 60 -> selected, +10
            _rec("AAA", "t0", {"pullback_strength_score": 80.0,
                               "rebound_strength_score": 100.0}, 10.0),
            # final = 50 / 1.5 = 33.33 >= 60 -> NO; ignored fwd
            _rec("AAA", "t1", {"pullback_strength_score": 50.0}, 999.0),
            # final = 60 / 1.5 = 40.0 >= 60 -> NO; ignored fwd
            _rec("AAA", "t2", {"pullback_strength_score": 60.0}, -5.0),
            # final = (100 + 0 (None)) / 1.5 = 66.67 >= 60 -> selected, +20
            _rec("AAA", "t3", {"pullback_strength_score": 100.0,
                               "rebound_strength_score": None}, 20.0),
        ]
        ev = weight_search.evaluate_candidate(records, artifact)
        # selected: t0(+10), t3(+20); total = 30; mean = 15.0
        # wins = 2 (t0, t3); hit = 2/2 = 1.0
        self.assertEqual(
            ev,
            weight_search.CandidateEvaluation(
                version="cand_a",
                selected_count=2,
                mean_selected_return_bps=15.0,
                hit_rate=1.0,
                total_return_bps=30.0,
            ),
        )

    def test_zero_selected_yields_zero_mean_and_hit(self):
        weights = {"pullback_strength_score": 1.0}
        artifact = _artifact("cand_b", weights, 60.0)
        records = [
            # final = 10 < 60 -> not selected
            _rec("AAA", "t0", {"pullback_strength_score": 10.0}, 5.0),
            # final = 0 (None) < 60 -> not selected
            _rec("AAA", "t1", {"pullback_strength_score": None}, 5.0),
        ]
        ev = weight_search.evaluate_candidate(records, artifact)
        self.assertEqual(
            ev,
            weight_search.CandidateEvaluation(
                version="cand_b",
                selected_count=0,
                mean_selected_return_bps=0.0,
                hit_rate=0.0,
                total_return_bps=0.0,
            ),
        )


class CandidateObjectiveTest(unittest.TestCase):
    def _eval(self, selected_count, mean):
        return weight_search.CandidateEvaluation(
            version="v",
            selected_count=selected_count,
            mean_selected_return_bps=mean,
            hit_rate=0.0,
            total_return_bps=mean * selected_count,
        )

    def test_below_min_selected_returns_negative_infinity(self):
        ev = self._eval(selected_count=2, mean=12.5)
        self.assertEqual(
            weight_search.candidate_objective(ev, min_selected=3),
            float("-inf"),
        )

    def test_boundary_and_default_min_selected(self):
        # at/above min_selected returns mean; default min_selected == 1
        cases = [
            # (selected_count, mean, min_selected_kwargs, expected)
            (3, 12.5, {"min_selected": 3}, 12.5),
            (2, 12.5, {"min_selected": 3}, float("-inf")),
            (0, 0.0, {}, float("-inf")),  # default min_selected=1
            (1, 7.0, {}, 7.0),
        ]
        for count, mean, kwargs, expected in cases:
            with self.subTest(count=count, kwargs=kwargs):
                ev = self._eval(selected_count=count, mean=mean)
                self.assertEqual(
                    weight_search.candidate_objective(ev, **kwargs), expected
                )


class GenerateCoordinateCandidatesTest(unittest.TestCase):
    def test_default_count_and_leading_versions(self):
        base = schema.default_artifact()
        candidates = weight_search.generate_coordinate_candidates(base)
        # count = len(thresholds) + 15 * len(non-1.0 scales) * len(thresholds)
        #       = 3 + 15 * 2 * 3 = 93
        self.assertEqual(len(candidates), 93)
        leading = [c.version for c in candidates[:5]]
        self.assertEqual(
            leading,
            [
                "base@thr55",
                "base@thr60",
                "base@thr65",
                "pullback_strength_scorex0.5@thr55",
                "pullback_strength_scorex0.5@thr60",
            ],
        )

    def test_weight_clamp_scale_and_all_validate(self):
        base = schema.default_artifact()
        candidates = weight_search.generate_coordinate_candidates(base)
        by_version = {c.version: c for c in candidates}

        # base pullback weight is 1.0; x1.5 clamps to 1.0
        clamped = by_version["pullback_strength_scorex1.5@thr55"]
        self.assertEqual(clamped.weights["pullback_strength_score"], 1.0)
        # other weights untouched (rebound base weight 1.0)
        self.assertEqual(clamped.weights["rebound_strength_score"], 1.0)

        # x0.5 halves the base weight: 1.0 * 0.5 = 0.5
        halved = by_version["pullback_strength_scorex0.5@thr65"]
        self.assertEqual(halved.weights["pullback_strength_score"], 0.5)
        self.assertEqual(halved.buy_threshold, 65.0)

        # liquidity_score base weight is 0.25; x0.5 -> 0.125
        liq = by_version["liquidity_scorex0.5@thr60"]
        self.assertEqual(liq.weights["liquidity_score"], 0.125)

        # every candidate independently passes schema validation
        for candidate in candidates:
            with self.subTest(version=candidate.version):
                self.assertIsNone(schema.validate_artifact(candidate))


def _single_pullback_rec(ts, p, f):
    return _rec("AAA", ts, {"pullback_strength_score": p}, f)


class WalkForwardSearchTest(unittest.TestCase):
    def _ab_candidates(self):
        # final_score == pullback value; A selects >=50, B selects >=80
        wa = {"pullback_strength_score": 1.0}
        return [
            _artifact("A", dict(wa), 50.0),
            _artifact("B", dict(wa), 80.0),
        ]

    def test_full_report_equality_with_tie_break(self):
        records = [
            _single_pullback_rec("t0", 90.0, 10.0),
            _single_pullback_rec("t1", 60.0, -4.0),
            _single_pullback_rec("t2", 85.0, 20.0),
            _single_pullback_rec("t3", 40.0, 6.0),
            _single_pullback_rec("t4", 95.0, 30.0),
        ]
        report = weight_search.walk_forward_search(
            records,
            self._ab_candidates(),
            train_size=2,
            test_size=1,
            step=1,
        )
        CE = weight_search.CandidateEvaluation
        WR = weight_search.WindowResult
        expected = weight_search.WalkForwardReport(
            window_results=(
                WR(
                    window_index=0,
                    best_version="B",
                    train_eval=CE("B", 1, 10.0, 1.0, 10.0),
                    test_eval=CE("B", 1, 20.0, 1.0, 20.0),
                ),
                WR(
                    window_index=1,
                    best_version="B",
                    train_eval=CE("B", 1, 20.0, 1.0, 20.0),
                    test_eval=CE("B", 0, 0.0, 0.0, 0.0),
                ),
                WR(
                    window_index=2,
                    best_version="A",
                    train_eval=CE("A", 1, 20.0, 1.0, 20.0),
                    test_eval=CE("A", 1, 30.0, 1.0, 30.0),
                ),
            ),
            version_win_counts={"A": 1, "B": 2},
            overall_best_version="B",
            mean_test_return_bps=50.0 / 3,
        )
        self.assertEqual(report, expected)

    def test_version_win_counts_follow_candidate_input_order(self):
        # dict equality ignores insertion order, so pin the spec's
        # candidates-input-order guarantee explicitly: A is first in
        # _ab_candidates even though B wins more windows.
        records = [
            _single_pullback_rec("t0", 90.0, 10.0),
            _single_pullback_rec("t1", 60.0, -4.0),
            _single_pullback_rec("t2", 85.0, 20.0),
            _single_pullback_rec("t3", 40.0, 6.0),
            _single_pullback_rec("t4", 95.0, 30.0),
        ]
        report = weight_search.walk_forward_search(
            records,
            self._ab_candidates(),
            train_size=2,
            test_size=1,
            step=1,
        )
        self.assertEqual(
            list(report.version_win_counts.items()), [("A", 1), ("B", 2)]
        )

    def test_no_test_leakage_into_candidate_selection(self):
        # candX (thr 50) wins on TRAIN; candY (thr 80) would do better on TEST.
        # The chosen best_version must be the TRAIN winner (X), never Y.
        wa = {"pullback_strength_score": 1.0}
        cand_x = _artifact("X", dict(wa), 50.0)
        cand_y = _artifact("Y", dict(wa), 80.0)
        records = [
            # train (t0, t1): X selects both -> mean 5.0; Y selects only t1 -> 2.0
            _single_pullback_rec("t0", 60.0, 8.0),
            _single_pullback_rec("t1", 90.0, 2.0),
            # test (t2, t3): X is forced to take the -50 loser at t2;
            # Y (had it been chosen) would skip t2 and only take +30 at t3
            _single_pullback_rec("t2", 60.0, -50.0),
            _single_pullback_rec("t3", 90.0, 30.0),
        ]
        report = weight_search.walk_forward_search(
            records, [cand_x, cand_y], train_size=2, test_size=2, step=1
        )
        self.assertEqual(len(report.window_results), 1)
        wr = report.window_results[0]
        # Train winner X is chosen (not the test-better Y)
        self.assertEqual(wr.best_version, "X")
        self.assertEqual(report.overall_best_version, "X")
        # test_eval reflects X on the test fold: mean (-50 + 30) / 2 = -10.0
        self.assertEqual(wr.test_eval.mean_selected_return_bps, -10.0)
        # Independent check: Y on the same test fold WOULD have been +30.0,
        # i.e. strictly better -- yet it was not selected (no leakage).
        test_records = [r for r in records if r.ts in ("t2", "t3")]
        y_on_test = weight_search.evaluate_candidate(test_records, cand_y)
        self.assertEqual(y_on_test.mean_selected_return_bps, 30.0)

    def test_all_below_min_selected_first_candidate_wins(self):
        wa = {"pullback_strength_score": 1.0}
        cand_p = _artifact("P", dict(wa), 95.0)
        cand_q = _artifact("Q", dict(wa), 99.0)
        records = [
            # train (t0, t1): neither candidate selects anything (< 95)
            _single_pullback_rec("t0", 90.0, 5.0),
            _single_pullback_rec("t1", 80.0, 5.0),
            # test (t2, t3): only P selects t2 (96 >= 95)
            _single_pullback_rec("t2", 96.0, 7.0),
            _single_pullback_rec("t3", 50.0, 1.0),
        ]
        report = weight_search.walk_forward_search(
            records,
            [cand_p, cand_q],
            train_size=2,
            test_size=2,
            step=1,
            min_selected=2,
        )
        wr = report.window_results[0]
        # both objectives are -inf on train -> first candidate (P) wins
        self.assertEqual(wr.best_version, "P")
        self.assertEqual(report.overall_best_version, "P")
        self.assertEqual(report.version_win_counts, {"P": 1})
        self.assertEqual(
            wr.train_eval,
            weight_search.CandidateEvaluation("P", 0, 0.0, 0.0, 0.0),
        )
        self.assertEqual(
            wr.test_eval,
            weight_search.CandidateEvaluation("P", 1, 7.0, 1.0, 7.0),
        )

    def test_value_errors(self):
        cands = self._ab_candidates()
        records = [_single_pullback_rec(f"t{i}", 90.0, 1.0) for i in range(5)]
        with self.subTest(case="empty_candidates"):
            with self.assertRaises(ValueError):
                weight_search.walk_forward_search(
                    records, [], train_size=2, test_size=1, step=1
                )
        with self.subTest(case="no_complete_windows"):
            # only 2 timestamps but train_size=3 -> zero complete windows
            short = [_single_pullback_rec("t0", 90.0, 1.0),
                     _single_pullback_rec("t1", 90.0, 1.0)]
            with self.assertRaises(ValueError):
                weight_search.walk_forward_search(
                    short, cands, train_size=3, test_size=1, step=1
                )


class BuildSearchConsoleLinesTest(unittest.TestCase):
    def test_console_lines_literal(self):
        wa = {"pullback_strength_score": 1.0}
        candidates = [
            _artifact("A", dict(wa), 50.0),
            _artifact("B", dict(wa), 80.0),
        ]
        records = [
            _single_pullback_rec("t0", 90.0, 10.0),
            _single_pullback_rec("t1", 60.0, -4.0),
            _single_pullback_rec("t2", 85.0, 20.0),
            _single_pullback_rec("t3", 40.0, 6.0),
            _single_pullback_rec("t4", 95.0, 30.0),
        ]
        report = weight_search.walk_forward_search(
            records, candidates, train_size=2, test_size=1, step=1
        )
        lines = weight_search.build_search_console_lines(report)
        self.assertEqual(
            lines,
            [
                "window=0 best=B train_sel=1 train_mean=10.00 "
                "test_sel=1 test_mean=20.00 test_hit=1.00",
                "window=1 best=B train_sel=1 train_mean=20.00 "
                "test_sel=0 test_mean=0.00 test_hit=0.00",
                "window=2 best=A train_sel=1 train_mean=20.00 "
                "test_sel=1 test_mean=30.00 test_hit=1.00",
                "overall_best=B wins=2/3 mean_test_return_bps=16.67",
            ],
        )


if __name__ == "__main__":
    unittest.main()
