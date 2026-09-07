"""Tests for the Phase 3 autotuner runtime-override reader.

This is the FIRST runtime reader. It is gated to fail-safe empty: unless an
explicit enable flag is on, KIS_ENV is mock, and exactly one approved,
unexpired, Tier-A bundle exists, it returns {}. It NEVER raises (a crash must
not abort a trading cycle). See docs/live_autotuner_phase3_risk.md (M1–M10).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.autotuner.persist import workspace_paths
from app.autotuner.runtime_override import merge_safer_of, resolve_runtime_overrides

_NOW = datetime(2026, 6, 4, 10, 0, 0, tzinfo=timezone.utc)


def _approved_low_risk_bundle(
    proposals_dir: Path,
    to_value: int = 45,
    *,
    mode: str = "approved_low_risk",
    status: str = "approved",
    proposal_id: str = "atp_20260604_approved_low_risk_0001",
    expires_at: str = "2099-01-01T00:00:00+09:00",
) -> None:
    """Write one approved_low_risk Tier-A bundle into the proposals dir."""
    proposals_dir.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema_version": "0.1.0",
        "proposal_id": proposal_id,
        "created_at": "2026-06-04T09:00:00+09:00",
        "generated_by": {"kind": "human", "name": "test"},
        "mode": mode,
        "status": status,
        "reason": "slower sell cadence",
        "evidence": [
            {"source_type": "live_log", "source_id": "reconstruct_20260603"}
        ],
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
        "approval": {
            "approved_by": "human:test",
            "approved_at": "2026-06-04T09:30:00+09:00",
        },
        "ttl": {"expires_at": expires_at},
        "rollback": {"rollback_baseline_id": "settings_default_20260604"},
        "audit": {"events": []},
    }
    path = proposals_dir / f"{bundle['proposal_id']}.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")


def _approved_shallow_top_k_bundle(proposals_dir: Path) -> None:
    proposals_dir.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema_version": "0.1.0",
        "proposal_id": "atp_20260604_approved_low_risk_0001",
        "created_at": "2026-06-04T09:00:00+09:00",
        "generated_by": {"kind": "human", "name": "test"},
        "mode": "approved_low_risk",
        "status": "approved",
        "reason": "reduce shallow candidate count",
        "evidence": [
            {"source_type": "live_log", "source_id": "reconstruct_20260603"}
        ],
        "baseline": {
            "baseline_id": "settings_default_20260604",
            "values": {"buy_scan_shallow_top_k": 10},
        },
        "changes": [
            {
                "parameter": "buy_scan_shallow_top_k",
                "from_value": 10,
                "to_value": 8,
                "type": "int",
                "risk_tier": "A",
                "eligible_phase": 3,
                "bound_min": 5,
                "bound_max": 20,
                "max_step": 2,
                "validation_status": "passed",
                "rationale": "reduce request pressure",
                "notes": "",
            }
        ],
        "constraints_checked": {},
        "risk_review": None,
        "approval": {
            "approved_by": "human:test",
            "approved_at": "2026-06-04T09:30:00+09:00",
        },
        "ttl": {"expires_at": "2099-01-01T00:00:00+09:00"},
        "rollback": {"rollback_baseline_id": "settings_default_20260604"},
        "audit": {"events": []},
    }
    path = proposals_dir / f"{bundle['proposal_id']}.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")


class RuntimeOverrideGateTests(unittest.TestCase):
    def test_disabled_returns_empty_even_with_valid_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _approved_low_risk_bundle(workspace_paths(tmp)["proposals"])
            out = resolve_runtime_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled=False
            )
            self.assertEqual(out, {})

    def test_enabled_mock_with_single_valid_bundle_emits_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _approved_low_risk_bundle(workspace_paths(tmp)["proposals"])
            out = resolve_runtime_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled=True
            )
            self.assertEqual(out, {"sell_check_interval_seconds": 45})

    def test_enabled_mock_shallow_top_k_bundle_emits_and_merges_safer_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _approved_shallow_top_k_bundle(workspace_paths(tmp)["proposals"])
            out = resolve_runtime_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled=True
            )

        self.assertEqual(out, {"buy_scan_shallow_top_k": 8})

        merged = merge_safer_of(
            {"effective_buy_scan_shallow_top_k": 10},
            out,
        )
        self.assertEqual(merged["effective_buy_scan_shallow_top_k"], 8)

    def test_override_value_is_read_from_the_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # 40 is a valid Tier-A move (within 30..120, step 5 <= max_step 10).
            _approved_low_risk_bundle(workspace_paths(tmp)["proposals"], to_value=40)
            out = resolve_runtime_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled=True
            )
            self.assertEqual(out, {"sell_check_interval_seconds": 40})

    def test_non_approved_bundle_is_ignored(self) -> None:
        # A leftover draft/mock proposal must never be applied (M2).
        with tempfile.TemporaryDirectory() as tmp:
            _approved_low_risk_bundle(
                workspace_paths(tmp)["proposals"], mode="mock", status="draft"
            )
            out = resolve_runtime_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled=True
            )
            self.assertEqual(out, {})

    def test_expired_against_injected_now_is_ignored(self) -> None:
        # expires_at is far-future (validator's real-clock check accepts), but
        # the injected now is even later, so the resolver's own expiry fires (M3).
        with tempfile.TemporaryDirectory() as tmp:
            _approved_low_risk_bundle(workspace_paths(tmp)["proposals"])
            far_future_now = datetime(2100, 1, 1, tzinfo=timezone.utc)
            out = resolve_runtime_overrides(
                project_root=tmp, now=far_future_now, env="mock", enabled=True
            )
            self.assertEqual(out, {})

    def test_bundle_failing_revalidation_is_ignored(self) -> None:
        # A forged/out-of-bounds bundle must be re-validated and rejected (M2).
        with tempfile.TemporaryDirectory() as tmp:
            _approved_low_risk_bundle(
                workspace_paths(tmp)["proposals"], to_value=999  # bound_max is 120
            )
            out = resolve_runtime_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled=True
            )
            self.assertEqual(out, {})

    def test_live_env_returns_empty(self) -> None:
        # Phase 3 is mock-only; live is a separate hard gate not built here (M5).
        with tempfile.TemporaryDirectory() as tmp:
            _approved_low_risk_bundle(workspace_paths(tmp)["proposals"])
            out = resolve_runtime_overrides(
                project_root=tmp, now=_NOW, env="live", enabled=True
            )
            self.assertEqual(out, {})

    def test_bad_now_does_not_raise(self) -> None:
        # Any malformed input (here a non-datetime now) must degrade to {} (M1).
        with tempfile.TemporaryDirectory() as tmp:
            _approved_low_risk_bundle(workspace_paths(tmp)["proposals"])
            out = resolve_runtime_overrides(
                project_root=tmp, now="not-a-datetime", env="mock", enabled=True
            )  # must not raise
            self.assertEqual(out, {})

    def test_malformed_bundle_does_not_raise(self) -> None:
        # A corrupt artifact must never crash a trading cycle (M1).
        with tempfile.TemporaryDirectory() as tmp:
            proposals = workspace_paths(tmp)["proposals"]
            proposals.mkdir(parents=True, exist_ok=True)
            (proposals / "broken.json").write_text("{ not json", encoding="utf-8")
            out = resolve_runtime_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled=True
            )  # must not raise
            self.assertEqual(out, {})

    def test_oversized_bundle_is_skipped_without_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proposals = workspace_paths(tmp)["proposals"]
            _approved_low_risk_bundle(proposals)
            path = proposals / "atp_20260604_approved_low_risk_0001.json"
            bundle = json.loads(path.read_text(encoding="utf-8"))
            bundle["padding"] = "x" * (70 * 1024)
            path.write_text(json.dumps(bundle), encoding="utf-8")

            out = resolve_runtime_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled=True
            )  # must not raise

        self.assertEqual(out, {})

    def test_multiple_active_bundles_returns_empty(self) -> None:
        # Ambiguity is unsafe: exactly one active approved bundle, else {} (M4).
        with tempfile.TemporaryDirectory() as tmp:
            proposals = workspace_paths(tmp)["proposals"]
            _approved_low_risk_bundle(proposals, proposal_id="atp_20260604_approved_low_risk_0001")
            _approved_low_risk_bundle(proposals, proposal_id="atp_20260604_approved_low_risk_0002")
            out = resolve_runtime_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled=True
            )
            self.assertEqual(out, {})


class MergeSaferOfTests(unittest.TestCase):
    """safer-of (M7): an override must never beat a protective clamp toward aggressive."""

    def test_override_applies_when_it_is_the_safer_value(self) -> None:
        rrc = {"effective_sell_check_interval_seconds": 35}
        merged = merge_safer_of(rrc, {"sell_check_interval_seconds": 45})
        # slower sell cadence is safer -> override (45) wins over 35
        self.assertEqual(merged["effective_sell_check_interval_seconds"], 45)

    def test_protective_interval_clamp_beats_aggressive_override(self) -> None:
        # degraded mode slowed sell to 50s; an override to 40s is MORE aggressive
        # and must be ignored (max wins).
        rrc = {"effective_sell_check_interval_seconds": 50}
        merged = merge_safer_of(rrc, {"sell_check_interval_seconds": 40})
        self.assertEqual(merged["effective_sell_check_interval_seconds"], 50)

    def test_protective_cap_clamp_beats_aggressive_override(self) -> None:
        # degraded mode cut scan_max to 20; an override to 30 is MORE aggressive
        # and must be ignored (min wins).
        rrc = {"effective_scan_symbols_max_per_cycle": 20}
        merged = merge_safer_of(rrc, {"scan_symbols_max_per_cycle": 30})
        self.assertEqual(merged["effective_scan_symbols_max_per_cycle"], 20)


if __name__ == "__main__":
    unittest.main()
