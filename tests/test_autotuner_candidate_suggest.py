"""Tests for Phase 5 — MCP-assisted candidate suggestion (proposals only).

The external open-trading-api backtester/MCP *screens* a parameter space and
produces proxy-backtest results. ``suggest_candidates`` turns those into
whitelist-validated curated-plan entries — the same plan shape the human-gated
propose-cycle consumes. It SUGGESTS only: no live write, no auto-apply; an invalid
or out-of-bounds screened candidate is dropped (trust boundary §3, non-goals §8).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.autotuner.candidate_suggest import suggest_candidates
from app.autotuner.propose_cycle import run_propose_cycle

_NOW = datetime(2026, 6, 5, 10, 0, 0, tzinfo=timezone.utc)


class SuggestCandidatesTests(unittest.TestCase):
    def test_valid_screened_candidate_becomes_a_plan_entry(self) -> None:
        screening = [
            {
                "parameter": "buy_scan_shallow_top_k",
                "to_value": 12,
                "verdict": "pass",
                "backtest_eval": "/w/eval.json",
            }
        ]
        plan = suggest_candidates(
            screening,
            baseline_values={"buy_scan_shallow_top_k": 10},
            date="20260605",
        )
        self.assertEqual(len(plan), 1)
        entry = plan[0]
        self.assertEqual(entry["parameter"], "buy_scan_shallow_top_k")
        self.assertEqual(entry["from_value"], 10)
        self.assertEqual(entry["to_value"], 12)
        self.assertEqual(entry["proposal_id"], "atp_20260605_shadow_0001")
        self.assertEqual(entry["backtest_eval"], "/w/eval.json")

    def test_invalid_screened_candidates_are_dropped(self) -> None:
        screening = [
            {"parameter": "buy_scan_shallow_top_k", "to_value": 12, "verdict": "pass"},
            {"parameter": "buy_scan_shallow_top_k", "to_value": 999, "verdict": "pass"},  # > bound_max 20
            {"parameter": "kis_env", "to_value": "live", "verdict": "pass"},  # forbidden / unknown
            {"parameter": "sell_watch_max_holdings_per_tick", "to_value": 5, "verdict": "pass"},  # blocked
        ]
        plan = suggest_candidates(
            screening,
            baseline_values={"buy_scan_shallow_top_k": 10},
            date="20260605",
        )
        self.assertEqual(
            [(e["parameter"], e["to_value"]) for e in plan],
            [("buy_scan_shallow_top_k", 12)],
        )

    def test_failing_verdict_is_dropped(self) -> None:
        screening = [
            {"parameter": "buy_scan_shallow_top_k", "to_value": 12, "verdict": "fail"}
        ]
        plan = suggest_candidates(
            screening, baseline_values={"buy_scan_shallow_top_k": 10}, date="20260605"
        )
        self.assertEqual(plan, [])

    def test_suggested_candidate_feeds_the_propose_cycle(self) -> None:
        # the loop closes: screening -> suggest -> propose-cycle builds a valid draft.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            eval_path = root / "eval.json"
            eval_path.write_text(
                json.dumps(
                    {"candidate_run_id": "r", "deltas": {"sharpe": 0.1}, "verdict": "pass"}
                ),
                encoding="utf-8",
            )
            screening = [
                {
                    "parameter": "buy_scan_shallow_top_k",
                    "to_value": 12,
                    "verdict": "pass",
                    "backtest_eval": str(eval_path),
                }
            ]
            plan = suggest_candidates(
                screening,
                baseline_values={"buy_scan_shallow_top_k": 10},
                date="20260605",
            )
            result = run_propose_cycle(
                plan,
                project_root=root,
                now=_NOW,
                read_only=True,
                out_dir=root / "proposals",
            )
            self.assertEqual(result["outcomes"][0]["status"], "built")


if __name__ == "__main__":
    unittest.main()
