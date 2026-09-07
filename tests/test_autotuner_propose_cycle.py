"""Tests for the autotuner periodic propose-cycle (post-Phase-4 Step 4).

One tick of the human-in-the-loop loop: for each operator-curated candidate it
collects bridged evidence, shadow-evaluates it into a DRAFT proposal, optionally
persists the draft under _workspace (write-gated), and renders a human
notification report. It NEVER approves and NEVER applies anything to the runtime;
candidates come from a curated plan (auto-generation is a deliberate non-goal).
A bad candidate is recorded as refused, never aborting the tick.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.autotuner.propose_cycle import run_propose_cycle
from app.autotuner.validator import validate_proposal

_NOW = datetime(2026, 6, 4, 10, 0, 0, tzinfo=timezone.utc)


def _write_eval(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "candidate_run_id": "run_cand_001",
                "deltas": {"sharpe": 0.12, "max_drawdown": -1.5, "total_return": 2.1},
                "verdict": "pass",
            }
        ),
        encoding="utf-8",
    )


def _candidate(tmp: Path, *, proposal_id="atp_20260604_shadow_0001", to_value=12) -> dict:
    eval_path = tmp / f"{proposal_id}_eval.json"
    _write_eval(eval_path)
    return {
        "proposal_id": proposal_id,
        "parameter": "buy_scan_shallow_top_k",
        "from_value": 10,
        "to_value": to_value,
        "reason": "periodic shadow eval of shallow_top_k",
        "backtest_eval": str(eval_path),
    }


class ProposeCycleDryRunTests(unittest.TestCase):
    def test_dry_run_builds_draft_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dir = root / "proposals"
            result = run_propose_cycle(
                [_candidate(root)],
                project_root=root,
                now=_NOW,
                read_only=True,
                out_dir=out_dir,
            )
            self.assertEqual(len(result["outcomes"]), 1)
            self.assertEqual(result["outcomes"][0]["status"], "built")
            self.assertEqual(result["written_paths"], [])
            self.assertFalse(out_dir.exists() and any(out_dir.iterdir()))


class ProposeCycleWriteModeTests(unittest.TestCase):
    def test_write_mode_persists_a_valid_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dir = root / "proposals"
            result = run_propose_cycle(
                [_candidate(root)],
                project_root=root,
                now=_NOW,
                read_only=False,
                out_dir=out_dir,
            )
            self.assertEqual(len(result["written_paths"]), 1)
            written = list(out_dir.glob("*.json"))
            self.assertEqual(len(written), 1)
            artifact = json.loads(written[0].read_text(encoding="utf-8"))
            self.assertEqual(artifact["mode"], "shadow")
            self.assertTrue(validate_proposal(artifact).accepted)

    def test_invalid_candidate_is_refused_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dir = root / "proposals"
            good = _candidate(root, proposal_id="atp_20260604_shadow_0001", to_value=12)
            bad = _candidate(root, proposal_id="atp_20260604_shadow_0002", to_value=999)
            result = run_propose_cycle(
                [good, bad],
                project_root=root,
                now=_NOW,
                read_only=False,
                out_dir=out_dir,
            )
            statuses = {o["proposal_id"]: o["status"] for o in result["outcomes"]}
            self.assertEqual(statuses["atp_20260604_shadow_0001"], "built")
            self.assertEqual(statuses["atp_20260604_shadow_0002"], "refused")
            # only the valid candidate is written; the bad one is skipped, not fatal
            self.assertEqual(len(result["written_paths"]), 1)
            bad_outcome = next(
                o for o in result["outcomes"] if o["proposal_id"].endswith("0002")
            )
            self.assertIn("reason", bad_outcome)


class ProposeCycleEvidenceTests(unittest.TestCase):
    def test_malformed_live_log_is_refused_not_fatal(self) -> None:
        # A malformed live_log artifact (a JSON list, not an object) must refuse
        # that candidate and let the tick continue — never abort with AttributeError.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dir = root / "proposals"
            good = _candidate(root, proposal_id="atp_20260604_shadow_0001", to_value=12)
            bad = _candidate(root, proposal_id="atp_20260604_shadow_0002", to_value=14)
            bad_sess = root / "bad_session.json"
            bad_sess.write_text(json.dumps([1, 2, 3]), encoding="utf-8")  # list, not object
            bad["live_log_summary"] = str(bad_sess)

            result = run_propose_cycle(
                [bad, good],
                project_root=root,
                now=_NOW,
                read_only=False,
                out_dir=out_dir,
            )

            statuses = {o["proposal_id"]: o["status"] for o in result["outcomes"]}
            self.assertEqual(statuses["atp_20260604_shadow_0002"], "refused")
            self.assertEqual(statuses["atp_20260604_shadow_0001"], "built")

    def test_collects_both_backtest_and_live_log_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dir = root / "proposals"
            candidate = _candidate(root, proposal_id="atp_20260604_shadow_0003")
            sess = root / "session_20260603.json"
            sess.write_text(
                json.dumps({"session_date": "2026-06-03", "cycles": 9}),
                encoding="utf-8",
            )
            candidate["live_log_summary"] = str(sess)
            result = run_propose_cycle(
                [candidate],
                project_root=root,
                now=_NOW,
                read_only=False,
                out_dir=out_dir,
            )
            self.assertEqual(result["outcomes"][0]["status"], "built")
            artifact = json.loads(next(out_dir.glob("*.json")).read_text(encoding="utf-8"))
            kinds = {e["source_type"] for e in artifact["evidence"]}
            self.assertEqual(kinds, {"backtest", "live_log"})


class ProposeCycleReportTests(unittest.TestCase):
    def test_report_summarises_drafts_and_demands_human_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = _candidate(root, proposal_id="atp_20260604_shadow_0001", to_value=12)
            bad = _candidate(root, proposal_id="atp_20260604_shadow_0002", to_value=999)
            result = run_propose_cycle(
                [good, bad],
                project_root=root,
                now=_NOW,
                read_only=True,
                out_dir=root / "proposals",
            )
            report = result["report"]
            self.assertIn("Autotuner Propose Cycle", report)
            self.assertIn("atp_20260604_shadow_0001", report)
            self.assertIn("refused", report.lower())
            # human-in-the-loop: the tick never approves; it asks a human to review
            self.assertIn("approval", report.lower())


if __name__ == "__main__":
    unittest.main()
