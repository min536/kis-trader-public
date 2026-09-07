"""CLI: live_health_check

One-command diagnostics summary for next-day live verification.

This is an offline convenience tool. It reads existing logs and snapshots and
prints the most important operational counts on one screen.

Usage:
    python3 -m app.tools.live_health_check --date 20260408 --account <ACCOUNT>
    python3 -m app.tools.live_health_check --date 20260408 --account <ACCOUNT> --session REGULAR
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.backtest.reconstruct import build_budget_rescue_index
from app.core.file_read_limits import (
    LocalReadLimitError,
    iter_lines_bounded,
    iter_tail_lines_window,
)
from app.tools.analyze_budget_bottlenecks import run_analysis as run_budget_analysis
from app.tools.operational_day_diagnostics import build_operational_day_diagnostics

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_BOLD = "\033[1m"
_CYAN = "\033[96m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"
_CYCLE_SNAPSHOT_TAIL_LINES = 5000
_CYCLE_SNAPSHOT_WINDOW_BYTES = 64 * 1024 * 1024


def _h(text: str) -> str:
    return f"{_BOLD}{_CYAN}{text}{_RESET}"


def _ok(text: str) -> str:
    return f"{_GREEN}{text}{_RESET}"


def _warn(text: str) -> str:
    return f"{_YELLOW}{text}{_RESET}"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    try:
        line_iter = iter_lines_bounded(path, encoding="utf-8")
        for _lineno, raw in line_iter:
            raw = raw.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    except LocalReadLimitError as exc:
        raise RuntimeError(f"{path} exceeds local read limit") from exc
    return rows


def _decode_jsonl_rows(line_iter: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _lineno, raw in line_iter:
        raw = raw.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


@dataclass
class RecentSnapshotsResult:
    """Filtered cycle-snapshot rows plus tolerant-read diagnostics."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    skipped_oversized: int = 0
    tail_window_truncated: bool = False


def _load_recent_cycle_snapshots_for_date(
    path: Path,
    *,
    date: str,
    session: str,
    max_tail_lines: int = _CYCLE_SNAPSHOT_TAIL_LINES,
    window_bytes: int = _CYCLE_SNAPSHOT_WINDOW_BYTES,
    max_line_bytes: int | None = None,
) -> RecentSnapshotsResult:
    if not path.exists():
        return RecentSnapshotsResult()

    skipped: list[int] = []

    def _on_skip(_lineno: int, _byte_len: int) -> None:
        skipped.append(_byte_len)

    rows = _decode_jsonl_rows(
        iter_tail_lines_window(
            path,
            max_lines=max_tail_lines,
            window_bytes=window_bytes,
            encoding="utf-8",
            max_line_bytes=max_line_bytes,
            on_skip=_on_skip,
        )
    )
    tail_window_truncated = path.stat().st_size > window_bytes
    filtered = [
        row for row in rows
        if _ts_date(row.get("timestamp") or row.get("ts")) == date
        and _session_matches_snapshot(row, session)
    ]
    return RecentSnapshotsResult(
        rows=filtered,
        skipped_oversized=len(skipped),
        tail_window_truncated=tail_window_truncated,
    )


def _candidate_outcomes_path(account: str, date: str) -> Path:
    account_path = _PROJECT_ROOT / "logs" / f"candidate_outcomes_{account}_{date}.jsonl"
    if account_path.exists():
        return account_path
    return _PROJECT_ROOT / "logs" / f"candidate_outcomes_{date}.jsonl"


def _cycle_snapshots_path(account: str) -> Path:
    account_path = _PROJECT_ROOT / "data" / f"cycle_snapshots_{account}.jsonl"
    if account_path.exists():
        return account_path
    return _PROJECT_ROOT / "data" / "cycle_snapshots.jsonl"


def _cycle_stats_path(account: str, date: str) -> Path:
    account_path = _PROJECT_ROOT / "logs" / f"cycle_stats_{account}_{date}.jsonl"
    if account_path.exists():
        return account_path
    return _PROJECT_ROOT / "logs" / f"cycle_stats_{date}.jsonl"


def _ts_date(ts: Any) -> str:
    return str(ts or "")[:10].replace("-", "")


