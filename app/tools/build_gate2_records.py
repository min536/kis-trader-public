"""CLI: build gate2 evaluation-record parquet files from replay minute data (R1).

Streams the record build in MONTHLY chunks so the full period never resides in
memory: for each calendar month in ``[start, end]`` it builds that month's
records (``app.research.replay.record_runner.build_records``) and appends them as
a row-group to one parquet file per horizon
(``records_<start>_<end>_h<h>.parquet``), then releases the chunk. Progress is
logged every 10 trading days.

Trading days are discovered from the parquet cache's ``date=YYYY-MM-DD``
partitions (the only days with data) — no separate market calendar.

Safety: no broker/KIS/Toss network calls. Like ``app.tools.build_backfill_parquet``,
this CLI **refuses to run when ``BUY_SCAN_QUOTE_KIS_ENV`` is set** — a truthy
value would make the real scanner issue a live quote token
(service.py:649 -> quote_account.py:186-199). The real (multi-GB) record
production run is operator work.

History continuity: ONE history/live store pair spans the WHOLE run (created
once in ``run`` and passed into every monthly chunk's ``build_records``, with
``reseed_before_first_day=True`` from the second chunk on), so cross-day history
is continuous across month boundaries — mirroring the runtime's persistent
cycle-snapshots file. Without this, every month would restart with empty history
and the first ~8 trading days per month (3000-record technical window ≈ 7.7
days) would see systematically thinner trend/macd/momentum context than runtime
— a calendar-correlated bias that could distort which dims look predictive in W2.

Usage::

    python -m app.tools.build_gate2_records \
        --parquet-root data/toss_minute_parquet_backfill \
        --out-dir results/gate2_backtest \
        --start 2023-01-02 --end 2025-03-31 \
        --horizons 10,30,60 --max-symbols 200
"""

from __future__ import annotations

import argparse
import gc
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

from app.research.replay.parquet_provider import ParquetMinuteProvider
from app.research.replay.record_runner import (
    PARQUET_COLUMNS,
    build_records,
)
from app.research.replay.scan_driver import (
    ReplayHistoryWriter,
    ReplayLiveSnapshotWriter,
    replay_settings,
)
from app.gate2.schema import CONDITION_SCORE_NAMES


def _parse_horizons(raw: str) -> tuple[int, ...]:
    return tuple(int(x) for x in str(raw).split(",") if x.strip())


def _iter_partition_dates(parquet_root: Path):
    """Yield trading ``date`` objects from ``date=YYYY-MM-DD`` partitions, ascending."""
    for date_dir in sorted(p for p in parquet_root.glob("date=*") if p.is_dir()):
        part = date_dir / "part.parquet"
        if not part.exists():
            continue
        try:
            yield date.fromisoformat(date_dir.name[len("date=") :])
        except ValueError:
            continue


def _discover_symbols(parquet_root: Path, max_symbols: int | None) -> tuple[str, ...]:
    """Sorted symbols across the cache, capped at ``max_symbols`` (or all)."""
    import pandas as pd

    symbols: set[str] = set()
    for date_dir in sorted(p for p in parquet_root.glob("date=*") if p.is_dir()):
        part = date_dir / "part.parquet"
        if not part.exists():
            continue
        frame = pd.read_parquet(part, columns=["symbol"])
        symbols.update(str(s) for s in frame["symbol"].unique())
    ordered = sorted(symbols)
    if max_symbols is not None and max_symbols >= 0:
        ordered = ordered[:max_symbols]
    return tuple(ordered)


def _months_of_dates(dates: list[date]) -> list[tuple[int, int, list[date]]]:
    """Group ascending ``dates`` into ``(year, month, [days])`` chunks."""
    chunks: list[tuple[int, int, list[date]]] = []
    for day in dates:
        key = (day.year, day.month)
        if chunks and (chunks[-1][0], chunks[-1][1]) == key:
            chunks[-1][2].append(day)
        else:
            chunks.append((key[0], key[1], [day]))
    return chunks


