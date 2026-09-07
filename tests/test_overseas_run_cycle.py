"""Tests for app/overseas_runtime/run_cycle.py — Phase 6: SELL-then-BUY orchestrator."""
import types
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

US_EASTERN = ZoneInfo("America/New_York")
# Wednesday 2026-06-10 10:00 ET — regular session
OPEN_NOW = datetime(2026, 6, 10, 10, 0, 0, tzinfo=US_EASTERN)
# Saturday — closed
CLOSED_NOW = datetime(2026, 6, 14, 10, 0, 0, tzinfo=US_EASTERN)

BUY_SYMBOLS = ["AAPL", "MSFT"]

PRICE_DETAIL = {
    "symb": None,  # filled per symbol
    "last": "97",
    "open": "100",
    "high": "100",
    "low": "96",
    "base": "100",
    "rate": "-3",
}


def _price_detail(sym, exc):
    d = dict(PRICE_DETAIL)
    d["symb"] = sym
    return d


def _make_orderable():
    return types.SimpleNamespace(max_order_qty=100, orderable_cash=100_000.0)


def _fetch_orderable(sym, price, exc):
    return _make_orderable()


def _make_policy():
    from app.overseas_execution.policy import OverseasFlowPolicy
    return OverseasFlowPolicy(
        max_budget_per_trade_usd=5000.0,
        max_account_exposure_pct=20.0,
        max_qty_per_trade=50,
    )


def _make_submit():
    calls = []

    def stub(**kwargs):
        calls.append(dict(kwargs))
        return {"action": "overseas_order_submitted", "symbol": kwargs.get("symbol")}

    stub.calls = calls
    return stub


def _low_threshold_us_artifact():
    """``load_us_artifact()`` but with ``buy_threshold=0`` — mirrors
    ``test_sim_runner.py``'s ``_low_threshold_artifact()``. Post gate2 score_v2
    normalization fix, ``final_score`` is a weight-normalized [0, 100] average
    (see ``weighted_gate2_score``); PRICE_DETAIL's realistic-but-modest pullback
    scores ~42 against the real US weights (verified empirically), well BELOW
    the production ``score_v2_us_w0.json`` threshold of 60 — several dims (trend/
    macd/mean_reversion/price_velocity/volatility/portfolio_diversification/
    liquidity) have no source in the overseas scorer and are always ``None``
    (0 contribution but full weight in the denominator), which caps the
    reachable ceiling at ~55.8 even with every drivable dim maxed. These tests
    are about cycle ORCHESTRATION (sell-before-buy ordering, market-closed
    blocking, state recording) — not gate2 discrimination — so a 0 threshold
    keeps the real US weights/scoring path exercised while making candidate
    selection deterministic."""
    from app.overseas_stock.selection import load_us_artifact

    base = load_us_artifact()
    return type(base)(
        version=base.version,
        weights=dict(base.weights),
        buy_threshold=0.0,
        normalization_caps=dict(base.normalization_caps),
    )


class TestOverseasRunCycleBuyOnly(unittest.TestCase):
    """Test 3: BUY-only cycle (empty sell_plan) submits buy and records state."""

    def test_buy_only_cycle_submits_and_records(self):
        from app.overseas_runtime.run_cycle import run_overseas_cycle
        from app.overseas_runtime.state import new_overseas_runtime_state

        submit = _make_submit()
        state = new_overseas_runtime_state()
        result = run_overseas_cycle(
            buy_symbols=BUY_SYMBOLS,
            policy=_make_policy(),
            state=state,
            risk_log_path="/tmp/test_risk.log",
            fetch_detail=_price_detail,
            fetch_orderable=_fetch_orderable,
            sell_plan=(),
            confirm=True,
            now=OPEN_NOW,
            market_open=True,
            submit_order=submit,
            artifact=_low_threshold_us_artifact(),
        )

        self.assertIsNotNone(result.selected)
        self.assertIsNotNone(result.buy_result)
        self.assertEqual(result.buy_result.action, "submitted")
        # submit_order called once with side="buy"
        buy_calls = [c for c in submit.calls if c.get("side") == "buy"]
        self.assertEqual(len(buy_calls), 1)
        # state has a BUY entry
        buy_orders = [o for o in state["recent_orders"] if o["side"] == "BUY"]
        self.assertEqual(len(buy_orders), 1)