def _bool_val(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def _session_matches_outcome(row: dict[str, Any], session: str) -> bool:
    if not session:
        return True
    return str(row.get("session") or "").upper() == session.upper()


def _session_matches_snapshot(row: dict[str, Any], session: str) -> bool:
    if not session:
        return True
    snap_session = str(((row.get("market_session") or {}).get("session")) or "").upper()
    return snap_session == session.upper()


def _session_matches_stats(row: dict[str, Any], session: str) -> bool:
    if not session:
        return True
    return str(row.get("session") or "").upper() == session.upper()


def _fmt_optional_count(value: int | None) -> str:
    if value is None:
        return "—"
    return str(value)


def _fmt_counter(counter: dict[str, Any], *, limit: int = 5) -> str:
    pairs: list[tuple[str, int]] = []
    for key, value in (counter or {}).items():
        try:
            count = int(value or 0)
        except (TypeError, ValueError):
            continue
        pairs.append((str(key), count))
    if not pairs:
        return "—"
    pairs.sort(key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{key}:{count}" for key, count in pairs[:limit])


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "—"


def build_health_summary(*, account: str, date: str, session: str = "") -> dict[str, Any]:
    candidate_path = _candidate_outcomes_path(account, date)
    snapshot_path = _cycle_snapshots_path(account)
    stats_path = _cycle_stats_path(account, date)

    candidate_rows = _load_jsonl(candidate_path)
    if not candidate_rows:
        # P0-c: degrade gracefully instead of hard-failing. Under lane mode the
        # candidate_outcomes file may be absent while snapshots/stats still exist;
        # the health summary continues with empty funnel counts + a warning so the
        # EOD wrapper stays exit-0. (see docs/eod_lane_account_incident_20260704.md)
        print(
            f"No candidate_outcomes rows found for account={account} date={date} "
            "— continuing with empty funnel counts (lane-mode observability gap).",
            file=sys.stderr,
        )

    filtered_candidates = [
        row for row in candidate_rows
        if _session_matches_outcome(row, session)
    ]

    snapshots_result = _load_recent_cycle_snapshots_for_date(
        snapshot_path,
        date=date,
        session=session,
    )
    filtered_snapshots = snapshots_result.rows
    stats_rows = _load_jsonl(stats_path)
    filtered_stats = [
        row for row in stats_rows
        if _session_matches_stats(row, session)
    ]

    snapshot_context_available = bool(filtered_snapshots)
    stats_context_available = bool(filtered_stats)
    rescue_index = build_budget_rescue_index(filtered_snapshots) if snapshot_context_available else {}

    def _row_value(row: dict[str, Any], primary: str, fallback: str) -> Any:
        if primary in row and row.get(primary) is not None:
            return row.get(primary)
        return row.get(fallback)

    final_candidate_count = sum(_bool_val(row.get("final_candidate")) for row in filtered_candidates)
    buy_signal_count = sum(
        _bool_val(_row_value(row, "buy_signal", "final_candidate"))
        for row in filtered_candidates
    )
    executed_count = sum(_bool_val(row.get("executed")) for row in filtered_candidates)
    buy_signal_executed_count = sum(
        _bool_val(_row_value(row, "buy_signal_executed", "executed"))
        for row in filtered_candidates
    )
    trade_budget_limited_count = sum(
        1
        for row in filtered_candidates
        if str(row.get("rejection_reason") or "").strip() == "trade_budget_limited"
    )
    core_deep_evaluated_count = sum(
        1
        for row in filtered_candidates
        if _bool_val(row.get("deep_evaluated"))
        and str(row.get("selection_bucket") or "").strip() == "core"
    )
    budget_rescue_applied_count = None
    core_rescue_applied_count = None
    if snapshot_context_available:
        budget_rescue_applied_count = sum(
            1
            for payload in rescue_index.values()
            if _bool_val(payload.get("budget_rescue_applied"))
        )
        core_rescue_applied_count = sum(
            1
            for snapshot in filtered_snapshots
            if _bool_val(
                ((snapshot.get("selection_details") or {}).get("staged_scan") or {}).get(
                    "core_rescue_applied"
                )
            )
        )

    budget_summary = None
    flags: dict[str, Any] = {}
    sell_watch: dict[str, Any] = {}
    if snapshot_context_available or stats_context_available:
        budget_summary = run_budget_analysis(account=account, date=date, session=session)
        flags = dict(budget_summary.get("flags") or {})
        sell_watch = dict(budget_summary.get("sell_watch") or {})
    operational = build_operational_day_diagnostics(account=account, date=date)
    buy_notional = dict(operational.get("buy_notional") or {})
    sell_reasons = dict(operational.get("sell_reasons") or {})
    same_day_reentries = list(operational.get("same_day_stop_loss_reentries") or [])
    warnings = list(operational.get("warnings") or [])
    buy_order_success_count = int(buy_notional.get("buy_order_success_count", 0) or 0)

    return {
        "account": account,
        "date": date,
        "session": session or "ALL",
        "candidate_path": str(candidate_path),
        "snapshot_path": str(snapshot_path),
        "stats_path": str(stats_path),
        "candidate_rows": len(filtered_candidates),
        "snapshot_rows": len(filtered_snapshots),
        "snapshot_rows_skipped_oversized": snapshots_result.skipped_oversized,
        "snapshot_tail_window_truncated": snapshots_result.tail_window_truncated,
        "stats_rows": len(filtered_stats),
        "snapshot_context_available": snapshot_context_available,
        "stats_context_available": stats_context_available,
        "final_candidate_count": final_candidate_count,
        "buy_signal_count": buy_signal_count,
        "executed_count": executed_count,
        "buy_signal_executed_count": buy_signal_executed_count,
        "buy_order_success_count": buy_order_success_count,
        "buy_signal_order_alignment_ok": buy_signal_executed_count == buy_order_success_count,
        "trade_budget_limited_count": trade_budget_limited_count,
        "budget_rescue_applied_count": budget_rescue_applied_count,
        "core_rescue_applied_count": core_rescue_applied_count,
        "core_deep_evaluated_count": core_deep_evaluated_count,
        "order_log_path": operational.get("order_log_path"),
        "buy_notional_limit_krw": buy_notional.get("buy_notional_limit_krw"),
        "buy_notional_used_krw": buy_notional.get("buy_notional_used_krw"),
        "buy_notional_success_krw": buy_notional.get("buy_notional_success_krw"),
        "buy_notional_remaining_krw": buy_notional.get("buy_notional_remaining_krw"),
        "buy_notional_utilization_pct": buy_notional.get("buy_notional_utilization_pct"),
        "buy_notional_pressure_level": buy_notional.get("buy_notional_pressure_level"),
        "buy_notional_threshold_crossed_at": dict(
            buy_notional.get("buy_notional_threshold_crossed_at") or {}
        ),
        "blocked_buy_daily_notional_limit_count": buy_notional.get(
            "blocked_buy_daily_notional_limit_count"
        ),
        "blocked_buy_daily_notional_limit_by_hour": dict(
            buy_notional.get("blocked_buy_daily_notional_limit_by_hour") or {}
        ),
        "late_day_buy_notional_blocked_count": buy_notional.get(
            "late_day_buy_notional_blocked_count"
        ),
        "late_day_buy_notional_blocked_symbols": dict(
            buy_notional.get("late_day_buy_notional_blocked_symbols") or {}
        ),
        "sell_success_count": sell_reasons.get("sell_success_count"),
        "sell_reason_distribution": dict(sell_reasons.get("sell_reason_distribution") or {}),
        "stop_loss_ratio_pct": sell_reasons.get("stop_loss_ratio_pct"),
        "dominant_sell_reason": sell_reasons.get("dominant_sell_reason"),
        "dominant_sell_reason_share_pct": sell_reasons.get("dominant_sell_reason_share_pct"),
        "defensive_day": bool(sell_reasons.get("defensive_day")),
        "defensive_operator_note": sell_reasons.get("operator_note"),
        "same_day_stop_loss_reentries": same_day_reentries,
        "warnings": warnings,
        "rate_limit_triggered_count": (
            int(flags.get("rate_limit_triggered", 0) or 0)
            if budget_summary is not None
            else None
        ),
        "sell_watch_partial_count": (
            int(flags.get("sell_watch_partial", 0) or 0)
            if budget_summary is not None
            else None
        ),
        "buy_scan_rate_limit_skip_count": (
            int(flags.get("skipped_buy_scan_budget_limited", 0) or 0)
            if budget_summary is not None
            else None
        ),
        "rate_limit_sources": (
            dict(sell_watch.get("rate_limit_sources") or {})
            if budget_summary is not None
            else {}
        ),
    }


def print_health_summary(summary: dict[str, Any]) -> None:
    alignment_text = (
        _ok("yes")
        if summary.get("buy_signal_order_alignment_ok")
        else _warn("no")
    )
    threshold_crossed = summary.get("buy_notional_threshold_crossed_at") or {}
    threshold_text = ", ".join(
        f"{label}:{str(ts)[11:16]}"
        for label, ts in threshold_crossed.items()
        if ts
    ) or "—"
    same_day_reentries = summary.get("same_day_stop_loss_reentries") or []
    same_day_reentry_text = (
        ", ".join(
            f"{item.get('symbol')}({item.get('minutes_gap')}m)"
            for item in same_day_reentries[:5]
        )
        if same_day_reentries
        else "—"
    )

    print()
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(_h("  live_health_check"))
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(f"  account : {summary['account']}")
    print(f"  date    : {summary['date']}")
    print(f"  session : {summary['session']}")
    print(
        "  rows    : "
        f"candidate={summary['candidate_rows']} | "
        f"snapshots={summary['snapshot_rows']} | "
        f"stats={summary['stats_rows']}"
    )
    print()
    print(_h("  1. Trading Funnel Health"))
    print(f"  final_candidate        : {summary['final_candidate_count']}")
    print(f"  buy_signal             : {summary['buy_signal_count']}")
    executed_text = _ok(str(summary["executed_count"])) if summary["executed_count"] > 0 else str(summary["executed_count"])
    print(f"  executed               : {executed_text}")
    print(f"  buy_signal_executed    : {summary['buy_signal_executed_count']}")
    print(f"  buy_order_success      : {summary['buy_order_success_count']}")
    print(f"  signal/order aligned   : {alignment_text}")
    budget_limited_text = (
        _warn(str(summary["trade_budget_limited_count"]))
        if summary["trade_budget_limited_count"] > 0
        else "0"
    )
    print(f"  trade_budget_limited   : {budget_limited_text}")
    print(f"  budget_rescue_applied  : {_fmt_optional_count(summary['budget_rescue_applied_count'])}")
    print(f"  core_rescue_applied    : {_fmt_optional_count(summary['core_rescue_applied_count'])}")
    print(f"  core deep_evaluated    : {summary['core_deep_evaluated_count']}")
    print()
    print(_h("  2. Buy Notional Pressure"))
    print(
        "  used / limit           : "
        f"{summary.get('buy_notional_used_krw', 0):,} / "
        f"{summary.get('buy_notional_limit_krw', 0):,} KRW"
    )
    print(
        "  remaining              : "
        f"{int(summary.get('buy_notional_remaining_krw', 0) or 0):,} KRW"
    )
    print(f"  utilization            : {_fmt_pct(summary.get('buy_notional_utilization_pct'))}")
    print(f"  pressure level         : {summary.get('buy_notional_pressure_level') or '—'}")
    print(f"  threshold crossed      : {threshold_text}")
    print(
        "  blocked by hour        : "
        f"{_fmt_counter(summary.get('blocked_buy_daily_notional_limit_by_hour') or {})}"
    )
    print(
        "  late-day blocks        : "
        f"{_fmt_optional_count(summary.get('late_day_buy_notional_blocked_count'))}"
    )
    print(
        "  late-day blocked syms  : "
        f"{_fmt_counter(summary.get('late_day_buy_notional_blocked_symbols') or {})}"
    )
    print()
    print(_h("  3. Sell / Defensive Read"))
    print(
        "  sell reasons           : "
        f"{_fmt_counter(summary.get('sell_reason_distribution') or {})}"
    )
    print(f"  stop_loss share        : {_fmt_pct(summary.get('stop_loss_ratio_pct'))}")
    print(
        "  defensive day          : "
        + (_warn("yes") if summary.get("defensive_day") else "no")
    )
    print(f"  stop-loss reentries    : {same_day_reentry_text}")
    note = str(summary.get("defensive_operator_note") or "").strip()
    if note:
        print(f"  operator note          : {note}")
    warnings = summary.get("warnings") or []
    if warnings:
        print(f"  warnings               : {', '.join(str(item) for item in warnings)}")
    print()
    print(_h("  4. Rate-Limit / Partial Summary"))
    print(f"  rate_limit_triggered   : {_fmt_optional_count(summary['rate_limit_triggered_count'])}")
    print(f"  sell_watch_partial     : {_fmt_optional_count(summary['sell_watch_partial_count'])}")
    print(f"  buy_scan rl skips      : {_fmt_optional_count(summary['buy_scan_rate_limit_skip_count'])}")
    sources = summary.get("rate_limit_sources") or {}
    if sources:
        source_text = ", ".join(f"{key}:{value}" for key, value in sorted(sources.items()))
        print(f"  rate_limit_sources     : {source_text}")
    else:
        print("  rate_limit_sources     : —")
    print()
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="One-command live verification health check")
    parser.add_argument("--date", required=True, help="Date YYYYMMDD")
    parser.add_argument("--account", required=True, help="Account identifier")
    parser.add_argument("--session", default="", help="Optional session filter")
    args = parser.parse_args()

    summary = build_health_summary(
        account=args.account,
        date=args.date,
        session=args.session,
    )
    print_health_summary(summary)


if __name__ == "__main__":
    main()
