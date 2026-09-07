"""Tests for the autotuner evidence-source bridge (post-Phase-4 Step 3).

These adapters bridge the pre-existing research pipeline's real outputs into the
autotuner's evidence entries WITHOUT a rewrite (see
docs/live_autotuner_pipeline_reconciliation.md):
- a ``run_proposal_backtest`` evaluation dict -> a ``backtest`` evidence entry,
- a session-summary dict -> a ``live_log`` evidence entry.
They are read-only/proposal-generation only and never apply anything.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.autotuner.evidence_sources import (
    backtest_result_to_evidence,
    live_log_summary_to_evidence,
    load_backtest_evidence,
    load_live_log_evidence,
)


def _eval_result(*, verdict: str = "pass") -> dict:
    """The shape run_proposal_backtest writes to research/evaluations/."""
    return {
        "evaluation_type": "proposal_vs_baseline",
        "proposal_id": "atp_20260604_shadow_0001",
        "candidate_run_id": "run_cand_001",
        "baseline_run_id": "run_base_000",
        "candidate_performance": {"sharpe": 1.32, "max_drawdown": 11.0, "total_return": 8.4},
        "baseline_performance": {"sharpe": 1.20, "max_drawdown": 12.5, "total_return": 6.3},
        "deltas": {"sharpe": 0.12, "max_drawdown": -1.5, "total_return": 2.1, "win_rate": 0.03},
        "pass_criteria": {"sharpe_improves": True, "mdd_acceptable": True, "overall": True},
        "verdict": verdict,
    }


class BacktestResultToEvidenceTests(unittest.TestCase):
    def test_shapes_a_backtest_evidence_entry(self) -> None:
        ev = backtest_result_to_evidence(
            _eval_result(),
            source_id="bt_run_cand_001",
            source_path="research/evaluations/eval_atp_20260604_shadow_0001.json",
            generated_at="2026-06-04T11:00:00+09:00",
        )
        self.assertEqual(ev["source_type"], "backtest")
        self.assertEqual(ev["source_id"], "bt_run_cand_001")
        self.assertEqual(ev["generated_at"], "2026-06-04T11:00:00+09:00")
        # summary should carry the verdict and the sharpe delta signal
        self.assertIn("pass", ev["summary"])
        self.assertIn("sharpe", ev["summary"])
        # proxy-evidence honesty (D4) is preserved by the shaper
        self.assertIn("proxy", ev["limitations"])

    def test_pass_verdict_is_higher_confidence_than_fail(self) -> None:
        ev_pass = backtest_result_to_evidence(
            _eval_result(verdict="pass"),
            source_id="x", source_path="p", generated_at="t",
        )
        ev_fail = backtest_result_to_evidence(
            _eval_result(verdict="fail"),
            source_id="x", source_path="p", generated_at="t",
        )
        self.assertEqual(ev_pass["confidence"], "medium")
        self.assertEqual(ev_fail["confidence"], "low")
        self.assertIn("fail", ev_fail["summary"])


class LoadBacktestEvidenceTests(unittest.TestCase):
    def test_reads_eval_json_and_derives_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eval_atp_20260604_shadow_0001.json"
            path.write_text(json.dumps(_eval_result()), encoding="utf-8")
            ev = load_backtest_evidence(path)
            self.assertEqual(ev["source_type"], "backtest")
            self.assertEqual(ev["source_path"], str(path))
            # source_id defaults from the eval's candidate_run_id
            self.assertIn("run_cand_001", ev["source_id"])

    def test_load_delegates_to_the_shaper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eval.json"
            path.write_text(json.dumps(_eval_result()), encoding="utf-8")
            ev = load_backtest_evidence(path)
            # full evidence shape from the shaper, not a bare dict
            self.assertIn("proxy", ev["limitations"])
            self.assertIn("verdict", ev["summary"])
            self.assertTrue(ev["generated_at"])  # defaulted from file mtime

    def test_reads_real_aggregate_eval_shape(self) -> None:
        # The real on-disk file run_proposal_backtest writes wraps per-evaluation
        # results in `evaluations[]` with a top-level `overall_verdict` — the
        # evidence shaper must read that, not degrade to verdict=unknown.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eval_agg.json"
            path.write_text(
                json.dumps(
                    {
                        "proposal_id": "atp_agg",
                        "evaluations": [
                            {"changes": [{"parameter": "p", "to_value": 12}],
                             "deltas": {"sharpe": 0.2}, "verdict": "pass"}
                        ],
                        "overall_verdict": "pass",
                    }
                ),
                encoding="utf-8",
            )
            ev = load_backtest_evidence(path)
            self.assertEqual(ev["source_type"], "backtest")
            self.assertIn("pass", ev["summary"])
            self.assertEqual(ev["confidence"], "medium")  # pass -> medium

    def test_evidence_carries_provenance_from_the_eval(self) -> None:
        # E1: a backtest evidence entry should carry the eval's provenance
        # (source/repo_head/data_window) so the proxy origin is auditable.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eval_prov.json"
            path.write_text(
                json.dumps(
                    {
                        "proposal_id": "atp_prov",
                        "provenance": {
                            "source": "open-trading-api",
                            "repo_head": "abc123",
                            "data_window": {"start_date": "2025-10-11", "end_date": "2026-04-11"},
                        },
                        "evaluations": [
                            {"changes": [{"parameter": "p", "to_value": 12}],
                             "deltas": {"sharpe": 0.2}, "verdict": "pass"}
                        ],
                        "overall_verdict": "pass",
                    }
                ),
                encoding="utf-8",
            )
            ev = load_backtest_evidence(path)
            self.assertEqual(ev["provenance"]["source"], "open-trading-api")
            self.assertEqual(ev["provenance"]["repo_head"], "abc123")
            self.assertEqual(
                ev["provenance"]["data_window"],
                {"start_date": "2025-10-11", "end_date": "2026-04-11"},
            )

    def test_load_refuses_a_non_object_eval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_backtest_evidence(path)

    def test_load_refuses_an_oversized_eval(self) -> None:
        # docstring promises a size-bounded read: a huge artifact must not be
        # slurped wholesale (proposal-generation path safety).
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "huge.json"
            payload = _eval_result()
            payload["padding"] = "x" * (2 * 1024 * 1024)  # ~2 MiB
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_backtest_evidence(path)


class LiveLogSummaryToEvidenceTests(unittest.TestCase):
    def test_shapes_a_live_log_evidence_entry(self) -> None:
        ev = live_log_summary_to_evidence(
            {"session_date": "2026-06-03", "cycles": 42, "buys": 3, "sells": 2},
            source_id="ll_20260603",
            source_path="research/sessions/session_20260603.json",
            generated_at="2026-06-04T08:00:00+09:00",
        )
        self.assertEqual(ev["source_type"], "live_log")
        self.assertEqual(ev["source_id"], "ll_20260603")
        self.assertIn("2026-06-03", ev["summary"])
        self.assertIn("reconstruction", ev["limitations"])

    def test_load_live_log_evidence_reads_summary_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session_20260603.json"
            path.write_text(
                json.dumps({"session_date": "2026-06-03", "cycles": 42}),
                encoding="utf-8",
            )
            ev = load_live_log_evidence(path)
            self.assertEqual(ev["source_type"], "live_log")
            self.assertEqual(ev["source_path"], str(path))
            self.assertIn("2026-06-03", ev["source_id"])

    def test_load_live_log_delegates_to_shaper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            path.write_text(
                json.dumps({"session_date": "2026-06-03", "cycles": 7}),
                encoding="utf-8",
            )
            ev = load_live_log_evidence(path)
            self.assertIn("reconstruction", ev["limitations"])
            self.assertIn("2026-06-03", ev["summary"])
            self.assertTrue(ev["generated_at"])


if __name__ == "__main__":
    unittest.main()
