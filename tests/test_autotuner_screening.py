"""Tests for the backtest->screening aggregator (Phase A).

Closes the loop between run_proposal_backtest and autotuner candidate suggestion.
Each run_proposal_backtest eval JSON is self-describing — it already carries the
proposal `changes` (parameter/to_value) and its own `verdict` derived from
pass_criteria. ``evals_to_screening`` aggregates a set of such evals into the
screening shape ``suggest_candidates`` consumes ({parameter, to_value, verdict,
backtest_eval}). The verdict is the eval's own (source of truth, not operator-set);
read-only/offline (it never runs the backtester or applies anything).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.autotuner.candidate_suggest import suggest_candidates
from app.autotuner.screening import evals_to_screening


def _write_eval(path: Path, *, parameter: str, to_value, verdict: str) -> None:
    # The real on-disk shape written by run_proposal_backtest: a top-level
    # `evaluations` array, with changes/verdict INSIDE each evaluation.
    path.write_text(
        json.dumps(
            {
                "proposal_id": "atp_x",
                "direction": "",
                "evaluations": [
                    {
                        "evaluation_type": "proposal_vs_baseline",
                        "candidate_run_id": "run_x",
                        "changes": [
                            {"parameter": parameter, "from_value": 10, "to_value": to_value}
                        ],
                        "deltas": {"sharpe": 0.1},
                        "verdict": verdict,
                    }
                ],
                "overall_verdict": verdict,
            }
        ),
        encoding="utf-8",
    )


class EvalsToScreeningTests(unittest.TestCase):
    def test_self_describing_evals_become_screening_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "eval_pass.json"
            bad = root / "eval_fail.json"
            _write_eval(good, parameter="buy_scan_shallow_top_k", to_value=12, verdict="pass")
            _write_eval(bad, parameter="buy_scan_shallow_top_k", to_value=18, verdict="fail")

            screening = evals_to_screening([good, bad])

            self.assertEqual(
                screening,
                [
                    {
                        "parameter": "buy_scan_shallow_top_k",
                        "to_value": 12,
                        "verdict": "pass",
                        "backtest_eval": str(good),
                        "repo_head": None,
                        "generated_at": None,
                        "strategy_family": None,
                        "data_window": None,
                    },
                    {
                        "parameter": "buy_scan_shallow_top_k",
                        "to_value": 18,
                        "verdict": "fail",
                        "backtest_eval": str(bad),
                        "repo_head": None,
                        "generated_at": None,
                        "strategy_family": None,
                        "data_window": None,
                    },
                ],
            )


    def test_screening_entries_carry_provenance(self) -> None:
        # E1: each screening entry should surface provenance — repo_head +
        # generated_at (run-wide) and strategy_family + data_window (per evaluation).
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eval_prov.json"
            path.write_text(
                json.dumps(
                    {
                        "proposal_id": "atp_prov",
                        "provenance": {
                            "source": "open-trading-api",
                            "repo_head": "abc123",
                            "generated_at": "2026-06-07T10:00:00+00:00",
                        },
                        "evaluations": [
                            {
                                "changes": [
                                    {"parameter": "buy_scan_shallow_top_k", "to_value": 12}
                                ],
                                "verdict": "pass",
                                "strategy_family": "core_family_approx",
                                "data_window": {"start_date": "2025-10-11", "end_date": "2026-04-11"},
                            }
                        ],
                        "overall_verdict": "pass",
                    }
                ),
                encoding="utf-8",
            )
            entry = evals_to_screening([path])[0]
            self.assertEqual(entry["repo_head"], "abc123")
            self.assertEqual(entry["generated_at"], "2026-06-07T10:00:00+00:00")
            self.assertEqual(entry["strategy_family"], "core_family_approx")
            self.assertEqual(entry["data_window"], {"start_date": "2025-10-11", "end_date": "2026-04-11"})

    def test_research_domain_eval_yields_no_screening(self) -> None:
        # Domain boundary: run_proposal_backtest writes research-domain changes
        # ({family, param_id, new_value} — RSI etc.), a DISJOINT domain from the
        # autotuner ({parameter, to_value}). A research eval has no `parameter`
        # key, so it intentionally produces NO screening entries (not a bug).
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eval_research.json"
            path.write_text(
                json.dumps(
                    {
                        "proposal_id": "research_x",
                        "evaluations": [
                            {
                                "changes": [
                                    {"family": "core_family_approx",
                                     "param_id": "rsi_entry_lower", "new_value": 50.0}
                                ],
                                "verdict": "pass",
                            }
                        ],
                        "overall_verdict": "pass",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(evals_to_screening([path]), [])

    def test_malformed_missing_and_oversized_evals_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "ok.json"
            malformed = root / "bad.json"
            oversized = root / "huge.json"
            no_changes = root / "nochanges.json"
            _write_eval(good, parameter="buy_scan_shallow_top_k", to_value=12, verdict="pass")
            malformed.write_text("{not json", encoding="utf-8")
            oversized.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")
            no_changes.write_text(json.dumps({"verdict": "pass"}), encoding="utf-8")

            screening = evals_to_screening(
                [malformed, oversized, no_changes, root / "missing.json", good]
            )

            # only the well-formed eval survives; nothing raised on the bad inputs
            self.assertEqual([e["to_value"] for e in screening], [12])


    def test_screening_output_feeds_candidate_suggest(self) -> None:
        # the loop closes: real backtest evals -> screening -> suggest plan.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            passed = root / "eval_pass.json"
            failed = root / "eval_fail.json"
            _write_eval(passed, parameter="buy_scan_shallow_top_k", to_value=12, verdict="pass")
            _write_eval(failed, parameter="buy_scan_shallow_top_k", to_value=18, verdict="fail")

            screening = evals_to_screening([passed, failed])
            plan = suggest_candidates(
                screening,
                baseline_values={"buy_scan_shallow_top_k": 10},
                date="20260607",
            )

            # only the pass move survives the suggest gate; eval ref carried through
            self.assertEqual(len(plan), 1)
            self.assertEqual(plan[0]["parameter"], "buy_scan_shallow_top_k")
            self.assertEqual(plan[0]["to_value"], 12)
            self.assertEqual(plan[0]["from_value"], 10)
            self.assertEqual(plan[0]["backtest_eval"], str(passed))


def _write_walk_forward_eval(
    path: Path, *, parameter: str, to_value, in_verdict: str, out_verdict: str, status: str, downgrade_to
) -> None:
    path.write_text(
        json.dumps(
            {
                "proposal_id": "operational_cadence_x",
                "evaluations": [
                    {
                        "evaluation_type": "operational_cadence_replay",
                        "changes": [
                            {"parameter": parameter, "from_value": 10, "to_value": to_value}
                        ],
                        "verdict": in_verdict,
                        "verdict_reconciliation": {
                            "in_sample_verdict": in_verdict,
                            "out_of_sample_verdict": out_verdict,
                            "agree": in_verdict == out_verdict,
                            "status": status,
                            "downgrade_to": downgrade_to,
                        },
                    }
                ],
                "overall_verdict": in_verdict,
            }
        ),
        encoding="utf-8",
    )


class WalkForwardScreeningDowngradeTests(unittest.TestCase):
    def test_holdout_contradiction_downgrades_pass_to_inconclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eval_holdout.json"
            _write_walk_forward_eval(
                path,
                parameter="buy_scan_shallow_top_k",
                to_value=8,
                in_verdict="pass",
                out_verdict="fail",
                status="holdout_contradicts",
                downgrade_to="inconclusive",
            )

            entry = evals_to_screening([path])[0]
            self.assertEqual(entry["verdict"], "inconclusive")

            # suggest drops the downgraded candidate -> no suggestion reaches the gate
            plan = suggest_candidates(
                evals_to_screening([path]),
                baseline_values={"buy_scan_shallow_top_k": 10},
                date="20260613",
            )
            self.assertEqual(plan, [])

    def test_confirmed_holdout_keeps_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eval_confirmed.json"
            _write_walk_forward_eval(
                path,
                parameter="buy_scan_shallow_top_k",
                to_value=8,
                in_verdict="pass",
                out_verdict="pass",
                status="confirmed",
                downgrade_to=None,
            )

            entry = evals_to_screening([path])[0]
            self.assertEqual(entry["verdict"], "pass")

            plan = suggest_candidates(
                evals_to_screening([path]),
                baseline_values={"buy_scan_shallow_top_k": 10},
                date="20260613",
            )
            self.assertEqual(len(plan), 1)
            self.assertEqual(plan[0]["to_value"], 8)


if __name__ == "__main__":
    unittest.main()
