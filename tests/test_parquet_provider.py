"""Tests for app.research.replay.parquet_provider (D2).

All parquet fixtures are synthetic, built under tmp dirs. Real data/ paths are
never touched. No broker/KIS/Toss network calls. Python 3.13.

The provider wraps HistoricalMinuteDataProvider (existing) for the observable
minute-bar surface and precomputes daily rollups (from minute bars) for the
look-ahead-safe prev_close / daily_history queries.
"""

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path


def _minute_stamps(start_hhmm, count, step_min=1):
    """Return ``count`` "HH:MM" strings starting at start_hhmm, step apart."""
    h, m = (int(x) for x in start_hhmm.split(":"))
    total = h * 60 + m
    out = []
    for _ in range(count):
        out.append(f"{total // 60:02d}:{total % 60:02d}")
        total += step_min
    return out


def _write_day_parquet(root, date_str, per_symbol_rows):
    """Write ``root/date=<date_str>/part.parquet`` from per-symbol row lists.

    ``per_symbol_rows`` maps symbol -> list of
    ``(datetime_str, open, high, low, close, volume)`` tuples. Columns match the
    D1 converter output: datetime, open, high, low, close, volume, symbol.
    """
    import pandas as pd

    frames = []
    for symbol, rows in per_symbol_rows.items():
        df = pd.DataFrame(
            rows,
            columns=["datetime", "open", "high", "low", "close", "volume"],
        )
        df["symbol"] = symbol
        frames.append(df)
    combined = pd.concat(frames, ignore_index=True)
    day_dir = Path(root) / f"date={date_str}"
    day_dir.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(day_dir / "part.parquet", engine="pyarrow", index=False)


def _session_rows(date_str, *, day_open, high, low, close, volume=10,
                  start="09:00", count=391):
    """Intraday session rows for one symbol on one day.

    The FIRST bar carries ``day_open`` as its open (so the daily rollup open is
    ``day_open``); the LAST bar carries ``close`` (daily close). ``high``/``low``
    are placed on interior bars so the daily rollup high/low resolve to them.
    """
    stamps = _minute_stamps(start, count)
    rows = []
    for i, s in enumerate(stamps):
        if i == 0:
            o, h, lo, c = day_open, day_open, day_open, day_open
        elif i == 1:
            o = h = lo = c = high  # inject the daily high
        elif i == 2:
            o = h = lo = c = low  # inject the daily low
        elif i == len(stamps) - 1:
            o = h = lo = c = close  # last bar -> daily close
        else:
            o = h = lo = c = (high + low) / 2.0
        rows.append((f"{date_str} {s}:00", o, h, lo, c, volume))
    return rows


class PrevCloseTest(unittest.TestCase):
    def test_prev_close_uses_last_trading_day_before_and_first_day_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "parquet"
            # 3 symbols x 3 days. Distinct closes per symbol/day.
            days = ["2026-06-10", "2026-06-11", "2026-06-12"]
            closes = {
                "005930": [100.0, 110.0, 120.0],
                "000660": [200.0, 205.0, 210.0],
                "035720": [50.0, 48.0, 55.0],
            }
            for di, day_str in enumerate(days):
                per_symbol = {
                    sym: _session_rows(
                        day_str,
                        day_open=closes[sym][di] - 5.0,
                        high=closes[sym][di] + 5.0,
                        low=closes[sym][di] - 10.0,
                        close=closes[sym][di],
                    )
                    for sym in closes
                }
                _write_day_parquet(root, day_str, per_symbol)

            from app.research.replay.parquet_provider import ParquetMinuteProvider

            provider = ParquetMinuteProvider(root)

            # First trading day -> no prior close -> None.
            self.assertIsNone(provider.prev_close("005930", date(2026, 6, 10)))
            # Second day -> prior day's close (day 06-10 close = 100.0).
            self.assertEqual(
                provider.prev_close("005930", date(2026, 6, 11)), 100.0
            )
            # Third day -> the immediately prior trading day's close.
            self.assertEqual(
                provider.prev_close("000660", date(2026, 6, 12)), 205.0
            )
            self.assertEqual(
                provider.prev_close("035720", date(2026, 6, 12)), 48.0
            )
            # A day equal to the earliest with no strictly-earlier trading day.
            self.assertIsNone(provider.prev_close("035720", date(2026, 6, 10)))