def _record_schema():
    """Explicit pyarrow schema so chunks with all-None score columns still align."""
    import pyarrow as pa

    fields = [pa.field("symbol", pa.string()), pa.field("ts", pa.string())]
    fields += [pa.field(name, pa.float64()) for name in CONDITION_SCORE_NAMES]
    fields.append(pa.field("forward_return_bps", pa.float64()))
    fields.append(pa.field("truncated", pa.bool_()))
    return pa.schema(fields)


def _rows_to_table(rows, schema):
    """Build a pyarrow Table for one chunk's ``RecordRow``s under ``schema``."""
    import pyarrow as pa

    columns: dict[str, list] = {col: [] for col in PARQUET_COLUMNS}
    for row in rows:
        columns["symbol"].append(row.symbol)
        columns["ts"].append(row.ts)
        for name in CONDITION_SCORE_NAMES:
            value = row.scores.get(name)
            columns[name].append(None if value is None else float(value))
        columns["forward_return_bps"].append(float(row.forward_return_bps))
        columns["truncated"].append(bool(row.truncated))
    arrays = [
        pa.array(columns[field.name], type=field.type) for field in schema
    ]
    return pa.Table.from_arrays(arrays, schema=schema)


def run(
    *,
    parquet_root,
    out_dir,
    start: date,
    end: date,
    horizons: tuple[int, ...],
    cost_roundtrip_bps: float,
    max_symbols: int | None,
    session_open: str,
    session_close: str,
    market_open: str = "09:00",
    market_close: str = "15:30",
    rollup_lookback_days: int = 120,
    history_flush_ticks: int = 390,
    log=print,
) -> dict:
    """Build per-horizon record parquet files over ``[start, end]``, streaming by
    month. Returns a report dict ``{trading_days, records: {h: count}, ...}``.

    ``market_open``/``market_close`` bound the provider (governing ``day_open``
    and the daily rollups that feed ``prev_close``) — they are the TRUE session
    and must include the real open bar. ``session_open``/``session_close`` bound
    only the tick-iteration window inside ``build_records`` (a subset)."""
    import pyarrow.parquet as pq

    parquet_root = Path(parquet_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    symbols = _discover_symbols(parquet_root, max_symbols)
    all_dates = [d for d in _iter_partition_dates(parquet_root) if start <= d <= end]
    month_chunks = _months_of_dates(all_dates)
    schema = _record_schema()

    settings = replay_settings()
    provider = ParquetMinuteProvider(
        parquet_root,
        symbols=symbols,
        session_open=market_open,
        session_close=market_close,
        min_date=start - timedelta(days=max(int(rollup_lookback_days), 0)),
        max_date=end,
    )

    writers: dict[int, "pq.ParquetWriter"] = {}
    paths: dict[int, Path] = {}
    counts: dict[int, int] = {int(h): 0 for h in horizons}
    for h in horizons:
        path = out_dir / f"records_{start.isoformat()}_{end.isoformat()}_h{int(h)}.parquet"
        paths[int(h)] = path
        writers[int(h)] = pq.ParquetWriter(str(path), schema)

    days_done = 0
    try:
        # ONE tmp store + ONE history/live writer pair for the WHOLE run:
        # every monthly chunk shares them, and reseed_before_first_day carries
        # the previous chunk's history tail across the month boundary (like an
        # intra-chunk day boundary). This keeps look-back history continuous
        # for the whole period, mirroring the runtime's persistent
        # cycle-snapshots file — no periodic empty-history restart.
        with tempfile.TemporaryDirectory(prefix="gate2_records_cli_") as tmp:
            tmp_path = Path(tmp)
            live_dir = tmp_path / "live"
            live_dir.mkdir()
            history_writer = ReplayHistoryWriter(
                tmp_path / "cycle_snapshots.jsonl",
                history_refresh_ticks=history_flush_ticks,
            )
            live_writer = ReplayLiveSnapshotWriter(live_dir)

            for chunk_index, (year, month, days) in enumerate(month_chunks):
                gc_was_enabled = gc.isenabled()
                if gc_was_enabled:
                    gc.disable()
                try:
                    per_h = build_records(
                        provider=provider,
                        dates=days,
                        horizons_min=horizons,
                        cost_roundtrip_bps=cost_roundtrip_bps,
                        settings=settings,
                        session_open=session_open,
                        session_close=session_close,
                        history_writer=history_writer,
                        live_writer=live_writer,
                        reseed_before_first_day=(chunk_index > 0),
                    )
                finally:
                    if gc_was_enabled:
                        gc.enable()
                        gc.collect()
                for h in horizons:
                    rows = per_h[int(h)]
                    table = _rows_to_table(rows, schema)
                    writers[int(h)].write_table(table)
                    counts[int(h)] += len(rows)
                    # rows/table released as the loop rebinds -> chunk not retained.
                days_done += len(days)
                if days_done // 10 != (days_done - len(days)) // 10:
                    log(
                        f"[build_gate2_records] {days_done}/{len(all_dates)} "
                        f"trading days ({year}-{month:02d} chunk done)"
                    )
    finally:
        for writer in writers.values():
            writer.close()

    return {
        "trading_days": len(all_dates),
        "symbols": len(symbols),
        "horizons": [int(h) for h in horizons],
        "records": counts,
        "output_files": {int(h): str(paths[int(h)]) for h in horizons},
        "rollup_lookback_days": int(rollup_lookback_days),
        "history_flush_ticks": int(history_flush_ticks),
    }


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(
        description=(
            "Build gate2 evaluation-record parquet files from replay minute "
            "data, streamed in monthly chunks (R1)."
        )
    )
    parser.add_argument("--parquet-root", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--horizons", default="10,30,60")
    parser.add_argument("--cost-roundtrip-bps", type=float, default=0.0)
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--session-open", default="09:01", help="tick-window open")
    parser.add_argument("--session-close", default="15:30", help="tick-window close")
    parser.add_argument(
        "--market-open", default="09:00", help="true session open (provider/day_open)"
    )
    parser.add_argument(
        "--market-close", default="15:30", help="true session close (provider)"
    )
    parser.add_argument(
        "--rollup-lookback-days",
        type=int,
        default=120,
        help="calendar days before --start to include for previous-close rollups",
    )
    parser.add_argument(
        "--history-flush-ticks",
        type=int,
        default=390,
        help=(
            "debug JSONL flush interval for replay history; scanner reads the "
            "same replay buffer in memory"
        ),
    )
    args = parser.parse_args(argv)

    # Environment guard: replay/record must never issue live quote tokens.
    if os.environ.get("BUY_SCAN_QUOTE_KIS_ENV"):
        raise RuntimeError(
            "gate2 record build requires BUY_SCAN_QUOTE_KIS_ENV unset"
        )

    report = run(
        parquet_root=args.parquet_root,
        out_dir=args.out_dir,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        horizons=_parse_horizons(args.horizons),
        cost_roundtrip_bps=args.cost_roundtrip_bps,
        max_symbols=args.max_symbols,
        session_open=args.session_open,
        session_close=args.session_close,
        market_open=args.market_open,
        market_close=args.market_close,
        rollup_lookback_days=args.rollup_lookback_days,
        history_flush_ticks=args.history_flush_ticks,
    )
    print(
        f"gate2 records -> {args.out_dir} "
        f"trading_days={report['trading_days']} "
        f"symbols={report['symbols']} "
        f"records={report['records']} "
        f"rollup_lookback_days={report['rollup_lookback_days']} "
        f"history_flush_ticks={report['history_flush_ticks']}"
    )
    return report


if __name__ == "__main__":  # pragma: no cover
    main()
