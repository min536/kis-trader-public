"""Tests for app/overseas_execution/buy_flow.py — Phase 5 orchestration."""
import types
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

US_EASTERN = ZoneInfo("America/New_York")


def _make_candidate(symbol="AAPL", price=150.0):
    return types.SimpleNamespace(
        symbol=symbol,
        exchange="NASD",
        snapshot=types.SimpleNamespace(current_price=price),
        final_score=300.0,
    )


def _make_orderable(orderable_cash=100_000.0, max_order_qty=100):
    return types.SimpleNamespace(max_order_qty=max_order_qty, orderable_cash=orderable_cash)


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


def _make_stub_fetch(orderable):
    def stub(sym, price, exchange):
        return orderable
    return stub


# Fixed "market open" ET datetime (Wednesday 2026-06-10 10:00 ET)
OPEN_NOW = datetime(2026, 6, 10, 10, 0, 0, tzinfo=US_EASTERN)
# Fixed "market closed" ET datetime (Saturday)
CLOSED_NOW = datetime(2026, 6, 14, 10, 0, 0, tzinfo=US_EASTERN)


class TestOverseasFlowPolicy(unittest.TestCase):
    """Test that OverseasFlowPolicy is a frozen dataclass."""

    def test_policy_is_frozen(self):
        from app.overseas_execution.policy import OverseasFlowPolicy
        p = OverseasFlowPolicy(max_budget_per_trade_usd=5000.0, max_account_exposure_pct=20.0, max_qty_per_trade=50)
        with self.assertRaises((AttributeError, TypeError)):
            p.max_budget_per_trade_usd = 9999.0  # type: ignore[misc]

    def test_policy_defaults(self):
        """OverseasFlowPolicy defaults match spec."""
        from app.overseas_execution.policy import OverseasFlowPolicy
        p = OverseasFlowPolicy(max_budget_per_trade_usd=1.0, max_account_exposure_pct=5.0, max_qty_per_trade=10)
        self.assertIsNone(p.account_equity_usd)
        self.assertEqual(p.limit_offset_bps, 0.0)
        self.assertFalse(p.risk_enabled is False)  # risk_enabled defaults True
        self.assertEqual(p.max_orders, 0)
        self.assertEqual(p.max_notional_usd, 0.0)


class TestBuyFlow_MarketClosed(unittest.TestCase):
    """Test 1: market_open=False → blocked_market_closed, submit not called."""

    def test_blocked_when_market_closed(self):
        from app.overseas_execution.buy_flow import run_overseas_buy_flow

        submit = _make_stub_submit()
        policy = _make_policy()
        candidate = _make_candidate()
        orderable = _make_orderable()

        result = run_overseas_buy_flow(
            candidate=candidate,
            policy=policy,
            state={"recent_orders": []},
            risk_log_path="/tmp/_nonexistent_overseas_orders.jsonl",
            fetch_orderable=_make_stub_fetch(orderable),
            market_open=False,
            confirm_buy=True,
            now=OPEN_NOW,
            submit_order=submit,
        )

        self.assertEqual(result.action, "blocked_market_closed")
        self.assertEqual(len(submit.calls), 0)
        self.assertEqual(result.qty, 0)
        self.assertIsNone(result.sizing)
        self.assertIsNone(result.risk)


class TestBuyFlow_ZeroQty(unittest.TestCase):
    """Test 2: fetch_orderable returns orderable_cash=0 → blocked_zero_qty."""

    def test_blocked_when_zero_qty(self):
        from app.overseas_execution.buy_flow import run_overseas_buy_flow

        submit = _make_stub_submit()
        policy = _make_policy()
        candidate = _make_candidate()
        # orderable_cash=0 → cash_cap_qty=0 → recommended_qty=0
        orderable = _make_orderable(orderable_cash=0.0)

        result = run_overseas_buy_flow(
            candidate=candidate,
            policy=policy,
            state={"recent_orders": []},
            risk_log_path="/tmp/_nonexistent_overseas_orders.jsonl",
            fetch_orderable=_make_stub_fetch(orderable),
            market_open=True,
            confirm_buy=True,
            now=OPEN_NOW,
            submit_order=submit,
        )

        self.assertEqual(result.action, "blocked_zero_qty")
        self.assertEqual(len(submit.calls), 0)
        self.assertIsNotNone(result.sizing)


class TestBuyFlow_OrderGuardBlock(unittest.TestCase):
    """Test 3: same-symbol BUY today hits same_symbol_max_buys_per_day=1 → blocked_order_guard."""

    def test_blocked_by_order_guard(self):
        from app.overseas_execution.buy_flow import run_overseas_buy_flow

        submit = _make_stub_submit()
        # Policy with same_symbol_max_buys_per_day=1
        policy = _make_policy(same_symbol_max_buys_per_day=1)
        candidate = _make_candidate()
        orderable = _make_orderable()

        # State: one BUY of AAPL today (ET date 2026-06-10)
        state = {
            "recent_orders": [
                {
                    "side": "BUY",
                    "symbol": "AAPL",
                    "qty": 5,
                    "action": "order_submitted",
                    "date": "2026-06-10",
                    "timestamp": "2026-06-10T10:00:00-04:00",
                }
            ]
        }

        result = run_overseas_buy_flow(
            candidate=candidate,
            policy=policy,
            state=state,
            risk_log_path="/tmp/_nonexistent_overseas_orders.jsonl",
            fetch_orderable=_make_stub_fetch(orderable),
            market_open=True,
            confirm_buy=True,
            now=OPEN_NOW,
            submit_order=submit,
        )

        self.assertEqual(result.action, "blocked_order_guard")
        self.assertEqual(len(submit.calls), 0)


