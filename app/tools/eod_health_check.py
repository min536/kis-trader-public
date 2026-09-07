"""CLI: eod_health_check

Compact end-of-day health check for live-session verification.

This is an offline convenience wrapper around existing diagnostics tools. It
reuses live_health_check and postrun_diagnostics, adds latest-date detection,
and prints the most important end-of-day answers on one screen.

Usage:
    python3 -m app.tools.eod_health_check --account mock_12345678_01
    python3 -m app.tools.eod_health_check --date 20260409 --account mock_12345678_01
    python3 -m app.tools.eod_health_check --date 20260409 --account mock_12345678_01 --session REGULAR
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import sys
from pathlib import Path
from typing import Any

from app.core.file_read_limits import (
    LocalReadLimitError,
    iter_lines_bounded,
    iter_tail_lines_window,
)
from app.tools.live_health_check import build_health_summary
from app.tools.postrun_diagnostics import build_report

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CYCLE_SNAPSHOT_TAIL_LINES = 5000
_CYCLE_SNAPSHOT_WINDOW_BYTES = 64 * 1024 * 1024
_ACCOUNT_DATE_RE = re.compile(r"^candidate_outcomes_(?P<account>.+)_(?P<date>\d{8})\.jsonl$")
_GENERIC_DATE_RE = re.compile(r"^candidate_outcomes_(?P<date>\d{8})\.jsonl$")
_STATS_ACCOUNT_DATE_RE = re.compile(r"^cycle_stats_(?P<account>.+)_(?P<date>\d{8})\.jsonl$")
_STATS_GENERIC_DATE_RE = re.compile(r"^cycle_stats_(?P<date>\d{8})\.jsonl$")

_BOLD = "\033[1m"
_CYAN = "\033[96m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _h(text: str) -> str:
    return f"{_BOLD}{_CYAN}{text}{_RESET}"


def _ok(text: str) -> str:
    return f"{_GREEN}{text}{_RESET}"


def _warn(text: str) -> str:
    return f"{_YELLOW}{text}{_RESET}"


def _dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}"


def _bool_val(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def _ts_date(ts: Any) -> str:
    return str(ts or "")[:10].replace("-", "")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    try:
        for _lineno, raw in iter_lines_bounded(path, encoding="utf-8"):
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


def _load_recent_snapshots_for_date(
    path: Path, *, date: str, session: str
) -> list[dict[str, Any]]:
    """Tolerant cycle-snapshot loader: reads only the tail window (never the
    whole 381MB file) and skips oversized lines instead of raising, then filters
    to the requested date + session. (P0: EOD immediate unblock.)"""
    if not path.exists():
        return []
    rows = _decode_snapshot_rows(
        iter_tail_lines_window(
            path,
            max_lines=_CYCLE_SNAPSHOT_TAIL_LINES,
            window_bytes=_CYCLE_SNAPSHOT_WINDOW_BYTES,
            encoding="utf-8",
        )
    )
    return [
        row
        for row in rows
        if _ts_date(row.get("timestamp") or row.get("ts")) == date
        and _session_matches_snapshot(row, session)
    ]


def _decode_snapshot_rows(line_iter: Any) -> list[dict[str, Any]]:
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


def _session_matches_outcome(row: dict[str, Any], session: str) -> bool:
    if not session:
        return True
    return str(row.get("session") or "").upper() == session.upper()


def _session_matches_snapshot(row: dict[str, Any], session: str) -> bool:
    if not session:
        return True
    snap_session = str(((row.get("market_session") or {}).get("session")) or "").upper()
    return snap_session == session.upper()


def _scan_dates(
    logs_dir: Path,
    *,
    glob: str,
    account_re: re.Pattern[str],
    generic_re: re.Pattern[str],
    account: str,
) -> str | None:
    """Return the max date across account-scoped files (preferred) or generic
    fallback files matching the glob, or None when there are no matches."""
    account_dates: list[str] = []
    fallback_dates: list[str] = []
    for path in logs_dir.glob(glob):
        name = path.name
        matched_account = account_re.match(name)
        if matched_account and matched_account.group("account") == account:
            account_dates.append(matched_account.group("date"))
            continue
        matched_generic = generic_re.match(name)
        if matched_generic:
            fallback_dates.append(matched_generic.group("date"))
    if account_dates:
        return max(account_dates)
    if fallback_dates:
        return max(fallback_dates)
    return None


def _outcomes_date_set(logs_dir: Path, account: str) -> set[str]:
    dates: set[str] = set()
    for path in logs_dir.glob("candidate_outcomes*.jsonl"):
        name = path.name
        matched_account = _ACCOUNT_DATE_RE.match(name)
        if matched_account and matched_account.group("account") == account:
            dates.add(matched_account.group("date"))
            continue
        matched_generic = _GENERIC_DATE_RE.match(name)
        if matched_generic:
            dates.add(matched_generic.group("date"))
    return dates


def detect_latest_market_date(account: str) -> str:
    logs_dir = _PROJECT_ROOT / "logs"
    outcomes_date = _scan_dates(
        logs_dir,
        glob="candidate_outcomes*.jsonl",
        account_re=_ACCOUNT_DATE_RE,
        generic_re=_GENERIC_DATE_RE,
        account=account,
    )
    stats_date = _scan_dates(
        logs_dir,
        glob="cycle_stats*.jsonl",
        account_re=_STATS_ACCOUNT_DATE_RE,
        generic_re=_STATS_GENERIC_DATE_RE,
        account=account,
    )
    candidates = [d for d in (outcomes_date, stats_date) if d is not None]
    if not candidates:
        raise FileNotFoundError(
            f"No candidate_outcomes or cycle_stats logs found under "
            f"{logs_dir} for account={account}."
        )
    winning = max(candidates)
    if winning not in _outcomes_date_set(logs_dir, account):
        print(
            f"candidate_outcomes missing for {winning} — lane-mode observability "
            "gap (see docs/eod_lane_account_incident_20260704.md)",
            file=sys.stderr,
        )
    return winning


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "—"
    return f"{100.0 * part / whole:.0f}%"


def _fmt_optional_count(value: int | None) -> str:
    if value is None:
        return "—"
    return str(value)


def _fmt_pct_value(value: Any) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "—"


def _build_core_rescue_join_summary(*, account: str, date: str, session: str) -> dict[str, int]:
    candidate_rows = [
        row
        for row in _load_jsonl(_candidate_outcomes_path(account, date))
        if _session_matches_outcome(row, session)
    ]
    snapshots = _load_recent_snapshots_for_date(
        _cycle_snapshots_path(account), date=date, session=session
    )

    rescued_targets: set[tuple[str, str]] = set()
    rescued_cycles = 0
    for snapshot in snapshots:
        staged = ((snapshot.get("selection_details") or {}).get("staged_scan") or {})
        if not _bool_val(staged.get("core_rescue_applied")):
            continue
        rescued_cycles += 1
        cycle_id = str(snapshot.get("cycle_id") or "").strip()
        symbol = str(staged.get("core_rescue_selected_symbol") or "").strip()
        if cycle_id and symbol:
            rescued_targets.add((cycle_id, symbol))

    deep_count = 0
    final_count = 0
    executed_count = 0
    for row in candidate_rows:
        key = (
            str(row.get("cycle_id") or "").strip(),
            str(row.get("symbol") or "").strip(),
        )
        if key not in rescued_targets:
            continue
        if _bool_val(row.get("deep_evaluated")):
            deep_count += 1
        if _bool_val(row.get("final_candidate")):
            final_count += 1
        if _bool_val(row.get("executed")):
            executed_count += 1

    return {
        "core_rescue_cycles": rescued_cycles,
        "core_rescue_targets": len(rescued_targets),
        "core_rescue_deep_evaluated_rows": deep_count,
        "core_rescue_final_candidate_rows": final_count,
        "core_rescue_executed_rows": executed_count,
    }


def _build_core_rescue_stall_diagnosis(
    *,
    account: str,
    date: str,
    session: str,
) -> dict[str, Any]:
    candidate_rows = [
        row
        for row in _load_jsonl(_candidate_outcomes_path(account, date))
        if _session_matches_outcome(row, session)
    ]
    candidate_by_key = {
        (
            str(row.get("cycle_id") or "").strip(),
            str(row.get("symbol") or "").strip(),
        ): row
        for row in candidate_rows
    }
    snapshots = _load_recent_snapshots_for_date(
        _cycle_snapshots_path(account), date=date, session=session
    )

    selected_symbol_counts: Counter[str] = Counter()
    stage_counts: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()
    buy_skip_counts: Counter[str] = Counter()
    stop_reason_counts: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    rescued_cycles = 0
    candidate_matches = 0
    rate_limit_triggered_count = 0
    sell_watch_partial_count = 0

    for snapshot in snapshots:
        staged = ((snapshot.get("selection_details") or {}).get("staged_scan") or {})
        if not _bool_val(staged.get("core_rescue_applied")):
            continue
        rescued_cycles += 1
        cycle_id = str(snapshot.get("cycle_id") or "").strip()
        symbol = str(staged.get("core_rescue_selected_symbol") or "").strip()
        if symbol:
            selected_symbol_counts[symbol] += 1
        if _bool_val(snapshot.get("rate_limit_triggered")):
            rate_limit_triggered_count += 1
        if _bool_val(snapshot.get("sell_watch_partial")):
            sell_watch_partial_count += 1

        buy_skip_reason = str(snapshot.get("buy_scan_skipped_reason") or "").strip() or "none"
        buy_skip_counts[buy_skip_reason] += 1
        row = candidate_by_key.get((cycle_id, symbol))
        if row is None:
            stop_reason_counts["missing_candidate_row"] += 1
            continue

        candidate_matches += 1
        stage = str(row.get("stage_reached") or "unknown")
        outcome = str(row.get("selection_outcome") or "none")
        stage_counts[stage] += 1
        outcome_counts[outcome] += 1

        if _bool_val(row.get("deep_evaluated")):
            stop_reason = "deep_evaluated"
        elif outcome == "shortlisted_not_deep_evaluated":
            if buy_skip_reason == "rate_limit_detected":
                stop_reason = "shortlist_only_rate_limit_detected"
            elif _bool_val(snapshot.get("sell_watch_partial")):
                stop_reason = "shortlist_only_sell_watch_partial"
            else:
                stop_reason = "shortlist_only_other"
        else:
            stop_reason = f"stopped_at_{stage}"
        stop_reason_counts[stop_reason] += 1

        if len(samples) < 3:
            samples.append(
                {
                    "ts": snapshot.get("timestamp") or snapshot.get("ts"),
                    "cycle_id": cycle_id,
                    "symbol": symbol,
                    "stage_reached": stage,
                    "selection_outcome": outcome,
                    "buy_scan_skipped_reason": buy_skip_reason,
                    "rate_limit_triggered": bool(snapshot.get("rate_limit_triggered")),
                    "sell_watch_partial": bool(snapshot.get("sell_watch_partial")),
                }
            )

    return {
        "rescued_cycles": rescued_cycles,
        "candidate_matches": candidate_matches,
        "selected_symbol_counts": dict(selected_symbol_counts),
        "stage_counts": dict(stage_counts),
        "outcome_counts": dict(outcome_counts),
        "buy_skip_counts": dict(buy_skip_counts),
        "stop_reason_counts": dict(stop_reason_counts),
        "rate_limit_triggered_count": rate_limit_triggered_count,
        "sell_watch_partial_count": sell_watch_partial_count,
        "samples": samples,
    }


def _compact_counter_text(counter: dict[str, Any], *, limit: int = 3) -> str:
    pairs: list[tuple[str, int]] = []
    for key, value in counter.items():
        try:
            count = int(value or 0)
        except (TypeError, ValueError):
            continue
        pairs.append((str(key), count))
    if not pairs:
        return "—"
    pairs.sort(key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{key}:{count}" for key, count in pairs[:limit])


def print_eod_health_check(
    *,
    summary: dict[str, Any],
    report: dict[str, Any],
    core_rescue_join: dict[str, int],
    core_rescue_stall: dict[str, Any],
    auto_detected_date: bool,
) -> None:
    final_candidate_count = int(summary.get("final_candidate_count", 0) or 0)
    executed_count = int(summary.get("executed_count", 0) or 0)
    trade_budget_limited_count = int(summary.get("trade_budget_limited_count", 0) or 0)
    budget_rescue_applied_count = summary.get("budget_rescue_applied_count")
    core_rescue_applied_count = summary.get("core_rescue_applied_count")
    core_deep_evaluated_count = int(summary.get("core_deep_evaluated_count", 0) or 0)
    rate_limit_triggered_count = summary.get("rate_limit_triggered_count")
    sell_watch_partial_count = summary.get("sell_watch_partial_count")
    buy_scan_rl_skip_count = summary.get("buy_scan_rate_limit_skip_count")
    rate_limit_sources = summary.get("rate_limit_sources") or {}
    buy_signal_count = int(summary.get("buy_signal_count", 0) or 0)
    buy_signal_executed_count = int(summary.get("buy_signal_executed_count", 0) or 0)
    buy_order_success_count = int(summary.get("buy_order_success_count", 0) or 0)
    buy_notional_used_krw = int(summary.get("buy_notional_used_krw", 0) or 0)
    buy_notional_limit_krw = int(summary.get("buy_notional_limit_krw", 0) or 0)
    buy_notional_remaining_krw = int(summary.get("buy_notional_remaining_krw", 0) or 0)
    buy_notional_pressure_level = str(summary.get("buy_notional_pressure_level") or "—")
    late_day_buy_notional_blocked_count = int(
        summary.get("late_day_buy_notional_blocked_count", 0) or 0
    )
    stop_loss_ratio_pct = summary.get("stop_loss_ratio_pct")
    defensive_day = bool(summary.get("defensive_day"))
    sell_reason_distribution = summary.get("sell_reason_distribution") or {}
    same_day_reentries = list(summary.get("same_day_stop_loss_reentries") or [])
    warnings = list(summary.get("warnings") or [])
    threshold_crossed = summary.get("buy_notional_threshold_crossed_at") or {}
    threshold_text = ", ".join(
        f"{label}:{str(ts)[11:16]}"
        for label, ts in threshold_crossed.items()
        if ts
    ) or "—"
    same_day_reentry_text = (
        ", ".join(
            f"{item.get('symbol')}({item.get('minutes_gap')}m)"
            for item in same_day_reentries[:5]
        )
        if same_day_reentries
        else "—"
    )

    budget = report.get("budget", {}) or {}
    conclusion = report.get("conclusion", {}) or {}
    cycles_analyzed = int(((budget.get("meta") or {}).get("cycles_analyzed", 0)) or 0)
    bottleneck = str(conclusion.get("bottleneck") or "—")
    hint = str(conclusion.get("hint") or "").strip()

    print()
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(_h("  eod_health_check"))
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(f"  account : {summary['account']}")
    print(
        f"  date    : {summary['date']}"
        + (" " + _dim("(auto-detected latest market day)") if auto_detected_date else "")
    )
    print(f"  session : {summary['session']}")
    print(
        "  rows    : "
        f"candidate={summary['candidate_rows']} | "
        f"snapshots={summary['snapshot_rows']} | "
        f"stats={summary['stats_rows']}"
    )
    snapshot_rows_skipped_oversized = int(
        summary.get("snapshot_rows_skipped_oversized", 0) or 0
    )
    snapshot_tail_window_truncated = bool(
        summary.get("snapshot_tail_window_truncated")
    )
    if snapshot_rows_skipped_oversized > 0:
        print(
            _warn(
                "  WARN    : "
                f"{snapshot_rows_skipped_oversized} cycle-snapshot row(s) skipped "
                "(oversized line over read limit) — health counts partial"
            )
        )
    if snapshot_tail_window_truncated:
        print(
            _warn(
                "  WARN    : cycle-snapshot tail window truncated "
                "(file exceeds read window) — oldest rows omitted; "
                "run --rotate-active (see docs/eod_lane_account_incident_20260704.md)"
            )
        )
    print()
    print(_h("  1. Headline Counts"))
    print(f"  final_candidate        : {final_candidate_count}")
    print(f"  buy_signal             : {buy_signal_count}")
    print(f"  executed               : {_ok(str(executed_count)) if executed_count > 0 else executed_count}")
    print(f"  buy_signal_executed    : {buy_signal_executed_count}")
    print(f"  buy_order_success      : {buy_order_success_count}")
    print(
        "  signal/order aligned   : "
        + (
            _ok("yes")
            if bool(summary.get("buy_signal_order_alignment_ok"))
            else _warn("no")
        )
    )
    print(
        "  final->executed        : "
        f"{executed_count}/{final_candidate_count} ({_pct(executed_count, final_candidate_count)})"
    )
    print(
        "  trade_budget_limited   : "
        + (_warn(str(trade_budget_limited_count)) if trade_budget_limited_count > 0 else "0")
    )
    print(f"  budget_rescue_applied  : {_fmt_optional_count(budget_rescue_applied_count)}")
    print(f"  core_rescue_applied    : {_fmt_optional_count(core_rescue_applied_count)}")
    print(f"  core deep_evaluated    : {core_deep_evaluated_count}")
    print()
    print(_h("  2. Rescue / Core Read"))
    budget_rescue_text = (
        _ok("yes")
        if isinstance(budget_rescue_applied_count, int) and budget_rescue_applied_count > 0
        else _dim("no")
        if isinstance(budget_rescue_applied_count, int)
        else "—"
    )
    print(f"  did budget rescue fire : {budget_rescue_text}")
    print(
        "  core rescue -> deep    : "
        f"{core_rescue_join['core_rescue_deep_evaluated_rows']}"
        f"/{core_rescue_join['core_rescue_targets']} rescued rows reached deep_eval"
    )
    print(
        "  core rescue -> final   : "
        f"{core_rescue_join['core_rescue_final_candidate_rows']}"
        f"/{core_rescue_join['core_rescue_targets']} rescued rows reached final_candidate"
    )
    print(
        "  core rescue -> exec    : "
        f"{core_rescue_join['core_rescue_executed_rows']}"
        f"/{core_rescue_join['core_rescue_targets']} rescued rows executed"
    )
    print()
    print(_h("  3. Operational Pressure"))
    print(f"  cycles analyzed        : {cycles_analyzed}")
    print(
        "  buy notional used      : "
        f"{buy_notional_used_krw:,}/{buy_notional_limit_krw:,} KRW"
    )
    print(f"  buy notional remaining : {buy_notional_remaining_krw:,} KRW")
    print(
        "  buy utilization        : "
        f"{_fmt_pct_value(summary.get('buy_notional_utilization_pct'))}"
    )
    print(f"  buy pressure level     : {buy_notional_pressure_level}")
    print(f"  threshold crossed      : {threshold_text}")
    print(
        "  notional blocks/hour   : "
        f"{_compact_counter_text(summary.get('blocked_buy_daily_notional_limit_by_hour') or {})}"
    )
    print(
        "  late-day limit blocks  : "
        f"{late_day_buy_notional_blocked_count}"
    )
    print(f"  rate_limit_triggered   : {_fmt_optional_count(rate_limit_triggered_count)}")
    print(f"  sell_watch_partial     : {_fmt_optional_count(sell_watch_partial_count)}")
    print(f"  buy_scan rl skips      : {_fmt_optional_count(buy_scan_rl_skip_count)}")
    if rate_limit_sources:
        source_text = ", ".join(f"{key}:{value}" for key, value in sorted(rate_limit_sources.items()))
        print(f"  rate_limit_sources     : {source_text}")
    else:
        print("  rate_limit_sources     : —")
    print()
    print(_h("  4. Defensive Read"))
    print(
        "  sell reasons           : "
        f"{_compact_counter_text(sell_reason_distribution)}"
    )
    print(f"  stop_loss share        : {_fmt_pct_value(stop_loss_ratio_pct)}")
    print(
        "  defensive day          : "
        + (_warn("yes") if defensive_day else "no")
    )
    print(f"  stop-loss reentries    : {same_day_reentry_text}")
    operator_note = str(summary.get("defensive_operator_note") or "").strip()
    if operator_note:
        print(f"  operator note          : {operator_note}")
    print()
    print(_h("  5. EOD Read"))
    print(
        "  budget rescue status   : "
        f"{_fmt_optional_count(budget_rescue_applied_count)} applied | "
        f"budget_limited={trade_budget_limited_count}"
    )
    print(
        "  execution conversion   : "
        f"{executed_count}/{final_candidate_count} "
        f"({_pct(executed_count, final_candidate_count)})"
    )
    print(
        "  core rescue status     : "
        f"{core_rescue_join['core_rescue_cycles']} rescue cycles | "
        f"rescued deep_eval={core_rescue_join['core_rescue_deep_evaluated_rows']}"
        f"/{core_rescue_join['core_rescue_targets']}"
    )
    print(
        "  rate-limit pressure    : "
        f"rl={_fmt_optional_count(rate_limit_triggered_count)}/{cycles_analyzed} | "
        f"sell_partial={_fmt_optional_count(sell_watch_partial_count)}/{cycles_analyzed}"
    )
    if warnings:
        print(f"  warnings               : {', '.join(str(item) for item in warnings)}")
    print(f"  postrun conclusion     : {bottleneck}")
    if hint and core_deep_evaluated_count > 0:
        print(f"  hint                   : {hint}")
    print()
    print(_h("  6. Core Rescue Stall Diagnosis"))
    print(
        "  rescued cycles         : "
        f"{core_rescue_stall['rescued_cycles']} | "
        f"candidate matches={core_rescue_stall['candidate_matches']}"
    )
    print(
        "  selected symbols       : "
        f"{_compact_counter_text(core_rescue_stall['selected_symbol_counts'])}"
    )
    print(
        "  reached stage          : "
        f"{_compact_counter_text(core_rescue_stall['stage_counts'])}"
    )
    print(
        "  selection outcome      : "
        f"{_compact_counter_text(core_rescue_stall['outcome_counts'])}"
    )
    print(
        "  stop reason            : "
        f"{_compact_counter_text(core_rescue_stall['stop_reason_counts'])}"
    )
    print(
        "  buy-scan skipped       : "
        f"{_compact_counter_text(core_rescue_stall['buy_skip_counts'])}"
    )
    print(
        "  pressure overlap       : "
        f"rate_limit={core_rescue_stall['rate_limit_triggered_count']}/{core_rescue_stall['rescued_cycles']} | "
        f"sell_partial={core_rescue_stall['sell_watch_partial_count']}/{core_rescue_stall['rescued_cycles']}"
    )
    samples = core_rescue_stall.get("samples") or []
    if samples:
        sample = samples[0]
        print(
            "  sample                 : "
            f"{sample.get('ts')} {sample.get('symbol')} "
            f"stage={sample.get('stage_reached')} "
            f"outcome={sample.get('selection_outcome')} "
            f"skip={sample.get('buy_scan_skipped_reason')}"
        )
    print()
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-command end-of-day health check for live-session verification."
    )
    parser.add_argument("--date", default="", help="Date YYYYMMDD. Defaults to latest available market-day log.")
    parser.add_argument("--account", required=True, help="Account identifier")
    parser.add_argument("--session", default="REGULAR", help="Optional session filter")
    parser.add_argument("--last-n-cycles", type=int, default=0, help="Optional cycle window passed through to postrun_diagnostics")
    args = parser.parse_args()

    date = str(args.date or "").strip()
    auto_detected_date = False
    if not date:
        try:
            date = detect_latest_market_date(args.account)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        auto_detected_date = True

    summary = build_health_summary(
        account=args.account,
        date=date,
        session=args.session,
    )
    report = build_report(
        account=args.account,
        date=date,
        session=args.session,
        last_n_cycles=args.last_n_cycles,
    )
    core_rescue_join = _build_core_rescue_join_summary(
        account=args.account,
        date=date,
        session=args.session,
    )
    core_rescue_stall = _build_core_rescue_stall_diagnosis(
        account=args.account,
        date=date,
        session=args.session,
    )
    print_eod_health_check(
        summary=summary,
        report=report,
        core_rescue_join=core_rescue_join,
        core_rescue_stall=core_rescue_stall,
        auto_detected_date=auto_detected_date,
    )


if __name__ == "__main__":
    main()
