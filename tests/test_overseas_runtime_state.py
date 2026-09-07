"""Tests for app/overseas_runtime/state.py — Phase 6: ET-dated runtime state writer."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

US_EASTERN = ZoneInfo("America/New_York")
OPEN_NOW = datetime(2026, 6, 10, 10, 0, 0, tzinfo=US_EASTERN)


class TestNewOverseasRuntimeState(unittest.TestCase):
    """Test 1: new_overseas_runtime_state() is trusted and has empty recent_orders."""

    def test_new_state_is_trusted_with_empty_orders(self):
        from app.overseas_runtime.state import new_overseas_runtime_state
        from app.runtime_state import is_runtime_state_trusted

        state = new_overseas_runtime_state()
        self.assertTrue(is_runtime_state_trusted(state))
        self.assertEqual(state["recent_orders"], [])


class TestRecordOverseasOrderInState(unittest.TestCase):
    """Test 2: record_overseas_order_in_state appends a guard-schema entry."""

    def test_record_buy_appends_entry(self):
        from app.overseas_runtime.state import new_overseas_runtime_state, record_overseas_order_in_state

        state = new_overseas_runtime_state()
        record_overseas_order_in_state(state, side="buy", symbol="AAPL", qty=3, now=OPEN_NOW)

        self.assertEqual(len(state["recent_orders"]), 1)
        entry = state["recent_orders"][0]
        self.assertEqual(entry["side"], "BUY")
        self.assertEqual(entry["symbol"], "AAPL")
        self.assertEqual(entry["qty"], 3)
        self.assertEqual(entry["action"], "order_submitted")
        self.assertEqual(entry["date"], OPEN_NOW.date().isoformat())


if __name__ == "__main__":
    unittest.main()
