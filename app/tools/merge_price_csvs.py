"""Merge engine_backtest OHLCV CSV files.

This tool is file-only and does not call broker APIs.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


FIELDNAMES = ["date", "symbol", "open", "high", "low", "close", "volume", "prev_close"]


def merge_price_csvs(inputs: list[Path], output: Path) -> int:
    """Merge CSV rows, deduplicating by (date, symbol) and sorting output."""

    output.parent.mkdir(parents=True, exist_ok=True)

    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, str]] = []

    for path in inputs:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                key = (str(row.get("date") or ""), str(row.get("symbol") or ""))
                if key in seen:
                    continue
                seen.add(key)
                rows.append({name: str(row.get(name) or "") for name in FIELDNAMES})

    rows.sort(key=lambda row: (row["date"], row["symbol"]))

    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge engine_backtest OHLCV CSV files.")
    parser.add_argument("inputs", nargs="+", help="Input CSV files")
    parser.add_argument("--output", required=True, help="Merged CSV output path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    inputs = [Path(raw_path) for raw_path in args.inputs]
    output = Path(args.output)
    row_count = merge_price_csvs(inputs, output)
    print(f"Merged {len(inputs)} files / {row_count} rows -> {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
