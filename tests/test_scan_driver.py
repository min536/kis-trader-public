"""Slice F2 — scanner replay driver + store-seam writers (contract tests).

These tests are the *gates* for the two runtime-state store seams the replay
driver must satisfy exactly:

- **Seam A (live rank)** — ``ReplayLiveSnapshotWriter`` writes the
  ``live_snapshot.json`` that the REAL
  ``app.market_data.live_snapshot.get_live_snapshot_signal`` reads. If the JSON
  shape drifts, ``volume_rank_score`` / ``volume_power_score`` are silently
  poisoned, so the contract test asserts the reader returns the expected ranks
  (1-based list positions) after a dual redirect (env + import-cached
  ``SNAPSHOT_PATH``).
- **Seam B (price history)** — ``ReplayHistoryWriter`` appends a per-tick
  observation record to a JSONL that the REAL
  ``app.math_models.history.get_recent_symbol_price_history`` reads (patched
  ``_cycle_snapshots_file``).

research → runtime imports are pure-calculation only; no ``get_settings`` /
broker imports; no importing from ``tests/``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

import app.market_data.live_snapshot as live_snapshot
import app.math_models.history as history_module
from app.research.replay.minute_provider import (
    HistoricalMinuteDataProvider,
    MinuteBar,
)
from app.research.replay.scan_driver import (
    ReplayHistoryWriter,
    ReplayLiveSnapshotWriter,
    replay_settings,
    run_scan_at,
)


def _redirect_live_snapshot(monkeypatch, tmp_dir: Path) -> None:
    """Dual-redirect precedent (see tests/test_payload_synth.py):

    env alone is ineffective in a warm process because ``SNAPSHOT_PATH`` is
    resolved once at import time (live_snapshot.py:36) and read as a module
    global at call time (:296). Redirect BOTH the documented C-4 P1 dir env AND
    the import-cached ``SNAPSHOT_PATH`` so the reader reads only our tmp file.
    """
    monkeypatch.setenv(live_snapshot.LIVE_SNAPSHOT_DIR_ENV, str(tmp_dir))
    monkeypatch.setattr(
        live_snapshot, "SNAPSHOT_PATH", tmp_dir / "live_snapshot.json"
    )


def test_live_snapshot_writer_round_trips_through_real_reader(monkeypatch, tmp_path):
    """F2-a contract gate: writer output must satisfy the REAL reader.

    Ranks are 1-based list positions in each ``source_symbols`` list; combined
    rank comes from ``top_symbols``. A symbol absent from a list is rankless
    (None) there.
    """
    tmp_dir = tmp_path / "live"
    tmp_dir.mkdir()
    _redirect_live_snapshot(monkeypatch, tmp_dir)

    writer = ReplayLiveSnapshotWriter(tmp_dir)
    ordered = {
        "volume_rank": ["005930", "000660", "035420"],
        "fluctuation_rank": ["005930", "000660", "035420"],
        "volume_power_rank": ["005930", "000660", "035420"],
    }
    writer.write(datetime(2024, 6, 3, 10, 30), ordered)

    # File actually landed where the reader looks.
    assert (tmp_dir / "live_snapshot.json").exists()

    signal = live_snapshot.get_live_snapshot_signal("005930")
    assert signal is not None
    assert signal.volume_rank == 1
    assert signal.fluctuation_rank == 1
    assert signal.volume_power_rank == 1
    assert signal.combined_rank == 1  # top_symbols position
    assert signal.ranked_source_count == 3

    mid = live_snapshot.get_live_snapshot_signal("035420")
    assert mid is not None
    assert mid.volume_rank == 3
    assert mid.volume_power_rank == 3
    assert mid.combined_rank == 3

    # A symbol not present in any list gets rankless treatment (all None).
    absent = live_snapshot.get_live_snapshot_signal("068270")
    assert absent is not None
    assert absent.volume_rank is None
    assert absent.fluctuation_rank is None
    assert absent.volume_power_rank is None
    assert absent.combined_rank is None
    assert absent.ranked_source_count == 0


def _obs_row(
    symbol: str, price: int, *, open_price: int, low_price: int, chg: float
) -> dict:
    return {
        "symbol": symbol,
        "current_price": price,
        "open_price": open_price,
        "low_price": low_price,
        "prev_day_change_pct": chg,
    }


def test_history_writer_appends_minute_series_read_by_real_reader(monkeypatch, tmp_path):
    """F2-b contract gate part 1: per-tick appends form a minute-interval series
    that the REAL ``get_recent_symbol_price_history`` returns in time order.

    The writer's flat-dict source is ``observed_market_snapshots`` (source
    ``observed_cycle``, history.py:182-212); the reader normalizes
    ``current_price`` -> ``price``. Records accumulate per tick (~per minute),
    NOT per day.
    """
    jsonl = tmp_path / "cycle_snapshots.jsonl"
    monkeypatch.setattr(
        history_module, "_cycle_snapshots_file", lambda: jsonl
    )
    # lru_cache is keyed on (limit, mtime marker); clear so this test's file is
    # parsed fresh regardless of prior test state.
    history_module._build_symbol_histories.cache_clear()

    writer = ReplayHistoryWriter(jsonl)
    ticks = [
        (datetime(2024, 6, 3, 9, 1), 70100),
        (datetime(2024, 6, 3, 9, 2), 70250),
        (datetime(2024, 6, 3, 9, 3), 70050),
    ]
    for t, price in ticks:
        writer.append_tick(
            t,
            [_obs_row("005930", price, open_price=70000, low_price=69900, chg=1.0)],
        )

    history_module._build_symbol_histories.cache_clear()
    rows = history_module.get_recent_symbol_price_history("005930", limit=60)

    assert [row["timestamp"] for row in rows] == [
        "2024-06-03T09:01:00",
        "2024-06-03T09:02:00",
        "2024-06-03T09:03:00",
    ]
    assert [row["price"] for row in rows] == [70100, 70250, 70050]
    # Flat-dict fields survive the round-trip.
    assert rows[0]["open_price"] == 70000
    assert rows[0]["low_price"] == 69900
    assert rows[0]["prev_day_change_pct"] == 1.0


def test_history_writer_reseed_preserves_previous_day_tail(monkeypatch, tmp_path):
    """F2-b contract gate part 2: at day start the writer reseeds the file from
    the previous day's tail (bounded to the technical limit) so history-dependent
    scores still see the prior session; then new-day appends extend it."""
    jsonl = tmp_path / "cycle_snapshots.jsonl"
    monkeypatch.setattr(
        history_module, "_cycle_snapshots_file", lambda: jsonl
    )
    history_module._build_symbol_histories.cache_clear()

    # Keep the retained tail tiny so the test is fast but the mechanism is exact.
    writer = ReplayHistoryWriter(jsonl, reseed_tail_records=2)
    for minute, price in ((1, 70100), (2, 70200), (3, 70300)):
        writer.append_tick(
            datetime(2024, 6, 3, 9, minute),
            [_obs_row("005930", price, open_price=70000, low_price=69900, chg=1.0)],
        )

    # New trading day: reseed drops all but the last 2 previous-day records.
    writer.reseed_for_new_day()
    writer.append_tick(
        datetime(2024, 6, 4, 9, 1),
        [_obs_row("005930", 71000, open_price=70800, low_price=70700, chg=0.5)],
    )

    history_module._build_symbol_histories.cache_clear()
    rows = history_module.get_recent_symbol_price_history("005930", limit=60)

    assert [row["timestamp"] for row in rows] == [
        "2024-06-03T09:02:00",  # tail of previous day survived (last 2)
        "2024-06-03T09:03:00",
        "2024-06-04T09:01:00",  # new-day append extends it
    ]
    assert [row["price"] for row in rows] == [70200, 70300, 71000]


class _FakeProvider:
    """Minimal provider: an in-memory ``HistoricalMinuteDataProvider`` plus a
    ``prev_close`` lookup, matching the surface ``run_scan_at`` reads from
    ``ParquetMinuteProvider`` (universe_at / volume_rank_at / observable_snapshot
    / prev_close)."""

    def __init__(self, bars, prev_closes: dict[str, float]) -> None:
        self._inner = HistoricalMinuteDataProvider(bars)
        self._prev_closes = dict(prev_closes)

    def universe_at(self, t):
        return self._inner.universe_at(t)

    def volume_rank_at(self, t, top_n):
        return self._inner.volume_rank_at(t, top_n)

    def observable_snapshot(self, symbol, t):
        return self._inner.observable_snapshot(symbol, t)

    def prev_close(self, symbol, day):
        return self._prev_closes.get(symbol)


def test_run_scan_at_rejects_when_quote_env_set(monkeypatch, tmp_path):
    """F2-c entry guard: a truthy ``BUY_SCAN_QUOTE_KIS_ENV`` would make the real
    scanner issue a live token (service.py:649 -> quote_account.py:186-199), so
    the driver must hard-refuse before touching the provider."""
    monkeypatch.setenv("BUY_SCAN_QUOTE_KIS_ENV", "live")

    live_dir = tmp_path / "live"
    live_dir.mkdir()
    _redirect_live_snapshot(monkeypatch, live_dir)
    jsonl = tmp_path / "cycle.jsonl"
    monkeypatch.setattr(history_module, "_cycle_snapshots_file", lambda: jsonl)

    provider = _FakeProvider([], {})
    live_writer = ReplayLiveSnapshotWriter(live_dir)
    history_writer = ReplayHistoryWriter(jsonl)

    with pytest.raises(RuntimeError, match="BUY_SCAN_QUOTE_KIS_ENV unset"):
        run_scan_at(
            t=datetime(2024, 6, 3, 10, 30),
            provider=provider,
            sim=None,
            settings=replay_settings(),
            history_writer=history_writer,
            live_writer=live_writer,
        )


def _bar(symbol, ts, o, h, low, c, v):
    return MinuteBar(symbol=symbol, ts=ts, open=o, high=h, low=low, close=c, volume=v)


def test_run_scan_at_selects_pullback_then_rise_candidate(monkeypatch, tmp_path):
    """F2-c driver scenario: a symbol that pulls back below its open then rises
    off the intraday low is selected as ``candidate=True`` through the REAL
    ``scan_target_symbols`` (not a mock). This exercises the full seam wiring:
    synthesized payloads + live-rank store + history store + portfolio snapshot.
    """
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    _redirect_live_snapshot(monkeypatch, live_dir)
    jsonl = tmp_path / "cycle.jsonl"
    monkeypatch.setattr(history_module, "_cycle_snapshots_file", lambda: jsonl)
    history_module._build_symbol_histories.cache_clear()
    monkeypatch.delenv("BUY_SCAN_QUOTE_KIS_ENV", raising=False)

    scan_day = datetime(2024, 6, 3)
    t = scan_day.replace(hour=9, minute=30)

    def at(minute, second=0):
        return scan_day.replace(hour=9, minute=minute, second=second)

    # 005930: open 70000, dips to a 69000 intraday low, recovers to 69500 at t.
    # -> below open (pullback), rebounded off the low, mid-range recovery,
    #    prev_day_change ~ -0.71% (controlled down). Highest cumulative volume so
    #    it ranks #1 in the live-rank store (volume/power rank rules pass too).
    # 000660: flat/uninteresting filler that does not clear the buy rules.
    bars = [
        _bar("005930", at(0), 70000, 70050, 70000, 70000, 50_000),
        _bar("005930", at(15), 69800, 69800, 69000, 69200, 80_000),
        _bar("005930", at(30), 69300, 69600, 69300, 69500, 70_000),
        _bar("000660", at(0), 50000, 50100, 49990, 50050, 1_000),
        _bar("000660", at(15), 50050, 50200, 50000, 50120, 1_200),
        _bar("000660", at(30), 50120, 50300, 50100, 50250, 900),
    ]
    provider = _FakeProvider(
        bars,
        prev_closes={"005930": 70000.0, "000660": 50000.0},
    )

    live_writer = ReplayLiveSnapshotWriter(live_dir)
    history_writer = ReplayHistoryWriter(jsonl)

    results = run_scan_at(
        t=t,
        provider=provider,
        sim=None,
        settings=replay_settings(),
        history_writer=history_writer,
        live_writer=live_writer,
    )

    by_symbol = {r.symbol: r for r in results}
    assert "005930" in by_symbol
    candidate = by_symbol["005930"]
    assert candidate.candidate is True, (
        f"expected 005930 candidate=True; passed_count={candidate.passed_count} "
        f"score={candidate.score} reason={candidate.final_reason}"
    )
    # Live-rank seam took effect: the #1-volume symbol got a volume rank.
    assert candidate.market_snapshot.live_snapshot_available is True
    assert candidate.market_snapshot.live_volume_rank == 1
