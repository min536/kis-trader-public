"""Fetch Toss Securities minute bars into research raw CSV partitions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.research.ingest.toss_minute_fetcher import (
    fetch_toss_minute_csvs,
    summary_to_dict,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--symbols-file", default="")
    parser.add_argument("--raw-dir", default="data/toss_minute_raw")
    parser.add_argument("--before", default="")
    parser.add_argument("--rate-limit-per-second", type=float, default=4.5)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--retry-backoff-seconds", type=float, default=2.0)
    parser.add_argument("--max-pages-per-symbol", type=int, default=0)
    parser.add_argument("--limit-symbols", type=int, default=0)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--mode", default="best-effort")
    args = parser.parse_args(argv)

    symbols = _resolve_symbols(args)
    if args.limit_symbols > 0:
        symbols = symbols[: args.limit_symbols]

    plan = {
        "symbol_count": len(symbols),
        "raw_dir": args.raw_dir,
        "before": args.before or None,
        "rate_limit_per_second": args.rate_limit_per_second,
        "max_pages_per_symbol": args.max_pages_per_symbol,
        "plan_only": bool(args.plan_only),
    }
    print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
    if args.plan_only:
        return 0

    summary = fetch_toss_minute_csvs(
        symbols=symbols,
        raw_dir=args.raw_dir,
        before=args.before or None,
        rate_limit_per_second=args.rate_limit_per_second,
        max_retries=args.max_retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
        max_pages_per_symbol=args.max_pages_per_symbol,
        mode=args.mode,
    )
    print(json.dumps(summary_to_dict(summary), ensure_ascii=False, sort_keys=True))
    return 0


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    supplied = sum(bool(value) for value in (args.symbols.strip(), args.symbols_file))
    if supplied != 1:
        raise SystemExit("choose exactly one symbol source: --symbols or --symbols-file")
    if args.symbols_file:
        return _read_symbols_file(Path(args.symbols_file))
    return [item.strip() for item in args.symbols.split(",") if item.strip()]


def _read_symbols_file(path: Path) -> list[str]:
    symbols: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        symbols.extend(item.strip() for item in text.split(",") if item.strip())
    return symbols


if __name__ == "__main__":
    raise SystemExit(main())
