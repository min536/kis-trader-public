"""Audit minute-cache date gaps and prepare API-free recovery dry runs."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.research.coverage.calendar_gaps import (
    build_calendar_gap_report,
    build_monthly_fetch_windows,
    build_plan_only_commands,
    calendar_gap_report_to_dict,
)


DEFAULT_ROOTS = (
    "data/toss_minute_parquet_backfill",
    "data/minute_parquet_live",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        default=None,
        help="partition root; repeat to combine historical and live stores",
    )
    parser.add_argument("--expected-start", default="")
    parser.add_argument("--expected-end", default="")
    parser.add_argument(
        "--calendar-csv",
        default="",
        help="optional trusted CSV containing a date column",
    )
    parser.add_argument(
        "--symbols-file",
        default="data/minute_universe/current_plus_etf200_20260624.txt",
    )
    parser.add_argument(
        "--raw-dir",
        default="data/minute_raw_gap_recovery",
        help="isolated destination proposed to the fetch dry run",
    )
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)

    roots = tuple(args.root or DEFAULT_ROOTS)
    expected_start = date.fromisoformat(args.expected_start) if args.expected_start else None
    expected_end = (
        date.fromisoformat(args.expected_end) if args.expected_end else _today_kst()
    )
    calendar_dates = (
        _read_calendar_dates(Path(args.calendar_csv)) if args.calendar_csv else ()
    )

    report = build_calendar_gap_report(
        roots,
        expected_start=expected_start,
        expected_end=expected_end,
        expected_trading_dates=calendar_dates,
    )
    windows = build_monthly_fetch_windows(report)
    commands = build_plan_only_commands(
        windows,
        symbols_file=args.symbols_file,
        raw_dir=args.raw_dir,
    )
    payload = {
        "gap_report": calendar_gap_report_to_dict(report),
        "calendar_source": _calendar_source_summary(args.calendar_csv, calendar_dates),
        "fetch_plan": {
            "window_count": len(windows),
            "windows": [
                {"start": window.start, "end": window.end, "reason": window.reason}
                for window in windows
            ],
            "symbols_file": args.symbols_file,
            "isolated_raw_dir": args.raw_dir,
            "commands": list(commands),
        },
        "safety": {
            "broker_api_called": False,
            "commands_are_plan_only": True,
            "market_holidays_inferred_from_weekdays": False,
        },
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


def _read_calendar_dates(calendar_csv: Path) -> tuple[str, ...]:
    dates: set[date] = set()
    with calendar_csv.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "date" not in reader.fieldnames:
            raise ValueError(f"calendar CSV must contain a date column: {calendar_csv}")
        for row in reader:
            raw = str(row.get("date", "")).strip()
            if raw:
                dates.add(date.fromisoformat(raw))
    return tuple(day.isoformat() for day in sorted(dates))


def _calendar_source_summary(source: str, dates: tuple[str, ...]) -> dict:
    return {
        "path": source or None,
        "trading_date_count": len(dates),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
    }


def _today_kst() -> date:
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


if __name__ == "__main__":
    raise SystemExit(main())
