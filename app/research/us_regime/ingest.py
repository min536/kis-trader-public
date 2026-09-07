"""M-D1 — US index daily-bar ingest (source adapter: csv-first).

Leaf research module. pandas/pyarrow imported inside functions. No ``app.*``
runtime imports. See ``docs/us_regime_weight_plan_20260702.md`` §2.
"""

from __future__ import annotations

REQUIRED_COLUMNS = ("date", "open", "high", "low", "close", "volume")


def import_us_daily_csv(csv_path, *, asset: str, out_root) -> int:
    """Validate a US daily-bar CSV and write ``out_root/<asset>.parquet``.

    Validation (raises ``ValueError`` with the offending 1-based data-row number
    on the first violation):

    - required columns present (``REQUIRED_COLUMNS``);
    - ``date`` strictly ascending (no duplicates, no descending);
    - OHLC consistency ``low <= open, close <= high``.

    ``volume`` may be 0 (index symbols). Returns the number of rows written.
    """
    from pathlib import Path

    import pandas as pd

    frame = pd.read_csv(csv_path)
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    frame = frame[list(REQUIRED_COLUMNS)].copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.date

    prev_date = None
    for i, row in enumerate(frame.itertuples(index=False), start=1):
        d = row.date
        if prev_date is not None and d <= prev_date:
            kind = "duplicate" if d == prev_date else "descending"
            raise ValueError(
                f"date not strictly ascending at data row {i}: "
                f"{d} ({kind}, prev {prev_date})"
            )
        o, h, low, c = (
            float(row.open),
            float(row.high),
            float(row.low),
            float(row.close),
        )
        if not (low <= o <= h and low <= c <= h):
            raise ValueError(
                f"OHLC inconsistency at data row {i}: "
                f"open={o} high={h} low={low} close={c}"
            )
        prev_date = d

    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / f"{asset}.parquet"
    frame.to_parquet(out_path, index=False)
    return len(frame)


def load_us_daily(out_root, asset: str):
    """Load ``out_root/<asset>.parquet`` into a DataFrame."""
    from pathlib import Path

    import pandas as pd

    path = Path(out_root) / f"{asset}.parquet"
    frame = pd.read_parquet(path)
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    return frame


def fetch_us_daily(source: str, **kwargs) -> int:
    """Source-adapter extension point. Only ``source == "csv"`` is implemented.

    ``"kis_overseas"`` / ``"toss_us"`` raise ``NotImplementedError`` —
    plan §8-① escalation: extending the US data source beyond CSV (e.g. a new
    KIS 기간별시세 TR — ``app/overseas_stock/market_data.py`` currently exposes
    only current-price APIs) is a separate, approval-gated work item. Stop and
    report before wiring a non-CSV source.
    """
    if source == "csv":
        return import_us_daily_csv(
            kwargs.pop("csv_path"),
            asset=kwargs.pop("asset"),
            out_root=kwargs.pop("out_root"),
        )
    if source in ("kis_overseas", "toss_us"):
        raise NotImplementedError(
            f"source {source!r} requires a new US daily-bar API — "
            "plan §8-① escalation (approval-gated, do not implement here)"
        )
    raise ValueError(f"unknown source: {source!r}")
