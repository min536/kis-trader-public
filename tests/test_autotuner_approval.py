"""Tests for the autotuner approval transition (Step 2 of the post-Phase-4 roadmap).

The approval step is the missing human gate for the autotuner's own artifact:
it takes a reviewed draft/shadow proposal and performs the ``draft -> approved``
transition (stamp ``approval``/``ttl``, set the approved mode), re-validating with
``validate_proposal`` so an invalid or out-of-scope change can never be approved.
It is pure (returns a dict, never writes) and refuses (raises) on any doubt.
See docs/live_autotuner_pipeline_reconciliation.md.
"""

from __future__ import annotations

import unittest

from app.autotuner.approval import approve_proposal
from app.autotuner.validator import validate_proposal


def _tier_a_draft(
    *,
    to_value: int = 45,
    evidence: list | None = None,
) -> dict:
    """A reviewed-but-not-approved Tier A draft (has baseline + live_log evidence)."""
    if evidence is None:
        evidence = [
            {"source_type": "live_log", "source_id": "reconstruct_20260603"}
        ]
    return {
        "schema_version": "0.1.0",
        "proposal_id": "atp_20260604_shadow_0001",
        "created_at": "2026-06-04T09:00:00+09:00",
        "generated_by": {"kind": "human", "name": "test"},
        "mode": "shadow",
        "status": "draft",
        "reason": "slower sell cadence",
        "evidence": evidence,
        "baseline": {
            "baseline_id": "settings_default_20260604",
            "values": {"sell_check_interval_seconds": 35},
        },
        "changes": [
            {
                "parameter": "sell_check_interval_seconds",
                "from_value": 35,
                "to_value": to_value,
                "type": "int",
                "risk_tier": "A",
                "eligible_phase": 3,
                "bound_min": 30,
                "bound_max": 120,
                "max_step": 10,
                "validation_status": "passed",
                "rationale": "reduce request pressure",
                "notes": "",
            }
        ],
        "constraints_checked": {},
        "risk_review": None,
        "approval": None,
        "ttl": None,
        "rollback": {"rollback_baseline_id": "settings_default_20260604"},
        "audit": {"events": []},
    }


def _tier_b_draft(
    *,
    live_log_count: int = 2,
    risk_review: dict | None = None,
) -> dict:
    draft = _tier_a_draft(
        evidence=[
            {"source_type": "live_log", "source_id": f"reconstruct_2026060{i}"}
            for i in range(live_log_count)
        ]
    )
    draft["reason"] = "longer rebuy cooldown"
    draft["baseline"]["values"] = {"rebuy_cooldown_minutes": 60}
    draft["changes"] = [
        {
            "parameter": "rebuy_cooldown_minutes",
            "from_value": 60,
            "to_value": 75,
            "type": "int",
            "risk_tier": "B",
            "eligible_phase": 4,
            "bound_min": 15,
            "bound_max": 240,
            "max_step": 15,
            "validation_status": "passed",
            "rationale": "reduce repeated buy pressure",
            "notes": "",
        }
    ]
    draft["risk_review"] = (
        {"reviewer": "human:risk", "status": "approved"}
        if risk_review is None
        else risk_review
    )
    return draft


class ApproveTierATests(unittest.TestCase):
    def test_approve_tier_a_draft_returns_validated_approved(self) -> None:
        approved = approve_proposal(
            _tier_a_draft(),
            approved_by="human:test",
            approved_at="2026-06-04T09:30:00+09:00",
            expires_at="2099-01-01T00:00:00+09:00",
        )
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["mode"], "approved_low_risk")
        self.assertEqual(approved["approval"]["approved_by"], "human:test")
        self.assertEqual(approved["ttl"]["expires_at"], "2099-01-01T00:00:00+09:00")
        self.assertTrue(validate_proposal(approved).accepted)

    def test_refuse_to_approve_an_out_of_bounds_change(self) -> None:
        # bound_max is 120; a forged 999 must never be approvable (re-validation).
        with self.assertRaises(ValueError):
            approve_proposal(
                _tier_a_draft(to_value=999),
                approved_by="human:test",
                approved_at="2026-06-04T09:30:00+09:00",
                expires_at="2099-01-01T00:00:00+09:00",
            )

    def test_refuse_a_tier_b_change_on_the_low_risk_path(self) -> None:
        # Step 2 approves Tier A (approved_low_risk) only; a Tier B param must be
        # refused here (high-risk approval is a separate, opt-in gate).
        draft = _tier_a_draft()
        draft["baseline"]["values"] = {"rebuy_cooldown_minutes": 60}
        draft["changes"] = [
            {
                "parameter": "rebuy_cooldown_minutes",
                "from_value": 60,
                "to_value": 75,
                "type": "int",
                "risk_tier": "B",
                "eligible_phase": 4,
                "bound_min": 15,
                "bound_max": 240,
                "max_step": 15,
                "validation_status": "passed",
                "rationale": "longer cooldown",
                "notes": "",
            }
        ]
        with self.assertRaises(ValueError):
            approve_proposal(
                draft,
                approved_by="human:test",
                approved_at="2026-06-04T09:30:00+09:00",
                expires_at="2099-01-01T00:00:00+09:00",
            )

    def test_approve_tier_b_requires_high_risk_opt_in(self) -> None:
        approved = approve_proposal(
            _tier_b_draft(),
            approved_by="human:test",
            approved_at="2026-06-04T09:30:00+09:00",
            expires_at="2099-01-01T00:00:00+09:00",
            high_risk=True,
        )
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["mode"], "approved_high_risk")
        self.assertTrue(validate_proposal(approved, allow_high_risk=True).accepted)

    def test_refuse_high_risk_without_two_live_log_entries(self) -> None:
        with self.assertRaises(ValueError):
            approve_proposal(
                _tier_b_draft(live_log_count=1),
                approved_by="human:test",
                approved_at="2026-06-04T09:30:00+09:00",
                expires_at="2099-01-01T00:00:00+09:00",
                high_risk=True,
            )

    def test_refuse_high_risk_without_risk_review(self) -> None:
        with self.assertRaises(ValueError):
            approve_proposal(
                _tier_b_draft(risk_review={}),
                approved_by="human:test",
                approved_at="2026-06-04T09:30:00+09:00",
                expires_at="2099-01-01T00:00:00+09:00",
                high_risk=True,
            )


if __name__ == "__main__":
    unittest.main()
