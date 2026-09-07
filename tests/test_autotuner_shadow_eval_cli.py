"""Tests for the autotuner shadow-eval CLI (Step 3 operational entrypoint).

Composes a proposed cadence change + real bridged evidence (a run_proposal_backtest
evaluation and/or a session summary) into a schema-valid ``mode=shadow`` DRAFT
proposal written under ``_workspace/autotuner/proposals/``. Read-only / proposal
generation only: it never approves and never applies anything to the runtime.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.autotuner.validator import validate_proposal
from app.tools.autotuner_shadow_eval import main


def _write_eval(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "evaluation_type": "proposal_vs_baseline",
                "candidate_run_id": "run_cand_001",
                "deltas": {"sharpe": 0.12, "max_drawdown": -1.5, "total_return": 2.1},
                "verdict": "pass",
            }
        ),
        encoding="utf-8",
    )


class ShadowEvalCliTests(unittest.TestCase):
    def test_writes_draft_shadow_with_real_backtest_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = Path(tmp) / "eval.json"
            out_dir = Path(tmp) / "proposals"
            _write_eval(eval_path)
            code = main(
                [
                    "--parameter", "buy_scan_shallow_top_k",
                    "--to-value", "12",
                    "--from-value", "10",
                    "--reason", "shadow eval of shallow_top_k",
                    "--proposal-id", "atp_20260604_shadow_0007",
                    "--backtest-eval", str(eval_path),
                    "--out-dir", str(out_dir),
                ]
            )
            self.assertEqual(code, 0)
            written = list(out_dir.glob("*.json"))
            self.assertEqual(len(written), 1)
            artifact = json.loads(written[0].read_text(encoding="utf-8"))
            self.assertEqual(artifact["mode"], "shadow")
            self.assertTrue(validate_proposal(artifact).accepted)
            self.assertTrue(
                any(e["source_type"] == "backtest" for e in artifact["evidence"])
            )

    def test_refuses_when_no_evidence_source_given(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "proposals"
            code = main(
                [
                    "--parameter", "buy_scan_shallow_top_k",
                    "--to-value", "12",
                    "--from-value", "10",
                    "--reason", "no evidence",
                    "--proposal-id", "atp_20260604_shadow_0008",
                    "--out-dir", str(out_dir),
                ]
            )
            self.assertEqual(code, 1)
            self.assertFalse(out_dir.exists() and any(out_dir.iterdir()))

    def test_bundles_backtest_and_live_log_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = Path(tmp) / "eval.json"
            sess_path = Path(tmp) / "session_20260603.json"
            out_dir = Path(tmp) / "proposals"
            _write_eval(eval_path)
            sess_path.write_text(
                json.dumps({"session_date": "2026-06-03", "cycles": 9}), encoding="utf-8"
            )
            code = main(
                [
                    "--parameter", "buy_scan_shallow_top_k",
                    "--to-value", "12",
                    "--from-value", "10",
                    "--reason", "shadow eval with live cross-check",
                    "--proposal-id", "atp_20260604_shadow_0009",
                    "--backtest-eval", str(eval_path),
                    "--live-log-summary", str(sess_path),
                    "--out-dir", str(out_dir),
                ]
            )
            self.assertEqual(code, 0)
            artifact = json.loads(next(out_dir.glob("*.json")).read_text(encoding="utf-8"))
            kinds = {e["source_type"] for e in artifact["evidence"]}
            self.assertEqual(kinds, {"backtest", "live_log"})


if __name__ == "__main__":
    unittest.main()
