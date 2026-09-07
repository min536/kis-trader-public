"""CLI: measure submit-side slippage from the order log (W4). Bounded read, no broker."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.auth.account_scope import get_order_log_path
from app.auth.settings import get_settings
from app.core.file_read_limits import iter_lines_bounded
from app.core.time_utils import get_korean_now
from app.reporting.slippage_measure import build_slippage_report_summary
from app.reporting.slippage_report import format_slippage_report


def _date_arg(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError(f"--date must be YYYY-MM-DD, got {value!r}")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure submit-side slippage from the order log (bounded read).",
    )
    parser.add_argument(
        "--date",
        type=_date_arg,
        default=None,
        help="Filter to YYYY-MM-DD (KST) and label the report; default = today (KST).",
    )
    return parser.parse_args(argv)


def load_order_log_records(path: Path, *, date_prefix: str | None = None) -> list[dict[str, Any]]:
    """Bounded read of the JSONL order log into dict records (no broker call).

    ``date_prefix`` (``YYYY-MM-DD``) keeps only records whose KST-isoformat
    ``timestamp`` starts with that date. Junk lines / non-dict rows are skipped.
    """
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for _lineno, raw_line in iter_lines_bounded(path):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if date_prefix is not None:
            timestamp = str(record.get("timestamp", "")).strip()
            if not timestamp.startswith(date_prefix):
                continue
        records.append(record)
    return records


_SCOPE_NOTE = (
    "note: submit-side only (quote_at_submit vs reference). Today quote==reference "
    "and there is no fill price, so this reads ~0bps — infra ready; real fill "
    "slippage needs a live-shadow/fill source (operator gate)."
)


def build_slippage_report_text(records, *, settings, report_date: str) -> str:
    summary = build_slippage_report_summary(
        records,
        report_date=report_date,
        policy_buy_bps=settings.buy_slippage_bps,
        policy_sell_bps=settings.sell_slippage_bps,
    )
    return format_slippage_report(summary) + "\n" + _SCOPE_NOTE


def main(argv=None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    path = get_order_log_path(settings)
    records = load_order_log_records(path, date_prefix=args.date)
    report_date = (
        args.date.replace("-", "")
        if args.date
        else get_korean_now().strftime("%Y%m%d")
    )
    print(build_slippage_report_text(records, settings=settings, report_date=report_date))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
