"""Tests for the autotuner approval CLI (thin wrapper over app.autotuner.approval).

The CLI defaults to a read-only review (dry-run): it prints a summary and writes
NOTHING unless the human passes the explicit ``--approve`` flag. Only on
``--approve`` does it perform the validated draft -> approved transition and write
the artifact into the proposals dir the runtime reader consumes. Local-file only.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.autotuner.validator import validate_proposal
from app.tools.autotuner_approve import main


def _write_tier_a_draft(path: Path, proposal_id: str = "atp_20260604_shadow_0001") -> None:
    draft = {
        "schema_version": "0.1.0",
        "proposal_id": proposal_id,
        "created_at": "2026-06-04T09:00:00+09:00",
        "generated_by": {"kind": "human", "name": "test"},
        "mode": "shadow",
        "status": "draft",
        "reason": "slower sell cadence",
        "evidence": [{"source_type": "live_log", "source_id": "reconstruct_20260603"}],
        "baseline": {
            "baseline_id": "settings_default_20260604",
            "values": {"sell_check_interval_seconds": 35},
        },
        "changes": [
            {
                "parameter": "sell_check_interval_seconds",
                "from_value": 35,
                "to_value": 45,
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
    path.write_text(json.dumps(draft), encoding="utf-8")


def _write_tier_b_draft(path: Path) -> None:
    draft = {
        "schema_version": "0.1.0",
        "proposal_id": "atp_20260604_shadow_0002",
        "created_at": "2026-06-04T09:00:00+09:00",
        "generated_by": {"kind": "human", "name": "test"},
        "mode": "shadow",
        "status": "draft",
        "reason": "longer rebuy cooldown",
        "evidence": [
            {"source_type": "live_log", "source_id": "reconstruct_20260603"},
            {"source_type": "live_log", "source_id": "reconstruct_20260604"},
        ],
        "baseline": {
            "baseline_id": "settings_default_20260604",
            "values": {"rebuy_cooldown_minutes": 60},
        },
        "changes": [
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
        ],
        "constraints_checked": {},
        "risk_review": {"reviewer": "human:risk", "status": "approved"},
        "approval": None,
        "ttl": None,
        "rollback": {"rollback_baseline_id": "settings_default_20260604"},
        "audit": {"events": []},
    }
    path.write_text(json.dumps(draft), encoding="utf-8")


class ApproveCliTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            draft_path = Path(tmp) / "draft.json"
            out_dir = Path(tmp) / "proposals"
            _write_tier_a_draft(draft_path)
            code = main(
                [
                    "--proposal-file", str(draft_path),
                    "--approved-by", "human:test",
                    "--out-dir", str(out_dir),
                ]
            )
            self.assertEqual(code, 0)
            self.assertFalse(out_dir.exists() and any(out_dir.iterdir()))

    def test_approve_flag_writes_validated_approved_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            draft_path = Path(tmp) / "draft.json"
            out_dir = Path(tmp) / "proposals"
            _write_tier_a_draft(draft_path)
            code = main(
                [
                    "--proposal-file", str(draft_path),
                    "--approved-by", "human:test",
                    "--out-dir", str(out_dir),
                    "--approve",
                ]
            )
            self.assertEqual(code, 0)
            written = list(out_dir.glob("*.json"))
            self.assertEqual(len(written), 1)
            artifact = json.loads(written[0].read_text(encoding="utf-8"))
            self.assertEqual(artifact["status"], "approved")
            self.assertEqual(artifact["mode"], "approved_low_risk")
            self.assertTrue(validate_proposal(artifact).accepted)

    def test_high_risk_flag_writes_validated_high_risk_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            draft_path = Path(tmp) / "draft.json"
            out_dir = Path(tmp) / "proposals"
            _write_tier_b_draft(draft_path)
            code = main(
                [
                    "--proposal-file", str(draft_path),
                    "--approved-by", "human:test",
                    "--out-dir", str(out_dir),
                    "--high-risk",
                    "--approve",
                ]
            )
            self.assertEqual(code, 0)
            written = list(out_dir.glob("*.json"))
            self.assertEqual(len(written), 1)
            artifact = json.loads(written[0].read_text(encoding="utf-8"))
            self.assertEqual(artifact["status"], "approved")
            self.assertEqual(artifact["mode"], "approved_high_risk")
            self.assertTrue(validate_proposal(artifact, allow_high_risk=True).accepted)

    def test_second_approval_supersedes_the_prior_active_bundle(self) -> None:
        # D6: approving a second bundle must supersede the first so the runtime
        # reader keeps seeing exactly one active approved bundle (not fail-closed).
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "proposals"
            first = Path(tmp) / "first.json"
            second = Path(tmp) / "second.json"
            _write_tier_a_draft(first, proposal_id="atp_20260604_shadow_0001")
            _write_tier_a_draft(second, proposal_id="atp_20260605_shadow_0001")

            for draft in (first, second):
                self.assertEqual(
                    main(
                        [
                            "--proposal-file", str(draft),
                            "--approved-by", "human:test",
                            "--out-dir", str(out_dir),
                            "--approve",
                        ]
                    ),
                    0,
                )

            approved = [
                json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(out_dir.glob("*.json"))
            ]
            statuses = {a["proposal_id"]: a["status"] for a in approved}
            self.assertEqual(statuses["atp_20260604_shadow_0001"], "superseded")
            self.assertEqual(statuses["atp_20260605_shadow_0001"], "approved")
            active = [a for a in approved if a["status"] == "approved"]
            self.assertEqual(len(active), 1)


if __name__ == "__main__":
    unittest.main()
