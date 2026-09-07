"""Tests for app.research.replay.sizing (R3)."""

import unittest

from app.research.replay.sizing import (
    DEFAULT_SIZING_TIERS,
    SizingTier,
    apply_budget_caps,
    score_to_budget_multiplier,
)


class DefaultTiersTest(unittest.TestCase):
    def test_default_sizing_tiers_literal(self):
        self.assertEqual(
            DEFAULT_SIZING_TIERS,
            (
                SizingTier(min_score=60.0, multiplier=0.5),
                SizingTier(min_score=70.0, multiplier=1.0),
                SizingTier(min_score=85.0, multiplier=1.25),
            ),
        )


class ScoreToBudgetMultiplierTest(unittest.TestCase):
    def test_boundary_cases_stepped_curve(self):
        cases = [
            (0.0, 0.0),
            (59.99, 0.0),
            (60.0, 0.5),
            (69.99, 0.5),
            (70.0, 1.0),
            (84.99, 1.0),
            (85.0, 1.25),
            (100.0, 1.25),
        ]
        for score, expected in cases:
            with self.subTest(score=score):
                self.assertEqual(score_to_budget_multiplier(score), expected)


class ApplyBudgetCapsTest(unittest.TestCase):
    def test_caps_take_minimum_of_budget_cash_and_max(self):
        cases = [
            # (budget, cash, max_budget, expected)
            (1000.0, 5000.0, 2000.0, 1000.0),  # budget is smallest
            (5000.0, 3000.0, 2000.0, 2000.0),  # max_budget binds
            (5000.0, 1500.0, 2000.0, 1500.0),  # cash binds
            (0.0, 100.0, 100.0, 0.0),  # zero budget stays zero
        ]
        for budget, cash, max_budget, expected in cases:
            with self.subTest(budget=budget, cash=cash, max_budget=max_budget):
                self.assertEqual(
                    apply_budget_caps(budget, cash=cash, max_budget=max_budget),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
