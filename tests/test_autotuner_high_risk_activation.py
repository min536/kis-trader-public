"""Tests for the Phase 4 Tier-B high-risk activation wrapper (post-Phase-4 Step 5).

Wires resolve_high_risk_overrides + merge_safer_of_settings into the run_cycle
regime_state seam, behind a SEPARATE default-OFF flag, mock-only. The override is
safer-of against the protective regime clamp (cooldown=max, same_symbol=min) so it
can only ever make Tier B MORE conservative, never looser. Default-OFF must be
byte-identical; the wrapper never raises. See docs/live_autotuner_phase4_risk.md.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import app.main as main_module
from app.autotuner.persist import workspace_paths
from app.autotuner.runtime_activation import apply_high_risk_overrides

_NOW = datetime(2026, 6, 4, 10, 0, 0, tzinfo=timezone.utc)


@dataclass
class _Settings:
    kis_env: str = "mock"
    base_url: str = ""
    rebuy_cooldown_minutes: int = 30


def _regime(*, cooldown: int = 30, same_symbol: int = 3) -> dict:
    return {
        "current_regime": "NORMAL",
        "effective_rebuy_cooldown_minutes": cooldown,
        "effective_same_symbol_max_buys_per_day": same_symbol,
        "effective_buy_max_qty_per_trade": 10,
    }


def _write_high_risk_bundle(
    proposals_dir: Path,
    *,
    parameter: str = "rebuy_cooldown_minutes",
    from_value: int = 30,
    to_value: int = 45,
    proposal_id: str = "atp_20260604_approved_high_risk_0001",
) -> None:
    proposals_dir.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema_version": "0.1.0",
        "proposal_id": proposal_id,
        "created_at": "2026-06-04T09:00:00+09:00",
        "generated_by": {"kind": "human", "name": "test"},
        "mode": "approved_high_risk",
        "status": "approved",
        "reason": "lengthen rebuy cooldown",
        "evidence": [
            {"source_type": "live_log", "source_id": "r0"},
            {"source_type": "live_log", "source_id": "r1"},
        ],
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
                "risk_tier": "B",
                "eligible_phase": 4,
                "bound_min": 15,
                "bound_max": 240,
                "max_step": 15,
                "validation_status": "passed",
                "rationale": "reduce buy frequency",
                "notes": "",
            }
        ],
        "constraints_checked": {},
        "risk_review": {"reviewer": "human:test", "status": "approved"},
        "approval": {
            "approved_by": "human:test",
            "approved_at": "2026-06-04T09:30:00+09:00",
        },
        "ttl": {"expires_at": "2099-01-01T00:00:00+09:00"},
        "rollback": {"rollback_baseline_id": "settings_default_20260604"},
        "audit": {"events": []},
    }
    (proposals_dir / f"{proposal_id}.json").write_text(json.dumps(bundle), encoding="utf-8")


class HighRiskActivationGateTests(unittest.TestCase):
    def test_flag_off_returns_regime_state_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_high_risk_bundle(workspace_paths(tmp)["proposals"])
            regime = _regime()
            out = apply_high_risk_overrides(
                regime,
                settings=_Settings(),
                project_root=tmp,
                now=_NOW,
                environ={},  # flag absent -> OFF
            )
            self.assertEqual(out, regime)

    def test_mock_with_valid_bundle_applies_safer_of(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # bundle proposes cooldown 30 -> 45 (longer = safer); regime baseline 30
            _write_high_risk_bundle(workspace_paths(tmp)["proposals"])
            out = apply_high_risk_overrides(
                _regime(cooldown=30),
                settings=_Settings(kis_env="mock"),
                project_root=tmp,
                now=_NOW,
                environ={"AUTOTUNER_HIGH_RISK_OVERRIDES_ENABLED": "true"},
            )
            self.assertEqual(out["effective_rebuy_cooldown_minutes"], 45)

    def test_regime_multiplier_scales_cooldown_override_before_safer_of(self) -> None:
        # Tier B proposes the base rebuy cooldown. If RISK_OFF has already
        # multiplied the base 20 -> 80, then a safer proposal 20 -> 35 must
        # become 35 * 4 = 140 before the effective-regime safer-of merge.
        with tempfile.TemporaryDirectory() as tmp:
            _write_high_risk_bundle(
                workspace_paths(tmp)["proposals"], from_value=20, to_value=35
            )
            out = apply_high_risk_overrides(
                _regime(cooldown=80),
                settings=_Settings(kis_env="mock", rebuy_cooldown_minutes=20),
                project_root=tmp,
                now=_NOW,
                environ={"AUTOTUNER_HIGH_RISK_OVERRIDES_ENABLED": "true"},
            )
            self.assertEqual(out["effective_rebuy_cooldown_minutes"], 140)

    def test_aggressive_effective_override_is_ignored_safer_of(self) -> None:
        # If the current effective regime clamp is already more protective than
        # the scaled proposal, the max merge keeps the current value.
        with tempfile.TemporaryDirectory() as tmp:
            _write_high_risk_bundle(workspace_paths(tmp)["proposals"], to_value=45)
            out = apply_high_risk_overrides(
                _regime(cooldown=120),
                settings=_Settings(kis_env="mock", rebuy_cooldown_minutes=60),
                project_root=tmp,
                now=_NOW,
                environ={"AUTOTUNER_HIGH_RISK_OVERRIDES_ENABLED": "true"},
            )
            self.assertEqual(out["effective_rebuy_cooldown_minutes"], 120)

    def test_none_regime_state_is_safe(self) -> None:
        # run_cycle must never crash on the seam: a None regime_state (flag on)
        # returns None without raising.
        with tempfile.TemporaryDirectory() as tmp:
            _write_high_risk_bundle(workspace_paths(tmp)["proposals"])
            out = apply_high_risk_overrides(
                None,
                settings=_Settings(kis_env="mock"),
                project_root=tmp,
                now=_NOW,
                environ={"AUTOTUNER_HIGH_RISK_OVERRIDES_ENABLED": "true"},
            )  # must not raise
            self.assertIsNone(out)

    def test_live_env_leaves_regime_unchanged(self) -> None:
        # Phase 4 activation is mock-only; live must never consume a bundle (W2).
        with tempfile.TemporaryDirectory() as tmp:
            _write_high_risk_bundle(workspace_paths(tmp)["proposals"])
            regime = _regime(cooldown=30)
            out = apply_high_risk_overrides(
                regime,
                settings=_Settings(kis_env="live"),
                project_root=tmp,
                now=_NOW,
                environ={"AUTOTUNER_HIGH_RISK_OVERRIDES_ENABLED": "true"},
            )
            self.assertEqual(out["effective_rebuy_cooldown_minutes"], 30)


class MainHighRiskWiringTests(unittest.TestCase):
    def test_build_regime_state_routes_through_high_risk_activation(self) -> None:
        settings = SimpleNamespace(kis_env="mock")
        base = {"effective_rebuy_cooldown_minutes": 30}
        adjusted = {"effective_rebuy_cooldown_minutes": 45}
        with (
            mock.patch("app.main._risk_build_regime_state", return_value=base),
            mock.patch(
                "app.main.apply_high_risk_overrides", return_value=adjusted
            ) as apply,
        ):
            out = main_module._build_regime_state(
                settings=settings,
                daily_pnl_brake_state=None,
                drawdown_state=None,
            )
        self.assertIs(out, adjusted)
        apply.assert_called_once_with(
            base,
            settings=settings,
            project_root=main_module.PROJECT_ROOT,
            now=mock.ANY,
        )


if __name__ == "__main__":
    unittest.main()
