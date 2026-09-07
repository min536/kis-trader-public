"""Unit tests for backtester/engine_backtest/portfolio.py"""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock

from backtester.engine_backtest.portfolio import (
    BacktestPortfolio,
    ClosedTrade,
    _bps,
)


def _settings(
    buy_fee_bps: float = 15.0,
    buy_slippage_bps: float = 5.0,
    sell_fee_bps: float = 15.0,
    sell_tax_bps: float = 20.0,
    sell_slippage_bps: float = 5.0,
) -> MagicMock:
    s = MagicMock()
    s.buy_fee_bps = buy_fee_bps
    s.buy_slippage_bps = buy_slippage_bps
    s.sell_fee_bps = sell_fee_bps
    s.sell_tax_bps = sell_tax_bps
    s.sell_slippage_bps = sell_slippage_bps
    return s


DATE_A = date(2026, 4, 1)
DATE_B = date(2026, 4, 10)


class BpsHelperTests(unittest.TestCase):
    def test_bps_rounds_to_int(self) -> None:
        self.assertEqual(_bps(10_000_000, 15.0), 15_000)
        self.assertEqual(_bps(10_000_000, 20.0), 20_000)

    def test_bps_zero_rate(self) -> None:
        self.assertEqual(_bps(9_999_999, 0.0), 0)

    def test_bps_fractional(self) -> None:
        # 7 bps on 1,000,000 = 700.0 → int 700
        self.assertEqual(_bps(1_000_000, 7.0), 700)


class ExecuteBuyTests(unittest.TestCase):
    def _portfolio(self, cash: int = 10_000_000) -> BacktestPortfolio:
        return BacktestPortfolio(initial_cash=cash)

    def test_buy_deducts_notional_fee_slippage(self) -> None:
        p = self._portfolio(10_000_000)
        s = _settings(buy_fee_bps=15, buy_slippage_bps=5)
        # 100주 × 50,000 = 5,000,000
        p.execute_buy("AAA", qty=100, price=50_000, trading_date=DATE_A, settings=s)
        notional = 5_000_000
        expected_deducted = notional + _bps(notional, 15) + _bps(notional, 5)
        self.assertEqual(p.cash, 10_000_000 - expected_deducted)

    def test_buy_creates_position(self) -> None:
        p = self._portfolio()
        p.execute_buy("AAA", qty=10, price=100_000, trading_date=DATE_A, settings=_settings())
        self.assertIn("AAA", p.positions)
        pos = p.positions["AAA"]
        self.assertEqual(pos.qty, 10)
        self.assertEqual(pos.avg_cost, 100_000)
        self.assertEqual(pos.buy_date, DATE_A)

    def test_buy_skipped_when_insufficient_cash(self) -> None:
        p = self._portfolio(cash=100)
        p.execute_buy("AAA", qty=100, price=50_000, trading_date=DATE_A, settings=_settings())
        self.assertNotIn("AAA", p.positions)
        self.assertEqual(p.cash, 100)

    def test_buy_merges_position_and_recalculates_avg_cost(self) -> None:
        p = self._portfolio(20_000_000)
        s = _settings()
        # First buy: 100 × 10,000
        p.execute_buy("AAA", qty=100, price=10_000, trading_date=DATE_A, settings=s)
        # Second buy: 100 × 12,000
        p.execute_buy("AAA", qty=100, price=12_000, trading_date=DATE_B, settings=s)
        pos = p.positions["AAA"]
        self.assertEqual(pos.qty, 200)
        # avg = (100*10000 + 100*12000) // 200 = 11000
        self.assertEqual(pos.avg_cost, 11_000)

    def test_buy_sets_high_water_mark_to_buy_price(self) -> None:
        p = self._portfolio()
        p.execute_buy("AAA", qty=10, price=80_000, trading_date=DATE_A, settings=_settings())
        self.assertEqual(p.positions["AAA"].high_water_mark, 80_000)

    def test_zero_fee_buy_deducts_only_notional(self) -> None:
        p = self._portfolio(10_000_000)
        s = _settings(buy_fee_bps=0, buy_slippage_bps=0)
        p.execute_buy("AAA", qty=50, price=10_000, trading_date=DATE_A, settings=s)
        self.assertEqual(p.cash, 10_000_000 - 500_000)


