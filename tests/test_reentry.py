import unittest
from datetime import datetime
from types import SimpleNamespace

from app.core.time_utils import KOREA_TZ
from app.strategy.reentry import evaluate_reentry_eligibility


class ReentryEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base_runtime_state = {
            "last_exit_reason_by_symbol": {"051910": "stop_loss"},
            "last_exit_at_by_symbol": {
                "051910": datetime(2026, 4, 13, 9, 29, tzinfo=KOREA_TZ).isoformat()
            },
            "last_exit_price_by_symbol": {"051910": 310000},
            "buy_entries_by_symbol_today": {},
            "current_regime": "NORMAL",
        }
        self.settings = SimpleNamespace(
            rebuy_cooldown_minutes=30,
            stop_loss_same_day_reentry_min_minutes=120,
            same_symbol_max_buys_per_day=3,
        )
        self.fresh_candidate = SimpleNamespace(
            candidate=True,
            passed_count=4,
            enabled_count=5,
            score=1.2,
            cost_block_reason="",
        )
        self.snapshot = {
            "current_price": 312000,
            "open_price": 309000,
            "prev_day_change_pct": -0.2,
        }

    def test_same_day_stop_loss_reentry_is_blocked_before_floor_even_with_fresh_setup(self) -> None:
        result = evaluate_reentry_eligibility(
            symbol="051910",
            runtime_state=self.base_runtime_state,
            settings=self.settings,
            regime_state={"current_regime": "NORMAL"},
            daily_pnl_brake_state=None,
            current_snapshot=self.snapshot,
            residual_position_qty=0,
            candidate=self.fresh_candidate,
            now=datetime(2026, 4, 13, 10, 45, tzinfo=KOREA_TZ),
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.state, "blocked_same_day_stop_loss_reentry")
        self.assertEqual(result.cooldown_minutes_effective, 120)

    def test_same_day_stop_loss_reentry_can_resume_after_floor_with_fresh_setup(self) -> None:
        result = evaluate_reentry_eligibility(
            symbol="051910",
            runtime_state=self.base_runtime_state,
            settings=self.settings,
            regime_state={"current_regime": "NORMAL"},
            daily_pnl_brake_state=None,
            current_snapshot=self.snapshot,
            residual_position_qty=0,
            candidate=self.fresh_candidate,
            now=datetime(2026, 4, 13, 11, 35, tzinfo=KOREA_TZ),
        )

        self.assertTrue(result.allowed)
        self.assertEqual(result.state, "allowed_fresh_setup_after_stop")


if __name__ == "__main__":
    unittest.main()
