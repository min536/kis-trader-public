"""End-to-end mock validation of the full autotuner loop (post-Phase-4 capstone).

Proves the independently-unit-tested components actually COMPOSE into a working
human-in-the-loop loop, entirely in mock, with no broker calls and no runtime
application beyond the gated readers:

    propose-cycle (DRAFT shadow)  ->  human approve (approved_low_risk)
        ->  resolve_runtime_overrides (mock, enabled)  ->  merge_safer_of

Nothing here touches the live path; every step is the real module composed.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.autotuner.approval import approve_proposal
from app.autotuner.generator import write_proposal
from app.autotuner.persist import workspace_paths
from app.autotuner.propose_cycle import run_propose_cycle
from app.autotuner.runtime_override import merge_safer_of, resolve_runtime_overrides
from app.autotuner.validator import validate_proposal

_NOW = datetime(2026, 6, 4, 10, 0, 0, tzinfo=timezone.utc)


def _seed_evidence(root: Path) -> tuple[str, str]:
    eval_path = root / "eval.json"
    eval_path.write_text(
        json.dumps(
            {"candidate_run_id": "run_cand_001", "deltas": {"sharpe": 0.1}, "verdict": "pass"}
        ),
        encoding="utf-8",
    )
    sess_path = root / "session_20260603.json"
    sess_path.write_text(
        json.dumps({"session_date": "2026-06-03", "cycles": 9}), encoding="utf-8"
    )
    return str(eval_path), str(sess_path)


def _plan(backtest_eval: str, live_log: str) -> list:
    return [
        {
            "proposal_id": "atp_20260604_shadow_0001",
            "parameter": "buy_scan_shallow_top_k",
            "from_value": 10,
            "to_value": 12,
            "reason": "e2e shallow_top_k",
            "backtest_eval": backtest_eval,
            "live_log_summary": live_log,
        }
    ]


class AutotunerTierAEndToEndTests(unittest.TestCase):
    def test_propose_approve_resolve_merge_full_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proposals = workspace_paths(root)["proposals"]
            backtest_eval, live_log = _seed_evidence(root)

            # 1) propose-cycle writes a DRAFT shadow proposal (Tier A, with live_log).
            cycle = run_propose_cycle(
                _plan(backtest_eval, live_log),
                project_root=root,
                now=_NOW,
                read_only=False,
                out_dir=proposals,
            )
            self.assertEqual(cycle["outcomes"][0]["status"], "built")
            draft = json.loads(
                Path(cycle["written_paths"][0]).read_text(encoding="utf-8")
            )
            self.assertEqual(draft["mode"], "shadow")

            # 2) human approves -> approved_low_risk, persisted over the same id.
            approved = approve_proposal(
                draft,
                approved_by="human:test",
                approved_at="2026-06-04T09:30:00+09:00",
                expires_at="2099-01-01T00:00:00+09:00",
            )
            self.assertEqual(approved["mode"], "approved_low_risk")
            self.assertTrue(validate_proposal(approved).accepted)
            write_proposal(approved, proposals)

            # 3) the gated runtime reader (mock, enabled) picks up the single bundle.
            overrides = resolve_runtime_overrides(
                project_root=root, now=_NOW, env="mock", enabled=True
            )
            self.assertEqual(overrides, {"buy_scan_shallow_top_k": 12})

            # 4) safer-of merge applies it (fewer shallow candidates is safer = min).
            merged = merge_safer_of({"effective_buy_scan_shallow_top_k": 20}, overrides)
            self.assertEqual(merged["effective_buy_scan_shallow_top_k"], 12)

    def test_unapproved_draft_is_not_consumed_by_the_runtime_reader(self) -> None:
        # The same draft, left UNAPPROVED (mode=shadow), must never reach runtime.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proposals = workspace_paths(root)["proposals"]
            backtest_eval, live_log = _seed_evidence(root)
            run_propose_cycle(
                _plan(backtest_eval, live_log),
                project_root=root,
                now=_NOW,
                read_only=False,
                out_dir=proposals,
            )
            overrides = resolve_runtime_overrides(
                project_root=root, now=_NOW, env="mock", enabled=True
            )
            self.assertEqual(overrides, {})


if __name__ == "__main__":
    unittest.main()
