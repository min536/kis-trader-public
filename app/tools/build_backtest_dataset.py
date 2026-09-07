"""CLI: build_backtest_dataset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.backtest.reconstruct import (
    build_backtest_signals,
    summarize_signals,
    write_backtest_signals,
)

_BOLD = "\033[1m"
_CYAN = "\033[96m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"
_WIDTH = 68


def _header(text: str) -> str:
    return f"{_BOLD}{_CYAN}{text}{_RESET}"


def _ok(text: str) -> str:
    return f"{_GREEN}{text}{_RESET}"


def _warn(text: str) -> str:
    return f"{_YELLOW}{text}{_RESET}"


def _print_terminal_report(
    summary: dict[str, object],
    output_path: Path,
    *,
    account: str,
    date: str,
    session: str,
    deep_eval_only: bool,
) -> None:
    total = int(summary["total_signals"])
    covered = int(summary["market_data_available"])
    coverage_pct = float(summary["market_data_coverage_pct"])

    print()
    print(_header("═" * _WIDTH))
    print(_header("  build_backtest_dataset"))
    print(_header("═" * _WIDTH))
    print(f"  account : {account}")
    print(f"  date    : {date}")
    print(f"  session : {session or 'ALL'}")
    print(f"  stages  : {'deep_eval+' if deep_eval_only else 'all'}")
    print()
    print(_header("─" * _WIDTH))
    print(_header("  Coverage Summary"))
    print(_header("─" * _WIDTH))
    coverage_text = f"{covered} / {total} ({coverage_pct}%)"
    print(f"  Total signals       : {total}")
    print(f"  Market data covered : {_ok(coverage_text) if coverage_pct >= 80 else _warn(coverage_text)}")
    print(f"  Candidates          : {int(summary['candidates'])}")
    print(f"  Executed (entries)  : {int(summary['executed'])}")
    print(f"  With exit outcome   : {int(summary['with_exit_outcome'])}")
    print()
    print(_header("─" * _WIDTH))
    print(_header("  By Bucket"))
    print(_header("─" * _WIDTH))
    for bucket, count in sorted(dict(summary["by_bucket"]).items()):
        print(f"  {bucket:<16} {int(count):>5}")
    print()
    print(_header("─" * _WIDTH))
    print(_header("  Output"))
    print(_header("─" * _WIDTH))
    print(f"  {_ok('✓')} Written to: {output_path}")
    print()
    print(_header("═" * _WIDTH))
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build backtest dataset from existing logs")
    parser.add_argument("--date", required=True, help="Date YYYYMMDD")
    parser.add_argument("--account", required=True, help="Account identifier")
    parser.add_argument("--session", default="", help="Filter by session")
    parser.add_argument(
        "--all-stages",
        action="store_true",
        help="Include all candidate stages instead of only deep_eval+",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON summary")
    args = parser.parse_args()

    deep_eval_only = not args.all_stages
    signals = build_backtest_signals(
        account=args.account,
        date=args.date,
        session=args.session,
        deep_eval_only=deep_eval_only,
    )
    if not signals:
        print(
            f"No signals found for account={args.account} date={args.date} session={args.session or 'ALL'}",
            file=sys.stderr,
        )
        sys.exit(1)

    output_path = write_backtest_signals(signals, account=args.account, date=args.date)
    summary = summarize_signals(signals)
    if args.json:
        print(json.dumps({**summary, "output_path": str(output_path)}, indent=2, ensure_ascii=False))
    else:
        _print_terminal_report(
            summary,
            output_path,
            account=args.account,
            date=args.date,
            session=args.session,
            deep_eval_only=deep_eval_only,
        )


if __name__ == "__main__":
    main()