class TestOverseasRunCycleNoCandidate(unittest.TestCase):
    """Test 5: empty buy_symbols → selected is None, buy_result is None, no buy submit."""

    def test_no_candidate_no_buy(self):
        from app.overseas_runtime.run_cycle import run_overseas_cycle
        from app.overseas_runtime.state import new_overseas_runtime_state

        submit = _make_submit()
        state = new_overseas_runtime_state()
        result = run_overseas_cycle(
            buy_symbols=[],
            policy=_make_policy(),
            state=state,
            risk_log_path="/tmp/test_risk.log",
            fetch_detail=_price_detail,
            fetch_orderable=_fetch_orderable,
            confirm=True,
            now=OPEN_NOW,
            market_open=True,
            submit_order=submit,
        )

        self.assertIsNone(result.selected)
        self.assertIsNone(result.buy_result)
        buy_calls = [c for c in submit.calls if c.get("side") == "buy"]
        self.assertEqual(len(buy_calls), 0)


class TestOverseasRunCycleMarketClosed(unittest.TestCase):
    """Test 6: market closed → buy blocked, any sell blocked, submit never called."""

    def test_market_closed_blocks_all(self):
        from app.overseas_runtime.run_cycle import run_overseas_cycle
        from app.overseas_runtime.state import new_overseas_runtime_state

        submit = _make_submit()
        state = new_overseas_runtime_state()
        sell_plan = [
            {"symbol": "TSLA", "holding_qty": 10, "trigger": "stop_loss", "quote_price": 90.0}
        ]
        result = run_overseas_cycle(
            buy_symbols=BUY_SYMBOLS,
            policy=_make_policy(),
            state=state,
            risk_log_path="/tmp/test_risk.log",
            fetch_detail=_price_detail,
            fetch_orderable=_fetch_orderable,
            sell_plan=sell_plan,
            confirm=True,
            now=CLOSED_NOW,
            market_open=False,
            submit_order=submit,
            artifact=_low_threshold_us_artifact(),
        )

        self.assertIsNotNone(result.buy_result)
        self.assertEqual(result.buy_result.action, "blocked_market_closed")
        self.assertEqual(len(result.sell_results), 1)
        self.assertEqual(result.sell_results[0].action, "blocked_market_closed")
        self.assertEqual(len(submit.calls), 0)


class TestOverseasRunCycleSellThenBuy(unittest.TestCase):
    """Test 4: SELL executes before BUY (call order check)."""

    def test_sell_before_buy_order(self):
        from app.overseas_runtime.run_cycle import run_overseas_cycle
        from app.overseas_runtime.state import new_overseas_runtime_state

        submit = _make_submit()
        state = new_overseas_runtime_state()
        sell_plan = [
            {"symbol": "TSLA", "holding_qty": 10, "trigger": "stop_loss", "quote_price": 90.0}
        ]
        result = run_overseas_cycle(
            buy_symbols=BUY_SYMBOLS,
            policy=_make_policy(),
            state=state,
            risk_log_path="/tmp/test_risk.log",
            fetch_detail=_price_detail,
            fetch_orderable=_fetch_orderable,
            sell_plan=sell_plan,
            confirm=True,
            now=OPEN_NOW,
            market_open=True,
            submit_order=submit,
            artifact=_low_threshold_us_artifact(),
        )

        # Both sell and buy should have been submitted
        self.assertEqual(len(result.sell_results), 1)
        self.assertEqual(result.sell_results[0].action, "submitted")
        self.assertIsNotNone(result.buy_result)
        self.assertEqual(result.buy_result.action, "submitted")

        # sell call comes BEFORE buy call
        sell_calls = [i for i, c in enumerate(submit.calls) if c.get("side") == "sell"]
        buy_calls = [i for i, c in enumerate(submit.calls) if c.get("side") == "buy"]
        self.assertTrue(sell_calls, "expected at least one sell call")
        self.assertTrue(buy_calls, "expected at least one buy call")
        self.assertLess(sell_calls[0], buy_calls[0], "sell must precede buy")


if __name__ == "__main__":
    unittest.main()
