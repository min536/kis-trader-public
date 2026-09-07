"""Tests for app/overseas_execution/order_guard.py (ET-clock wrappers)."""
import unittest


class TestEvaluateOverseasBuyGuard(unittest.TestCase):
    """Tests 4-5: evaluate_overseas_buy_guard."""

    def test_buy_guard_benign_args_returns_allowed(self) -> None:
        from app.overseas_execution.order_guard import evaluate_overseas_buy_guard

        state = {"recent_orders": []}
        result = evaluate_overseas_buy_guard(
            state=state,
            symbol="AAPL",
            qty=1,
            block_rebuy_symbols_bought_today=False,
            allow_one_buy_per_symbol_per_day=False,
            rebuy_cooldown_minutes=0,
            same_symbol_max_buys_per_day=0,
            order_cooldown_minutes=0,
            blocked_cooldown_minutes=0,
        )
        self.assertTrue(result.allowed)


    def test_buy_guard_forwards_injected_now_for_daily_limit(self) -> None:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        from app.overseas_execution.order_guard import evaluate_overseas_buy_guard

        US_EASTERN = ZoneInfo("America/New_York")
        # Fixed ET "now": Wed 2026-06-10 10:00 ET
        fixed_now = datetime(2026, 6, 10, 10, 0, 0, tzinfo=US_EASTERN)
        today_date = fixed_now.date().isoformat()

        # One BUY order recorded at 9:55 ET same day
        state = {
            "recent_orders": [
                {
                    "date": today_date,
                    "side": "BUY",
                    "symbol": "AAPL",
                    "qty": 1,
                    "action": "order_submitted",
                    "timestamp": (fixed_now - timedelta(minutes=5)).isoformat(),
                }
            ]
        }

        result = evaluate_overseas_buy_guard(
            state=state,
            symbol="AAPL",
            qty=1,
            block_rebuy_symbols_bought_today=False,
            allow_one_buy_per_symbol_per_day=False,
            rebuy_cooldown_minutes=0,
            same_symbol_max_buys_per_day=1,
            order_cooldown_minutes=0,
            blocked_cooldown_minutes=0,
            now=fixed_now,
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.action, "blocked_buy_same_symbol_daily_limit")


class TestEvaluateOverseasSellGuard(unittest.TestCase):
    """Test 6: evaluate_overseas_sell_guard."""

    def test_sell_guard_benign_args_returns_allowed(self) -> None:
        from app.overseas_execution.order_guard import evaluate_overseas_sell_guard

        state = {"recent_orders": []}
        result = evaluate_overseas_sell_guard(
            state=state,
            symbol="AAPL",
            holding_qty=10,
            qty=1,
            block_resell_symbols_sold_today=False,
            allow_one_sell_trigger_per_symbol_per_day=False,
            order_cooldown_minutes=0,
            blocked_cooldown_minutes=0,
            trigger="take_profit",
        )
        self.assertTrue(result.allowed)


if __name__ == "__main__":
    unittest.main()
