"""CLI: rate_limit_audit

Offline audit for recent KIS rate-limit pressure.

Usage:
    python -m app.tools.rate_limit_audit --days 5
    python -m app.tools.rate_limit_audit --account mock_acct_x --date 20260609
    python -m app.tools.rate_limit_audit --days 5 --json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from app.core.jsonl import read_jsonl_objects

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CYCLE_STATS_RE = re.compile(r"^cycle_stats_(?P<account>.+)_(?P<date>\d{8})\.jsonl$")
_ALL_SESSION_VALUES = {"", "ALL", "*"}


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _date_from_ts(value: object) -> str:
    return _clean_text(value)[:10].replace("-", "")


def _safe_int(value: object) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _session_filter_enabled(session: str | None) -> bool:
    return _clean_text(session).upper() not in _ALL_SESSION_VALUES


def _row_session(row: dict[str, Any]) -> str:
    market_session = row.get("market_session")
    if isinstance(market_session, dict):
        nested = _clean_text(market_session.get("session"))
        if nested:
            return nested.upper()
    return _clean_text(row.get("session")).upper()


def _matches_session(row: dict[str, Any], session: str | None) -> bool:
    if not _session_filter_enabled(session):
        return True
    return _row_session(row) == _clean_text(session).upper()


def _matches_date(row: dict[str, Any], date: str) -> bool:
    row_date = _date_from_ts(row.get("timestamp") or row.get("ts"))
    return row_date == date


def _load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], []
    return read_jsonl_objects(path)


def _discover_cycle_stats(root: Path) -> list[dict[str, object]]:
    logs_dir = root / "logs"
    entries: list[dict[str, object]] = []
    for path in logs_dir.glob("cycle_stats_*_*.jsonl"):
        match = _CYCLE_STATS_RE.match(path.name)
        if not match:
            continue
        entries.append(
            {
                "account": match.group("account"),
                "date": match.group("date"),
                "path": path,
            }
        )
    entries.sort(key=lambda item: (str(item["date"]), str(item["account"])))
    return entries


def _target_dates(
    entries: list[dict[str, object]],
    *,
    days: int,
    date: str | None,
    account: str | None,
) -> list[str]:
    if date:
        return [_clean_text(date)]
    dates = sorted(
        {
            str(entry["date"])
            for entry in entries
            if not account or str(entry["account"]) == account
        }
    )
    if days <= 0:
        return dates
    return dates[-days:]


def _accounts_for_date(
    entries: list[dict[str, object]],
    *,
    date: str,
    account: str | None,
) -> list[str]:
    if account:
        return [account]
    accounts = sorted(
        {
            str(entry["account"])
            for entry in entries
            if str(entry["date"]) == date
        }
    )
    return accounts


def _cycle_stats_path(root: Path, account: str, date: str) -> Path:
    return root / "logs" / f"cycle_stats_{account}_{date}.jsonl"


def _cycle_snapshots_path(root: Path, account: str) -> Path:
    account_path = root / "data" / f"cycle_snapshots_{account}.jsonl"
    if account_path.exists():
        return account_path
    return root / "data" / "cycle_snapshots.jsonl"


def _source_from_row(row: dict[str, Any]) -> str:
    direct = _clean_text(row.get("rate_limit_source"))
    if direct:
        return direct
    budget_status = row.get("last_budget_status")
    if isinstance(budget_status, dict):
        return _clean_text(budget_status.get("last_rate_limit_source"))
    return ""


def _row_has_rate_limit(row: dict[str, Any]) -> bool:
    if row.get("rate_limit_triggered") is True:
        return True
    if _safe_int(row.get("backoff_applied_seconds")) > 0:
        return True
    return bool(_clean_text(row.get("rate_limit_source")))


def _analyze_account_day(
    *,
    root: Path,
    account: str,
    date: str,
    session: str,
) -> dict[str, Any]:
    stats_rows, stats_errors = _load_jsonl(_cycle_stats_path(root, account, date))
    stats_rows = [row for row in stats_rows if _matches_session(row, session)]

    snapshot_path = _cycle_snapshots_path(root, account)
    snapshot_rows_all, snapshot_errors = _load_jsonl(snapshot_path)
    snapshot_rows = [
        row
        for row in snapshot_rows_all
        if _matches_date(row, date) and _matches_session(row, session)
    ]

    source_counts: Counter[str] = Counter()
    sell_watch_drain_count = 0
    balance_sell_watch_drain_count = 0
    scheduler_backoff_wait_count = 0
    backoff_skip_count = 0
    max_backoff_seconds = 0
    previous_source = ""

    for row in snapshot_rows:
        source = _source_from_row(row)
        if _row_has_rate_limit(row):
            if source:
                source_counts[source] += 1
            max_backoff_seconds = max(
                max_backoff_seconds,
                _safe_int(row.get("backoff_applied_seconds")),
            )
            budget_status = row.get("last_budget_status")
            if isinstance(budget_status, dict):
                max_backoff_seconds = max(
                    max_backoff_seconds,
                    _safe_int(budget_status.get("backoff_remaining_seconds")),
                )

        drain_ms = _safe_float(row.get("sell_watch_backoff_drain_ms"))
        if drain_ms > 0:
            sell_watch_drain_count += 1
            related_source = source or previous_source
            if related_source == "balance":
                balance_sell_watch_drain_count += 1

        scheduler_state = row.get("scheduler_state")
        if isinstance(scheduler_state, dict):
            if _clean_text(scheduler_state.get("decision")) == "API_BACKOFF_WAIT":
                scheduler_backoff_wait_count += 1

        final_action = _clean_text(row.get("final_action"))
        if final_action in {"HOLD_API_BACKOFF", "HOLD_BALANCE_RATE_LIMIT"}:
            backoff_skip_count += 1

        if source:
            previous_source = source

    return {
        "account": account,
        "date": date,
        "cycle_rows": len(stats_rows),
        "snapshot_rows": len(snapshot_rows),
        "rate_limit_sources": dict(sorted(source_counts.items())),
        "rate_limit_hits": sum(source_counts.values()),
        "sell_watch_drain_count": sell_watch_drain_count,
        "balance_sell_watch_drain_count": balance_sell_watch_drain_count,
        "scheduler_backoff_wait_count": scheduler_backoff_wait_count,
        "backoff_skip_count": backoff_skip_count,
        "max_backoff_seconds": max_backoff_seconds,
        "warnings": tuple(
            [
                (
                    "WARNING: rate-limit balance->sell_watch drain detected "
                    f"(date={date} count={balance_sell_watch_drain_count})."
                )
            ]
            if balance_sell_watch_drain_count
            else []
        ),
        "load_errors": tuple(stats_errors + snapshot_errors),
    }


def _merge_day_summaries(date: str, account_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    source_counts: Counter[str] = Counter()
    warnings: list[str] = []
    load_errors: list[str] = []
    for summary in account_summaries:
        source_counts.update(summary["rate_limit_sources"])
        warnings.extend(summary["warnings"])
        load_errors.extend(summary["load_errors"])

    return {
        "date": date,
        "accounts": tuple(summary["account"] for summary in account_summaries),
        "cycle_rows": sum(int(summary["cycle_rows"]) for summary in account_summaries),
        "snapshot_rows": sum(int(summary["snapshot_rows"]) for summary in account_summaries),
        "rate_limit_sources": dict(sorted(source_counts.items())),
        "rate_limit_hits": sum(source_counts.values()),
        "sell_watch_drain_count": sum(
            int(summary["sell_watch_drain_count"]) for summary in account_summaries
        ),
        "balance_sell_watch_drain_count": sum(
            int(summary["balance_sell_watch_drain_count"]) for summary in account_summaries
        ),
        "scheduler_backoff_wait_count": sum(
            int(summary["scheduler_backoff_wait_count"]) for summary in account_summaries
        ),
        "backoff_skip_count": sum(
            int(summary["backoff_skip_count"]) for summary in account_summaries
        ),
        "max_backoff_seconds": max(
            (int(summary["max_backoff_seconds"]) for summary in account_summaries),
            default=0,
        ),
        "warnings": tuple(warnings),
        "load_errors": tuple(load_errors),
    }


def build_rate_limit_audit(
    *,
    root: str | Path = _PROJECT_ROOT,
    days: int = 5,
    date: str | None = None,
    account: str | None = None,
    session: str = "REGULAR",
) -> dict[str, Any]:
    root_path = Path(root)
    account_text = _clean_text(account) or None
    date_text = _clean_text(date) or None
    entries = _discover_cycle_stats(root_path)
    dates = _target_dates(entries, days=days, date=date_text, account=account_text)
    day_summaries: list[dict[str, Any]] = []

    for target_date in dates:
        accounts = _accounts_for_date(entries, date=target_date, account=account_text)
        account_summaries = [
            _analyze_account_day(
                root=root_path,
                account=acct,
                date=target_date,
                session=session,
            )
            for acct in accounts
        ]
        day_summaries.append(_merge_day_summaries(target_date, account_summaries))

    source_counts: Counter[str] = Counter()
    warnings: list[str] = []
    for day_summary in day_summaries:
        source_counts.update(day_summary["rate_limit_sources"])
        warnings.extend(day_summary["warnings"])

    balance_drain_total = sum(
        int(day["balance_sell_watch_drain_count"]) for day in day_summaries
    )
    rate_limit_total = sum(int(day["rate_limit_hits"]) for day in day_summaries)
    if balance_drain_total:
        assessment = "balance_sell_watch_drain_detected"
    elif rate_limit_total:
        assessment = "rate_limits_without_balance_drain"
    else:
        assessment = "no_rate_limits"

    return {
        "meta": {
            "root": str(root_path),
            "account": account_text or "ALL",
            "session": _clean_text(session).upper() or "ALL",
            "days_requested": days,
            "date": date_text,
            "dates": tuple(dates),
        },
        "days": tuple(day_summaries),
        "totals": {
            "rate_limit_hits": rate_limit_total,
            "rate_limit_sources": dict(sorted(source_counts.items())),
            "sell_watch_drain_count": sum(
                int(day["sell_watch_drain_count"]) for day in day_summaries
            ),
            "balance_sell_watch_drain_count": balance_drain_total,
            "scheduler_backoff_wait_count": sum(
                int(day["scheduler_backoff_wait_count"]) for day in day_summaries
            ),
            "backoff_skip_count": sum(
                int(day["backoff_skip_count"]) for day in day_summaries
            ),
            "max_backoff_seconds": max(
                (int(day["max_backoff_seconds"]) for day in day_summaries),
                default=0,
            ),
        },
        "warnings": tuple(warnings),
        "assessment": assessment,
    }


def _format_sources(sources: dict[str, int]) -> str:
    if not sources:
        return "-"
    return ", ".join(f"{key}:{value}" for key, value in sorted(sources.items()))


def format_rate_limit_audit(audit: dict[str, Any]) -> str:
    meta = audit["meta"]
    totals = audit["totals"]
    lines = [
        "# rate_limit_audit",
        f"account={meta['account']} session={meta['session']} dates={','.join(meta['dates']) or '-'}",
        "",
        "date       cycles snaps hits sources                 sell_drain balance_drain backoff_wait skip max_backoff_s",
    ]
    for day in audit["days"]:
        lines.append(
            f"{day['date']:<10} "
            f"{int(day['cycle_rows']):>6} "
            f"{int(day['snapshot_rows']):>5} "
            f"{int(day['rate_limit_hits']):>4} "
            f"{_format_sources(day['rate_limit_sources']):<23} "
            f"{int(day['sell_watch_drain_count']):>10} "
            f"{int(day['balance_sell_watch_drain_count']):>13} "
            f"{int(day['scheduler_backoff_wait_count']):>12} "
            f"{int(day['backoff_skip_count']):>4} "
            f"{int(day['max_backoff_seconds']):>13}"
        )
    lines.extend(
        [
            "",
            "totals:"
            f" hits={totals['rate_limit_hits']}"
            f" sources={_format_sources(totals['rate_limit_sources'])}"
            f" sell_drain={totals['sell_watch_drain_count']}"
            f" balance_drain={totals['balance_sell_watch_drain_count']}"
            f" backoff_wait={totals['scheduler_backoff_wait_count']}"
            f" skip={totals['backoff_skip_count']}"
            f" max_backoff_s={totals['max_backoff_seconds']}",
            f"assessment={audit['assessment']}",
        ]
    )
    if audit["warnings"]:
        lines.append("")
        lines.extend(str(warning) for warning in audit["warnings"])
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit recent KIS rate-limit pressure.")
    parser.add_argument("--account", default=None, help="Optional account signature filter.")
    parser.add_argument("--date", default=None, help="Single YYYYMMDD date to audit.")
    parser.add_argument("--days", type=int, default=5, help="Recent unique cycle_stats dates.")
    parser.add_argument(
        "--session",
        default="REGULAR",
        help="Session filter; use ALL to include every session.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    parser.add_argument(
        "--root",
        type=Path,
        default=_PROJECT_ROOT,
        help="Project root; defaults to this repository.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    audit = build_rate_limit_audit(
        root=args.root,
        days=args.days,
        date=args.date,
        account=args.account,
        session=args.session,
    )
    if args.json:
        print(json.dumps(audit, ensure_ascii=False, sort_keys=True))
    else:
        print(format_rate_limit_audit(audit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