class ExecuteSellTests(unittest.TestCase):
    def _portfolio_with_position(
        self,
        symbol: str = "AAA",
        qty: int = 100,
        avg_cost: int = 10_000,
        cash: int = 0,
    ) -> BacktestPortfolio:
        p = BacktestPortfolio(initial_cash=cash)
        p.seed_position(symbol=symbol, qty=qty, avg_cost=avg_cost, buy_date=DATE_A)
        return p

    def test_sell_returns_closed_trade(self) -> None:
        p = self._portfolio_with_position()
        trade = p.execute_sell("AAA", qty=100, price=12_000, trading_date=DATE_B, sell_trigger="take_profit", settings=_settings())
        self.assertIsInstance(trade, ClosedTrade)
        self.assertEqual(trade.symbol, "AAA")
        self.assertEqual(trade.sell_trigger, "take_profit")

    def test_sell_removes_position_when_full_close(self) -> None:
        p = self._portfolio_with_position(qty=100)
        p.execute_sell("AAA", qty=100, price=12_000, trading_date=DATE_B, sell_trigger="take_profit", settings=_settings())
        self.assertNotIn("AAA", p.positions)

    def test_partial_sell_reduces_qty(self) -> None:
        p = self._portfolio_with_position(qty=100)
        p.execute_sell("AAA", qty=40, price=12_000, trading_date=DATE_B, sell_trigger="take_profit", settings=_settings())
        self.assertIn("AAA", p.positions)
        self.assertEqual(p.positions["AAA"].qty, 60)

    def test_sell_pnl_calculation(self) -> None:
        # buy 100 × 10,000 = 1,000,000  |  sell 100 × 12,000 = 1,200,000
        # gross pnl = 200,000  |  costs = sell_fee + sell_tax + sell_slip + buy_fee + buy_slip
        p = self._portfolio_with_position(qty=100, avg_cost=10_000)
        s = _settings(sell_fee_bps=15, sell_tax_bps=20, sell_slippage_bps=5, buy_fee_bps=15, buy_slippage_bps=5)
        trade = p.execute_sell("AAA", qty=100, price=12_000, trading_date=DATE_B, sell_trigger="tp", settings=s)
        buy_notional = 100 * 10_000
        sell_notional = 100 * 12_000
        gross = sell_notional - buy_notional
        costs = (
            _bps(sell_notional, 15) + _bps(sell_notional, 20) + _bps(sell_notional, 5)
            + _bps(buy_notional, 15) + _bps(buy_notional, 5)
        )
        self.assertEqual(trade.gross_pnl_krw, gross)
        self.assertEqual(trade.net_pnl_krw, gross - costs)

    def test_sell_adds_proceeds_to_cash(self) -> None:
        p = self._portfolio_with_position(qty=100, avg_cost=10_000, cash=0)
        s = _settings(sell_fee_bps=0, sell_tax_bps=0, sell_slippage_bps=0, buy_fee_bps=0, buy_slippage_bps=0)
        p.execute_sell("AAA", qty=100, price=12_000, trading_date=DATE_B, sell_trigger="tp", settings=s)
        self.assertEqual(p.cash, 100 * 12_000)

    def test_sell_returns_none_for_missing_symbol(self) -> None:
        p = BacktestPortfolio(initial_cash=1_000_000)
        result = p.execute_sell("ZZZ", qty=10, price=5_000, trading_date=DATE_B, sell_trigger="tp", settings=_settings())
        self.assertIsNone(result)

    def test_sell_caps_qty_at_position_qty(self) -> None:
        p = self._portfolio_with_position(qty=50)
        trade = p.execute_sell("AAA", qty=200, price=10_000, trading_date=DATE_B, sell_trigger="tp", settings=_settings())
        self.assertEqual(trade.qty, 50)
        self.assertNotIn("AAA", p.positions)

    def test_sell_hold_days_calculated_correctly(self) -> None:
        p = self._portfolio_with_position(qty=10)
        trade = p.execute_sell("AAA", qty=10, price=10_000, trading_date=DATE_B, sell_trigger="eod", settings=_settings())
        self.assertEqual(trade.hold_days, (DATE_B - DATE_A).days)

    def test_gross_pnl_pct_correct(self) -> None:
        # avg 10000, sell 11000 → gross +10%
        p = self._portfolio_with_position(qty=100, avg_cost=10_000)
        s = _settings(sell_fee_bps=0, sell_tax_bps=0, sell_slippage_bps=0, buy_fee_bps=0, buy_slippage_bps=0)
        trade = p.execute_sell("AAA", qty=100, price=11_000, trading_date=DATE_B, sell_trigger="tp", settings=s)
        self.assertAlmostEqual(trade.gross_pnl_pct, 10.0, places=1)

    def test_stop_loss_trade_has_negative_pnl(self) -> None:
        p = self._portfolio_with_position(qty=100, avg_cost=10_000)
        s = _settings(sell_fee_bps=0, sell_tax_bps=0, sell_slippage_bps=0, buy_fee_bps=0, buy_slippage_bps=0)
        trade = p.execute_sell("AAA", qty=100, price=9_000, trading_date=DATE_B, sell_trigger="stop_loss", settings=s)
        self.assertLess(trade.net_pnl_krw, 0)
        self.assertAlmostEqual(trade.gross_pnl_pct, -10.0, places=1)


