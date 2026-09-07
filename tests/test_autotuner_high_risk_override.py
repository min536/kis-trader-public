"""Tests for the Phase 4 Tier-B high-risk runtime-override reader.

Stronger-gated sibling of the Phase 3 reader: a SEPARATE default-OFF
`enabled_high_risk` flag, mock-only, requires approved_high_risk +
>=2 live_log evidence + a risk_review block, exactly one bundle, and Tier B
only (Tier C/D never apply). Never raises. See docs/live_autotuner_phase4_risk.md
(M11-M15).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.autotuner.persist import workspace_paths
from app.autotuner.runtime_override import (
    merge_safer_of_settings,
    resolve_high_risk_overrides,
)

_NOW = datetime(2026, 6, 4, 10, 0, 0, tzinfo=timezone.utc)


def _high_risk_bundle(
    proposals_dir: Path,
    *,
    to_value: int = 45,
    live_log_count: int = 2,
    risk_review: bool = True,
    parameter: str = "rebuy_cooldown_minutes",
    risk_tier: str = "B",
    from_value: int = 30,
    bound_min: int = 15,
    bound_max: int = 240,
    max_step: int = 15,
    proposal_id: str = "atp_20260604_approved_high_risk_0001",
    mode: str = "approved_high_risk",
    status: str = "approved",
) -> None:
    proposals_dir.mkdir(parents=True, exist_ok=True)
    evidence = [
        {"source_type": "live_log", "source_id": f"r{i}"} for i in range(live_log_count)
    ]
    bundle = {
        "schema_version": "0.1.0",
        "proposal_id": proposal_id,
        "created_at": "2026-06-04T09:00:00+09:00",
        "generated_by": {"kind": "human", "name": "test"},
        "mode": mode,
        "status": status,
        "reason": "lengthen rebuy cooldown",
        "evidence": evidence,
        "baseline": {
            "baseline_id": "settings_default_20260604",
            "values": {parameter: from_value},
        },
        "changes": [
            {
                "parameter": parameter,
                "from_value": from_value,
                "to_value": to_value,
                "type": "int",
                "risk_tier": risk_tier,
                "eligible_phase": 4,
                "bound_min": bound_min,
                "bound_max": bound_max,
                "max_step": max_step,
                "validation_status": "passed",
                "rationale": "reduce buy frequency",
                "notes": "",
            }
        ],
        "constraints_checked": {},
        "risk_review": {"reviewer": "human:test", "status": "approved"}
        if risk_review
        else None,
        "approval": {
            "approved_by": "human:test",
            "approved_at": "2026-06-04T09:30:00+09:00",
        },
        "ttl": {"expires_at": "2099-01-01T00:00:00+09:00"},
        "rollback": {"rollback_baseline_id": "settings_default_20260604"},
        "audit": {"events": []},
    }
    (proposals_dir / f"{proposal_id}.json").write_text(json.dumps(bundle), encoding="utf-8")


class HighRiskGateTests(unittest.TestCase):
    def test_disabled_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _high_risk_bundle(workspace_paths(tmp)["proposals"])
            out = resolve_high_risk_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled_high_risk=False
            )
            self.assertEqual(out, {})

    def test_enabled_mock_single_valid_tier_b_bundle_emits_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _high_risk_bundle(workspace_paths(tmp)["proposals"], to_value=45)
            out = resolve_high_risk_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled_high_risk=True
            )
            self.assertEqual(out, {"rebuy_cooldown_minutes": 45})

    def test_override_value_is_read_from_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # 40 is valid: within 15..240, step 10 <= max_step 15.
            _high_risk_bundle(workspace_paths(tmp)["proposals"], to_value=40)
            out = resolve_high_risk_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled_high_risk=True
            )
            self.assertEqual(out, {"rebuy_cooldown_minutes": 40})

    def test_non_high_risk_bundle_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _high_risk_bundle(
                workspace_paths(tmp)["proposals"], mode="mock", status="draft"
            )
            out = resolve_high_risk_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled_high_risk=True
            )
            self.assertEqual(out, {})

    def test_out_of_bounds_bundle_is_revalidated_and_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _high_risk_bundle(
                workspace_paths(tmp)["proposals"], to_value=300  # > bound_max 240
            )
            out = resolve_high_risk_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled_high_risk=True
            )
            self.assertEqual(out, {})

    def test_requires_at_least_two_live_log_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # 1 live_log passes the validator (>=1) but the high-risk path needs >=2 (M14)
            _high_risk_bundle(workspace_paths(tmp)["proposals"], live_log_count=1)
            out = resolve_high_risk_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled_high_risk=True
            )
            self.assertEqual(out, {})

    def test_requires_risk_review_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _high_risk_bundle(workspace_paths(tmp)["proposals"], risk_review=False)
            out = resolve_high_risk_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled_high_risk=True
            )
            self.assertEqual(out, {})

    def test_live_env_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _high_risk_bundle(workspace_paths(tmp)["proposals"])
            out = resolve_high_risk_overrides(
                project_root=tmp, now=_NOW, env="live", enabled_high_risk=True
            )
            self.assertEqual(out, {})

    def test_malformed_bundle_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proposals = workspace_paths(tmp)["proposals"]
            proposals.mkdir(parents=True, exist_ok=True)
            (proposals / "broken.json").write_text("{ not json", encoding="utf-8")
            out = resolve_high_risk_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled_high_risk=True
            )  # must not raise
            self.assertEqual(out, {})

    def test_expired_against_injected_now_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _high_risk_bundle(workspace_paths(tmp)["proposals"])
            far_future = datetime(2100, 1, 1, tzinfo=timezone.utc)
            out = resolve_high_risk_overrides(
                project_root=tmp, now=far_future, env="mock", enabled_high_risk=True
            )
            self.assertEqual(out, {})

    def test_multiple_active_bundles_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proposals = workspace_paths(tmp)["proposals"]
            _high_risk_bundle(proposals, proposal_id="atp_20260604_approved_high_risk_0001")
            _high_risk_bundle(proposals, proposal_id="atp_20260604_approved_high_risk_0002")
            out = resolve_high_risk_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled_high_risk=True
            )
            self.assertEqual(out, {})

    def test_tier_c_blocked_param_never_applies(self) -> None:
        # The cardinal Phase 4 invariant: Tier C stays blocked even here (M11/D1).
        with tempfile.TemporaryDirectory() as tmp:
            _high_risk_bundle(
                workspace_paths(tmp)["proposals"],
                parameter="buy_max_qty_per_trade",
                risk_tier="C",
                from_value=10,
                to_value=12,
                bound_min=None,
                bound_max=None,
                max_step=None,
            )
            out = resolve_high_risk_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled_high_risk=True
            )
            self.assertEqual(out, {})


class MergeSaferOfSettingsTests(unittest.TestCase):
    """Tier B safer-of (M15): longer cooldown / fewer same-symbol buys win."""

    def test_longer_cooldown_override_applies(self) -> None:
        merged = merge_safer_of_settings(
            {"rebuy_cooldown_minutes": 30}, {"rebuy_cooldown_minutes": 45}
        )
        self.assertEqual(merged["rebuy_cooldown_minutes"], 45)

    def test_shorter_cooldown_override_is_ignored(self) -> None:
        # current cooldown 60 is already longer (safer); override 45 ignored.
        merged = merge_safer_of_settings(
            {"rebuy_cooldown_minutes": 60}, {"rebuy_cooldown_minutes": 45}
        )
        self.assertEqual(merged["rebuy_cooldown_minutes"], 60)

    def test_same_symbol_cap_takes_the_smaller(self) -> None:
        # current limit 1 is already tighter (safer); override 3 must be ignored.
        merged = merge_safer_of_settings(
            {"same_symbol_max_buys_per_day": 1}, {"same_symbol_max_buys_per_day": 3}
        )
        self.assertEqual(merged["same_symbol_max_buys_per_day"], 1)


if __name__ == "__main__":
    unittest.main()
