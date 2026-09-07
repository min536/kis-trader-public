"""The autotuner CLIs must be runnable as `python -m app.tools.<cli>`.

Each module needs an ``if __name__ == "__main__": raise SystemExit(main())`` guard;
without it, `python -m ...` imports the module and exits 0 having done nothing —
exactly what the operator runbook documents, so it must actually work.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _run(module: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=_REPO,
        capture_output=True,
        text=True,
    )


def _draft(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1.0",
                "proposal_id": "atp_20260605_shadow_0001",
                "created_at": "2026-06-05T09:00:00+09:00",
                "generated_by": {"kind": "human", "name": "t"},
                "mode": "shadow",
                "status": "pending_review",
                "reason": "cli",
                "evidence": [{"source_type": "live_log", "source_id": "r0"}],
                "baseline": {"baseline_id": "b", "values": {"buy_scan_shallow_top_k": 10}},
                "changes": [
                    {
                        "parameter": "buy_scan_shallow_top_k",
                        "from_value": 10,
                        "to_value": 12,
                        "type": "int",
                        "risk_tier": "A",
                        "eligible_phase": 3,
                        "bound_min": 5,
                        "bound_max": 20,
                        "max_step": 2,
                        "validation_status": "passed",
                        "rationale": "x",
                        "notes": "",
                    }
                ],
                "constraints_checked": {},
                "risk_review": None,
                "approval": None,
                "ttl": None,
                "rollback": {"rollback_baseline_id": "b"},
                "audit": {"events": []},
            }
        ),
        encoding="utf-8",
    )


class CliEntrypointTests(unittest.TestCase):
    def test_status_entrypoint_runs(self) -> None:
        # passes real CLI args -> the entrypoint must forward sys.argv (not main()).
        r = _run("app.tools.autotuner_status", ["--env", "mock", "--enabled"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("flag_enabled : True", r.stdout)

    def test_propose_entrypoint_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "plan.json"
            plan.write_text("[]", encoding="utf-8")
            r = _run("app.tools.autotuner_propose", ["--plan", str(plan)])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Autotuner Propose Cycle", r.stdout)

    def test_approve_entrypoint_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.json"
            _draft(draft)
            r = _run(
                "app.tools.autotuner_approve",
                ["--proposal-file", str(draft), "--approved-by", "human:t"],
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("atp_20260605_shadow_0001", r.stdout)

    def test_shadow_eval_entrypoint_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = Path(tmp) / "eval.json"
            eval_path.write_text(
                json.dumps(
                    {"candidate_run_id": "r", "deltas": {"sharpe": 0.1}, "verdict": "pass"}
                ),
                encoding="utf-8",
            )
            r = _run(
                "app.tools.autotuner_shadow_eval",
                [
                    "--parameter", "buy_scan_shallow_top_k",
                    "--to-value", "12",
                    "--from-value", "10",
                    "--reason", "cli",
                    "--proposal-id", "atp_20260605_shadow_0002",
                    "--backtest-eval", str(eval_path),
                    "--out-dir", str(Path(tmp) / "out"),
                ],
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("draft shadow proposal written", r.stdout)


if __name__ == "__main__":
    unittest.main()
