"""Minute-data quality gate for the backfill parquet cache (D1).

Leaf research module under app/research/coverage/. Stdlib-only for the pure
functions; ``build_quality_report`` reads parquet lazily via pyarrow (imported
inside the function, mirroring the ingest converter's discipline). No imports
from app.* runtime modules.

Public API
----------
- ``detect_price_jumps`` — adjacent trading-day close moves at/above a
  threshold (unadjusted split/rights candidates).
- ``intraday_gap_ratio`` — fraction of the expected intraday minutes missing.
- ``halt_spans`` — runs of >= 30 consecutive volume==0 minutes.
- ``build_quality_report`` — per-symbol roll-up + ``exclude_recommended``.
"""

from __future__ import annotations


def detect_price_jumps(daily_closes, *, threshold_pct: float = 30.0):
    """Flag adjacent trading days whose |close change %| >= ``threshold_pct``.

    ``daily_closes`` is an ordered list of ``(date, close)`` tuples (ascending
    trading days). Returns a list of dicts, one per flagged day:
    ``{"date", "prev_close", "close", "change_pct"}`` where ``change_pct`` is
    signed ``(close / prev_close - 1) * 100``. Days with a zero/None previous
    close are skipped (no defined change).
    """
    flags: list[dict] = []
    for i in range(1, len(daily_closes)):
        prev_date, prev_close = daily_closes[i - 1]
        date, close = daily_closes[i]
        if prev_close in (None, 0) or close is None:
            continue
        change_pct = (close / prev_close - 1.0) * 100.0
        if abs(change_pct) >= threshold_pct:
            flags.append(
                {
                    "date": date,
                    "prev_close": prev_close,
                    "close": close,
                    "change_pct": change_pct,
                }
            )
    return flags


def _hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")[:2]
    return int(h) * 60 + int(m)


def intraday_gap_ratio(
    bars_minutes,
    *,
    session_open: str = "09:00",
    session_close: str = "15:30",
) -> float:
    """Fraction of expected intraday minutes that are missing.

    Expected minutes = every 1-minute stamp in ``[session_open, session_close]``
    inclusive (391 for 09:00..15:30). ``bars_minutes`` is a list of "HH:MM"
    (or "HH:MM:SS") strings; only distinct stamps that fall inside the session
    window count as present. Returns ``missing / expected`` in ``[0.0, 1.0]``.
    """
    open_min = _hhmm_to_minutes(session_open)
    close_min = _hhmm_to_minutes(session_close)
    expected = close_min - open_min + 1
    if expected <= 0:
        return 0.0
    present = set()
    for stamp in bars_minutes:
        minute = _hhmm_to_minutes(stamp)
        if open_min <= minute <= close_min:
            present.add(minute)
    missing = expected - len(present)
    return missing / expected


def halt_spans(bars, *, min_run: int = 30):
    """Runs of ``>= min_run`` consecutive ``volume == 0`` minute bars.

    ``bars`` is an ordered list of ``(time, volume)`` tuples (``time`` is any
    stamp string, ``volume`` an int). Returns a list of ``(start, end)`` tuples
    where ``start``/``end`` are the first and last stamps of each qualifying
    zero-volume run, in order.
    """
    spans: list[tuple[str, str]] = []
    run_start = None
    run_end = None
    run_len = 0
    for stamp, volume in bars:
        if volume == 0:
            if run_len == 0:
                run_start = stamp
            run_end = stamp
            run_len += 1
        else:
            if run_len >= min_run:
                spans.append((run_start, run_end))
            run_len = 0
            run_start = None
            run_end = None
    if run_len >= min_run:
        spans.append((run_start, run_end))
    return spans


def _percentile(values, pct: float) -> float:
    """Linear-interpolated percentile of ``values`` (pct in [0, 100])."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return float(ordered[low] + (ordered[high] - ordered[low]) * frac)


def build_quality_report(
    parquet_root,
    *,
    symbols,
    dates,
    session_open: str = "09:00",
    session_close: str = "15:30",
    jump_threshold_pct: float = 30.0,
    gap_p95_exclude: float = 0.2,
):
    """Per-symbol minute-data quality roll-up over the parquet cache.

    Reads ``<parquet_root>/date=<d>/part.parquet`` for each ``d`` in ``dates``
    (missing partitions are skipped), keeps rows whose ``symbol`` is in
    ``symbols``, and computes per symbol::

        {"jump_flags": int, "gap_ratio_p95": float, "halt_days": int}

    plus a top-level ``exclude_recommended`` list of symbols with
    ``jump_flags >= 1`` OR ``gap_ratio_p95 > gap_p95_exclude``.

    Memory: the scan is one streaming pass over date partitions — a single
    date's frame is resident at a time and per-symbol state is reduced to
    scalars immediately, so the full 2,072-date x 200-symbol cache is safe.

    pandas/pyarrow are imported inside the function (leaf-module discipline).
    """
    import pandas as pd
    from pathlib import Path

    parquet_root = Path(parquet_root)
    wanted = list(symbols)
    ordered_dates = sorted(dates)

    # Single pass over date partitions (per-date streaming): only ONE date's
    # frame is resident at a time, and per symbol we keep scalar accumulators
    # — never DataFrames. The real cache is 2,072 dates x 200 symbols;
    # holding per-day frames across the scan would OOM.
    daily_closes: dict[str, list[tuple[str, float]]] = {s: [] for s in wanted}
    gap_ratios: dict[str, list[float]] = {s: [] for s in wanted}
    halt_day_counts: dict[str, int] = {s: 0 for s in wanted}
    for date_str in ordered_dates:
        part = parquet_root / f"date={date_str}" / "part.parquet"
        if not part.exists():
            continue
        frame = pd.read_parquet(part)
        if "symbol" not in frame.columns:
            continue
        frame = frame[frame["symbol"].isin(wanted)]
        if frame.empty:
            continue
        frame = frame.copy()
        frame["_dt"] = pd.to_datetime(frame["datetime"])
        for symbol, sframe in frame.groupby("symbol"):
            key = str(symbol)
            sframe = sframe.sort_values("_dt", kind="stable")
            closes = sframe["close"].tolist()
            daily_closes[key].append((date_str, float(closes[-1])))
            minute_stamps = [dt.strftime("%H:%M") for dt in sframe["_dt"]]
            gap_ratios[key].append(
                intraday_gap_ratio(
                    minute_stamps,
                    session_open=session_open,
                    session_close=session_close,
                )
            )
            vol_bars = [
                (dt.strftime("%H:%M"), int(v))
                for dt, v in zip(sframe["_dt"], sframe["volume"])
            ]
            if halt_spans(vol_bars):
                halt_day_counts[key] += 1
        # frame/sframe are rebound on the next iteration, so this date's
        # partition is released before the next one loads.

    report_symbols: dict[str, dict] = {}
    exclude_recommended: list[str] = []
    for symbol in wanted:
        jumps = detect_price_jumps(
            daily_closes[symbol], threshold_pct=jump_threshold_pct
        )
        gap_ratio_p95 = _percentile(gap_ratios[symbol], 95.0)
        report_symbols[symbol] = {
            "jump_flags": len(jumps),
            "gap_ratio_p95": gap_ratio_p95,
            "halt_days": halt_day_counts[symbol],
        }
        if len(jumps) >= 1 or gap_ratio_p95 > gap_p95_exclude:
            exclude_recommended.append(symbol)

    return {
        "parquet_root": str(parquet_root),
        "dates": list(ordered_dates),
        "symbols": report_symbols,
        "exclude_recommended": sorted(exclude_recommended),
    }
