"""Tests for the Phase 2 autotuner evidence adapters.

Pure functions that shape a backtest result or a live-log reconstruction into
a schema-valid evidence entry (docs/live_autotuner_proposal_schema.md §7).
They make NO network/broker calls — the caller supplies already-collected
references; these just shape them and stamp the right source_type/limitations.
"""

from __future__ import annotations

import unittest

from app.autotuner.evidence import backtest_evidence, live_log_evidence


class BacktestEvidenceTests(unittest.TestCase):
    def test_backtest_evidence_is_marked_proxy(self) -> None:
        entry = backtest_evidence(
            source_id="bt_run_1",
            source_path="results/backtests/bt_run_1.json",
            generated_at="2026-06-04T11:00:00+09:00",
            summary="candidate set scored marginally higher",
        )
        self.assertEqual(entry["source_type"], "backtest")
        self.assertIn("proxy", entry["limitations"].lower())
        self.assertEqual(entry["source_id"], "bt_run_1")

    def test_live_log_evidence_has_live_log_source_type(self) -> None:
        entry = live_log_evidence(
            source_id="reconstruct_20260603",
            source_path="data/backtest/reconstruct_20260603.jsonl",
            generated_at="2026-06-04T09:30:00+09:00",
            summary="deeper eval would have re-ranked 3 candidates; no sell-miss",
        )
        self.assertEqual(entry["source_type"], "live_log")
        self.assertEqual(entry["source_id"], "reconstruct_20260603")
        self.assertIn("reconstruction", entry["limitations"].lower())


if __name__ == "__main__":
    unittest.main()