class DailyHistoryExcludesQueryDayTest(unittest.TestCase):
    def test_daily_history_returns_prior_days_only_and_never_query_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "parquet"
            days = ["2026-06-10", "2026-06-11", "2026-06-12"]
            # Distinct daily OHLC per day so rollups are identifiable.
            spec = {
                "2026-06-10": dict(day_open=95.0, high=108.0, low=90.0, close=100.0),
                "2026-06-11": dict(day_open=101.0, high=118.0, low=99.0, close=110.0),
                "2026-06-12": dict(day_open=111.0, high=128.0, low=109.0, close=120.0),
            }
            for day_str in days:
                _write_day_parquet(
                    root,
                    day_str,
                    {"005930": _session_rows(day_str, volume=13, **spec[day_str])},
                )

            from app.research.replay.parquet_provider import ParquetMinuteProvider

            provider = ParquetMinuteProvider(root)

            # n=2 prior days before 06-12 -> [06-10, 06-11], NOT 06-12 itself.
            hist = provider.daily_history("005930", date(2026, 6, 12), 2)
            self.assertEqual([r["date"] for r in hist], ["2026-06-10", "2026-06-11"])
            # Explicit look-ahead assertion: the query day must never appear.
            self.assertNotIn("2026-06-12", [r["date"] for r in hist])

            # Rollup aggregation from minute bars: open=first, high=max,
            # low=min, close=last, volume=sum (391 bars x 13).
            row10 = hist[0]
            self.assertEqual(row10["date"], "2026-06-10")
            self.assertEqual(row10["open"], 95.0)
            self.assertEqual(row10["high"], 108.0)
            self.assertEqual(row10["low"], 90.0)
            self.assertEqual(row10["close"], 100.0)
            self.assertEqual(row10["volume"], 391 * 13)

            # Querying the earliest day returns nothing (no strictly-prior days).
            self.assertEqual(provider.daily_history("005930", date(2026, 6, 10), 5), [])

            # n larger than available -> all strictly-prior days, still no
            # query-day leak.
            hist_all = provider.daily_history("005930", date(2026, 6, 12), 10)
            self.assertEqual(
                [r["date"] for r in hist_all], ["2026-06-10", "2026-06-11"]
            )

            # A query day that itself has no data still only looks back.
            hist_gap = provider.daily_history("005930", date(2026, 6, 13), 1)
            self.assertEqual([r["date"] for r in hist_gap], ["2026-06-12"])


class SessionFilterExcludesPreMarketTest(unittest.TestCase):
    def test_load_window_drops_08xx_rows_from_observable_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "parquet"
            day_str = "2026-06-11"
            # Pre-market rows at 08:30, 08:59 then a normal 09:00..15:30 session.
            pre = [
                (f"{day_str} 08:30:00", 90.0, 90.0, 90.0, 90.0, 500),
                (f"{day_str} 08:59:00", 91.0, 91.0, 91.0, 91.0, 700),
            ]
            session = _session_rows(
                day_str, day_open=100.0, high=105.0, low=95.0, close=102.0,
                volume=10,
            )
            _write_day_parquet(root, day_str, {"005930": pre + session})

            from app.research.replay.parquet_provider import ParquetMinuteProvider

            provider = ParquetMinuteProvider(root)
            provider.load_window(date(2026, 6, 11), date(2026, 6, 11))

            # An observation taken at 08:45 (after the pre-market bar but before
            # the open) must be None: pre-market rows are filtered out entirely.
            t_pre = datetime(2026, 6, 11, 8, 45)
            self.assertIsNone(provider.observable_snapshot("005930", t_pre))

            # At 09:00 the day opens at 100.0 (not 90.0/91.0 from pre-market),
            # and the pre-market 500+700 volume is excluded from the day's cum
            # volume.
            t_open = datetime(2026, 6, 11, 9, 0)
            obs_open = provider.observable_snapshot("005930", t_open)
            self.assertIsNotNone(obs_open)
            self.assertEqual(obs_open.day_open, 100.0)
            self.assertEqual(obs_open.day_cum_volume, 10)

            # By end of session, day_cum_volume is the full session (391 x 10),
            # never including the 1200 pre-market volume.
            t_close = datetime(2026, 6, 11, 15, 30)
            obs_close = provider.observable_snapshot("005930", t_close)
            self.assertEqual(obs_close.day_cum_volume, 391 * 10)
            self.assertEqual(obs_close.day_high, 105.0)
            self.assertEqual(obs_close.day_low, 95.0)

            # The daily rollup (independent path) is also pre-market-free.
            prev = provider.prev_close("005930", date(2026, 6, 12))
            self.assertEqual(prev, 102.0)


