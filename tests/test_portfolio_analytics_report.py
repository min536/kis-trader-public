from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.portfolio.analytics import compute_portfolio_analytics
from app.reporting.portfolio_analytics_report import (
    load_portfolio_analytics,
    render_portfolio_analytics_lines,
)

_SETTINGS = SimpleNamespace(
    buy_fee_bps=15, buy_slippage_bps=5,
    sell_fee_bps=15, sell_tax_bps=15, sell_slippage_bps=5,
)


def _trade(symbol, trigger, net):
    return {
        "symbol": symbol, "sell_trigger": trigger, "hold_days": 0.0,
        "buy_notional_krw": 1_000_000, "sell_notional_krw": 1_000_000 + net,
        "gross_pnl_krw": net, "net_pnl_krw": net,
    }


class RenderPortfolioAnalyticsLinesTests(unittest.TestCase):
    def test_lines_carry_total_and_per_symbol_and_per_trigger(self) -> None:
        analytics = compute_portfolio_analytics([
            _trade("005930", "take_profit", 25_000),
            _trade("000660", "stop_loss", -8_000),
            _trade("005930", "take_profit", 5_000),
        ])
        lines = render_portfolio_analytics_lines(analytics)
        text = "\n".join(lines)
        self.assertIn("005930", text)
        self.assertIn("take_profit", text)
        self.assertIn("stop_loss", text)
        # total net = 25000 - 8000 + 5000 = 22000
        self.assertIn("22,000", text)
        # all-time / cumulative — must not read as "today's"
        self.assertIn("to date", text)

    def test_empty_analytics_returns_no_lines(self) -> None:
        analytics = compute_portfolio_analytics([])
        self.assertEqual(render_portfolio_analytics_lines(analytics), [])


class LoadPortfolioAnalyticsTests(unittest.TestCase):
    def test_reads_order_log_into_analytics(self) -> None:
        sell = {
            "action": "sell_order_succeeded", "environment": "mock",
            "symbol": "005930", "qty": 10, "timestamp": "2026-06-11T15:00:00+09:00",
            "raw_response": {
                "trigger": "take_profit",
                "sell_plan": {"qty": 10, "current_price_krw": 73_000},
                "sell_strategy_details": {"details": {"average_cost": 70_000}},
            },
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "orders.jsonl"
            path.write_text(json.dumps(sell) + "\njunk line\n", encoding="utf-8")
            with mock.patch(
                "app.reporting.portfolio_analytics_report.get_order_log_path",
                return_value=path,
            ):
                analytics = load_portfolio_analytics(_SETTINGS)
        self.assertEqual(analytics.total.trade_count, 1)
        self.assertEqual(analytics.by_symbol["005930"].gross_pnl_krw, 30_000)

    def test_oversize_log_degrades_to_empty_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "orders.jsonl"
            path.write_text("x" * 200 + "\n", encoding="utf-8")
            with mock.patch(
                "app.reporting.portfolio_analytics_report.get_order_log_path",
                return_value=path,
            ), mock.patch.dict("os.environ", {"KIS_LOCAL_READ_MAX_BYTES": "10"}):
                analytics = load_portfolio_analytics(_SETTINGS)
        self.assertEqual(analytics.total.trade_count, 0)


if __name__ == "__main__":
    unittest.main()
