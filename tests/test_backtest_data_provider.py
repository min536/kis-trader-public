"""Unit tests for backtester/engine_backtest/data_provider.py"""
from __future__ import annotations

import io
import textwrap
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from backtester.engine_backtest.data_provider import BacktestDataProvider, DailyOHLCV


def _row(
    symbol: str = "AAA",
    date_val: date = date(2026, 1, 2),
    open_p: int = 10_000,
    high_p: int = 11_000,
    low_p: int = 9_500,
    close_p: int = 10_500,
    volume: int = 100_000,
    prev_close: int = 10_000,
) -> dict:
    return {
        "symbol": symbol,
        "date": date_val.isoformat(),
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "volume": volume,
        "prev_close": prev_close,
    }


def _provider(*rows: dict) -> BacktestDataProvider:
    return BacktestDataProvider.from_records(list(rows))


class FromRecordsTests(unittest.TestCase):
    def test_single_row_loaded(self) -> None:
        p = _provider(_row())
        self.assertIn("AAA", p.symbols())

    def test_get_snapshot_returns_market_snapshot(self) -> None:
        p = _provider(_row(symbol="AAA", date_val=date(2026, 1, 2), close_p=10_500))
        snap = p.get_snapshot("AAA", date(2026, 1, 2))
        self.assertIsNotNone(snap)
        self.assertEqual(snap.current_price, 10_500)
        self.assertEqual(snap.open_price, 10_000)

    def test_get_snapshot_returns_none_for_missing(self) -> None:
        p = _provider(_row())
        self.assertIsNone(p.get_snapshot("ZZZ", date(2026, 1, 2)))
        self.assertIsNone(p.get_snapshot("AAA", date(2099, 1, 1)))

    def test_get_row_returns_daily_ohlcv(self) -> None:
        p = _provider(_row(symbol="BBB", high_p=15_000, low_p=8_000))
        row = p.get_row("BBB", date(2026, 1, 2))
        self.assertIsInstance(row, DailyOHLCV)
        self.assertEqual(row.high_price, 15_000)
        self.assertEqual(row.low_price, 8_000)

    def test_duplicate_rows_last_wins(self) -> None:
        r1 = _row(close_p=10_000)
        r2 = _row(close_p=11_000)
        p = BacktestDataProvider.from_records([r1, r2])
        snap = p.get_snapshot("AAA", date(2026, 1, 2))
        self.assertEqual(snap.current_price, 11_000)

    def test_prev_day_change_pct_computed_from_prev_close(self) -> None:
        # close 11000, prev_close 10000 → +10%
        p = _provider(_row(close_p=11_000, prev_close=10_000))
        row = p.get_row("AAA", date(2026, 1, 2))
        self.assertAlmostEqual(row.prev_day_change_pct, 10.0, places=2)

    def test_flexible_column_names(self) -> None:
        """open_price / high_price / low_price / close_price aliases are supported."""
        rec = {
            "symbol": "CCC",
            "date": "2026-01-02",
            "open_price": 5_000,
            "high_price": 6_000,
            "low_price": 4_500,
            "close_price": 5_500,
            "volume": 50_000,
            "prev_close": 5_000,
        }
        p = BacktestDataProvider.from_records([rec])
        snap = p.get_snapshot("CCC", date(2026, 1, 2))
        self.assertEqual(snap.current_price, 5_500)


class TradingDatesTests(unittest.TestCase):
    def test_trading_dates_sorted_ascending(self) -> None:
        rows = [
            _row(date_val=date(2026, 1, 5)),
            _row(date_val=date(2026, 1, 2)),
            _row(date_val=date(2026, 1, 3)),
        ]
        p = BacktestDataProvider.from_records(rows)
        dates = p.trading_dates("AAA")
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(dates), 3)

    def test_universe_dates_is_union(self) -> None:
        rows = [
            _row(symbol="AAA", date_val=date(2026, 1, 2)),
            _row(symbol="BBB", date_val=date(2026, 1, 3)),
        ]
        p = BacktestDataProvider.from_records(rows)
        dates = p.universe_dates(["AAA", "BBB"])
        self.assertIn(date(2026, 1, 2), dates)
        self.assertIn(date(2026, 1, 3), dates)
        self.assertEqual(dates, sorted(dates))

    def test_universe_dates_deduplicates(self) -> None:
        rows = [
            _row(symbol="AAA", date_val=date(2026, 1, 2)),
            _row(symbol="BBB", date_val=date(2026, 1, 2)),
        ]
        p = BacktestDataProvider.from_records(rows)
        dates = p.universe_dates(["AAA", "BBB"])
        self.assertEqual(len(dates), 1)

    def test_unknown_symbol_returns_empty_dates(self) -> None:
        p = _provider(_row())
        self.assertEqual(p.trading_dates("ZZZZ"), [])


