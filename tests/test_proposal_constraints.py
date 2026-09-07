"""Tests for proposal_constraints."""
import unittest

from app.tools.proposal_constraints import (
    MAX_PARAMS_PER_PROPOSAL,
    ConstraintViolation,
    get_param,
    params_for_family,
    validate_proposal_changes,
)


class TestParamRegistry(unittest.TestCase):
    def test_core_params_exist(self):
        core = params_for_family("core_family_approx")
        ids = [p.param_id for p in core]
        self.assertIn("rsi_entry_lower", ids)
        self.assertIn("stop_loss_pct", ids)
        self.assertIn("take_profit_pct", ids)

    def test_continuation_params_exist(self):
        cont = params_for_family("continuation_family_approx")
        ids = [p.param_id for p in cont]
        self.assertIn("rsi_entry_lower", ids)
        self.assertIn("extension_cap_scalar", ids)

    def test_get_param_found(self):
        spec = get_param("core_family_approx", "rsi_entry_lower")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.current_value, 48.0)

    def test_get_param_not_found(self):
        self.assertIsNone(get_param("core_family_approx", "nonexistent"))

    def test_ranges_are_consistent(self):
        from app.tools.proposal_constraints import ALL_PARAMS
        for key, spec in ALL_PARAMS.items():
            self.assertLessEqual(spec.min_val, spec.current_value, msg=key)
            self.assertLessEqual(spec.current_value, spec.max_val, msg=key)
            self.assertGreater(spec.max_step, 0, msg=key)


class TestValidateProposalChanges(unittest.TestCase):
    def _valid_change(self):
        return {
            "family": "core_family_approx",
            "param_id": "rsi_entry_lower",
            "new_value": 46.0,
        }

    def test_valid_single_change(self):
        violations = validate_proposal_changes([self._valid_change()])
        self.assertEqual(violations, [])

    def test_out_of_range_low(self):
        violations = validate_proposal_changes([
            {"family": "core_family_approx", "param_id": "rsi_entry_lower", "new_value": 10.0}
        ])
        rules = [v.rule for v in violations]
        self.assertIn("out_of_range", rules)

    def test_out_of_range_high(self):
        violations = validate_proposal_changes([
            {"family": "core_family_approx", "param_id": "rsi_entry_lower", "new_value": 99.0}
        ])
        rules = [v.rule for v in violations]
        self.assertIn("out_of_range", rules)

    def test_step_too_large(self):
        # rsi_entry_lower max_step=4.0, current=48; change of 10 should fail
        violations = validate_proposal_changes([
            {"family": "core_family_approx", "param_id": "rsi_entry_lower", "new_value": 38.0}
        ])
        rules = [v.rule for v in violations]
        self.assertIn("step_too_large", rules)

    def test_step_at_boundary_passes(self):
        # Exactly max_step=4.0 should pass
        violations = validate_proposal_changes([
            {"family": "core_family_approx", "param_id": "rsi_entry_lower", "new_value": 44.0}
        ])
        self.assertEqual(violations, [])

    def test_too_many_params(self):
        changes = [
            {"family": "core_family_approx", "param_id": "rsi_entry_lower", "new_value": 46.0},
            {"family": "core_family_approx", "param_id": "stop_loss_pct", "new_value": 3.0},
            {"family": "core_family_approx", "param_id": "take_profit_pct", "new_value": 8.0},
            {"family": "core_family_approx", "param_id": "trailing_stop_pct", "new_value": 2.0},
        ]
        violations = validate_proposal_changes(changes)
        rules = [v.rule for v in violations]
        self.assertIn("max_params_per_proposal", rules)

    def test_exactly_max_params_ok(self):
        # 3 params from different conflict groups within the same family
        changes = [
            {"family": "core_family_approx", "param_id": "rsi_entry_lower", "new_value": 46.0},
            {"family": "core_family_approx", "param_id": "stop_loss_pct", "new_value": 3.0},
            {"family": "core_family_approx", "param_id": "sma_trend_period", "new_value": 20.0},
        ]
        violations = validate_proposal_changes(changes)
        self.assertEqual(violations, [])

    def test_conflict_group_violation(self):
        # rsi_entry_lower and rsi_entry_upper share conflict_group "rsi_entry"
        violations = validate_proposal_changes([
            {"family": "core_family_approx", "param_id": "rsi_entry_lower", "new_value": 46.0},
            {"family": "core_family_approx", "param_id": "rsi_entry_upper", "new_value": 74.0},
        ])
        rules = [v.rule for v in violations]
        self.assertIn("conflict_group", rules)

    def test_unknown_param(self):
        violations = validate_proposal_changes([
            {"family": "core_family_approx", "param_id": "fake_param", "new_value": 1.0}
        ])
        rules = [v.rule for v in violations]
        self.assertIn("unknown_param", rules)

    def test_missing_new_value(self):
        violations = validate_proposal_changes([
            {"family": "core_family_approx", "param_id": "rsi_entry_lower"}
        ])
        rules = [v.rule for v in violations]
        self.assertIn("missing_new_value", rules)

    def test_different_families_no_conflict(self):
        # Same param_id in different families — no conflict
        violations = validate_proposal_changes([
            {"family": "core_family_approx", "param_id": "rsi_entry_lower", "new_value": 46.0},
            {"family": "continuation_family_approx", "param_id": "rsi_entry_lower", "new_value": 53.0},
        ])
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
