"""Historical daily OHLCV data provider for engine backtest.

Provides MarketSnapshot objects from pre-loaded CSV data, replacing
the live inquire_price() API call in the real engine.

CSV format expected (one file per symbol OR combined):
  date,symbol,open,high,low,close,volume,prev_close

The backtester uses close_price as "current_price" at scan time.
This is a reasonable daily approximation — intraday scans see the
realised close, which reflects whether the intraday rules actually fired.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.market_data.schema import MarketSnapshot


@dataclass
class DailyOHLCV:
    symbol: str
    date: date
    open_price: int
    high_price: int
    low_price: int
    close_price: int
    volume: int
    prev_close: int  # previous trading day close
    prev_day_change_pct: float  # (close - prev_close) / prev_close * 100


class BacktestDataProvider:
    """In-memory store of DailyOHLCV rows, keyed by (symbol, date).

    Usage
    -----
    provider = BacktestDataProvider.from_csv("data/prices.csv")
    snap = provider.get_snapshot("005930", date(2026, 4, 13))
    """

    def __init__(self, rows: list[DailyOHLCV]) -> None:
        self._data: dict[tuple[str, date], DailyOHLCV] = {}
        for row in rows:
            self._data[(row.symbol, row.date)] = row
        # sorted trading dates per symbol (for calendar lookup)
        self._dates_by_symbol: dict[str, list[date]] = {}
        for row in rows:
            self._dates_by_symbol.setdefault(row.symbol, [])
            if row.date not in self._dates_by_symbol[row.symbol]:
                self._dates_by_symbol[row.symbol].append(row.date)
        for sym in self._dates_by_symbol:
            self._dates_by_symbol[sym].sort()

    # ── factory methods ────────────────────────────────────────────────────

    @classmethod
    def from_csv(cls, path: str | Path) -> "BacktestDataProvider":
        """Load from a CSV with columns:
        date, symbol, open, high, low, close, volume, prev_close
        """
        path = Path(path)
        rows: list[DailyOHLCV] = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for rec in reader:
                rows.append(_parse_csv_row(rec))
        return cls(rows)

    @classmethod
    def from_records(cls, records: list[dict[str, Any]]) -> "BacktestDataProvider":
        """Build from a list of dicts (same keys as CSV columns)."""
        rows = [_parse_csv_row(r) for r in records]
        return cls(rows)

    # ── public API ─────────────────────────────────────────────────────────

    def get_snapshot(self, symbol: str, trading_date: date) -> MarketSnapshot | None:
        """Return MarketSnapshot for symbol on trading_date, or None if missing."""
        row = self._data.get((symbol, trading_date))
        if row is None:
            return None
        # Use close as "current_price" — best daily approximation
        return MarketSnapshot(
            symbol=row.symbol,
            current_price=row.close_price,
            open_price=row.open_price,
            low_price=row.low_price,
            prev_day_change_pct=row.prev_day_change_pct,
        )

    def get_row(self, symbol: str, trading_date: date) -> DailyOHLCV | None:
        return self._data.get((symbol, trading_date))

    def trading_dates(self, symbol: str) -> list[date]:
        """All available trading dates for symbol, sorted ascending."""
        return list(self._dates_by_symbol.get(symbol, []))

    def universe_dates(self, symbols: list[str]) -> list[date]:
        """Union of trading dates across all symbols, sorted."""
        dates: set[date] = set()
        for sym in symbols:
            dates.update(self._dates_by_symbol.get(sym, []))
        return sorted(dates)

    def coverage(self) -> dict[str, tuple[date, date]]:
        """(first_date, last_date) for each symbol."""
        result: dict[str, tuple[date, date]] = {}
        for sym, dates in self._dates_by_symbol.items():
            if dates:
                result[sym] = (dates[0], dates[-1])
        return result

    def symbols(self) -> list[str]:
        return list(self._dates_by_symbol.keys())

    def has(self, symbol: str, trading_date: date) -> bool:
        return (symbol, trading_date) in self._data

    # ── data quality helpers ───────────────────────────────────────────────

    def detect_gaps(
        self,
        symbol: str,
        max_allowed_gap_days: int = 5,
    ) -> list[dict]:
        """Return a list of date gaps that exceed *max_allowed_gap_days*.

        Gaps of up to 5 calendar days are expected (weekends + 1 holiday).
        Larger gaps likely indicate missing data.

        Returns a list of dicts with keys: symbol, prev_date, next_date, gap_days.
        """
        dates = self._dates_by_symbol.get(symbol, [])
        if len(dates) < 2:
            return []
        gaps = []
        for prev, nxt in zip(dates, dates[1:]):
            gap = (nxt - prev).days
            if gap > max_allowed_gap_days:
                gaps.append(
                    {
                        "symbol": symbol,
                        "prev_date": prev.isoformat(),
                        "next_date": nxt.isoformat(),
                        "gap_days": gap,
                    }
                )
        return gaps

    def validate(self) -> list[dict]:
        """Run basic sanity checks on every row.

        Checks:
        - high >= low
        - close > 0, open > 0, low > 0
        - prev_day_change_pct is finite

        Returns a list of issue dicts (empty list = all ok).
        """
        import math as _math

        issues: list[dict] = []
        for (symbol, d), row in self._data.items():
            row_issues: list[str] = []
            if row.high_price < row.low_price:
                row_issues.append(f"high({row.high_price}) < low({row.low_price})")
            if row.close_price <= 0:
                row_issues.append(f"close={row.close_price} <= 0")
            if row.open_price <= 0:
                row_issues.append(f"open={row.open_price} <= 0")
            if row.low_price <= 0:
                row_issues.append(f"low={row.low_price} <= 0")
            if not _math.isfinite(row.prev_day_change_pct):
                row_issues.append(f"prev_day_change_pct is not finite")
            if row_issues:
                issues.append(
                    {
                        "symbol": symbol,
                        "date": d.isoformat(),
                        "issues": row_issues,
                    }
                )
        return issues


# ── internal helpers ───────────────────────────────────────────────────────

def _parse_csv_row(rec: dict[str, Any]) -> DailyOHLCV:
    date_val = rec["date"]
    if isinstance(date_val, date):
        d = date_val
    else:
        d = date.fromisoformat(str(date_val)[:10])

    open_p  = int(float(rec.get("open",  rec.get("open_price",  0)) or 0))
    high_p  = int(float(rec.get("high",  rec.get("high_price",  0)) or 0))
    low_p   = int(float(rec.get("low",   rec.get("low_price",   0)) or 0))
    close_p = int(float(rec.get("close", rec.get("close_price", 0)) or 0))
    volume  = int(float(rec.get("volume", 0) or 0))
    prev_c  = int(float(rec.get("prev_close", 0) or 0))

    if prev_c > 0 and close_p > 0:
        chg = (close_p - prev_c) / prev_c * 100
    else:
        try:
            chg = float(rec.get("prev_day_change_pct", 0) or 0)
        except (TypeError, ValueError):
            chg = 0.0

    if not math.isfinite(chg):
        chg = 0.0

    return DailyOHLCV(
        symbol=str(rec["symbol"]).strip(),
        date=d,
        open_price=open_p,
        high_price=high_p,
        low_price=low_p,
        close_price=close_p,
        volume=volume,
        prev_close=prev_c,
        prev_day_change_pct=round(chg, 4),
    )
