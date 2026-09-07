"""Fetch KIS domestic minute bars into research raw CSV partitions."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.research.ingest.kis_minute_fetcher import (
    build_current_plus_etf_symbols,
    download_kospi_master_etfs,
    fetch_minute_csvs,
    summary_to_dict,
)
from backtester.engine_backtest.settings_factory import make_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-plus-etf200", action="store_true")
    parser.add_argument("--target-count", type=int, default=200)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--symbols-file", default="")
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--raw-dir", default="data/minute_raw")
    parser.add_argument("--universe-output", default="")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--market-div-code", default="J")
    parser.add_argument("--start-time", default="090000")
    parser.add_argument("--end-time", default="153000")
    parser.add_argument("--include-past-data", default="Y")
    parser.add_argument("--include-fake-tick", default="")
    parser.add_argument("--delay", type=float, default=0.75)
    parser.add_argument("--mode", default="best-effort")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-backoff-seconds", type=float, default=2.0)
    parser.add_argument("--max-pages-per-symbol-day", type=int, default=8)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--rate-limit-per-second", type=float, default=0.0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit-symbols", type=int, default=0)
    parser.add_argument("--limit-days", type=int, default=0)
    args = parser.parse_args(argv)

    settings = make_settings()
    end_day = _parse_date(args.end) if args.end else _today_kst()
    start_day = _parse_date(args.start) if args.start else end_day - timedelta(days=365)
    if args.limit_days > 0:
        start_day = max(start_day, end_day - timedelta(days=args.limit_days - 1))

    symbols = _resolve_symbols(args, settings=settings, end_day=end_day)
    if args.limit_symbols > 0:
        symbols = symbols[: args.limit_symbols]

    print(
        json.dumps(
            {
                "symbol_count": len(symbols),
                "start": start_day.isoformat(),
                "end": end_day.isoformat(),
                "raw_dir": args.raw_dir,
                "plan_only": bool(args.plan_only),
                "rate_limit_per_second": args.rate_limit_per_second,
                "workers": args.workers,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if args.plan_only:
        return 0

    summary = fetch_minute_csvs(
        symbols=symbols,
        start_date=start_day,
        end_date=end_day,
        raw_dir=args.raw_dir,
        settings=settings,
        market_div_code=args.market_div_code,
        start_time=args.start_time,
        end_time=args.end_time,
        include_past_data=args.include_past_data,
        include_fake_tick=args.include_fake_tick,
        delay_seconds=args.delay,
        mode=args.mode,
        max_retries=args.max_retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
        max_pages_per_symbol_day=args.max_pages_per_symbol_day,
        workers=args.workers,
        rate_limit_per_second=args.rate_limit_per_second,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary_to_dict(summary), ensure_ascii=False, sort_keys=True))
    return 0


def _resolve_symbols(args: argparse.Namespace, *, settings, end_day: date) -> list[str]:
    supplied_sources = sum(
        bool(value)
        for value in (args.current_plus_etf200, args.symbols.strip(), args.symbols_file)
    )
    if supplied_sources != 1:
        raise SystemExit(
            "choose exactly one symbol source: --current-plus-etf200, --symbols, or --symbols-file"
        )

    if args.current_plus_etf200:
        current_symbols = list(settings.target_symbols)
        tagged_etfs = _load_tagged_etfs(Path("config/symbol_tags.yaml"))
        master_etfs = download_kospi_master_etfs()
        symbols = build_current_plus_etf_symbols(
            current_symbols=current_symbols,
            tagged_etfs=tagged_etfs,
            master_etfs=master_etfs,
            target_count=args.target_count,
        )
        output = (
            Path(args.universe_output)
            if args.universe_output
            else Path("data/minute_universe")
            / f"current_plus_etf{args.target_count}_{end_day.strftime('%Y%m%d')}.txt"
        )
        _write_symbols_file(output, symbols)
        print(
            json.dumps(
                {
                    "universe_output": str(output),
                    "current_symbol_count": len(current_symbols),
                    "added_symbol_count": max(len(symbols) - len(current_symbols), 0),
                    "master_etf_count": len(master_etfs),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return symbols

    if args.symbols_file:
        return _read_symbols_file(Path(args.symbols_file))
    return [item.strip() for item in args.symbols.split(",") if item.strip()]


def _load_tagged_etfs(path: Path) -> list[str]:
    try:
        import yaml
    except ImportError:
        return []
    if not path.exists():
        return []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = doc.get("symbols", doc) if isinstance(doc, dict) else {}
    codes: list[str] = []
    for code, meta in items.items():
        tags = meta.get("tags") or [] if isinstance(meta, dict) else []
        if "asset:etf" in tags:
            codes.append(str(code))
    return codes


def _read_symbols_file(path: Path) -> list[str]:
    symbols: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        symbols.extend(item.strip() for item in text.split(",") if item.strip())
    return symbols


def _write_symbols_file(path: Path, symbols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(symbols) + "\n", encoding="utf-8")


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw)


def _today_kst() -> date:
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


if __name__ == "__main__":
    raise SystemExit(main())
