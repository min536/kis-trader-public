"""Tests for the D6 ``superseded`` transition (single active bundle invariant).

Decision D6 (docs/live_autotuner_decisions.md): when a new bundle becomes
``approved``, the previously-active bundle of the same mode must transition to
``superseded`` so the runtime reader keeps seeing exactly one active bundle.
Without this, a second approval leaves two ``approved`` bundles and the
fail-closed runtime reader silently applies nothing. The supersede step is a
filesystem operation (the approval transition itself stays pure), so it lives in
the persist layer and is invoked by the approve CLI.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.autotuner.persist import supersede_active_bundles

_NOW = datetime(2026, 6, 6, 10, 0, 0, tzinfo=timezone.utc)


def _bundle(proposal_id: str, *, status="approved", mode="approved_low_risk", expires="2099-01-01T00:00:00+00:00"):
    return {
        "proposal_id": proposal_id,
        "mode": mode,
        "status": status,
        "ttl": {"expires_at": expires},
        "audit": {"events": []},
    }


def _write(directory: Path, bundle: dict) -> Path:
    path = directory / f"{bundle['proposal_id']}.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return path


class SupersedeActiveBundlesTests(unittest.TestCase):
    def test_prior_active_same_mode_bundle_is_superseded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            old_path = _write(directory, _bundle("atp_20260605_approved_low_risk_0001"))
            new_id = "atp_20260606_approved_low_risk_0001"
            _write(directory, _bundle(new_id))

            superseded = supersede_active_bundles(
                directory, keep_proposal_id=new_id, mode="approved_low_risk", now=_NOW
            )

            self.assertEqual(superseded, ["atp_20260605_approved_low_risk_0001"])
            old = json.loads(old_path.read_text(encoding="utf-8"))
            self.assertEqual(old["status"], "superseded")
            # explicit audit event referencing the replacing bundle (D6)
            events = old["audit"]["events"]
            self.assertTrue(
                any(e.get("event") == "superseded" and e.get("by") == new_id for e in events),
                msg=f"expected a superseded audit event, got: {events}",
            )


    def test_keep_and_other_modes_and_inactive_are_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            keep_id = "atp_20260606_approved_low_risk_0001"
            _write(directory, _bundle(keep_id))
            _write(directory, _bundle("atp_20260601_approved_high_risk_0001", mode="approved_high_risk"))
            _write(directory, _bundle("atp_20260601_approved_low_risk_0002", status="superseded"))
            expired_path = _write(
                directory,
                _bundle("atp_20260601_approved_low_risk_0003", expires="2000-01-01T00:00:00+00:00"),
            )

            superseded = supersede_active_bundles(
                directory, keep_proposal_id=keep_id, mode="approved_low_risk", now=_NOW
            )

            # keep is untouched; other mode untouched; already-superseded untouched;
            # an expired (already-inactive) bundle need not be rewritten.
            self.assertEqual(superseded, [])
            self.assertEqual(
                json.loads(expired_path.read_text(encoding="utf-8"))["status"], "approved"
            )


if __name__ == "__main__":
    unittest.main()
