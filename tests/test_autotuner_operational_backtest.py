"""Tests for the autotuner operational-cadence backtester.

This producer emits autotuner-domain evals ({parameter, to_value}) for
autotuner_screen. It is offline/read-only and stays disjoint from the
strategy/research backtester domain.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from app.autotuner.candidate_suggest import suggest_candidates
from app.autotuner.operational_backtest import (
    build_operational_cadence_eval,
    load_session_summaries,
)
from app.autotuner.screening import evals_to_screening

_NOW = datetime(2026, 6, 7, 10, 0, 0, tzinfo=timezone.utc)


def _session_summary(*, errors: int = 0) -> dict:
    return {
        "session_id": "session_20260607_mock",
        "metrics": {
            "api_call_count": 1200,
            "rate_limit_hits": 0,
            "runtime_errors": errors,
            "broker_errors": 0,
            "order_rejections": 0,
            "buy_scan_cycles": 30,
        },
    }


class OperationalCadenceBacktestTests(unittest.TestCase):
    def test_conservative_tier_a_candidate_emits_pass_eval(self) -> None:
        artifact = build_operational_cadence_eval(
            [{"parameter": "buy_scan_shallow_top_k", "to_value": 8}],
            baseline_values={"buy_scan_shallow_top_k": 10},
            session_summaries=[_session_summary()],
            generated_at=_NOW,
            source_paths=["session.json"],
        )

        self.assertEqual(artifact["overall_verdict"], "pass")
        evaluation = artifact["evaluations"][0]
        self.assertEqual(evaluation["verdict"], "pass")
        self.assertEqual(
            evaluation["changes"],
            [{"parameter": "buy_scan_shallow_top_k", "from_value": 10, "to_value": 8}],
        )
        self.assertEqual(evaluation["deltas"]["estimated_request_pressure_pct"], -20.0)
        self.assertEqual(artifact["provenance"]["source"], "kis-trader-operational-cadence")
        self.assertTrue(artifact["provenance"]["proxy_evidence"])

    def test_aggressive_or_blocked_candidates_emit_fail_eval(self) -> None:
        artifact = build_operational_cadence_eval(
            [
                {"parameter": "buy_scan_shallow_top_k", "to_value": 12},
                {"parameter": "buy_max_qty_per_trade", "to_value": 11},
            ],
            baseline_values={
                "buy_scan_shallow_top_k": 10,
                "buy_max_qty_per_trade": 10,
            },
            session_summaries=[_session_summary()],
            generated_at=_NOW,
        )

        self.assertEqual([item["verdict"] for item in artifact["evaluations"]], ["fail", "fail"])
        self.assertIn("increases operational pressure", artifact["evaluations"][0]["reasons"][0])
        self.assertIn("not screenable", " ".join(artifact["evaluations"][1]["reasons"]))

    def test_whitelist_loaded_once_per_batch(self) -> None:
        import app.autotuner.operational_backtest as obt

        real_load = obt.load_whitelist
        calls = {"n": 0}

        def _counting_load(*args, **kwargs):
            calls["n"] += 1
            return real_load(*args, **kwargs)

        obt.load_whitelist = _counting_load
        try:
            obt.build_operational_cadence_eval(
                [
                    {"parameter": "buy_scan_shallow_top_k", "to_value": 8},
                    {"parameter": "sell_check_interval_seconds", "to_value": 45},
                    {"parameter": "buy_scan_deep_eval_limit", "to_value": 3},
                ],
                baseline_values={
                    "buy_scan_shallow_top_k": 10,
                    "sell_check_interval_seconds": 35,
                    "buy_scan_deep_eval_limit": 5,
                },
                session_summaries=[_session_summary()],
                generated_at=_NOW,
            )
        finally:
            obt.load_whitelist = real_load

        self.assertEqual(calls["n"], 1)

    def test_provenance_includes_repo_head_when_provided(self) -> None:
        artifact = build_operational_cadence_eval(
            [{"parameter": "buy_scan_shallow_top_k", "to_value": 8}],
            baseline_values={"buy_scan_shallow_top_k": 10},
            session_summaries=[_session_summary()],
            generated_at=_NOW,
            repo_head="abc1234",
        )
        self.assertEqual(artifact["provenance"]["repo_head"], "abc1234")

    def test_empty_candidates_yield_empty_verdict(self) -> None:
        artifact = build_operational_cadence_eval(
            [],
            baseline_values={},
            session_summaries=[_session_summary()],
            generated_at=_NOW,
        )
        self.assertEqual(artifact["evaluations"], [])
        self.assertEqual(artifact["overall_verdict"], "empty")

    def test_source_session_errors_make_candidate_fail(self) -> None:
        artifact = build_operational_cadence_eval(
            [{"parameter": "sell_check_interval_seconds", "from_value": 35, "to_value": 45}],
            baseline_values={},
            session_summaries=[_session_summary(errors=1)],
            generated_at=_NOW,
        )
        self.assertEqual(artifact["evaluations"][0]["verdict"], "fail")
        self.assertIn("runtime errors", " ".join(artifact["evaluations"][0]["reasons"]))

    def test_output_feeds_screening_and_suggest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = Path(tmp) / "eval_operational.json"
            artifact = build_operational_cadence_eval(
                [{"parameter": "buy_scan_shallow_top_k", "to_value": 8}],
                baseline_values={"buy_scan_shallow_top_k": 10},
                session_summaries=[_session_summary()],
                generated_at=_NOW,
            )
            eval_path.write_text(json.dumps(artifact), encoding="utf-8")

            screening = evals_to_screening([eval_path])
            plan = suggest_candidates(
                screening,
                baseline_values={"buy_scan_shallow_top_k": 10},
                date="20260607",
            )

        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["parameter"], "buy_scan_shallow_top_k")
        self.assertEqual(plan[0]["to_value"], 8)
        self.assertEqual(plan[0]["backtest_eval"], str(eval_path))

    def test_load_session_summaries_skips_bad_or_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "good.json"
            bad = root / "bad.json"
            huge = root / "huge.json"
            good.write_text(json.dumps(_session_summary()), encoding="utf-8")
            bad.write_text("{bad", encoding="utf-8")
            huge.write_text("x" * (600 * 1024), encoding="utf-8")

            summaries, paths = load_session_summaries([bad, huge, good])

        self.assertEqual(len(summaries), 1)
        self.assertEqual(paths, [str(good)])


class WalkForwardHoldoutTests(unittest.TestCase):
    def test_train_pass_holdout_pass_is_confirmed(self) -> None:
        artifact = build_operational_cadence_eval(
            [{"parameter": "buy_scan_shallow_top_k", "to_value": 8}],
            baseline_values={"buy_scan_shallow_top_k": 10},
            session_summaries=[_session_summary()],
            holdout_summaries=[_session_summary()],
            generated_at=_NOW,
        )

        evaluation = artifact["evaluations"][0]
        # top-level verdict stays in-sample; downgrade (if any) is screening's job
        self.assertEqual(evaluation["verdict"], "pass")
        self.assertEqual(evaluation["in_sample"]["verdict"], "pass")
        self.assertEqual(evaluation["out_of_sample"]["verdict"], "pass")
        recon = evaluation["verdict_reconciliation"]
        self.assertEqual(recon["status"], "confirmed")
        self.assertTrue(recon["agree"])
        self.assertIsNone(recon["downgrade_to"])

    def test_train_pass_holdout_fail_flags_inconclusive_downgrade(self) -> None:
        # train window is clean, holdout window carries a runtime error so the same
        # move fails out-of-sample -> walk-forward contradiction.
        artifact = build_operational_cadence_eval(
            [{"parameter": "buy_scan_shallow_top_k", "to_value": 8}],
            baseline_values={"buy_scan_shallow_top_k": 10},
            session_summaries=[_session_summary()],
            holdout_summaries=[_session_summary(errors=1)],
            generated_at=_NOW,
        )

        evaluation = artifact["evaluations"][0]
        # producer keeps the in-sample verdict; the downgrade is screening's job
        self.assertEqual(evaluation["verdict"], "pass")
        self.assertEqual(evaluation["out_of_sample"]["verdict"], "fail")
        self.assertIn(
            "runtime errors",
            " ".join(evaluation["out_of_sample"]["reasons"]),
        )
        recon = evaluation["verdict_reconciliation"]
        self.assertEqual(recon["status"], "holdout_contradicts")
        self.assertFalse(recon["agree"])
        self.assertEqual(recon["downgrade_to"], "inconclusive")

    def test_no_holdout_keeps_pre_w1_shape(self) -> None:
        # backward compat: without a holdout window no walk-forward keys appear.
        artifact = build_operational_cadence_eval(
            [{"parameter": "buy_scan_shallow_top_k", "to_value": 8}],
            baseline_values={"buy_scan_shallow_top_k": 10},
            session_summaries=[_session_summary()],
            generated_at=_NOW,
        )
        evaluation = artifact["evaluations"][0]
        self.assertNotIn("in_sample", evaluation)
        self.assertNotIn("out_of_sample", evaluation)
        self.assertNotIn("verdict_reconciliation", evaluation)
        self.assertNotIn("holdout_sessions", artifact["provenance"])

    def test_holdout_window_inputs_recorded_in_provenance(self) -> None:
        # the holdout window paths are surfaced for human-review audit, separate
        # from the in-sample input_sessions.
        artifact = build_operational_cadence_eval(
            [{"parameter": "buy_scan_shallow_top_k", "to_value": 8}],
            baseline_values={"buy_scan_shallow_top_k": 10},
            session_summaries=[_session_summary()],
            source_paths=["train/session_a.json"],
            holdout_summaries=[_session_summary()],
            holdout_source_paths=["holdout/session_b.json"],
            generated_at=_NOW,
        )
        provenance = artifact["provenance"]
        self.assertEqual(provenance["input_sessions"], ["train/session_a.json"])
        self.assertEqual(provenance["holdout_sessions"], ["holdout/session_b.json"])

    def test_top_level_walk_forward_summary_counts_reconciliation(self) -> None:
        # The artifact-level overall_verdict reflects only the in-sample window, so a
        # holdout-aware summary is surfaced at the top level to keep a human/JSON reader
        # from being misled by overall_verdict='pass' on a holdout-contradicted move.
        artifact = build_operational_cadence_eval(
            [
                {"parameter": "buy_scan_shallow_top_k", "to_value": 8},
                {"parameter": "sell_check_interval_seconds", "from_value": 35, "to_value": 45},
            ],
            baseline_values={"buy_scan_shallow_top_k": 10},
            session_summaries=[_session_summary()],
            holdout_summaries=[_session_summary(errors=1)],
            generated_at=_NOW,
        )
        walk_forward = artifact["walk_forward"]
        self.assertEqual(walk_forward["holdout_contradicted"], 2)
        self.assertEqual(walk_forward["confirmed"], 0)
        self.assertEqual(walk_forward["in_sample_fail"], 0)

        # without a holdout window there is no walk_forward summary (pre-W1 shape)
        no_holdout = build_operational_cadence_eval(
            [{"parameter": "buy_scan_shallow_top_k", "to_value": 8}],
            baseline_values={"buy_scan_shallow_top_k": 10},
            session_summaries=[_session_summary()],
            generated_at=_NOW,
        )
        self.assertNotIn("walk_forward", no_holdout)

    def test_in_sample_deltas_are_copied_not_aliased(self) -> None:
        # in_sample must not share the mutable deltas/data_window objects with the
        # top-level evaluation, so a consumer mutating one cannot corrupt the other.
        artifact = build_operational_cadence_eval(
            [{"parameter": "buy_scan_shallow_top_k", "to_value": 8}],
            baseline_values={"buy_scan_shallow_top_k": 10},
            session_summaries=[_session_summary()],
            holdout_summaries=[_session_summary()],
            generated_at=_NOW,
        )
        evaluation = artifact["evaluations"][0]
        self.assertIsNot(evaluation["in_sample"]["deltas"], evaluation["deltas"])
        self.assertEqual(evaluation["in_sample"]["deltas"], evaluation["deltas"])
        self.assertIsNot(
            evaluation["in_sample"]["data_window"], evaluation["data_window"]
        )

    def test_in_sample_fail_is_not_rescued_by_clean_holdout(self) -> None:
        # train window already fails (runtime error); a clean holdout must NOT promote it.
        artifact = build_operational_cadence_eval(
            [{"parameter": "sell_check_interval_seconds", "from_value": 35, "to_value": 45}],
            baseline_values={},
            session_summaries=[_session_summary(errors=1)],
            holdout_summaries=[_session_summary()],
            generated_at=_NOW,
        )
        evaluation = artifact["evaluations"][0]
        self.assertEqual(evaluation["verdict"], "fail")
        self.assertEqual(evaluation["out_of_sample"]["verdict"], "pass")
        recon = evaluation["verdict_reconciliation"]
        self.assertEqual(recon["status"], "in_sample_fail")
        self.assertIsNone(recon["downgrade_to"])
        self.assertEqual(artifact["walk_forward"]["in_sample_fail"], 1)


class OperationalCadenceBacktestCliTests(unittest.TestCase):
    def test_cli_writes_eval_artifact(self) -> None:
        from app.tools.autotuner_operational_backtest import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.json"
            baseline = root / "baseline.json"
            summary = root / "session.json"
            out = root / "eval.json"
            candidates.write_text(
                json.dumps([{"parameter": "buy_scan_shallow_top_k", "to_value": 8}]),
                encoding="utf-8",
            )
            baseline.write_text(json.dumps({"buy_scan_shallow_top_k": 10}), encoding="utf-8")
            summary.write_text(json.dumps(_session_summary()), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(
                    [
                        "--candidates",
                        str(candidates),
                        "--baseline",
                        str(baseline),
                        "--session-summary",
                        str(summary),
                        "--out",
                        str(out),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertTrue(out.exists())
            artifact = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(artifact["evaluations"][0]["verdict"], "pass")
            self.assertIn("overall=pass", buf.getvalue())

    def test_cli_stamps_repo_head(self) -> None:
        from app.tools.autotuner_operational_backtest import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.json"
            baseline = root / "baseline.json"
            summary = root / "session.json"
            out = root / "eval.json"
            candidates.write_text(
                json.dumps([{"parameter": "buy_scan_shallow_top_k", "to_value": 8}]),
                encoding="utf-8",
            )
            baseline.write_text(json.dumps({"buy_scan_shallow_top_k": 10}), encoding="utf-8")
            summary.write_text(json.dumps(_session_summary()), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "--candidates",
                        str(candidates),
                        "--baseline",
                        str(baseline),
                        "--session-summary",
                        str(summary),
                        "--out",
                        str(out),
                        "--repo-head",
                        "deadbeef",
                    ]
                )

            self.assertEqual(code, 0)
            artifact = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(artifact["provenance"]["repo_head"], "deadbeef")

    def test_cli_holdout_dir_produces_walk_forward_eval(self) -> None:
        from app.tools.autotuner_operational_backtest import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.json"
            baseline = root / "baseline.json"
            train = root / "session.json"
            holdout_dir = root / "holdout"
            holdout_dir.mkdir()
            holdout = holdout_dir / "h1.json"
            out = root / "eval.json"
            candidates.write_text(
                json.dumps([{"parameter": "buy_scan_shallow_top_k", "to_value": 8}]),
                encoding="utf-8",
            )
            baseline.write_text(json.dumps({"buy_scan_shallow_top_k": 10}), encoding="utf-8")
            train.write_text(json.dumps(_session_summary()), encoding="utf-8")
            holdout.write_text(json.dumps(_session_summary(errors=1)), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(
                    [
                        "--candidates",
                        str(candidates),
                        "--baseline",
                        str(baseline),
                        "--session-summary",
                        str(train),
                        "--holdout-dir",
                        str(holdout_dir),
                        "--out",
                        str(out),
                    ]
                )

            self.assertEqual(code, 0)
            artifact = json.loads(out.read_text(encoding="utf-8"))
            evaluation = artifact["evaluations"][0]
            self.assertEqual(
                evaluation["verdict_reconciliation"]["status"], "holdout_contradicts"
            )
            self.assertEqual(evaluation["out_of_sample"]["verdict"], "fail")
            self.assertEqual(artifact["provenance"]["holdout_sessions"], [str(holdout)])
            # operator-facing print surfaces the holdout contradiction at the point of use
            self.assertIn("walk-forward holdout", buf.getvalue())
            self.assertIn("1 contradicted", buf.getvalue())

    def test_cli_holdout_summary_single_files_in_order(self) -> None:
        from app.tools.autotuner_operational_backtest import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.json"
            baseline = root / "baseline.json"
            train = root / "session.json"
            h1 = root / "h1.json"
            h2 = root / "h2.json"
            out = root / "eval.json"
            candidates.write_text(
                json.dumps([{"parameter": "buy_scan_shallow_top_k", "to_value": 8}]),
                encoding="utf-8",
            )
            baseline.write_text(json.dumps({"buy_scan_shallow_top_k": 10}), encoding="utf-8")
            train.write_text(json.dumps(_session_summary()), encoding="utf-8")
            h1.write_text(json.dumps(_session_summary()), encoding="utf-8")
            h2.write_text(json.dumps(_session_summary()), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "--candidates",
                        str(candidates),
                        "--baseline",
                        str(baseline),
                        "--session-summary",
                        str(train),
                        "--holdout-summary",
                        str(h1),
                        "--holdout-summary",
                        str(h2),
                        "--out",
                        str(out),
                    ]
                )

            self.assertEqual(code, 0)
            artifact = json.loads(out.read_text(encoding="utf-8"))
            # both explicit holdout files are recorded, in the order given
            self.assertEqual(
                artifact["provenance"]["holdout_sessions"], [str(h1), str(h2)]
            )
            evaluation = artifact["evaluations"][0]
            self.assertEqual(evaluation["out_of_sample"]["verdict"], "pass")
            self.assertEqual(
                evaluation["verdict_reconciliation"]["status"], "confirmed"
            )

    def test_cli_requires_local_session_evidence(self) -> None:
        from app.tools.autotuner_operational_backtest import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.json"
            baseline = root / "baseline.json"
            candidates.write_text(json.dumps([]), encoding="utf-8")
            baseline.write_text(json.dumps({}), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(
                    [
                        "--candidates",
                        str(candidates),
                        "--baseline",
                        str(baseline),
                        "--out",
                        str(root / "eval.json"),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("session-summary", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
