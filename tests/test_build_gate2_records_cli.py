"""Slice R1 — CLI ``app.tools.build_gate2_records`` tests.

The CLI builds gate2 evaluation-record parquet files over a date range, writing
one file per horizon (``records_<start>_<end>_h<h>.parquet``) in MONTHLY chunks
(build month -> write row-group -> release; never accumulating the full period
in memory). It refuses to run when ``BUY_SCAN_QUOTE_KIS_ENV`` is set (same guard
as ``app.tools.build_backfill_parquet``).

Tests point the output at ``tmp_path`` (never repo ``results/``) and the source
at a tiny synthetic parquet cache. research → runtime imports stay pure; no
imports from ``tests/`` into the runner.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

import app.market_data.live_snapshot as live_snapshot
import app.math_models.history as history_module


def _redirect_live_snapshot(monkeypatch, tmp_dir: Path) -> None:
    monkeypatch.setenv(live_snapshot.LIVE_SNAPSHOT_DIR_ENV, str(tmp_dir))
    monkeypatch.setattr(
        live_snapshot, "SNAPSHOT_PATH", tmp_dir / "live_snapshot.json"
    )


def _write_parquet_day(root: Path, day: date, rows: list[dict]) -> None:
    import pandas as pd

    part_dir = root / f"date={day.isoformat()}"
    part_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(part_dir / "part.parquet")


def _prev_close_row(symbol: str, prev_day: date, close: float) -> list[dict]:
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


def _riser_rows(symbol: str, day: date, *, minutes: range) -> list[dict]:
    rows: list[dict] = []
    for m in minutes:
        ts = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=m)
        if m == 0:
            o, h, low, c = 70000, 70050, 70000, 70000
        elif m <= 15:
            o, h, low, c = 69800, 69800, 69000, 69200
        else:
            base = 69500 + (m - 16) * 5
            o, h, low, c = base, base + 40, base - 10, base + 20
        rows.append(
            {
                "symbol": symbol,
                "datetime": ts.isoformat(),
                "open": float(o),
                "high": float(h),
                "low": float(low),
                "close": float(c),
                "volume": 70_000,
            }
        )
    return rows


def test_cli_rejects_when_quote_env_set(monkeypatch, tmp_path):
    """Env guard: a truthy ``BUY_SCAN_QUOTE_KIS_ENV`` would make the real scanner
    issue a live token, so the CLI hard-refuses (same guard as build_backfill)."""
    from app.tools import build_gate2_records

    monkeypatch.setenv("BUY_SCAN_QUOTE_KIS_ENV", "live")
    with pytest.raises(RuntimeError, match="BUY_SCAN_QUOTE_KIS_ENV unset"):
        build_gate2_records.main(
            [
                "--parquet-root",
                str(tmp_path / "pq"),
                "--out-dir",
                str(tmp_path / "out"),
                "--start",
                "2024-06-03",
                "--end",
                "2024-06-03",
            ]
        )


def test_cli_writes_per_horizon_files_over_monthly_chunks(monkeypatch, tmp_path):
    """The CLI writes one parquet per horizon spanning the whole range, streamed
    in monthly chunks. Two months of data must both appear in the single
    per-horizon output file."""
    import pandas as pd

    from app.tools import build_gate2_records

    monkeypatch.delenv("BUY_SCAN_QUOTE_KIS_ENV", raising=False)
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    _redirect_live_snapshot(monkeypatch, live_dir)
    jsonl = tmp_path / "cycle.jsonl"
    monkeypatch.setattr(history_module, "_cycle_snapshots_file", lambda: jsonl)
    history_module._build_symbol_histories.cache_clear()

    root = tmp_path / "pq"
    minutes = range(0, 21)  # short intraday span for speed (09:00..09:20)
    # Prior trading day BEFORE the [start,end] range so prev_close resolves for
    # June's riser without counting as an in-range trading day. July's riser uses
    # June's riser close as its prev_close (last in-cache day before it).
    _write_parquet_day(
        root, date(2024, 5, 31), _prev_close_row("005930", date(2024, 5, 31), 70000.0)
    )
    june_day = date(2024, 6, 3)
    july_day = date(2024, 7, 1)
    _write_parquet_day(root, june_day, _riser_rows("005930", june_day, minutes=minutes))
    _write_parquet_day(root, july_day, _riser_rows("005930", july_day, minutes=minutes))

    out_dir = tmp_path / "out"
    report = build_gate2_records.main(
        [
            "--parquet-root",
            str(root),
            "--out-dir",
            str(out_dir),
            "--start",
            "2024-06-01",
            "--end",
            "2024-07-31",
            "--horizons",
            "10",
            "--session-open",
            "09:01",
            "--session-close",
            "09:20",
        ]
    )

    out_file = out_dir / "records_2024-06-01_2024-07-31_h10.parquet"
    assert out_file.exists()
    frame = pd.read_parquet(out_file)

    # Both monthly chunks landed in the single per-horizon file.
    days = {ts[:10] for ts in frame["ts"]}
    assert "2024-06-03" in days
    assert "2024-07-01" in days
    # Column contract.
    from app.research.replay.record_runner import PARQUET_COLUMNS

    assert list(frame.columns) == list(PARQUET_COLUMNS)
    # The run report accounts for both trading days processed.
    assert report["trading_days"] == 2
    assert report["records"][10] == len(frame)


def test_cli_symbol_cap_limits_universe(monkeypatch, tmp_path):
    """``--max-symbols`` caps the scanned universe (fewer symbols -> the capped-out
    symbol never appears in the output)."""
    import pandas as pd

    from app.tools import build_gate2_records

    monkeypatch.delenv("BUY_SCAN_QUOTE_KIS_ENV", raising=False)
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    _redirect_live_snapshot(monkeypatch, live_dir)
    jsonl = tmp_path / "cycle.jsonl"
    monkeypatch.setattr(history_module, "_cycle_snapshots_file", lambda: jsonl)
    history_module._build_symbol_histories.cache_clear()

    root = tmp_path / "pq"
    minutes = range(0, 21)
    prev = date(2024, 5, 31)
    day = date(2024, 6, 3)
    _write_parquet_day(
        root,
        prev,
        _prev_close_row("005930", prev, 70000.0)
        + _prev_close_row("000660", prev, 50000.0),
    )
    _write_parquet_day(
        root,
        day,
        _riser_rows("005930", day, minutes=minutes)
        + _riser_rows("000660", day, minutes=minutes),
    )

    out_dir = tmp_path / "out"
    build_gate2_records.main(
        [
            "--parquet-root",
            str(root),
            "--out-dir",
            str(out_dir),
            "--start",
            "2024-06-03",
            "--end",
            "2024-06-03",
            "--horizons",
            "10",
            "--max-symbols",
            "1",
            "--session-open",
            "09:01",
            "--session-close",
            "09:20",
        ]
    )

    frame = pd.read_parquet(out_dir / "records_2024-06-03_2024-06-03_h10.parquet")
    # Cap of 1 symbol: only the first (sorted) symbol survives.
    assert set(frame["symbol"]) == {"000660"}


def test_cli_shares_history_across_monthly_chunks(monkeypatch, tmp_path):
    """History continuity through the CLI: ``run(...)`` must create ONE
    history/live writer pair for the WHOLE run, pass the same instances into
    every monthly chunk's ``build_records``, and set
    ``reseed_before_first_day=True`` from the second chunk on. Otherwise every
    month restarts with EMPTY history and the first ~8 trading days per month
    (3000-record window ≈ 7.7 days) get systematically thinner history than
    runtime — a calendar-correlated bias.

    Verified with a delegating spy on the CLI's ``build_records`` binding that
    captures the writer identities, reseed flags, and the writer's in-memory
    symbol histories after each chunk (the CLI's tmp dir dies with ``run``, so
    state must be captured in-flight). The scanner consumes exactly this
    in-memory buffer (``build_symbol_histories`` is patched over
    ``_build_symbol_histories`` in ``run_scan_at``); the JSONL file is now only
    a lazily-flushed debug artifact (``history_flush_ticks``), so it may not
    exist between small chunks. After the July chunk the buffer must contain
    June's rows followed by July's — proof of reseed-not-wipe across the
    month boundary.
    """
    from app.tools import build_gate2_records

    monkeypatch.delenv("BUY_SCAN_QUOTE_KIS_ENV", raising=False)
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    _redirect_live_snapshot(monkeypatch, live_dir)
    jsonl = tmp_path / "cycle.jsonl"
    monkeypatch.setattr(history_module, "_cycle_snapshots_file", lambda: jsonl)
    history_module._build_symbol_histories.cache_clear()

    root = tmp_path / "pq"
    minutes = range(0, 21)
    _write_parquet_day(
        root, date(2024, 5, 31), _prev_close_row("005930", date(2024, 5, 31), 70000.0)
    )
    june_day = date(2024, 6, 3)
    july_day = date(2024, 7, 1)
    _write_parquet_day(root, june_day, _riser_rows("005930", june_day, minutes=minutes))
    _write_parquet_day(root, july_day, _riser_rows("005930", july_day, minutes=minutes))

    real_build_records = build_gate2_records.build_records
    calls: list[dict] = []

    def spy(**kwargs):
        result = real_build_records(**kwargs)
        history_writer = kwargs.get("history_writer")
        calls.append(
            {
                "dates": list(kwargs["dates"]),
                "history_writer_id": (
                    id(history_writer) if history_writer is not None else None
                ),
                "live_writer_id": (
                    id(kwargs["live_writer"])
                    if kwargs.get("live_writer") is not None
                    else None
                ),
                "reseed": kwargs.get("reseed_before_first_day"),
                "history_ts_after": (
                    [
                        row["timestamp"]
                        for row in history_writer.build_symbol_histories(
                            100_000
                        ).get("005930", [])
                    ]
                    if history_writer is not None
                    else None
                ),
            }
        )
        return result

    monkeypatch.setattr(build_gate2_records, "build_records", spy)

    build_gate2_records.run(
        parquet_root=root,
        out_dir=tmp_path / "out",
        start=date(2024, 6, 1),
        end=date(2024, 7, 31),
        horizons=(10,),
        cost_roundtrip_bps=0.0,
        max_symbols=None,
        session_open="09:01",
        session_close="09:20",
    )

    assert len(calls) == 2  # June chunk, July chunk
    # ONE shared writer pair across both chunks.
    assert calls[0]["history_writer_id"] is not None
    assert calls[0]["history_writer_id"] == calls[1]["history_writer_id"]
    assert calls[0]["live_writer_id"] is not None
    assert calls[0]["live_writer_id"] == calls[1]["live_writer_id"]
    # First chunk fresh, subsequent chunks signal boundary continuity.
    assert calls[0]["reseed"] is False
    assert calls[1]["reseed"] is True

    # The shared in-memory history after the July chunk still holds June's
    # tail, followed in order by July's appends (reseed-not-wipe across the
    # month boundary) — this is the exact buffer the replayed scanner reads.
    timestamps = calls[1]["history_ts_after"]
    june_ts = [ts for ts in timestamps if ts.startswith("2024-06-03")]
    july_ts = [ts for ts in timestamps if ts.startswith("2024-07-01")]
    assert len(june_ts) == 20
    assert len(july_ts) == 20
    assert timestamps == june_ts + july_ts
