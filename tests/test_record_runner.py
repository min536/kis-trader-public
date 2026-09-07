"""Slice R1 — gate2 EvaluationRecord runner tests.

``build_records`` walks each trading day 09:01-15:30 in 1-minute ticks over the
REAL scan driver (``run_scan_at``), and for every gate1-pass candidate emits a
per-horizon ``EvaluationRecord`` (from ``app.research.gate2.weight_search``)
whose ``scores`` are the 15 v2 condition scores. Forward-return labels look
ahead to future minute bars (labels, not features). The record stage is
portfolio-stateless (``sim=None``).

research → runtime imports are pure-calculation only; no ``get_settings`` /
broker imports; no importing from ``tests/`` (the live-snapshot redirect helper
below is a local copy of the ``tests/test_scan_driver.py`` precedent, not an
import, to avoid cross-test coupling).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import app.market_data.live_snapshot as live_snapshot
import app.math_models.history as history_module


def _redirect_live_snapshot(monkeypatch, tmp_dir: Path) -> None:
    """Local copy of the dual-redirect precedent (tests/test_scan_driver.py).

    env alone is ineffective in a warm process because ``SNAPSHOT_PATH`` is
    resolved once at import time and read as a module global at call time.
    Redirect BOTH the C-4 P1 dir env AND the import-cached ``SNAPSHOT_PATH``.
    """
    monkeypatch.setenv(live_snapshot.LIVE_SNAPSHOT_DIR_ENV, str(tmp_dir))
    monkeypatch.setattr(
        live_snapshot, "SNAPSHOT_PATH", tmp_dir / "live_snapshot.json"
    )


def _write_parquet_day(root: Path, day: date, rows: list[dict]) -> None:
    """Write one ``date=YYYY-MM-DD/part.parquet`` partition from OHLCV rows.

    Each row: ``{symbol, datetime, open, high, low, close, volume}``.
    """
    import pandas as pd

    part_dir = root / f"date={day.isoformat()}"
    part_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(part_dir / "part.parquet")


def _minute_rows_pullback_riser(
    symbol: str, day: date, *, minutes: range
) -> list[dict]:
    """A symbol that opens at 70000, dips below open to a low, then recovers and
    grinds gently upward for the rest of the session — clears the buy rules like
    the F2 driver scenario, and always has fresh future bars for labels."""
    rows: list[dict] = []
    for m in minutes:
        ts = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=m)
        if m == 0:
            o, h, low, c, v = 70000, 70050, 70000, 70000, 50_000
        elif m <= 15:
            o, h, low, c, v = 69800, 69800, 69000, 69200, 80_000
        else:
            # gentle grind up from ~69500 by 5 KRW/min so entry != exit.
            base = 69500 + (m - 16) * 5
            o, h, low, c, v = base, base + 40, base - 10, base + 20, 70_000
        rows.append(
            {
                "symbol": symbol,
                "datetime": ts.isoformat(),
                "open": float(o),
                "high": float(h),
                "low": float(low),
                "close": float(c),
                "volume": int(v),
            }
        )
    return rows


def _prev_day_close_rows(symbol: str, prev_day: date, close: float) -> list[dict]:
    """A minimal prior-day partition so ``provider.prev_close`` resolves (a
    single flat bar at ``close``). Without a prior trading day in the cache the
    synthesized payloads would all be ``None`` and the scan empty."""
    ts = datetime.combine(prev_day, datetime.min.time()).replace(hour=9, minute=0)
    return [
        {
            "symbol": symbol,
            "datetime": ts.isoformat(),
            "open": float(close),
            "high": float(close),
            "low": float(close),
            "close": float(close),
            "volume": 10_000,
        }
    ]


def _minute_rows_flat_filler(
    symbol: str, day: date, *, minutes: range
) -> list[dict]:
    """A flat, uninteresting symbol that never clears the buy rules."""
    rows: list[dict] = []
    for m in minutes:
        ts = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=m)
        base = 50000 + m  # monotone tiny drift, no pullback
        rows.append(
            {
                "symbol": symbol,
                "datetime": ts.isoformat(),
                "open": float(base),
                "high": float(base + 30),
                "low": float(base - 5),
                "close": float(base + 10),
                "volume": 1_000,
            }
        )
    return rows


def test_record_count_equals_ticks_times_gate1_candidates(monkeypatch, tmp_path):
    """R1 ①: over a 2-symbol × 1-day synthetic cache, the per-horizon record
    count equals (# scan ticks that produced a gate1 candidate) summed over
    candidates — and only the riser (never the flat filler) is a candidate.

    The window is short (09:01-09:40) so the test is fast; the riser clears the
    buy rules from ~09:16 onward and always has a future bar to label against.
    """
    from app.research.replay.parquet_provider import ParquetMinuteProvider
    from app.research.replay.record_runner import build_records
    from app.research.replay.scan_driver import replay_settings

    monkeypatch.delenv("BUY_SCAN_QUOTE_KIS_ENV", raising=False)
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    _redirect_live_snapshot(monkeypatch, live_dir)
    jsonl = tmp_path / "cycle.jsonl"
    monkeypatch.setattr(history_module, "_cycle_snapshots_file", lambda: jsonl)
    history_module._build_symbol_histories.cache_clear()

    day = date(2024, 6, 3)
    prev_day = date(2024, 5, 31)  # prior trading day -> supplies prev_close
    root = tmp_path / "parquet"
    minutes = range(0, 41)  # 09:00 .. 09:40 bars available
    _write_parquet_day(
        root,
        prev_day,
        _prev_day_close_rows("005930", prev_day, 70000.0)
        + _prev_day_close_rows("000660", prev_day, 50000.0),
    )
    _write_parquet_day(
        root,
        day,
        _minute_rows_pullback_riser("005930", day, minutes=minutes)
        + _minute_rows_flat_filler("000660", day, minutes=minutes),
    )

    provider = ParquetMinuteProvider(root, symbols=("005930", "000660"))

    # Constrain the tick window to a short intraday span for speed.
    per_h = build_records(
        provider=provider,
        dates=[day],
        horizons_min=(10,),
        cost_roundtrip_bps=20.0,
        settings=replay_settings(),
        session_open="09:01",
        session_close="09:40",
    )

    records = per_h[10]
    assert records, "expected at least one candidate record"
    # The riser must be present; both symbols may pass the SOFT gate1
    # (passed_count >= 1) which is looser than candidate=True — the record stage
    # records EVERY gate1 pass, so we cross-check the count over all symbols.
    assert "005930" in {r.symbol for r in records}

    # Record count == sum over (symbol, tick) of gate1 passes that have a future
    # entry bar to label. Recompute independently via the same driver so the
    # assertion is a true cross-check, not a tautology.
    from app.research.replay.scan_driver import (
        ReplayHistoryWriter,
        ReplayLiveSnapshotWriter,
        run_scan_at,
    )

    provider.load_window(day, day)
    live_writer = ReplayLiveSnapshotWriter(live_dir)
    history_writer = ReplayHistoryWriter(jsonl)
    history_module._build_symbol_histories.cache_clear()

    expected = 0
    last_minute = 40  # last bar in the cache (09:40)
    for m in range(1, 41):  # ticks 09:01 .. 09:40
        t = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=m)
        results = run_scan_at(
            t=t,
            provider=provider,
            sim=None,
            settings=replay_settings(),
            history_writer=history_writer,
            live_writer=live_writer,
        )
        gate1 = [r for r in results if r.passed_count >= 1]
        # Entry bar = first bar with ts >= t+1min; within the 09:00..09:40 cache
        # that exists iff m+1 <= 40. horizon=10 exit may truncate but never drops.
        if m + 1 <= last_minute:
            expected += len(gate1)

    assert len(records) == expected


def _riser_bar_open(m: int) -> float:
    """The exact open price the riser fixture writes at minute ``m`` (must mirror
    ``_minute_rows_pullback_riser``)."""
    if m == 0:
        return 70000.0
    if m <= 15:
        return 69800.0
    return float(69500 + (m - 16) * 5)


def _riser_bar_close(m: int) -> float:
    if m == 0:
        return 70000.0
    if m <= 15:
        return 69200.0
    return float((69500 + (m - 16) * 5) + 20)


def test_label_math_matches_hand_computation_with_cost(monkeypatch, tmp_path):
    """R1 ②: for each emitted record, forward_return_bps equals the hand-computed
    ``(exit_open / entry_open - 1) * 10_000 - cost`` where entry_open is the open
    of the bar at tick+1min and exit_open is the open of the bar at tick+1min+h,
    both read independently from the fixture's price formula. Cost is deducted.
    """
    from app.research.replay.parquet_provider import ParquetMinuteProvider
    from app.research.replay.record_runner import build_records
    from app.research.replay.scan_driver import replay_settings

    monkeypatch.delenv("BUY_SCAN_QUOTE_KIS_ENV", raising=False)
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    _redirect_live_snapshot(monkeypatch, live_dir)
    jsonl = tmp_path / "cycle.jsonl"
    monkeypatch.setattr(history_module, "_cycle_snapshots_file", lambda: jsonl)
    history_module._build_symbol_histories.cache_clear()

    day = date(2024, 6, 3)
    prev_day = date(2024, 5, 31)
    root = tmp_path / "parquet"
    minutes = range(0, 41)
    _write_parquet_day(
        root,
        prev_day,
        _prev_day_close_rows("005930", prev_day, 70000.0),
    )
    _write_parquet_day(
        root,
        day,
        _minute_rows_pullback_riser("005930", day, minutes=minutes),
    )

    provider = ParquetMinuteProvider(root, symbols=("005930",))

    cost = 25.0
    horizon = 10
    last_minute = 40
    per_h = build_records(
        provider=provider,
        dates=[day],
        horizons_min=(horizon,),
        cost_roundtrip_bps=cost,
        settings=replay_settings(),
        session_open="09:01",
        session_close="09:40",
    )
    records = per_h[horizon]
    assert records, "expected at least one candidate record to label-check"

    for row in records:
        tick_minute = datetime.fromisoformat(row.ts).minute
        entry_open = _riser_bar_open(tick_minute + 1)
        exit_minute = tick_minute + 1 + horizon
        if exit_minute <= last_minute:
            exit_price = _riser_bar_open(exit_minute)
            expected_truncated = False
        else:
            # Session-close truncation: exit is the last same-day bar's close.
            exit_price = _riser_bar_close(last_minute)
            expected_truncated = True
        expected_bps = (exit_price / entry_open - 1.0) * 10_000.0 - cost
        assert row.truncated is expected_truncated
        assert row.forward_return_bps == expected_bps


def test_session_close_truncation_uses_last_bar_close(monkeypatch, tmp_path):
    """R1 ③: when tick+1min+h lands after the session's last bar, the exit is
    truncated to the LAST same-day bar's CLOSE (not an open) and truncated=True.

    Window 09:01-09:05 with horizon 10: every candidate tick's exit target
    (>= tick+11min) is past the last bar (09:05), so ALL records must truncate,
    and their exit price is the last bar's close (09:05 close = 69200)."""
    from app.research.replay.parquet_provider import ParquetMinuteProvider
    from app.research.replay.record_runner import build_records
    from app.research.replay.scan_driver import replay_settings

    monkeypatch.delenv("BUY_SCAN_QUOTE_KIS_ENV", raising=False)
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    _redirect_live_snapshot(monkeypatch, live_dir)
    jsonl = tmp_path / "cycle.jsonl"
    monkeypatch.setattr(history_module, "_cycle_snapshots_file", lambda: jsonl)
    history_module._build_symbol_histories.cache_clear()

    day = date(2024, 6, 3)
    prev_day = date(2024, 5, 31)
    root = tmp_path / "parquet"
    minutes = range(0, 6)  # bars 09:00 .. 09:05 only
    _write_parquet_day(
        root, prev_day, _prev_day_close_rows("005930", prev_day, 70000.0)
    )
    _write_parquet_day(
        root, day, _minute_rows_pullback_riser("005930", day, minutes=minutes)
    )

    provider = ParquetMinuteProvider(root, symbols=("005930",))

    cost = 30.0
    horizon = 10
    per_h = build_records(
        provider=provider,
        dates=[day],
        horizons_min=(horizon,),
        cost_roundtrip_bps=cost,
        settings=replay_settings(),
        session_open="09:01",
        session_close="09:05",
    )
    records = per_h[horizon]
    assert records, "expected at least one truncated record"

    last_close = _riser_bar_close(5)  # 09:05 close (m<=15 branch)
    assert last_close == 69200.0  # pin the truncation-exit source explicitly
    for row in records:
        assert row.truncated is True
        tick_minute = datetime.fromisoformat(row.ts).minute
        entry_open = _riser_bar_open(tick_minute + 1)
        expected_bps = (last_close / entry_open - 1.0) * 10_000.0 - cost
        assert row.forward_return_bps == expected_bps


def test_shared_writers_carry_history_across_build_records_calls(
    monkeypatch, tmp_path
):
    """Cross-chunk history continuity: when the caller injects ONE
    ``ReplayHistoryWriter``/``ReplayLiveSnapshotWriter`` pair into consecutive
    ``build_records`` calls and signals the chunk boundary with
    ``reseed_before_first_day=True``, the second call's history starts from the
    first call's tail (reseed-not-wipe) and extends it — no periodic
    empty-history restart at chunk boundaries (the 3000-record technical window
    is ~7.7 trading days, so a monthly empty restart would thin the first ~8
    days of EVERY month — a calendar-correlated bias, not noise).

    Asserted directly on the shared history JSONL: day-1 records survive the
    boundary (tail limit 3000 >> 20 ticks -> all retained) and are followed, in
    order, by day-2 records.
    """
    import json

    from app.research.replay.parquet_provider import ParquetMinuteProvider
    from app.research.replay.record_runner import build_records
    from app.research.replay.scan_driver import (
        ReplayHistoryWriter,
        ReplayLiveSnapshotWriter,
        replay_settings,
    )

    monkeypatch.delenv("BUY_SCAN_QUOTE_KIS_ENV", raising=False)
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    _redirect_live_snapshot(monkeypatch, live_dir)
    history_module._build_symbol_histories.cache_clear()

    day1 = date(2024, 6, 3)
    day2 = date(2024, 7, 1)  # next "monthly chunk"; prev_close = day1 close
    root = tmp_path / "parquet"
    minutes = range(0, 21)
    _write_parquet_day(
        root,
        date(2024, 5, 31),
        _prev_day_close_rows("005930", date(2024, 5, 31), 70000.0),
    )
    _write_parquet_day(
        root, day1, _minute_rows_pullback_riser("005930", day1, minutes=minutes)
    )
    _write_parquet_day(
        root, day2, _minute_rows_pullback_riser("005930", day2, minutes=minutes)
    )

    provider = ParquetMinuteProvider(root, symbols=("005930",))
    shared_history = ReplayHistoryWriter(tmp_path / "shared_cycle.jsonl")
    shared_live = ReplayLiveSnapshotWriter(live_dir)
    common = dict(
        provider=provider,
        horizons_min=(10,),
        cost_roundtrip_bps=0.0,
        settings=replay_settings(),
        session_open="09:01",
        session_close="09:20",
        history_writer=shared_history,
        live_writer=shared_live,
    )

    per1 = build_records(dates=[day1], **common)
    per2 = build_records(dates=[day2], reseed_before_first_day=True, **common)
    assert per1[10] and per2[10]  # both chunks scanned and produced records

    lines = [
        json.loads(line)
        for line in shared_history.path.read_text().splitlines()
        if line.strip()
    ]
    timestamps = [record["timestamp"] for record in lines]
    day1_ts = [ts for ts in timestamps if ts.startswith("2024-06-03")]
    day2_ts = [ts for ts in timestamps if ts.startswith("2024-07-01")]
    # Day-1 history survived the chunk boundary (20 ticks << 3000 tail limit)
    # and day-2 appends extended it, in order.
    assert len(day1_ts) == 20
    assert len(day2_ts) == 20
    assert timestamps == day1_ts + day2_ts


def test_records_parquet_round_trips_columns_and_values(tmp_path):
    """R1 ④: writing RecordRows to parquet and reading them back preserves the
    exact column set (symbol, ts, 15 score columns, forward_return_bps,
    truncated) and values, including a None score column (portfolio conditions
    are None at the record stage)."""
    import pandas as pd

    from app.gate2.schema import CONDITION_SCORE_NAMES
    from app.research.replay.record_runner import (
        PARQUET_COLUMNS,
        RecordRow,
        write_records_parquet,
    )

    scores_a = {name: float(i) for i, name in enumerate(CONDITION_SCORE_NAMES)}
    scores_a["portfolio_diversification_score"] = None  # record-stage None
    scores_b = {name: float(100 - i) for i, name in enumerate(CONDITION_SCORE_NAMES)}

    rows = [
        RecordRow(
            symbol="005930",
            ts="2024-06-03T09:15:00",
            scores=scores_a,
            forward_return_bps=12.5,
            truncated=False,
        ),
        RecordRow(
            symbol="000660",
            ts="2024-06-03T09:16:00",
            scores=scores_b,
            forward_return_bps=-7.25,
            truncated=True,
        ),
    ]

    out = tmp_path / "records_h10.parquet"
    write_records_parquet(rows, out)
    assert out.exists()

    frame = pd.read_parquet(out)
    assert list(frame.columns) == list(PARQUET_COLUMNS)
    assert len(frame) == 2

    r0 = frame.iloc[0]
    assert r0["symbol"] == "005930"
    assert r0["ts"] == "2024-06-03T09:15:00"
    assert r0["forward_return_bps"] == 12.5
    assert bool(r0["truncated"]) is False
    assert r0["pullback_strength_score"] == 0.0
    assert pd.isna(r0["portfolio_diversification_score"])  # None survives

    r1 = frame.iloc[1]
    assert r1["symbol"] == "000660"
    assert r1["forward_return_bps"] == -7.25
    assert bool(r1["truncated"]) is True