class CoverageAndHasTests(unittest.TestCase):
    def test_coverage_returns_first_and_last_date(self) -> None:
        rows = [
            _row(date_val=date(2026, 1, 2)),
            _row(date_val=date(2026, 1, 5)),
        ]
        p = BacktestDataProvider.from_records(rows)
        cov = p.coverage()
        self.assertEqual(cov["AAA"], (date(2026, 1, 2), date(2026, 1, 5)))

    def test_has_returns_true_for_existing(self) -> None:
        p = _provider(_row())
        self.assertTrue(p.has("AAA", date(2026, 1, 2)))

    def test_has_returns_false_for_missing(self) -> None:
        p = _provider(_row())
        self.assertFalse(p.has("AAA", date(2099, 1, 1)))


class DetectGapsTests(unittest.TestCase):
    def test_no_gap_within_tolerance(self) -> None:
        rows = [
            _row(date_val=date(2026, 1, 2)),
            _row(date_val=date(2026, 1, 5)),  # 3 days (Fri→Mon)
        ]
        p = BacktestDataProvider.from_records(rows)
        self.assertEqual(p.detect_gaps("AAA", max_allowed_gap_days=5), [])

    def test_large_gap_detected(self) -> None:
        rows = [
            _row(date_val=date(2026, 1, 2)),
            _row(date_val=date(2026, 1, 20)),  # 18-day gap
        ]
        p = BacktestDataProvider.from_records(rows)
        gaps = p.detect_gaps("AAA", max_allowed_gap_days=5)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["gap_days"], 18)
        self.assertEqual(gaps[0]["symbol"], "AAA")

    def test_single_date_no_gaps(self) -> None:
        p = _provider(_row())
        self.assertEqual(p.detect_gaps("AAA"), [])

    def test_unknown_symbol_no_gaps(self) -> None:
        p = _provider(_row())
        self.assertEqual(p.detect_gaps("ZZZ"), [])

    def test_multiple_gaps(self) -> None:
        rows = [
            _row(date_val=date(2026, 1, 2)),
            _row(date_val=date(2026, 1, 20)),  # gap 1
            _row(date_val=date(2026, 2, 10)),  # gap 2
        ]
        p = BacktestDataProvider.from_records(rows)
        gaps = p.detect_gaps("AAA", max_allowed_gap_days=5)
        self.assertEqual(len(gaps), 2)


class ValidateTests(unittest.TestCase):
    def test_valid_data_no_issues(self) -> None:
        p = _provider(_row())
        self.assertEqual(p.validate(), [])

    def test_detects_high_less_than_low(self) -> None:
        bad = _row(high_p=8_000, low_p=9_000)
        p = _provider(bad)
        issues = p.validate()
        self.assertEqual(len(issues), 1)
        self.assertTrue(any("high" in iss for iss in issues[0]["issues"]))

    def test_detects_zero_close(self) -> None:
        bad = _row(close_p=0)
        p = _provider(bad)
        issues = p.validate()
        self.assertEqual(len(issues), 1)
        self.assertTrue(any("close" in iss for iss in issues[0]["issues"]))

    def test_detects_zero_open(self) -> None:
        bad = _row(open_p=0)
        p = _provider(bad)
        issues = p.validate()
        self.assertTrue(any("open" in iss for iss in issues[0]["issues"]))

    def test_multiple_symbols_all_validated(self) -> None:
        rows = [
            _row(symbol="AAA", high_p=8_000, low_p=9_000),   # bad
            _row(symbol="BBB"),                                 # ok
            _row(symbol="CCC", close_p=0),                     # bad
        ]
        p = BacktestDataProvider.from_records(rows)
        issues = p.validate()
        symbols_with_issues = {i["symbol"] for i in issues}
        self.assertIn("AAA", symbols_with_issues)
        self.assertIn("CCC", symbols_with_issues)
        self.assertNotIn("BBB", symbols_with_issues)


class FromCsvTests(unittest.TestCase):
    def test_csv_loaded_correctly(self) -> None:
        csv_content = textwrap.dedent("""\
            date,symbol,open,high,low,close,volume,prev_close
            2026-01-02,AAA,10000,11000,9500,10500,100000,10000
            2026-01-03,AAA,10500,11500,10000,11000,120000,10500
        """)
        path = Path("/tmp/_test_data_provider.csv")
        path.write_text(csv_content)
        try:
            p = BacktestDataProvider.from_csv(path)
            self.assertEqual(len(p.symbols()), 1)
            self.assertEqual(len(p.trading_dates("AAA")), 2)
            snap = p.get_snapshot("AAA", date(2026, 1, 3))
            self.assertEqual(snap.current_price, 11_000)
        finally:
            path.unlink(missing_ok=True)

    def test_yyyymmdd_date_format_supported(self) -> None:
        """YYYYMMDD date strings (no hyphens) should parse correctly."""
        rec = _row()
        rec["date"] = "20260102"
        p = BacktestDataProvider.from_records([rec])
        self.assertTrue(p.has("AAA", date(2026, 1, 2)))


if __name__ == "__main__":
    unittest.main()
