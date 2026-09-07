"""Tests for the Phase 5 candidate-suggest CLI.

Turns an external screening result (from the open-trading-api backtester/MCP, an
operator-supplied JSON) into a curated `plan.json` that feeds the human-gated
`autotuner_propose` tick. It only emits whitelist-valid suggestions; it never calls
the broker and never applies anything.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.tools.autotuner_suggest import main


class SuggestCliTests(unittest.TestCase):
    def test_writes_a_plan_from_screening(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            screening = Path(tmp) / "screening.json"
            screening.write_text(
                json.dumps(
                    [
                        {
                            "parameter": "buy_scan_shallow_top_k",
                            "to_value": 12,
                            "verdict": "pass",
                            "backtest_eval": "/w/e.json",
                        },
                        {"parameter": "buy_scan_shallow_top_k", "to_value": 999, "verdict": "pass"},
                    ]
                ),
                encoding="utf-8",
            )
            baseline = Path(tmp) / "baseline.json"
            baseline.write_text(
                json.dumps({"buy_scan_shallow_top_k": 10}), encoding="utf-8"
            )
            out = Path(tmp) / "plan.json"
            code = main(
                [
                    "--screening", str(screening),
                    "--baseline", str(baseline),
                    "--date", "20260605",
                    "--out", str(out),
                ]
            )
            self.assertEqual(code, 0)
            plan = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(plan), 1)  # the out-of-bounds one is dropped
            self.assertEqual(plan[0]["proposal_id"], "atp_20260605_shadow_0001")
            self.assertEqual(plan[0]["to_value"], 12)


if __name__ == "__main__":
    unittest.main()
