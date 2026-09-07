"""Tests for the Phase 2 autotuner shadow runner.

The shadow runner composes generator + evidence + validation into a valid
``mode=shadow`` proposal. It is read-only: it evaluates a candidate against
already-collected evidence and never applies anything to the runtime. Like the
generator, it self-validates and refuses to emit an invalid artifact.
"""

from __future__ import annotations

import unittest

from app.autotuner.evidence import backtest_evidence
from app.autotuner.shadow import build_shadow_proposal
from app.autotuner.validator import validate_proposal


def _backtest_only() -> list:
    return [
        backtest_evidence(
            source_id="bt1",
            source_path="results/backtests/bt1.json",
            generated_at="2026-06-04T11:00:00+09:00",
            summary="proxy backtest",
        )
    ]


class BuildShadowProposalTests(unittest.TestCase):
    def test_build_shadow_proposal_is_a_valid_shadow(self) -> None:
        proposal = build_shadow_proposal(
            parameter="buy_scan_shallow_top_k",
            to_value=12,
            baseline_values={"buy_scan_shallow_top_k": 10},
            proposal_id="atp_20260604_shadow_0001",
            created_at="2026-06-04T11:00:00+09:00",
            reason="shadow eval",
            evidence=_backtest_only(),
        )
        self.assertEqual(proposal["mode"], "shadow")
        self.assertTrue(validate_proposal(proposal).accepted)

    def test_shadow_runner_refuses_empty_evidence(self) -> None:
        with self.assertRaises(ValueError):
            build_shadow_proposal(
                parameter="buy_scan_shallow_top_k",
                to_value=12,
                baseline_values={"buy_scan_shallow_top_k": 10},
                proposal_id="atp_20260604_shadow_0002",
                created_at="2026-06-04T11:00:00+09:00",
                reason="no evidence",
                evidence=[],
            )


if __name__ == "__main__":
    unittest.main()