class TestBuyFlow_RiskGuardBlock(unittest.TestCase):
    """Test 4: risk log already has 1 order today + max_orders=1 → blocked_risk_guard."""

    def test_blocked_by_risk_guard(self):
        import json
        import tempfile
        from pathlib import Path
        from app.overseas_execution.buy_flow import run_overseas_buy_flow

        submit = _make_stub_submit()
        # max_orders=1: already 1 order today → blocked
        policy = _make_policy(max_orders=1)
        candidate = _make_candidate()
        orderable = _make_orderable()

        # Write one overseas_order_submitted record with today's ET date
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "overseas_orders.jsonl"
            record = {
                "action": "overseas_order_submitted",
                "timestamp": "2026-06-10T09:45:00-04:00",  # ET: 2026-06-10
                "symbol": "MSFT",
                "qty": 2,
                "unit_price": "200.00",
            }
            log_path.write_text(json.dumps(record) + "\n")

            result = run_overseas_buy_flow(
                candidate=candidate,
                policy=policy,
                state={"recent_orders": []},
                risk_log_path=log_path,
                fetch_orderable=_make_stub_fetch(orderable),
                market_open=True,
                confirm_buy=True,
                now=OPEN_NOW,
                submit_order=submit,
            )

        self.assertEqual(result.action, "blocked_risk_guard")
        self.assertEqual(len(submit.calls), 0)
        self.assertIsNotNone(result.risk)


class TestBuyFlow_Preview(unittest.TestCase):
    """Test 5: confirm_buy=False → preview, submit not called, sizing+risk present."""

    def test_preview_when_confirm_off(self):
        from app.overseas_execution.buy_flow import run_overseas_buy_flow

        submit = _make_stub_submit()
        policy = _make_policy()
        candidate = _make_candidate()
        orderable = _make_orderable()

        result = run_overseas_buy_flow(
            candidate=candidate,
            policy=policy,
            state={"recent_orders": []},
            risk_log_path="/tmp/_nonexistent_overseas_orders.jsonl",
            fetch_orderable=_make_stub_fetch(orderable),
            market_open=True,
            confirm_buy=False,
            now=OPEN_NOW,
            submit_order=submit,
        )

        self.assertEqual(result.action, "preview")
        self.assertEqual(len(submit.calls), 0)
        self.assertIsNotNone(result.sizing)
        self.assertIsNotNone(result.risk)


class TestBuyFlow_HappyPath(unittest.TestCase):
    """Test 6: confirm_buy=True → submitted, stub called once, notify called once."""

    def test_happy_path_submitted(self):
        from app.overseas_execution.buy_flow import run_overseas_buy_flow
        from app.overseas_execution.limit_price import compute_overseas_limit_price

        notify_calls = []

        def notify_stub(**kwargs):
            notify_calls.append(kwargs)

        submit = _make_stub_submit()
        policy = _make_policy()
        candidate = _make_candidate()
        orderable = _make_orderable()
        expected_unit_price = compute_overseas_limit_price(side="buy", quote_price=150.0, offset_bps=0.0)

        result = run_overseas_buy_flow(
            candidate=candidate,
            policy=policy,
            state={"recent_orders": []},
            risk_log_path="/tmp/_nonexistent_overseas_orders.jsonl",
            fetch_orderable=_make_stub_fetch(orderable),
            market_open=True,
            confirm_buy=True,
            now=OPEN_NOW,
            submit_order=submit,
            notify=notify_stub,
        )

        self.assertEqual(result.action, "submitted")
        self.assertEqual(len(submit.calls), 1)
        call = submit.calls[0]
        self.assertEqual(call["side"], "buy")
        self.assertEqual(call["symbol"], "AAPL")
        self.assertEqual(call["unit_price"], expected_unit_price)
        self.assertGreater(call["qty"], 0)
        self.assertIsNotNone(result.order_record)
        self.assertEqual(len(notify_calls), 1)


class TestBuyFlow_LimitOffset(unittest.TestCase):
    """Test 7: limit_offset_bps=10, quote 150 → unit_price == 150.15 on result."""

    def test_unit_price_reflects_offset(self):
        from app.overseas_execution.buy_flow import run_overseas_buy_flow

        policy = _make_policy(limit_offset_bps=10.0)
        candidate = _make_candidate(price=150.0)
        orderable = _make_orderable()
        submit = _make_stub_submit()

        result = run_overseas_buy_flow(
            candidate=candidate,
            policy=policy,
            state={"recent_orders": []},
            risk_log_path="/tmp/_nonexistent_overseas_orders.jsonl",
            fetch_orderable=_make_stub_fetch(orderable),
            market_open=True,
            confirm_buy=False,
            now=OPEN_NOW,
            submit_order=submit,
        )

        # 150 * (1 + 10/10000) = 150 * 1.001 = 150.15
        self.assertAlmostEqual(result.unit_price, 150.15, places=5)
