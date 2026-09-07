"""CLI: convert Toss minute backfill CSVs to parquet + minute-data quality gate (D1).

Reuses ``app.research.ingest.minute_csv_to_parquet.convert_minute_csv_dir`` for
the CSV->parquet conversion (never reimplemented) and
``app.research.coverage.quality.build_quality_report`` for the quality gate.

The raw layout is::

    <raw>/date=YYYY-MM-DD/symbol=<SYM>_<YYYYMMDD>.csv
    <raw>/_state/                 (skipped)
    <raw>/fetch_manifest.jsonl    (skipped)

``_state/`` and ``fetch_manifest.jsonl`` are skipped because the converter only
globs ``date=*`` directories and ``symbol=*.csv`` files, and the report
enumeration below reads only ``date=*/part.parquet`` from the parquet output.

Safety: no broker/KIS/Toss network calls; the 11GB real conversion is operator
work. This module is invoked with ``BUY_SCAN_QUOTE_KIS_ENV`` unset (replay/record
processes must never trigger live quote-token issuance).

Usage::

    python -m app.tools.build_backfill_parquet \
        --raw-dir data/toss_minute_raw_backfill \
        --out-dir data/toss_minute_parquet_backfill \
        --report results/gate2_backtest/quality_report.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.research.coverage.quality import build_quality_report
from app.research.ingest.minute_csv_to_parquet import convert_minute_csv_dir


def _discover_symbols_and_dates(out_dir: Path):
    """Return (sorted symbols, sorted dates) from the parquet output cache.

    Reads only ``<out_dir>/date=*/part.parquet`` — ``_state`` and
    ``fetch_manifest.jsonl`` never appear here, so they are skipped by
    construction.
    """
    import pandas as pd

    symbols: set[str] = set()
    dates: set[str] = set()
    for date_dir in sorted(out_dir.glob("date=*")):
        if not date_dir.is_dir():
            continue
        part = date_dir / "part.parquet"
        if not part.exists():
            continue
        dates.add(date_dir.name[len("date=") :])
        frame = pd.read_parquet(part, columns=["symbol"])
        symbols.update(str(s) for s in frame["symbol"].unique())
    return sorted(symbols), sorted(dates)


def run(raw_dir, out_dir, report_path) -> dict:
    """Convert ``raw_dir`` -> ``out_dir`` parquet, then write the quality report.

    Returns the quality report dict (also written to ``report_path``).
    """
    raw_dir = Path(raw_dir)
    out_dir = Path(out_dir)
    report_path = Path(report_path)

    # Conversion (existing function — never reimplemented). It only reads
    # date=*/symbol=*.csv, so _state/ and fetch_manifest.jsonl are skipped.
    convert_minute_csv_dir(raw_dir, out_dir)

    symbols, dates = _discover_symbols_and_dates(out_dir)
    report = build_quality_report(out_dir, symbols=symbols, dates=dates)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    return report


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(
        description=(
            "Convert Toss minute backfill CSVs to parquet and emit a "
            "minute-data quality report (D1)."
        )
    )
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)

    # Environment guard: replay/record must never issue live quote tokens.
    if os.environ.get("BUY_SCAN_QUOTE_KIS_ENV"):
        raise RuntimeError(
            "backfill build requires BUY_SCAN_QUOTE_KIS_ENV unset"
        )

    report = run(args.raw_dir, args.out_dir, args.report)
    excluded = report.get("exclude_recommended", [])
    print(
        f"quality report -> {args.report} "
        f"symbols={len(report.get('symbols', {}))} "
        f"dates={len(report.get('dates', []))} "
        f"exclude_recommended={len(excluded)}"
    )
    return report


if __name__ == "__main__":  # pragma: no cover
    main()
