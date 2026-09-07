"""CLI: US index daily-bar ingest + KR→last-US-session calendar build (M-D1).

Thin wrapper over ``app.research.us_regime.ingest`` and
``app.research.us_regime.calendar_map`` — all validation/mapping logic lives in
those (tested) leaf modules. No broker/KIS/Toss network calls; the real
(multi-GB) data-load run is operator work.

Two modes:

- ``--csv <path> --asset SPX --out-root data/us_daily`` — validate one CSV and
  write ``<out-root>/<asset>.parquet``. Call once per asset.
- ``--build-calendar --parquet-root <toss-root> --us-root <out-root>
  --assets SPX,NDX,VIX,SOX --calendar-out <path>`` — build the KR→US map
  (KR canon = toss parquet ``date=`` partitions; US canon = ingested US
  parquet ``date`` column) and write ``<calendar-out>``
  (default ``results/gate2_backtest/kr_us_calendar.parquet``).

Usage::

    python -m app.tools.ingest_us_daily --csv spx.csv --asset SPX \
        --out-root data/us_daily
    python -m app.tools.ingest_us_daily --build-calendar \
        --parquet-root data/toss_minute_parquet_backfill \
        --us-root data/us_daily --assets SPX,NDX,VIX,SOX
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.research.us_regime.calendar_map import (
    build_kr_to_us_map,
    kr_trading_days,
    to_frame,
)
from app.research.us_regime.ingest import import_us_daily_csv, load_us_daily

DEFAULT_ASSETS = ("SPX", "NDX", "VIX", "SOX")
DEFAULT_CALENDAR_OUT = "results/gate2_backtest/kr_us_calendar.parquet"


def _us_session_days(us_root, assets):
    """Union of ``date`` values across ingested US asset parquets, ascending."""
    days: set = set()
    for asset in assets:
        frame = load_us_daily(us_root, asset)
        days.update(frame["date"].tolist())
    return sorted(days)


def build_calendar(*, parquet_root, us_root, assets, calendar_out) -> int:
    """Build the KR→US map and write ``calendar_out``. Returns mapped-day count."""
    kr_days = kr_trading_days(parquet_root)
    us_days = _us_session_days(us_root, assets)
    mapping = build_kr_to_us_map(kr_days, us_days)
    frame = to_frame(mapping)
    out_path = Path(calendar_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out_path, index=False)
    print(
        f"[ingest_us_daily] calendar written: {out_path} "
        f"({len(frame)} KR days mapped)"
    )
    return len(frame)


def _parse_assets(raw: str) -> tuple[str, ...]:
    return tuple(a.strip() for a in raw.split(",") if a.strip())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, help="US daily-bar CSV to ingest")
    parser.add_argument("--asset", help="asset label for the CSV (e.g. SPX)")
    parser.add_argument(
        "--out-root", type=Path, help="output root for <asset>.parquet"
    )
    parser.add_argument(
        "--build-calendar",
        action="store_true",
        help="build the KR->US calendar map",
    )
    parser.add_argument("--parquet-root", type=Path, help="toss minute parquet root")
    parser.add_argument("--us-root", type=Path, help="ingested US parquet root")
    parser.add_argument(
        "--assets",
        default=",".join(DEFAULT_ASSETS),
        help="comma-separated asset labels for --build-calendar",
    )
    parser.add_argument(
        "--calendar-out",
        default=DEFAULT_CALENDAR_OUT,
        help="output path for the calendar parquet",
    )
    args = parser.parse_args(argv)

    if args.csv is not None:
        if not args.asset or args.out_root is None:
            parser.error("--csv requires --asset and --out-root")
        n = import_us_daily_csv(
            args.csv, asset=args.asset, out_root=args.out_root
        )
        print(f"[ingest_us_daily] {args.asset}: {n} rows -> {args.out_root}")

    if args.build_calendar:
        if args.parquet_root is None or args.us_root is None:
            parser.error("--build-calendar requires --parquet-root and --us-root")
        build_calendar(
            parquet_root=args.parquet_root,
            us_root=args.us_root,
            assets=_parse_assets(args.assets),
            calendar_out=args.calendar_out,
        )

    if args.csv is None and not args.build_calendar:
        parser.error("nothing to do: pass --csv ... or --build-calendar")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
