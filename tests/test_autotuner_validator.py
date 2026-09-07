"""Tests for the Phase 0 autotuner proposal validator.

The validator is a pure function: it takes a proposal dict and returns an
accept/reject verdict with reasons. NOTHING in app.main or the session loop
reads it — it has no runtime write path by construction. These tests encode the
schema contract from docs/live_autotuner_proposal_schema.md §6/§10.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from app.autotuner.validator import load_whitelist, validate_proposal

_EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "docs" / "examples"


def _load_example(name: str) -> dict:
    with (_EXAMPLES_DIR / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def _valid_mock_proposal() -> dict:
    """A minimal, valid mode=mock proposal touching only one Tier A parameter."""
    return {
        "schema_version": "0.1.0",
        "proposal_id": "atp_test_mock_0001",
        "created_at": "2026-06-03T10:15:00+09:00",
        "generated_by": {"kind": "script", "name": "test", "version": "0"},
        "mode": "mock",
        "status": "draft",
        "reason": "test artifact",
        "evidence": [],
        "baseline": {
            "baseline_id": "settings_default_20260603",
            "values": {"buy_scan_shallow_top_k": 10},
        },
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
                "validation_status": "not_validated",
                "rationale": "probe two more shallow candidates",
                "notes": "mock only",
            }
        ],
        "constraints_checked": {},
        "risk_review": None,
        "approval": None,
        "ttl": None,
        "rollback": {"rollback_baseline_id": "settings_default_20260603"},
        "audit": {"events": []},
    }


class WhitelistConfigTests(unittest.TestCase):
    def test_load_whitelist_exposes_tier_a_parameter(self) -> None:
        params = load_whitelist()["parameters"]
        self.assertIn("buy_scan_shallow_top_k", params)
        self.assertEqual(params["buy_scan_shallow_top_k"]["tier"], "A")

    def test_load_whitelist_reads_from_yaml_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wl.yaml"
            path.write_text(
                "parameters:\n  sentinel_param:\n    tier: A\n", encoding="utf-8"
            )
            loaded = load_whitelist(path=path)
            self.assertIn("sentinel_param", loaded["parameters"])

    def test_load_whitelist_includes_blocked_tier_c_and_forbidden_list(self) -> None:
        whitelist = load_whitelist()
        params = whitelist["parameters"]
        # A high-risk Tier C param must be present and blocked by default.
        self.assertEqual(params["buy_max_qty_per_trade"]["tier"], "C")
        self.assertTrue(params["buy_max_qty_per_trade"]["blocked"])
        # The Tier D forbidden anchors must be loaded from config.
        self.assertIn("kis_env", whitelist["forbidden_tier_d"])


class ValidatorAcceptTests(unittest.TestCase):
    def test_accepts_valid_mock_tier_a_proposal(self) -> None:
        result = validate_proposal(_valid_mock_proposal())
        self.assertTrue(result.accepted, msg=f"unexpected rejections: {result.rejections}")
        self.assertEqual(result.rejections, [])

    def test_accepts_live_eligible_with_live_log_evidence(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["mode"] = "approved_low_risk"
        proposal["status"] = "approved"
        proposal["approval"] = {
            "approved_by": "human:test",
            "approved_at": "2026-06-04T10:00:00+09:00",
        }
        proposal["ttl"] = {"expires_at": "2099-01-01T00:00:00+09:00"}
        proposal["evidence"] = [
            {"source_type": "backtest", "source_id": "bt_1", "confidence": "low"},
            {"source_type": "live_log", "source_id": "reconstruct_20260603", "confidence": "low"},
        ]
        result = validate_proposal(proposal)
        self.assertTrue(result.accepted, msg=f"unexpected rejections: {result.rejections}")

    def test_accepts_approved_high_risk_tier_b_only_when_opted_in(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["mode"] = "approved_high_risk"
        proposal["status"] = "approved"
        proposal["approval"] = {
            "approved_by": "human:test",
            "approved_at": "2026-06-04T10:00:00+09:00",
        }
        proposal["ttl"] = {"expires_at": "2099-01-01T00:00:00+09:00"}
        proposal["evidence"] = [
            {"source_type": "live_log", "source_id": "r1"},
            {"source_type": "live_log", "source_id": "r2"},
        ]
        proposal["risk_review"] = {"reviewer": "human:test", "status": "approved"}
        proposal["baseline"]["values"] = {"rebuy_cooldown_minutes": 30}
        proposal["changes"][0] = {
            "parameter": "rebuy_cooldown_minutes",
            "from_value": 30,
            "to_value": 45,
            "type": "int",
            "risk_tier": "B",
            "eligible_phase": 4,
            "bound_min": 15,
            "bound_max": 240,
            "max_step": 15,
            "validation_status": "not_validated",
            "rationale": "lengthen cooldown",
            "notes": "",
        }
        # default (no opt-in) still rejects approved_high_risk
        self.assertFalse(validate_proposal(proposal).accepted)
        # opt-in accepts a Tier B high-risk bundle
        result = validate_proposal(proposal, allow_high_risk=True)
        self.assertTrue(result.accepted, msg=f"unexpected rejections: {result.rejections}")


class ValidatorRejectTests(unittest.TestCase):
    def test_rejects_unknown_parameter(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["changes"][0]["parameter"] = "definitely_not_a_real_parameter"
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("whitelist" in r.lower() or "unknown" in r.lower() for r in result.rejections),
            msg=f"expected a whitelist/unknown rejection, got: {result.rejections}",
        )

    def test_high_risk_opt_in_still_rejects_blocked_tier_c(self) -> None:
        # Even with allow_high_risk, a blocked Tier C param must be rejected (M11).
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["mode"] = "approved_high_risk"
        proposal["status"] = "approved"
        proposal["approval"] = {
            "approved_by": "human:test",
            "approved_at": "2026-06-04T10:00:00+09:00",
        }
        proposal["ttl"] = {"expires_at": "2099-01-01T00:00:00+09:00"}
        proposal["evidence"] = [{"source_type": "live_log", "source_id": "r1"}]
        proposal["changes"][0] = {
            "parameter": "buy_max_qty_per_trade",  # Tier C, blocked:true
            "from_value": 10,
            "to_value": 12,
            "type": "int",
            "risk_tier": "C",
            "eligible_phase": None,
            "bound_min": None,
            "bound_max": None,
            "max_step": None,
            "validation_status": "not_validated",
            "rationale": "x",
            "notes": "",
        }
        result = validate_proposal(proposal, allow_high_risk=True)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("blocked" in r.lower() for r in result.rejections),
            msg=f"expected a blocked rejection, got: {result.rejections}",
        )

    def test_rejects_tier_d_parameter(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["changes"][0]["parameter"] = "kis_env"
        proposal["changes"][0]["risk_tier"] = "D"
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("forbidden" in r.lower() or "tier d" in r.lower() for r in result.rejections),
            msg=f"expected a Tier D/forbidden rejection, got: {result.rejections}",
        )

    def test_rejects_out_of_bounds_change(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        # config bound_max for buy_scan_shallow_top_k is 20
        proposal["changes"][0]["to_value"] = 100
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("bound" in r.lower() for r in result.rejections),
            msg=f"expected a bounds rejection, got: {result.rejections}",
        )

    def test_rejects_string_to_value_for_int_parameter(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["changes"][0]["to_value"] = "12"
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("to_value" in r.lower() and "int" in r.lower() for r in result.rejections),
            msg=f"expected an int to_value rejection, got: {result.rejections}",
        )

    def test_rejects_shadow_mode_without_evidence(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["mode"] = "shadow"
        proposal["evidence"] = []
        proposal["ttl"] = {"expires_at": "2099-01-01T00:00:00+09:00"}
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("evidence" in r.lower() for r in result.rejections),
            msg=f"expected an evidence rejection, got: {result.rejections}",
        )

    def test_rejects_change_exceeding_max_step(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        # bound 5..20, max_step 2: 10 -> 16 is within bounds but step 6 > 2.
        proposal["changes"][0]["from_value"] = 10
        proposal["changes"][0]["to_value"] = 16
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("step" in r.lower() for r in result.rejections),
            msg=f"expected a max_step rejection, got: {result.rejections}",
        )

    def test_rejects_numeric_change_without_numeric_from_value(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["changes"][0]["from_value"] = None
        proposal["changes"][0]["to_value"] = 20
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("from_value" in r.lower() for r in result.rejections),
            msg=f"expected a from_value rejection, got: {result.rejections}",
        )

    def test_rejects_from_value_mismatch_with_baseline_value(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["baseline"]["values"]["buy_scan_shallow_top_k"] = 10
        proposal["changes"][0]["from_value"] = 9
        proposal["changes"][0]["to_value"] = 11
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("baseline" in r.lower() and "from_value" in r.lower() for r in result.rejections),
            msg=f"expected a baseline/from_value rejection, got: {result.rejections}",
        )

    def test_rejects_missing_baseline(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        del proposal["baseline"]
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("baseline" in r.lower() for r in result.rejections),
            msg=f"expected a baseline rejection, got: {result.rejections}",
        )

    def test_rejects_approved_without_approval_metadata(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["status"] = "approved"
        proposal["approval"] = None
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("approval" in r.lower() or "approved_by" in r.lower() for r in result.rejections),
            msg=f"expected an approval-metadata rejection, got: {result.rejections}",
        )

    def test_rejects_self_approval_by_automated_generator(self) -> None:
        # Schema §8: no proposal may self-approve — generated_by and approved_by
        # must not be the same automated actor. A human approver is still fine.
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["generated_by"] = {"kind": "script", "name": "autotuner_bot", "version": "0"}
        proposal["mode"] = "approved_low_risk"
        proposal["status"] = "approved"
        proposal["approval"] = {
            "approved_by": "script:autotuner_bot",
            "approved_at": "2026-06-04T10:00:00+09:00",
        }
        proposal["ttl"] = {"expires_at": "2099-01-01T00:00:00+09:00"}
        proposal["evidence"] = [
            {"source_type": "live_log", "source_id": "r1", "confidence": "low"},
        ]
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("self-approve" in r.lower() for r in result.rejections),
            msg=f"expected a self-approval rejection, got: {result.rejections}",
        )

    def test_rejects_cross_parameter_violation(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["baseline"]["values"] = {
            "scan_symbols_max_per_cycle": 30,
            "buy_scan_shallow_top_k": 6,
            "buy_scan_deep_eval_limit": 6,
        }
        # deep_eval 6 -> 7 makes deep_eval (7) > shallow_top_k (6): violation.
        proposal["changes"][0] = {
            "parameter": "buy_scan_deep_eval_limit",
            "from_value": 6,
            "to_value": 7,
            "type": "int",
            "risk_tier": "A",
            "eligible_phase": 3,
            "bound_min": 2,
            "bound_max": 8,
            "max_step": 1,
            "validation_status": "not_validated",
            "rationale": "test",
            "notes": "",
        }
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("cross" in r.lower() or "deep_eval" in r.lower() for r in result.rejections),
            msg=f"expected a cross-parameter rejection, got: {result.rejections}",
        )

    def test_rejects_expired_proposal(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["ttl"] = {"ttl_seconds": 1, "expires_at": "2020-01-01T00:00:00+09:00"}
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("expired" in r.lower() for r in result.rejections),
            msg=f"expected an expired rejection, got: {result.rejections}",
        )

    def test_rejects_approved_high_risk_mode_unconditionally(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["mode"] = "approved_high_risk"
        proposal["status"] = "approved"
        proposal["approval"] = {
            "approved_by": "human:test",
            "approved_at": "2026-06-03T10:00:00+09:00",
        }
        proposal["ttl"] = {"expires_at": "2099-01-01T00:00:00+09:00"}
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("high_risk" in r.lower() or "not enabled" in r.lower() for r in result.rejections),
            msg=f"expected an approved_high_risk-disabled rejection, got: {result.rejections}",
        )

    def test_rejects_blocked_parameter_even_in_mock_mode(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        # buy_max_qty_per_trade is Tier C, blocked:true in config.
        proposal["changes"][0] = {
            "parameter": "buy_max_qty_per_trade",
            "from_value": 10,
            "to_value": 12,
            "type": "int",
            "risk_tier": "C",
            "eligible_phase": None,
            "bound_min": None,
            "bound_max": None,
            "max_step": None,
            "validation_status": "not_validated",
            "rationale": "test",
            "notes": "",
        }
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("blocked" in r.lower() for r in result.rejections),
            msg=f"expected a blocked-parameter rejection, got: {result.rejections}",
        )

    def test_rejects_live_eligible_mode_without_ttl(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["mode"] = "approved_low_risk"
        proposal["status"] = "approved"
        proposal["approval"] = {
            "approved_by": "human:test",
            "approved_at": "2026-06-03T10:00:00+09:00",
        }
        proposal["ttl"] = None
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("ttl" in r.lower() for r in result.rejections),
            msg=f"expected a TTL-required rejection, got: {result.rejections}",
        )

    def test_rejects_live_eligible_without_live_log_evidence(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["mode"] = "approved_low_risk"
        proposal["status"] = "approved"
        proposal["approval"] = {
            "approved_by": "human:test",
            "approved_at": "2026-06-04T10:00:00+09:00",
        }
        proposal["ttl"] = {"expires_at": "2099-01-01T00:00:00+09:00"}
        # Evidence present, but only a proxy backtest — no live_log cross-validation.
        proposal["evidence"] = [
            {"source_type": "backtest", "source_id": "bt_1", "confidence": "low"}
        ]
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("live_log" in r.lower() for r in result.rejections),
            msg=f"expected a live_log cross-validation rejection, got: {result.rejections}",
        )

    def test_rejects_approved_low_risk_with_non_tier_a_parameter(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["mode"] = "approved_low_risk"
        # rebuy_cooldown_minutes is Tier B in config — not allowed under low_risk.
        proposal["changes"][0] = {
            "parameter": "rebuy_cooldown_minutes",
            "from_value": 30,
            "to_value": 45,
            "type": "int",
            "risk_tier": "B",
            "eligible_phase": 4,
            "bound_min": 15,
            "bound_max": 240,
            "max_step": 15,
            "validation_status": "not_validated",
            "rationale": "test",
            "notes": "",
        }
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("tier a" in r.lower() or "low_risk" in r.lower() for r in result.rejections),
            msg=f"expected an approved_low_risk tier rejection, got: {result.rejections}",
        )


class ValidatorRobustnessTests(unittest.TestCase):
    """A safety gate must degrade (reject) on malformed input, never raise."""

    def test_non_dict_proposals_are_rejected_not_raised(self) -> None:
        for proposal in (None, [], "not a dict"):
            with self.subTest(proposal=proposal):
                result = validate_proposal(proposal)  # must not raise
                self.assertFalse(result.accepted)

    def test_non_dict_ttl_is_rejected_not_raised(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["mode"] = "approved_low_risk"
        proposal["status"] = "approved"
        proposal["approval"] = {
            "approved_by": "human:test",
            "approved_at": "2026-06-04T10:00:00+09:00",
        }
        proposal["evidence"] = [{"source_type": "live_log", "source_id": "r1"}]
        proposal["ttl"] = ["not", "a", "dict"]
        result = validate_proposal(proposal)  # must not raise
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("ttl" in r.lower() for r in result.rejections),
            msg=f"expected a ttl rejection, got: {result.rejections}",
        )

    def test_changes_none_is_rejected_not_raised(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["changes"] = None
        result = validate_proposal(proposal)  # must not raise
        self.assertFalse(result.accepted)

    def test_non_dict_change_is_rejected_not_raised(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["changes"] = ["not a dict"]
        result = validate_proposal(proposal)  # must not raise
        self.assertFalse(result.accepted)

    def test_unhashable_parameter_name_is_rejected_not_raised(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["changes"][0]["parameter"] = ["unhashable"]
        result = validate_proposal(proposal)  # must not raise (set membership)
        self.assertFalse(result.accepted)

    def test_empty_changes_list_is_rejected(self) -> None:
        proposal = copy.deepcopy(_valid_mock_proposal())
        proposal["changes"] = []
        result = validate_proposal(proposal)
        self.assertFalse(result.accepted)


class ExampleArtifactTests(unittest.TestCase):
    """The shipped docs/examples/*.json must behave as their filenames claim."""

    def test_mock_example_is_accepted(self) -> None:
        result = validate_proposal(_load_example("live_autotuner_proposal_mock.json"))
        self.assertTrue(result.accepted, msg=f"unexpected rejections: {result.rejections}")

    def test_shadow_example_is_rejected_on_max_step(self) -> None:
        result = validate_proposal(_load_example("live_autotuner_proposal_shadow.json"))
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("step" in r.lower() for r in result.rejections),
            msg=f"expected the deliberate max_step rejection, got: {result.rejections}",
        )

    def test_tier_d_example_is_rejected(self) -> None:
        result = validate_proposal(
            _load_example("live_autotuner_proposal_rejected_tier_d.json")
        )
        self.assertFalse(result.accepted)
        self.assertTrue(
            any("forbidden" in r.lower() or "tier d" in r.lower() for r in result.rejections),
            msg=f"expected a Tier D rejection, got: {result.rejections}",
        )


if __name__ == "__main__":
    unittest.main()