class WindowBoundsTest(unittest.TestCase):
    def _build(self, root):
        days = ["2026-06-10", "2026-06-11", "2026-06-12"]
        vols = {"005930": 20, "000660": 30}
        for day_str in days:
            _write_day_parquet(
                root,
                day_str,
                {
                    "005930": _session_rows(
                        day_str, day_open=100.0, high=105.0, low=95.0,
                        close=102.0, volume=vols["005930"],
                    ),
                    "000660": _session_rows(
                        day_str, day_open=200.0, high=205.0, low=195.0,
                        close=202.0, volume=vols["000660"],
                    ),
                },
            )

    def test_access_outside_loaded_window_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "parquet"
            self._build(root)

            from app.research.replay.parquet_provider import ParquetMinuteProvider

            provider = ParquetMinuteProvider(root)

            # Before any load_window, minute access raises ValueError.
            with self.assertRaises(ValueError):
                provider.observable_snapshot("005930", datetime(2026, 6, 11, 10, 0))

            # Load only the middle day.
            provider.load_window(date(2026, 6, 11), date(2026, 6, 11))

            # In-window access works for all three delegated methods.
            t_in = datetime(2026, 6, 11, 15, 30)
            self.assertIsNotNone(provider.observable_snapshot("005930", t_in))
            self.assertEqual(
                provider.universe_at(t_in), ("000660", "005930")
            )
            # 000660 has higher per-bar volume -> ranks first.
            self.assertEqual(
                provider.volume_rank_at(t_in, 2), ("000660", "005930")
            )

            # A day AFTER the window raises for every minute-bar accessor.
            t_after = datetime(2026, 6, 12, 10, 0)
            with self.assertRaises(ValueError):
                provider.observable_snapshot("005930", t_after)
            with self.assertRaises(ValueError):
                provider.universe_at(t_after)
            with self.assertRaises(ValueError):
                provider.volume_rank_at(t_after, 5)

            # A day BEFORE the window also raises.
            t_before = datetime(2026, 6, 10, 10, 0)
            with self.assertRaises(ValueError):
                provider.observable_snapshot("005930", t_before)

            # Daily rollup queries are NOT window-bound: prev_close /
            # daily_history resolve across the full period regardless of the
            # loaded minute window.
            self.assertEqual(provider.prev_close("005930", date(2026, 6, 12)), 102.0)
            hist = provider.daily_history("005930", date(2026, 6, 12), 3)
            self.assertEqual(
                [r["date"] for r in hist], ["2026-06-10", "2026-06-11"]
            )

            # Re-loading a different window updates the bounds.
            provider.load_window(date(2026, 6, 12), date(2026, 6, 12))
            self.assertIsNotNone(
                provider.observable_snapshot("005930", datetime(2026, 6, 12, 10, 0))
            )
            with self.assertRaises(ValueError):
                provider.observable_snapshot("005930", datetime(2026, 6, 11, 10, 0))


if __name__ == "__main__":
    unittest.main()
