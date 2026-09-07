"""Tests for app.research.replay.broker_sim (R2)."""

import unittest
from datetime import datetime

from app.research.replay.broker_sim import (
    BrokerSimulator,
    SimCostParams,
    SimFill,
    SimPosition,
)


def _costs():
    return SimCostParams(
        buy_fee_bps=5.0,
        sell_fee_bps=5.0,
        sell_tax_bps=23.0,
        buy_slippage_bps=10.0,
        sell_slippage_bps=10.0,
    )


class BuyFillTest(unittest.TestCase):
    def test_buy_fill_literal_arithmetic(self):
        sim = BrokerSimulator(initial_cash=1_000_000.0, costs=_costs())
        ts = datetime(2026, 6, 12, 9, 0)
        fill = sim.buy("A", qty=10, ref_price=1000.0, ts=ts)
        # Deterministic per-spec formula (no rounding):
        #   fill_price = ref * (1 + buy_slippage_bps / 1e4)
        #   gross      = fill_price * qty
        #   fee        = gross * buy_fee_bps / 1e4
        #   cash_after = initial_cash - gross - fee
        exp_fill_price = 1000.0 * (1 + 10.0 / 1e4)
        exp_gross = exp_fill_price * 10
        exp_fee = exp_gross * 5.0 / 1e4
        exp_cash = 1_000_000.0 - exp_gross - exp_fee
        self.assertEqual(
            fill,
            SimFill(
                symbol="A",
                side="BUY",
                qty=10,
                ref_price=1000.0,
                fill_price=exp_fill_price,
                fee=exp_fee,
                tax=0.0,
                ts=ts,
                cash_after=exp_cash,
            ),
        )
        self.assertEqual(sim.cash, exp_cash)
        self.assertEqual(
            sim.positions["A"], SimPosition(qty=10, avg_price=exp_fill_price)
        )


class SellFillTest(unittest.TestCase):
    def test_sell_fill_literal_arithmetic_and_realized_pnl(self):
        sim = BrokerSimulator(initial_cash=1_000_000.0, costs=_costs())
        ts = datetime(2026, 6, 12, 9, 0)
        sim.buy("A", qty=10, ref_price=1000.0, ts=ts)
        cash_after_buy = sim.cash
        buy_fp = 1000.0 * (1 + 10.0 / 1e4)
        sell_ts = datetime(2026, 6, 12, 10, 0)
        fill = sim.sell("A", qty=10, ref_price=1100.0, ts=sell_ts)
        # sell_price = 1100 * (1 - 10/1e4)
        # gross = sell_price * 10
        # fee = gross * 5/1e4 ; tax = gross * 23/1e4
        # proceeds = gross - fee - tax ; cash_after = cash_after_buy + proceeds
        sell_fp = 1100.0 * (1 - 10.0 / 1e4)
        gross = sell_fp * 10
        fee = gross * 5.0 / 1e4
        tax = gross * 23.0 / 1e4
        proceeds = gross - fee - tax
        exp_cash = cash_after_buy + proceeds
        self.assertEqual(
            fill,
            SimFill(
                symbol="A",
                side="SELL",
                qty=10,
                ref_price=1100.0,
                fill_price=sell_fp,
                fee=fee,
                tax=tax,
                ts=sell_ts,
                cash_after=exp_cash,
            ),
        )
        self.assertEqual(sim.cash, exp_cash)
        self.assertNotIn("A", sim.positions)
        exp_realized = proceeds - buy_fp * 10
        self.assertEqual(sim.realized_pnl, exp_realized)

    def test_sell_insufficient_holdings_rejects(self):
        sim = BrokerSimulator(initial_cash=1_000_000.0, costs=_costs())
        ts = datetime(2026, 6, 12, 9, 0)
        sim.buy("A", qty=5, ref_price=1000.0, ts=ts)
        result = sim.sell("A", qty=10, ref_price=1100.0, ts=ts)
        self.assertIsNone(result)
        self.assertEqual(sim.positions["A"].qty, 5)
        self.assertEqual(sim.realized_pnl, 0.0)


class AvgPriceUpdateTest(unittest.TestCase):
    def test_second_buy_updates_weighted_average_price(self):
        sim = BrokerSimulator(initial_cash=1_000_000.0, costs=_costs())
        ts = datetime(2026, 6, 12, 9, 0)
        sim.buy("A", qty=10, ref_price=1000.0, ts=ts)
        sim.buy("A", qty=30, ref_price=2000.0, ts=ts)
        fp1 = 1000.0 * (1 + 10.0 / 1e4)
        fp2 = 2000.0 * (1 + 10.0 / 1e4)
        exp_avg = (fp1 * 10 + fp2 * 30) / 40
        self.assertEqual(sim.positions["A"], SimPosition(qty=40, avg_price=exp_avg))


class EquityTest(unittest.TestCase):
    def test_equity_and_record_equity_curve(self):
        sim = BrokerSimulator(initial_cash=1_000_000.0, costs=_costs())
        ts = datetime(2026, 6, 12, 9, 0)
        sim.buy("A", qty=10, ref_price=1000.0, ts=ts)
        # equity = cash + sum(qty * mark_price)
        mark = {"A": 1200.0}
        exp_equity = sim.cash + 10 * 1200.0
        self.assertEqual(sim.equity(mark), exp_equity)
        t2 = datetime(2026, 6, 12, 10, 0)
        sim.record_equity(t2, mark)
        self.assertEqual(sim.equity_curve, [(t2, exp_equity)])


class BuyRejectionTest(unittest.TestCase):
    def test_insufficient_cash_rejects_with_none_no_negative_cash(self):
        sim = BrokerSimulator(initial_cash=100.0, costs=_costs())
        # Need 10 * ~1001 + fee >> 100 cash -> hard reject.
        result = sim.buy("A", qty=10, ref_price=1000.0, ts=datetime(2026, 6, 12, 9, 0))
        self.assertIsNone(result)
        self.assertEqual(sim.cash, 100.0)
        self.assertEqual(sim.positions, {})
        self.assertEqual(sim.fills, [])


if __name__ == "__main__":
    unittest.main()
