"""Tests for the autotuner propose-cycle CLI (Step 4 periodic-tick entrypoint).

A scheduler invokes this every N minutes during market hours. It reads a curated
candidate plan, runs one read-only-by-default propose cycle, and prints the human
notification report. Only the explicit ``--allow-write`` flag persists drafts under
_workspace. It never approves and never applies anything.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.tools.autotuner_propose import main


def _setup_plan(tmp: Path) -> Path:
    eval_path = tmp / "eval.json"
    eval_path.write_text(
        json.dumps(
            {"candidate_run_id": "run_cand_001", "deltas": {"sharpe": 0.1}, "verdict": "pass"}
        ),
        encoding="utf-8",
    )
    plan = [
        {
            "proposal_id": "atp_20260604_shadow_0001",
            "parameter": "buy_scan_shallow_top_k",
            "from_value": 10,
            "to_value": 12,
            "reason": "periodic shadow eval",
            "backtest_eval": str(eval_path),
        }
    ]
    plan_path = tmp / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return plan_path


class ProposeCliTests(unittest.TestCase):
    def test_dry_run_writes_nothing_and_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dir = root / "proposals"
            plan_path = _setup_plan(root)
            code = main(
                [
                    "--plan", str(plan_path),
                    "--out-dir", str(out_dir),
                    "--project-root", str(root),
                ]
            )
            self.assertEqual(code, 0)
            self.assertFalse(out_dir.exists() and any(out_dir.iterdir()))

    def test_allow_write_persists_a_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dir = root / "proposals"
            plan_path = _setup_plan(root)
            code = main(
                [
                    "--plan", str(plan_path),
                    "--out-dir", str(out_dir),
                    "--project-root", str(root),
                    "--allow-write",
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(list(out_dir.glob("*.json"))), 1)

    def test_notify_slack_flag_invokes_notifier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = _setup_plan(root)
            with mock.patch("app.autotuner.notify.notify_propose_cycle") as notify:
                code = main(
                    [
                        "--plan", str(plan_path),
                        "--out-dir", str(root / "proposals"),
                        "--project-root", str(root),
                        "--notify-slack",
                    ]
                )
            self.assertEqual(code, 0)
            notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