class TotalValueTests(unittest.TestCase):
    def test_total_value_cash_only(self) -> None:
        p = BacktestPortfolio(initial_cash=5_000_000)
        self.assertEqual(p.total_value({}), 5_000_000)

    def test_total_value_includes_positions_at_market_price(self) -> None:
        p = BacktestPortfolio(initial_cash=1_000_000)
        p.seed_position(symbol="AAA", qty=100, avg_cost=10_000, buy_date=DATE_A)
        prices = {"AAA": 12_000}
        self.assertEqual(p.total_value(prices), 1_000_000 + 100 * 12_000)

    def test_total_value_falls_back_to_avg_cost_when_price_missing(self) -> None:
        p = BacktestPortfolio(initial_cash=0)
        p.seed_position(symbol="AAA", qty=100, avg_cost=10_000, buy_date=DATE_A)
        self.assertEqual(p.total_value({}), 100 * 10_000)

    def test_total_value_multi_position(self) -> None:
        p = BacktestPortfolio(initial_cash=0)
        p.seed_position(symbol="AAA", qty=100, avg_cost=10_000, buy_date=DATE_A)
        p.seed_position(symbol="BBB", qty=50, avg_cost=20_000, buy_date=DATE_A)
        prices = {"AAA": 11_000, "BBB": 21_000}
        expected = 100 * 11_000 + 50 * 21_000
        self.assertEqual(p.total_value(prices), expected)


class UpdateHighWaterMarkTests(unittest.TestCase):
    def test_high_water_mark_updated_when_price_rises(self) -> None:
        p = BacktestPortfolio(initial_cash=0)
        p.seed_position(symbol="AAA", qty=10, avg_cost=10_000, buy_date=DATE_A, high_water_mark=10_000)
        p.update_high_water_marks({"AAA": 13_000})
        self.assertEqual(p.positions["AAA"].high_water_mark, 13_000)

    def test_high_water_mark_not_decreased(self) -> None:
        p = BacktestPortfolio(initial_cash=0)
        p.seed_position(symbol="AAA", qty=10, avg_cost=10_000, buy_date=DATE_A, high_water_mark=12_000)
        p.update_high_water_marks({"AAA": 9_000})
        self.assertEqual(p.positions["AAA"].high_water_mark, 12_000)

    def test_high_water_mark_unchanged_when_price_missing(self) -> None:
        p = BacktestPortfolio(initial_cash=0)
        p.seed_position(symbol="AAA", qty=10, avg_cost=10_000, buy_date=DATE_A, high_water_mark=10_000)
        p.update_high_water_marks({})
        self.assertEqual(p.positions["AAA"].high_water_mark, 10_000)


class TradeLogTests(unittest.TestCase):
    def test_trade_log_accumulates(self) -> None:
        p = BacktestPortfolio(initial_cash=10_000_000)
        s = _settings()
        p.execute_buy("AAA", qty=100, price=10_000, trading_date=DATE_A, settings=s)
        p.execute_buy("BBB", qty=50, price=20_000, trading_date=DATE_A, settings=s)
        p.execute_sell("AAA", qty=100, price=11_000, trading_date=DATE_B, sell_trigger="tp", settings=s)
        p.execute_sell("BBB", qty=50, price=19_000, trading_date=DATE_B, sell_trigger="stop_loss", settings=s)
        self.assertEqual(len(p.trade_log), 2)
        symbols = {t.symbol for t in p.trade_log}
        self.assertEqual(symbols, {"AAA", "BBB"})

    def test_to_dict_contains_required_fields(self) -> None:
        p = BacktestPortfolio(initial_cash=5_000_000)
        p.seed_position(symbol="AAA", qty=10, avg_cost=10_000, buy_date=DATE_A)
        trade = p.execute_sell("AAA", qty=10, price=11_000, trading_date=DATE_B, sell_trigger="tp", settings=_settings())
        d = trade.to_dict()
        for key in ("symbol", "buy_date", "sell_date", "buy_price", "sell_price", "qty",
                    "gross_pnl_krw", "net_pnl_krw", "gross_pnl_pct", "net_pnl_pct",
                    "hold_days", "sell_trigger"):
            self.assertIn(key, d)


if __name__ == "__main__":
    unittest.main()
