"""Tests for app/overseas_execution/sell_flow.py — Phase 5 orchestration."""
import types
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

US_EASTERN = ZoneInfo("America/New_York")

# Fixed "market open" ET datetime (Wednesday 2026-06-10 10:00 ET)
OPEN_NOW = datetime(2026, 6, 10, 10, 0, 0, tzinfo=US_EASTERN)


def _make_policy(**overrides):
    from app.overseas_execution.policy import OverseasFlowPolicy
    defaults = dict(
        max_budget_per_trade_usd=5000.0,
        max_account_exposure_pct=20.0,
        max_qty_per_trade=50,
    )
    defaults.update(overrides)
    return OverseasFlowPolicy(**defaults)


def _make_stub_submit(symbol="AAPL"):
    calls = []

    def stub(**kwargs):
        calls.append(kwargs)
        return {"action": "overseas_order_submitted", "symbol": kwargs.get("symbol", symbol)}

    stub.calls = calls
    return stub


class TestSellFlow_NoHolding(unittest.TestCase):
    """Test 8: holding_qty=0 → blocked_no_holding, submit not called."""

    def test_blocked_when_no_holding(self):
        from app.overseas_execution.sell_flow import run_overseas_sell_flow

        submit = _make_stub_submit()
        policy = _make_policy()

        result = run_overseas_sell_flow(
            symbol="AAPL",
            holding_qty=0,
            trigger="stop_loss",
            quote_price=150.0,
            policy=policy,
            state={"recent_orders": []},
            risk_log_path="/tmp/_nonexistent_overseas_orders.jsonl",
            market_open=True,
            confirm_sell=True,
            now=OPEN_NOW,
            submit_order=submit,
        )

        self.assertEqual(result.action, "blocked_no_holding")
        self.assertEqual(len(submit.calls), 0)
        self.assertEqual(result.qty, 0)
        self.assertIsNone(result.sizing)


class TestSellFlow_UnknownTrigger(unittest.TestCase):
    """Test 9: unknown trigger → sell_sizing returns qty=0 → blocked_zero_qty."""

    def test_blocked_on_unknown_trigger(self):
        from app.overseas_execution.sell_flow import run_overseas_sell_flow

        submit = _make_stub_submit()
        policy = _make_policy()

        result = run_overseas_sell_flow(
            symbol="AAPL",
            holding_qty=10,
            trigger="UNKNOWN_TRIGGER",
            quote_price=150.0,
            policy=policy,
            state={"recent_orders": []},
            risk_log_path="/tmp/_nonexistent_overseas_orders.jsonl",
            market_open=True,
            confirm_sell=True,
            now=OPEN_NOW,
            submit_order=submit,
        )

        self.assertEqual(result.action, "blocked_zero_qty")
        self.assertEqual(len(submit.calls), 0)
        self.assertIsNotNone(result.sizing)


class TestSellFlow_OrderGuardBlock(unittest.TestCase):
    """Test 10: recent SELL within cooldown + order_cooldown_minutes>0 → blocked_order_guard."""

    def test_blocked_by_order_guard(self):
        from app.overseas_execution.sell_flow import run_overseas_sell_flow

        submit = _make_stub_submit()
        # order_cooldown_minutes=30: a SELL within 30 min → blocked
        policy = _make_policy(order_cooldown_minutes=30)

        # A pending sell intent that covers all 10 shares → sellable_residual_qty=0 → blocked.
        # stop_loss bypasses cooldown checks, but pending-intent guard fires before cooldown.
        state = {
            "recent_orders": [],
            "pending_sell_intents_by_symbol": {"AAPL": {"qty": 10}},
        }

        result = run_overseas_sell_flow(
            symbol="AAPL",
            holding_qty=10,
            trigger="stop_loss",
            quote_price=150.0,
            policy=policy,
            state=state,
            risk_log_path="/tmp/_nonexistent_overseas_orders.jsonl",
            market_open=True,
            confirm_sell=True,
            now=OPEN_NOW,
            submit_order=submit,
        )

        self.assertEqual(result.action, "blocked_order_guard")
        self.assertEqual(len(submit.calls), 0)


class TestSellFlow_Preview(unittest.TestCase):
    """Test 11: confirm_sell=False → preview, submit not called, sizing+risk present."""

    def test_preview_when_confirm_off(self):
        from app.overseas_execution.sell_flow import run_overseas_sell_flow

        submit = _make_stub_submit()
        policy = _make_policy()

        result = run_overseas_sell_flow(
            symbol="AAPL",
            holding_qty=10,
            trigger="stop_loss",
            quote_price=150.0,
            policy=policy,
            state={"recent_orders": []},
            risk_log_path="/tmp/_nonexistent_overseas_orders.jsonl",
            market_open=True,
            confirm_sell=False,
            now=OPEN_NOW,
            submit_order=submit,
        )

        self.assertEqual(result.action, "preview")
        self.assertEqual(len(submit.calls), 0)
        self.assertIsNotNone(result.sizing)
        self.assertIsNotNone(result.risk)


class TestSellFlow_HappyPath(unittest.TestCase):
    """Test 12: stop_loss, holding 10, confirm_sell=True → submitted, qty==10, side="sell"."""

    def test_happy_path_stop_loss(self):
        from app.overseas_execution.sell_flow import run_overseas_sell_flow

        notify_calls = []

        def notify_stub(**kwargs):
            notify_calls.append(kwargs)

        submit = _make_stub_submit()
        policy = _make_policy()

        result = run_overseas_sell_flow(
            symbol="AAPL",
            holding_qty=10,
            trigger="stop_loss",
            quote_price=150.0,
            policy=policy,
            state={"recent_orders": []},
            risk_log_path="/tmp/_nonexistent_overseas_orders.jsonl",
            market_open=True,
            confirm_sell=True,
            now=OPEN_NOW,
            submit_order=submit,
            notify=notify_stub,
        )

        self.assertEqual(result.action, "submitted")
        self.assertEqual(result.qty, 10)  # full exit for stop_loss
        self.assertEqual(len(submit.calls), 1)
        call = submit.calls[0]
        self.assertEqual(call["side"], "sell")
        self.assertEqual(call["symbol"], "AAPL")
        self.assertEqual(call["qty"], 10)
        self.assertIsNotNone(result.order_record)
        self.assertEqual(len(notify_calls), 1)


class TestSellFlow_LimitOffset(unittest.TestCase):
    """Test: sell limit offset — quote 150, offset_bps=10 → unit_price == 150*(1-0.001)=149.85."""

    def test_sell_unit_price_reflects_offset(self):
        from app.overseas_execution.sell_flow import run_overseas_sell_flow

        policy = _make_policy(limit_offset_bps=10.0)
        submit = _make_stub_submit()

        result = run_overseas_sell_flow(
            symbol="AAPL",
            holding_qty=10,
            trigger="stop_loss",
            quote_price=150.0,
            policy=policy,
            state={"recent_orders": []},
            risk_log_path="/tmp/_nonexistent_overseas_orders.jsonl",
            market_open=True,
            confirm_sell=False,
            now=OPEN_NOW,
            submit_order=submit,
        )

        # sell offset: price = 150 * (1 - 10/10000) = 150 * 0.999 = 149.85
        self.assertAlmostEqual(result.unit_price, 149.85, places=5)
