"""Tests for autotuner runtime-override diagnostics (observability).

The hot-path resolvers fail safe to {} silently. Operators need to know WHY an
override is or isn't applied (flag off, wrong env, no approved bundle, expired,
ambiguous). diagnose_runtime_overrides re-runs the same gate read-only and
returns a structured, human-readable explanation. It never raises.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.autotuner.diagnostics import (
    diagnose_high_risk_overrides,
    diagnose_live_shadow_runtime_overrides,
    diagnose_runtime_overrides,
)
from app.autotuner.persist import workspace_paths

_NOW = datetime(2026, 6, 4, 10, 0, 0, tzinfo=timezone.utc)


def _write_approved_low_risk(proposals: Path, *, to_value: int = 12, status: str = "approved") -> None:
    proposals.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema_version": "0.1.0",
        "proposal_id": "atp_20260604_approved_low_risk_0001",
        "created_at": "2026-06-04T09:00:00+09:00",
        "generated_by": {"kind": "human", "name": "test"},
        "mode": "approved_low_risk",
        "status": status,
        "reason": "diag",
        "evidence": [{"source_type": "live_log", "source_id": "r0"}],
        "baseline": {
            "baseline_id": "b",
            "values": {"buy_scan_shallow_top_k": 10},
        },
        "changes": [
            {
                "parameter": "buy_scan_shallow_top_k",
                "from_value": 10,
                "to_value": to_value,
                "type": "int",
                "risk_tier": "A",
                "eligible_phase": 3,
                "bound_min": 5,
                "bound_max": 20,
                "max_step": 2,
                "validation_status": "passed",
                "rationale": "x",
                "notes": "",
            }
        ],
        "constraints_checked": {},
        "risk_review": None,
        "approval": {"approved_by": "human:test", "approved_at": "2026-06-04T09:30:00+09:00"},
        "ttl": {"expires_at": "2099-01-01T00:00:00+09:00"},
        "rollback": {"rollback_baseline_id": "b"},
        "audit": {"events": []},
    }
    (proposals / f"{bundle['proposal_id']}.json").write_text(json.dumps(bundle), encoding="utf-8")


def _write_approved_high_risk(proposals: Path, *, to_value: int = 75) -> None:
    proposals.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema_version": "0.1.0",
        "proposal_id": "atp_20260604_approved_high_risk_0001",
        "created_at": "2026-06-04T09:00:00+09:00",
        "generated_by": {"kind": "human", "name": "test"},
        "mode": "approved_high_risk",
        "status": "approved",
        "reason": "diag high risk",
        "evidence": [
            {"source_type": "live_log", "source_id": "r0"},
            {"source_type": "live_log", "source_id": "r1"},
        ],
        "baseline": {
            "baseline_id": "b",
            "values": {"rebuy_cooldown_minutes": 60},
        },
        "changes": [
            {
                "parameter": "rebuy_cooldown_minutes",
                "from_value": 60,
                "to_value": to_value,
                "type": "int",
                "risk_tier": "B",
                "eligible_phase": 4,
                "bound_min": 15,
                "bound_max": 240,
                "max_step": 15,
                "validation_status": "passed",
                "rationale": "x",
                "notes": "",
            }
        ],
        "constraints_checked": {},
        "risk_review": {"reviewer": "human:risk", "status": "approved"},
        "approval": {"approved_by": "human:test", "approved_at": "2026-06-04T09:30:00+09:00"},
        "ttl": {"expires_at": "2099-01-01T00:00:00+09:00"},
        "rollback": {"rollback_baseline_id": "b"},
        "audit": {"events": []},
    }
    (proposals / f"{bundle['proposal_id']}.json").write_text(json.dumps(bundle), encoding="utf-8")


class DiagnoseRuntimeOverridesTests(unittest.TestCase):
    def test_flag_disabled_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = diagnose_runtime_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled=False
            )
            self.assertFalse(out["flag_enabled"])
            self.assertEqual(out["would_apply"], {})
            self.assertIn("disabled", out["reason"].lower())

    def test_live_env_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_approved_low_risk(workspace_paths(tmp)["proposals"])
            out = diagnose_runtime_overrides(
                project_root=tmp, now=_NOW, env="live", enabled=True
            )
            self.assertEqual(out["would_apply"], {})
            self.assertIn("mock", out["reason"].lower())

    def test_single_approved_bundle_would_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_approved_low_risk(workspace_paths(tmp)["proposals"])
            out = diagnose_runtime_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled=True
            )
            self.assertEqual(out["would_apply"], {"buy_scan_shallow_top_k": 12})
            self.assertIn("eligible", out["reason"].lower())

    def test_present_but_unapproved_bundle_is_explained(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_approved_low_risk(workspace_paths(tmp)["proposals"], status="draft")
            out = diagnose_runtime_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled=True
            )
            self.assertEqual(out["would_apply"], {})
            self.assertIn("no eligible", out["reason"].lower())

    def test_two_active_bundles_report_ambiguous(self) -> None:
        # Two approved+valid+unexpired bundles → the reader fails closed to {}.
        # Diagnostics must say WHY (ambiguous), not the generic "no eligible".
        with tempfile.TemporaryDirectory() as tmp:
            proposals = workspace_paths(tmp)["proposals"]
            _write_approved_low_risk(proposals)
            # a second distinct active bundle
            proposals.mkdir(parents=True, exist_ok=True)
            second = json.loads(
                (proposals / "atp_20260604_approved_low_risk_0001.json").read_text("utf-8")
            )
            second["proposal_id"] = "atp_20260605_approved_low_risk_0001"
            (proposals / f"{second['proposal_id']}.json").write_text(
                json.dumps(second), encoding="utf-8"
            )
            out = diagnose_runtime_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled=True
            )
            self.assertEqual(out["would_apply"], {})
            self.assertIn("ambiguous", out["reason"].lower())
            self.assertIn("2", out["reason"])

    def test_live_shadow_calculates_would_apply_without_runtime_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_approved_low_risk(workspace_paths(tmp)["proposals"])
            out = diagnose_live_shadow_runtime_overrides(
                project_root=tmp, now=_NOW, env="live"
            )
            self.assertTrue(out["shadow_only"])
            self.assertFalse(out["runtime_activation"])
            self.assertEqual(out["would_apply"], {"buy_scan_shallow_top_k": 12})
            self.assertIn("live-shadow", out["reason"].lower())

    def test_live_shadow_requires_live_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_approved_low_risk(workspace_paths(tmp)["proposals"])
            out = diagnose_live_shadow_runtime_overrides(
                project_root=tmp, now=_NOW, env="mock"
            )
            self.assertEqual(out["would_apply"], {})
            self.assertIn("not live", out["reason"].lower())

    def test_high_risk_status_calculates_tier_b_would_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_approved_high_risk(workspace_paths(tmp)["proposals"])
            out = diagnose_high_risk_overrides(
                project_root=tmp, now=_NOW, env="mock", enabled_high_risk=True
            )
            self.assertTrue(out["flag_enabled"])
            self.assertEqual(out["would_apply"], {"rebuy_cooldown_minutes": 75})
            self.assertIn("tier b", out["reason"].lower())


class StatusCliTests(unittest.TestCase):
    def test_status_cli_prints_diagnosis(self) -> None:
        import io
        from contextlib import redirect_stdout

        from app.tools.autotuner_status import main

        with tempfile.TemporaryDirectory() as tmp:
            _write_approved_low_risk(workspace_paths(tmp)["proposals"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["--project-root", tmp, "--env", "mock", "--enabled"])
            self.assertEqual(code, 0)
            self.assertIn("buy_scan_shallow_top_k", buf.getvalue())

    def test_status_cli_prints_live_shadow_diagnosis(self) -> None:
        import io
        from contextlib import redirect_stdout

        from app.tools.autotuner_status import main

        with tempfile.TemporaryDirectory() as tmp:
            _write_approved_low_risk(workspace_paths(tmp)["proposals"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["--project-root", tmp, "--env", "live", "--live-shadow"])
            self.assertEqual(code, 0)
            output = buf.getvalue()
            self.assertIn("live-shadow", output)
            self.assertIn("runtime_apply: False", output)
            self.assertIn("buy_scan_shallow_top_k", output)

    def test_status_cli_prints_high_risk_diagnosis(self) -> None:
        import io
        from contextlib import redirect_stdout

        from app.tools.autotuner_status import main

        with tempfile.TemporaryDirectory() as tmp:
            _write_approved_high_risk(workspace_paths(tmp)["proposals"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(
                    [
                        "--project-root", tmp,
                        "--env", "mock",
                        "--high-risk",
                        "--enabled-high-risk",
                    ]
                )
            self.assertEqual(code, 0)
            output = buf.getvalue()
            self.assertIn("Tier B high-risk", output)
            self.assertIn("rebuy_cooldown_minutes", output)


if __name__ == "__main__":
    unittest.main()
