"""Tests for the Phase 1 autotuner proposal generator.

The generator is a pure function: given a baseline and a desired parameter
change, it emits a schema-valid ``mode=mock`` proposal artifact. It performs
NO runtime injection and is not read by app.main or the session loop. Its
defining property is the round-trip: every proposal it emits is accepted by
``validate_proposal`` (docs/live_autotuner_proposal_schema.md).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.autotuner.generator import generate_mock_proposal, write_proposal
from app.autotuner.validator import validate_proposal


class GeneratorRoundTripTests(unittest.TestCase):
    def test_generated_mock_proposal_is_accepted_by_validator(self) -> None:
        proposal = generate_mock_proposal(
            parameter="buy_scan_shallow_top_k",
            to_value=12,
            baseline_values={"buy_scan_shallow_top_k": 10},
            proposal_id="atp_gen_0001",
            created_at="2026-06-04T10:00:00+09:00",
            reason="probe two more shallow candidates per cycle",
        )
        result = validate_proposal(proposal)
        self.assertTrue(result.accepted, msg=f"unexpected rejections: {result.rejections}")

    def test_generator_refuses_to_emit_out_of_bounds_proposal(self) -> None:
        with self.assertRaises(ValueError):
            generate_mock_proposal(
                parameter="buy_scan_shallow_top_k",
                to_value=100,  # config bound_max is 20
                baseline_values={"buy_scan_shallow_top_k": 10},
                proposal_id="atp_gen_0002",
                created_at="2026-06-04T10:00:00+09:00",
                reason="out of bounds on purpose",
            )

    def test_change_object_is_populated_from_whitelist_and_baseline(self) -> None:
        proposal = generate_mock_proposal(
            parameter="buy_scan_shallow_top_k",
            to_value=12,
            baseline_values={"buy_scan_shallow_top_k": 10},
            proposal_id="atp_gen_0004",
            created_at="2026-06-04T10:00:00+09:00",
            reason="r",
        )
        change = proposal["changes"][0]
        self.assertEqual(change["from_value"], 10)
        self.assertEqual(change["risk_tier"], "A")
        self.assertEqual(change["bound_min"], 5)
        self.assertEqual(change["bound_max"], 20)
        self.assertEqual(change["max_step"], 2)
        self.assertEqual(change["eligible_phase"], 3)
        self.assertEqual(proposal["mode"], "mock")
        self.assertEqual(proposal["status"], "draft")

    def test_generator_refuses_forbidden_tier_d_parameter(self) -> None:
        with self.assertRaises(ValueError):
            generate_mock_proposal(
                parameter="kis_env",  # Tier D, forbidden
                to_value="live",
                baseline_values={"kis_env": "mock"},
                proposal_id="atp_gen_0003",
                created_at="2026-06-04T10:00:00+09:00",
                reason="should never be emitted",
            )


class WriteProposalTests(unittest.TestCase):
    def test_write_proposal_rejects_path_traversal_id(self) -> None:
        proposal = generate_mock_proposal(
            parameter="buy_scan_shallow_top_k",
            to_value=12,
            baseline_values={"buy_scan_shallow_top_k": 10},
            proposal_id="atp_20260604_mock_0001",
            created_at="2026-06-04T10:00:00+09:00",
            reason="r",
        )
        proposal["proposal_id"] = "../escape"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "proposals"
            with self.assertRaises(ValueError):
                write_proposal(proposal, target)

    def test_write_proposal_persists_validatable_json(self) -> None:
        proposal = generate_mock_proposal(
            parameter="buy_scan_shallow_top_k",
            to_value=12,
            baseline_values={"buy_scan_shallow_top_k": 10},
            proposal_id="atp_20260604_mock_0001",
            created_at="2026-06-04T10:00:00+09:00",
            reason="r",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_proposal(proposal, tmp)
            self.assertTrue(Path(path).exists())
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertTrue(validate_proposal(loaded).accepted)


if __name__ == "__main__":
    unittest.main()
