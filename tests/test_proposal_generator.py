"""Tests for proposal_generator."""
import unittest

from app.tools.proposal_generator import (
    _extract_signals,
    _pick_direction,
    generate_proposal,
    _DIR_CONTINUATION_SHIFT,
    _DIR_RELAX_CORE_THRESHOLD,
    _DIR_TIGHTEN_CORE_RISK,
)
from app.tools.proposal_constraints import validate_proposal_changes


def _make_snapshot(
    *,
    core_deep=4,
    core_final=0,
    core_exec=0,
    dominant_core_reason="passed_count_insufficient",
    rule_contrast_verdict="threshold_only",
    gap_to_threshold=-2.1,
    core_sharpe=-0.356,
    cont_sharpe=1.134,
    ml_confidence="low",
    sell_watch_partial=5,
    sell_watch_rl=2,
    rate_limit_triggered=3,
):
    return {
        "as_of_date": "20260410",
        "account": "mock_test",
        "live_diagnostics": {
            "overnight_tuning": {
                "core_funnel": {
                    "deep_eval": core_deep,
                    "final_candidate": core_final,
                    "executed": core_exec,
                },
                "bottleneck": {
                    "dominant_core_reason": dominant_core_reason,
                    "hint": "",
                },
                "operational_pressure": {
                    "sell_watch_partial": sell_watch_partial,
                    "sell_watch_rate_limit": sell_watch_rl,
                    "rate_limit_triggered": rate_limit_triggered,
                },
                "rescue": {"medians_bps": {}},
            },
            "core_change_decision": {
                "known_facts": {
                    "rule_contrast": {
                        "verdict": rule_contrast_verdict,
                        "gap_to_threshold": gap_to_threshold,
                    }
                },
                "decision_read": {"recommended": "direction_1"},
            },
        },
        "backtest_baselines": {
            "core_family_approx": {
                "performance": {"sharpe": core_sharpe, "total_return": -2.3, "max_drawdown": 9.1}
            },
            "continuation_family_approx": {
                "performance": {"sharpe": cont_sharpe, "total_return": 6.8, "max_drawdown": 5.3}
            },
        },
        "ml_status": {
            "best_label_candidate": "label_positive_30m_net_cost",
            "best_label_confidence": ml_confidence,
        },
    }


class TestExtractSignals(unittest.TestCase):
    def test_extracts_core_funnel(self):
        sig = _extract_signals(_make_snapshot())
        self.assertEqual(sig["core_deep"], 4)
        self.assertEqual(sig["core_final"], 0)
        self.assertEqual(sig["core_exec"], 0)

    def test_extracts_sharpe(self):
        sig = _extract_signals(_make_snapshot())
        self.assertAlmostEqual(sig["core_sharpe"], -0.356)
        self.assertAlmostEqual(sig["cont_sharpe"], 1.134)

    def test_missing_backtest_returns_none(self):
        snap = _make_snapshot()
        snap["backtest_baselines"] = {}
        sig = _extract_signals(snap)
        self.assertIsNone(sig["core_sharpe"])
        self.assertIsNone(sig["cont_sharpe"])


class TestPickDirection(unittest.TestCase):
    def test_continuation_shift_when_cont_clearly_better(self):
        sig = _extract_signals(_make_snapshot(core_sharpe=-0.356, cont_sharpe=1.134))
        direction = _pick_direction(sig)
        self.assertEqual(direction, _DIR_CONTINUATION_SHIFT)

    def test_relax_threshold_when_close_sharpes(self):
        # cont only slightly better — should fall through to threshold logic
        sig = _extract_signals(_make_snapshot(
            core_sharpe=0.3, cont_sharpe=0.5,
            dominant_core_reason="passed_count_insufficient",
            rule_contrast_verdict="threshold_pressure",
        ))
        direction = _pick_direction(sig)
        self.assertEqual(direction, _DIR_RELAX_CORE_THRESHOLD)

    def test_tighten_risk_when_profit_buffer(self):
        sig = _extract_signals(_make_snapshot(
            core_sharpe=0.3, cont_sharpe=0.5,
            dominant_core_reason="profit_buffer_insufficient",
        ))
        direction = _pick_direction(sig)
        self.assertEqual(direction, _DIR_TIGHTEN_CORE_RISK)

    def test_continuation_shift_requires_both_conditions(self):
        # cont_sharpe > core_sharpe + 0.3 AND cont_sharpe > 0
        sig = _extract_signals(_make_snapshot(core_sharpe=-0.5, cont_sharpe=-0.1))
        direction = _pick_direction(sig)
        # cont_sharpe not > 0, so should NOT pick continuation_shift
        self.assertNotEqual(direction, _DIR_CONTINUATION_SHIFT)


class TestGenerateProposal(unittest.TestCase):
    def test_proposal_has_required_fields(self):
        proposal = generate_proposal(_make_snapshot())
        self.assertIn("proposal_id", proposal)
        self.assertIn("direction", proposal)
        self.assertIn("changes", proposal)
        self.assertIn("status", proposal)
        self.assertIn("constraint_violations", proposal)

    def test_valid_proposal_passes_constraints(self):
        proposal = generate_proposal(_make_snapshot())
        self.assertEqual(proposal["status"], "proposed")
        self.assertEqual(len(proposal["constraint_violations"]), 0)

    def test_changes_pass_constraint_validation(self):
        proposal = generate_proposal(_make_snapshot())
        violations = validate_proposal_changes(proposal["changes"])
        self.assertEqual(violations, [])

    def test_continuation_direction_targets_correct_family(self):
        # With cont clearly better, direction = continuation_shift
        proposal = generate_proposal(_make_snapshot())
        self.assertEqual(proposal["direction"], _DIR_CONTINUATION_SHIFT)
        for change in proposal["changes"]:
            self.assertEqual(change["family"], "continuation_family_approx")

    def test_reasoning_summary_not_empty(self):
        proposal = generate_proposal(_make_snapshot())
        self.assertGreater(len(proposal["reasoning_summary"]), 0)

    def test_risk_flags_include_ml_low_confidence(self):
        proposal = generate_proposal(_make_snapshot(ml_confidence="low"))
        flags = " ".join(proposal["risk_flags"]).lower()
        self.assertIn("ml", flags)

    def test_max_one_change_for_continuation_shift(self):
        proposal = generate_proposal(_make_snapshot())
        if proposal["direction"] == _DIR_CONTINUATION_SHIFT:
            self.assertLessEqual(len(proposal["changes"]), 3)

    def test_proposal_id_is_unique(self):
        import time
        p1 = generate_proposal(_make_snapshot())
        time.sleep(0.01)
        p2 = generate_proposal(_make_snapshot())
        self.assertNotEqual(p1["proposal_id"], p2["proposal_id"])

    def test_missing_backtest_data_does_not_crash(self):
        snap = _make_snapshot()
        snap["backtest_baselines"] = {}
        proposal = generate_proposal(snap)
        self.assertIn("proposal_id", proposal)


if __name__ == "__main__":
    unittest.main()
