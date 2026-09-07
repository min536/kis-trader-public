"""Read-only CLI: overseas (US stock) quotes snapshot.

Fetches live quotes for the configured (or supplied) US symbols and prints a
plain-text snapshot. Read-only: issues only KIS quotation queries, never an
order.

Example::

    python -m app.tools.overseas_report --symbols AAPL TSLA --exchange NASD
"""
from __future__ import annotations

import argparse
from typing import Optional, Sequence

from app.auth.settings import get_settings
from app.core.time_utils import get_korean_now
from app.overseas_runtime.report import build_overseas_report
from app.overseas_stock.config import load_overseas_config


def _resolve_symbols(args_symbols, settings):
    if args_symbols:
        return tuple(s.strip().upper() for s in args_symbols if s.strip())
    return load_overseas_config(settings).scan_symbols


def _format_report(report) -> str:
    lines = [
        f"Overseas snapshot @ {report.generated_at}",
        f"  env={report.environment}  account={report.account_masked}",
        "  quotes:",
    ]
    if report.quotes:
        for q in report.quotes:
            lines.append(
                f"    {q['symbol']:<6} {q['last_price']} {q['currency']} ({q['exchange_code']})"
            )
    else:
        lines.append("    (none)")
    if report.errors:
        lines.append("  errors:")
        for e in report.errors:
            lines.append(f"    {e['symbol']}: {e['error']}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.tools.overseas_report",
        description="Read-only overseas quotes snapshot.",
    )
    parser.add_argument(
        "--symbols", nargs="*", default=None,
        help="US symbols (default: OVERSEAS_SCAN_SYMBOLS).",
    )
    parser.add_argument(
        "--exchange", default=None,
        help="Trading exchange NASD/NYSE/AMEX (default: config).",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    symbols = _resolve_symbols(args.symbols, settings)
    if not symbols:
        print("no symbols (set --symbols or OVERSEAS_SCAN_SYMBOLS)")
        return 1
    report = build_overseas_report(
        symbols,
        exchange=args.exchange,
        settings=settings,
        now_iso=get_korean_now().isoformat(),
    )
    print(_format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
