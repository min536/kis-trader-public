"""Tests for app.research.replay.minute_provider (R1)."""

import unittest
from datetime import datetime

from app.research.replay.minute_provider import (
    HistoricalMinuteDataProvider,
    MinuteBar,
    MinuteObservation,
)


def _bar(symbol, ts, o, h, low, c, v):
    return MinuteBar(symbol=symbol, ts=ts, open=o, high=h, low=low, close=c, volume=v)


class ObservableSnapshotTest(unittest.TestCase):
    def test_snapshot_full_fields_literal(self):
        bars = [
            _bar("A", datetime(2026, 6, 12, 9, 0), 100.0, 101.0, 99.0, 100.5, 10),
            _bar("A", datetime(2026, 6, 12, 9, 1), 100.5, 103.0, 100.0, 102.0, 20),
            _bar("A", datetime(2026, 6, 12, 9, 2), 102.0, 102.5, 98.0, 99.0, 5),
        ]
        provider = HistoricalMinuteDataProvider(bars)
        obs = provider.observable_snapshot("A", datetime(2026, 6, 12, 9, 1))
        self.assertEqual(
            obs,
            MinuteObservation(
                symbol="A",
                ts=datetime(2026, 6, 12, 9, 1),
                bar_ts=datetime(2026, 6, 12, 9, 1),
                open=100.5,
                high=103.0,
                low=100.0,
                close=102.0,
                volume=20,
                day_open=100.0,
                day_high=103.0,
                day_low=99.0,
                day_cum_volume=30,
            ),
        )


class SnapshotBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.bars = [
            _bar("A", datetime(2026, 6, 12, 9, 0), 100.0, 101.0, 99.0, 100.5, 10),
            _bar("A", datetime(2026, 6, 12, 9, 2), 100.5, 103.0, 100.0, 102.0, 20),
        ]
        self.provider = HistoricalMinuteDataProvider(self.bars)

    def test_before_first_bar_is_none(self):
        self.assertIsNone(
            self.provider.observable_snapshot("A", datetime(2026, 6, 12, 8, 59))
        )

    def test_between_bars_uses_latest_at_or_before(self):
        # t=09:01 is between the 09:00 and 09:02 bars -> latest is 09:00
        obs = self.provider.observable_snapshot("A", datetime(2026, 6, 12, 9, 1))
        self.assertEqual(obs.bar_ts, datetime(2026, 6, 12, 9, 0))
        self.assertEqual(obs.close, 100.5)
        self.assertEqual(obs.day_cum_volume, 10)
        self.assertEqual(obs.day_high, 101.0)
        self.assertEqual(obs.day_low, 99.0)

    def test_after_last_bar_uses_last(self):
        obs = self.provider.observable_snapshot("A", datetime(2026, 6, 12, 15, 0))
        self.assertEqual(obs.bar_ts, datetime(2026, 6, 12, 9, 2))
        self.assertEqual(obs.day_cum_volume, 30)
        self.assertEqual(obs.day_high, 103.0)
        self.assertEqual(obs.day_low, 99.0)
        self.assertEqual(obs.day_open, 100.0)

    def test_unknown_symbol_is_none(self):
        self.assertIsNone(
            self.provider.observable_snapshot("ZZZ", datetime(2026, 6, 12, 9, 1))
        )


class DuplicateTimestampTest(unittest.TestCase):
    def test_duplicate_ts_for_same_symbol_raises(self):
        bars = [
            _bar("A", datetime(2026, 6, 12, 9, 0), 100.0, 101.0, 99.0, 100.5, 10),
            _bar("A", datetime(2026, 6, 12, 9, 0), 100.0, 101.0, 99.0, 100.5, 11),
        ]
        with self.assertRaises(ValueError):
            HistoricalMinuteDataProvider(bars)


class UniverseAtTest(unittest.TestCase):
    def test_universe_returns_sorted_symbols_with_bar_at_or_before_t(self):
        bars = [
            _bar("B", datetime(2026, 6, 12, 9, 0), 50.0, 50.0, 50.0, 50.0, 5),
            _bar("A", datetime(2026, 6, 12, 9, 1), 100.0, 100.0, 100.0, 100.0, 7),
            _bar("C", datetime(2026, 6, 12, 9, 3), 10.0, 10.0, 10.0, 10.0, 9),
        ]
        provider = HistoricalMinuteDataProvider(bars)
        # At 09:01 only B and A have observable bars; C's bar is at 09:03.
        self.assertEqual(
            provider.universe_at(datetime(2026, 6, 12, 9, 1)), ("A", "B")
        )
        self.assertEqual(provider.universe_at(datetime(2026, 6, 12, 8, 59)), ())
        self.assertEqual(
            provider.universe_at(datetime(2026, 6, 12, 9, 3)), ("A", "B", "C")
        )


class VolumeRankAtTest(unittest.TestCase):
    def test_volume_rank_descending_cum_volume_ties_symbol_asc(self):
        bars = [
            # A: cum volume 30 by 09:01
            _bar("A", datetime(2026, 6, 12, 9, 0), 10.0, 10.0, 10.0, 10.0, 10),
            _bar("A", datetime(2026, 6, 12, 9, 1), 10.0, 10.0, 10.0, 10.0, 20),
            # B: cum volume 100 by 09:01
            _bar("B", datetime(2026, 6, 12, 9, 0), 10.0, 10.0, 10.0, 10.0, 100),
            # C: cum volume 30 by 09:01 (ties with A -> C after A by symbol asc)
            _bar("C", datetime(2026, 6, 12, 9, 1), 10.0, 10.0, 10.0, 10.0, 30),
            # D: bar only after t -> excluded
            _bar("D", datetime(2026, 6, 12, 9, 5), 10.0, 10.0, 10.0, 10.0, 9999),
        ]
        provider = HistoricalMinuteDataProvider(bars)
        t = datetime(2026, 6, 12, 9, 1)
        self.assertEqual(provider.volume_rank_at(t, 3), ("B", "A", "C"))
        self.assertEqual(provider.volume_rank_at(t, 2), ("B", "A"))
        self.assertEqual(provider.volume_rank_at(t, 10), ("B", "A", "C"))


class NoFutureLeakageTest(unittest.TestCase):
    def test_snapshot_unaffected_by_bars_after_t(self):
        # Provider A has bars only up to t. Provider B additionally contains
        # bars strictly after t. The snapshot at t must be identical, proving
        # no future bar leaks into the output at time t.
        base = [
            _bar("A", datetime(2026, 6, 12, 9, 0), 100.0, 101.0, 99.0, 100.5, 10),
            _bar("A", datetime(2026, 6, 12, 9, 1), 100.5, 103.0, 100.0, 102.0, 20),
        ]
        future = [
            _bar("A", datetime(2026, 6, 12, 9, 2), 999.0, 9999.0, 1.0, 5.0, 100000),
            _bar("A", datetime(2026, 6, 12, 9, 3), 1.0, 2.0, 0.5, 1.5, 500000),
        ]
        t = datetime(2026, 6, 12, 9, 1)
        provider_without_future = HistoricalMinuteDataProvider(base)
        provider_with_future = HistoricalMinuteDataProvider(base + future)
        self.assertEqual(
            provider_without_future.observable_snapshot("A", t),
            provider_with_future.observable_snapshot("A", t),
        )


if __name__ == "__main__":
    unittest.main()
