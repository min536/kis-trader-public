from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.core.costs import calc_net_pnl
from app.portfolio.trade_records import build_closed_trade_records

_SETTINGS = SimpleNamespace(
    buy_fee_bps=15, buy_slippage_bps=5,
    sell_fee_bps=15, sell_tax_bps=15, sell_slippage_bps=5,
)


def _sell_record(*, symbol="005930", qty=10, sell_price=73_000, avg_cost=70_000,
                 trigger="take_profit", environment="mock"):
    return {
        "action": "sell_order_succeeded",
        "environment": environment,
        "symbol": symbol,
        "qty": qty,
        "timestamp": "2026-06-11T15:17:21+09:00",
        "raw_response": {
            "trigger": trigger,
            "sell_plan": {"qty": qty, "current_price_krw": sell_price, "notional_krw": sell_price * qty},
            "sell_strategy_details": {"details": {"average_cost": avg_cost}},
        },
    }


class BuildClosedTradeRecordsTests(unittest.TestCase):
    def test_self_contained_sell_record_maps_to_closed_trade(self) -> None:
        records = build_closed_trade_records([_sell_record()], settings=_SETTINGS)
        self.assertEqual(len(records), 1)
        trade = records[0]
        self.assertEqual(trade["symbol"], "005930")
        self.assertEqual(trade["sell_trigger"], "take_profit")
        self.assertEqual(trade["buy_notional_krw"], 700_000)
        self.assertEqual(trade["sell_notional_krw"], 730_000)
        self.assertEqual(trade["gross_pnl_krw"], 30_000)
        expected = calc_net_pnl(avg_cost_krw=70_000, current_price_krw=73_000, qty=10, settings=_SETTINGS)
        self.assertEqual(trade["net_pnl_krw"], int(expected["net_pnl_krw"]))

    def test_only_mock_sell_fills_are_counted(self) -> None:
        records = [
            {"action": "order_succeeded", "environment": "mock", "symbol": "X"},  # a buy
            _sell_record(environment="live"),  # not paper
            {"action": "sell_order_failed", "environment": "mock"},  # not a fill
            None,  # malformed
            "garbage",
            _sell_record(symbol="000660"),  # the one valid trade
        ]
        trades = build_closed_trade_records(records, settings=_SETTINGS)
        self.assertEqual([t["symbol"] for t in trades], ["000660"])

    def test_missing_trigger_is_unknown_and_invalid_priced_records_skipped(self) -> None:
        no_trigger = _sell_record(trigger="")
        zero_cost = _sell_record(symbol="000660", avg_cost=0)  # invalid cost basis
        trades = build_closed_trade_records([no_trigger, zero_cost], settings=_SETTINGS)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["sell_trigger"], "unknown")


if __name__ == "__main__":
    unittest.main()
