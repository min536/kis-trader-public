"""CLI: overnight_tuning_report

Compact overnight report that summarizes where tomorrow's manual tuning
attention should go. This is report-only; it does not edit settings.

Usage:
    python3 -m app.tools.overnight_tuning_report --account mock_12345678_01
    python3 -m app.tools.overnight_tuning_report --account mock_12345678_01 --date 20260410
    python3 -m app.tools.overnight_tuning_report --account mock_12345678_01 --date 20260410 --session REGULAR
    python3 -m app.tools.overnight_tuning_report --account mock_12345678_01 --output-json
    python3 -m app.tools.overnight_tuning_report --account mock_12345678_01 --output-json --output-file path/to/out.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from app.tools.analyze_core_bucket import run_analysis as run_core_bucket_analysis
from app.tools.eod_health_check import detect_latest_market_date
from app.tools.live_health_check import build_health_summary
from app.tools.postrun_diagnostics import build_report

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

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


def _fval(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and value.strip() in ("", "None")):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 0:
        return (ordered[mid - 1] + ordered[mid]) / 2
    return ordered[mid]


def _fmt_num(value: float | None, *, decimals: int = 2, width: int = 7) -> str:
    if value is None:
        return f"{'—':>{width}}"
    return f"{value:>{width}.{decimals}f}"


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "—"
    return f"{100.0 * part / whole:.0f}%"


def _signal_outcomes_path(account: str, date: str) -> Path:
    csv_path = _PROJECT_ROOT / "logs" / f"signal_outcomes_{account}_{date}.csv"
    if csv_path.exists():
        return csv_path
    jsonl_path = _PROJECT_ROOT / "logs" / f"signal_outcomes_{account}_{date}.jsonl"
    if jsonl_path.exists():
        return jsonl_path
    return csv_path


def _load_outcome_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as handle:
            for raw in handle:
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
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _is_deep_eval_rejected(row: dict[str, Any]) -> bool:
    outcome = str(row.get("selection_outcome") or "").strip()
    if outcome == "deep_eval_rejected":
        return True
    return _bool_val(row.get("deep_evaluated")) and not _bool_val(row.get("final_candidate"))


def _median_return(rows: list[dict[str, Any]], col: str) -> float | None:
    values: list[float] = []
    for row in rows:
        parsed = _fval(row.get(col))
        if parsed is not None:
            values.append(parsed)
    return _median(values)


def _covered_count(rows: list[dict[str, Any]], col: str) -> int:
    return sum(1 for row in rows if _fval(row.get(col)) is not None)


def _build_outcome_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rescue_rows = [row for row in rows if _bool_val(row.get("core_rescue_applied"))]
    ordinary_core_rows = [
        row
        for row in rows
        if str(row.get("selection_bucket") or "").strip() == "core"
        and not _bool_val(row.get("core_rescue_applied"))
    ]
    rejected_rows = [row for row in rows if _is_deep_eval_rejected(row)]
    final_rows = [row for row in rows if _bool_val(row.get("final_candidate"))]
    pb_rows = [
        row
        for row in rows
        if str(row.get("rejection_reason") or "").strip() == "profit_buffer_insufficient"
    ]
    return {
        "rescue_rows": rescue_rows,
        "ordinary_core_rows": ordinary_core_rows,
        "rejected_rows": rejected_rows,
        "final_rows": final_rows,
        "profit_buffer_rows": pb_rows,
        "rescue_medians": {
            "ret_5m_bps": _median_return(rescue_rows, "ret_5m_bps"),
            "ret_30m_bps": _median_return(rescue_rows, "ret_30m_bps"),
            "ret_eod_bps": _median_return(rescue_rows, "ret_eod_bps"),
        },
        "ordinary_core_eod": _median_return(ordinary_core_rows, "ret_eod_bps"),
        "rejected_eod": _median_return(rejected_rows, "ret_eod_bps"),
        "final_eod": _median_return(final_rows, "ret_eod_bps"),
        "profit_buffer_eod": _median_return(pb_rows, "ret_eod_bps"),
    }


def _build_attention_hints(
    *,
    report: dict[str, Any],
    health: dict[str, Any],
    core_bucket: dict[str, Any],
    outcomes: dict[str, Any],
) -> list[tuple[str, str]]:
    hints: list[tuple[str, str]] = []

    bottleneck = str((report.get("conclusion") or {}).get("bottleneck") or "")
    core_reasons = (
        (core_bucket.get("core_losers_stats") or {}).get("reasons") or {}
    )
    passed_count_insufficient = int(core_reasons.get("passed_count_insufficient", 0) or 0)
    profit_buffer_insufficient = int(core_reasons.get("profit_buffer_insufficient", 0) or 0)

    if bottleneck == "strategy_rule_insufficiency" or passed_count_insufficient > 0:
        hints.append(("core_rules", "core passed_count gap still dominant"))

    rate_limit_triggered = int(health.get("rate_limit_triggered_count") or 0)
    sell_watch_partial = int(health.get("sell_watch_partial_count") or 0)
    budget_recommendations = list(((report.get("budget") or {}).get("recommendations") or []))
    rate_limit_sources = dict(health.get("rate_limit_sources") or {})
    sell_watch_rl = int(rate_limit_sources.get("sell_watch", 0) or 0)
    if (
        sell_watch_partial >= 20
        or sell_watch_rl >= 10
        or rate_limit_triggered >= 20
        or any("sell_watch" in str(rec).lower() for rec in budget_recommendations)
    ):
        hints.append(("operational_pressure", "sell_watch rate-limit pressure still high"))

    core_rescue_cycles = int(health.get("core_rescue_applied_count") or 0)
    core_rescue_deep = int(health.get("core_deep_evaluated_count") or 0)
    core_exec = int(((report.get("bucket_final_exec") or {}).get("core") or {}).get("executed", 0) or 0)
    rescue_rows = outcomes.get("rescue_rows") or []
    rescue_final_eod = outcomes.get("final_eod")
    rescue_reject_eod = outcomes.get("rejected_eod")
    rescue_eod = (outcomes.get("rescue_medians") or {}).get("ret_eod_bps")
    if core_rescue_cycles > 0 and rescue_rows:
        if core_exec == 0 and rescue_eod is not None and rescue_final_eod is not None and rescue_eod < rescue_final_eod:
            hints.append(
                (
                    "core_rules",
                    "core rescue now reaches deep but not final, and post-entry payoff still trails finals",
                )
            )
        elif core_exec == 0:
            hints.append(("core_rules", "core rescue now reaches deep but not final"))
        elif rescue_reject_eod is not None and rescue_eod is not None and rescue_eod <= rescue_reject_eod:
            hints.append(("core_rules", "core rescue is not yet outperforming ordinary rejects"))
    elif core_rescue_cycles > 0 and core_rescue_deep > 0 and core_exec == 0:
        hints.append(("core_rules", "core rescue now reaches deep but not final"))

    pb_eod = outcomes.get("profit_buffer_eod")
    if profit_buffer_insufficient > 0 and pb_eod is not None and pb_eod > 0:
        hints.append(("risk_filter", "profit_buffer may need review"))

    trade_budget_limited = int(health.get("trade_budget_limited_count") or 0)
    if trade_budget_limited > 0:
        hints.append(("execution", "trade_budget_limited is still showing up"))

    if not hints:
        generic_hint = str((report.get("conclusion") or {}).get("hint") or "").strip()
        if generic_hint:
            hints.append(("summary", generic_hint))
        else:
            hints.append(("summary", "no dominant tuning candidate stood out; re-check after next close"))

    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label, hint in hints:
        key = (label.strip().lower(), hint.strip().lower())
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append((label, hint))
        if len(deduped) >= 3:
            break
    return deduped


def build_json_output(
    *,
    account: str,
    date: str,
    session: str,
    health: dict[str, Any],
    report: dict[str, Any],
    core_bucket: dict[str, Any],
    outcomes: dict[str, Any],
    hints: list[tuple[str, str]],
) -> dict[str, Any]:
    """Return a structured JSON-serialisable summary for downstream consumers."""
    core_funnel = (report.get("bucket_final_exec") or {}).get("core") or {}
    conclusion = report.get("conclusion") or {}
    rc = (core_bucket.get("rule_contrast_interpretation") or {})
    rescue_medians = outcomes.get("rescue_medians") or {}
    rescue_rows = outcomes.get("rescue_rows") or []
    rate_limit_sources = dict(health.get("rate_limit_sources") or {})

    return {
        "report_type": "overnight_tuning_report",
        "account": account,
        "date": date,
        "session": session or "ALL",
        "execution": {
            "final_count": int(health.get("final_candidate_count") or 0),
            "executed_count": int(health.get("executed_count") or 0),
            "trade_budget_limited": int(health.get("trade_budget_limited_count") or 0),
        },
        "core_funnel": {
            "deep_eval": int(core_funnel.get("deep_eval") or 0),
            "final_candidate": int(core_funnel.get("final_candidate") or 0),
            "executed": int(core_funnel.get("executed") or 0),
        },
        "bottleneck": {
            "dominant": str(conclusion.get("bottleneck") or "unknown"),
            "hint": str(conclusion.get("hint") or ""),
            "dominant_core_reason": str(conclusion.get("dominant_core_reason") or "unknown"),
        },
        "core_rule_contrast": {
            "verdict": str(rc.get("verdict") or "unknown"),
            "gap_to_threshold": rc.get("gap_to_threshold"),
        },
        "rescue": {
            "cycles": int(health.get("core_rescue_applied_count") or 0),
            "deep_reached": sum(1 for r in rescue_rows if _bool_val(r.get("deep_evaluated"))),
            "final_reached": sum(1 for r in rescue_rows if _bool_val(r.get("final_candidate"))),
            "executed": sum(1 for r in rescue_rows if _bool_val(r.get("executed"))),
            "medians_bps": {
                "ret_5m": rescue_medians.get("ret_5m_bps"),
                "ret_30m": rescue_medians.get("ret_30m_bps"),
                "ret_eod": rescue_medians.get("ret_eod_bps"),
            },
            "compare_eod_bps": {
                "ordinary_core": outcomes.get("ordinary_core_eod"),
                "rejects": outcomes.get("rejected_eod"),
                "finals": outcomes.get("final_eod"),
            },
        },
        "operational_pressure": {
            "rate_limit_triggered": int(health.get("rate_limit_triggered_count") or 0),
            "sell_watch_partial": int(health.get("sell_watch_partial_count") or 0),
            "sell_watch_rate_limit": int(rate_limit_sources.get("sell_watch", 0) or 0),
        },
        "tuning_hints": [
            {"area": label, "message": msg} for label, msg in hints
        ],
    }


def print_report(
    *,
    account: str,
    date: str,
    session: str,
    health: dict[str, Any],
    report: dict[str, Any],
    core_bucket: dict[str, Any],
    outcomes: dict[str, Any],
    outcome_path: Path,
    hints: list[tuple[str, str]],
) -> None:
    core_bucket_funnel = ((report.get("bucket_final_exec") or {}).get("core") or {})
    conclusion = report.get("conclusion") or {}
    rescue_rows = outcomes.get("rescue_rows") or []
    rescue_medians = outcomes.get("rescue_medians") or {}

    print()
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(_h("  overnight_tuning_report"))
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(f"  account : {account}")
    print(f"  date    : {date}")
    print(f"  session : {session or 'ALL'}")
    if outcome_path.exists():
        print(f"  outcomes: {outcome_path.name}")
    else:
        print(f"  outcomes: {_dim('missing — run export/analyze_signal_outcome_dataset first')}")
    print()

    print(_h("  1. Summary"))
    final_count = int(health.get("final_candidate_count") or 0)
    executed_count = int(health.get("executed_count") or 0)
    trade_budget_limited = int(health.get("trade_budget_limited_count") or 0)
    print(
        "  execution conversion"
        f" : {executed_count}/{final_count} ({_pct(executed_count, final_count)})"
    )
    print(
        "  trade_budget_limited"
        f" : {trade_budget_limited}"
        + (f" ({_warn('attention')})" if trade_budget_limited > 0 else "")
    )
    print(
        "  strongest bottleneck"
        f" : {str(conclusion.get('bottleneck') or 'unknown')}"
    )
    hint_text = str(conclusion.get("hint") or "").strip()
    if hint_text:
        print(f"  bottleneck read      : {hint_text}")
    print()

    print(_h("  2. Core Funnel"))
    print(
        "  core deep/final/exec"
        f" : {int(core_bucket_funnel.get('deep_eval', 0) or 0)}"
        f" / {int(core_bucket_funnel.get('final_candidate', 0) or 0)}"
        f" / {int(core_bucket_funnel.get('executed', 0) or 0)}"
    )
    core_summary = str(core_bucket.get("human_summary") or "").strip()
    if core_summary:
        print(f"  core read            : {core_summary}")
    print()

    print(_h("  3. Core Rescue Effectiveness"))
    print(
        "  rescue cycles"
        f" : {int(health.get('core_rescue_applied_count') or 0)}"
    )
    print(
        "  rescue funnel"
        f" : deep={sum(1 for row in rescue_rows if _bool_val(row.get('deep_evaluated')))}"
        f" | final={sum(1 for row in rescue_rows if _bool_val(row.get('final_candidate')))}"
        f" | executed={sum(1 for row in rescue_rows if _bool_val(row.get('executed')))}"
    )
    if rescue_rows:
        print(
            "  rescue coverage"
            f" : any={sum(1 for row in rescue_rows if any(_fval(row.get(col)) is not None for col in ('ret_5m_bps', 'ret_30m_bps', 'ret_eod_bps')))}/{len(rescue_rows)}"
            f" | eod={_covered_count(rescue_rows, 'ret_eod_bps')}/{len(rescue_rows)}"
        )
        print(
            "  rescue medians (bps)"
            f" : 5m={_fmt_num(rescue_medians.get('ret_5m_bps')).strip()}"
            f" | 30m={_fmt_num(rescue_medians.get('ret_30m_bps')).strip()}"
            f" | eod={_fmt_num(rescue_medians.get('ret_eod_bps')).strip()}"
        )
        print(
            "  compare eod"
            f" : ordinary_core={_fmt_num(outcomes.get('ordinary_core_eod')).strip()}"
            f" | rejects={_fmt_num(outcomes.get('rejected_eod')).strip()}"
            f" | finals={_fmt_num(outcomes.get('final_eod')).strip()}"
        )
    else:
        print(_dim("  rescue outcome rows are not available in the current signal_outcomes dataset"))
    print()

    print(_h("  4. Tomorrow Manual Tuning Attention"))
    for idx, (label, hint) in enumerate(hints, start=1):
        print(f"  {idx}. [{label}] {hint}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compact overnight report for next-day manual tuning attention"
    )
    parser.add_argument("--account", required=True, help="Account identifier")
    parser.add_argument(
        "--date",
        default="",
        help="Date YYYYMMDD. Defaults to latest available candidate_outcomes date.",
    )
    parser.add_argument("--session", default="", help="Optional session filter")
    parser.add_argument(
        "--output-json",
        action="store_true",
        help="Emit structured JSON to stdout (or --output-file) instead of the human-readable report",
    )
    parser.add_argument(
        "--output-file",
        default="",
        help="Write JSON output to this file path (only used with --output-json)",
    )
    args = parser.parse_args()

    date = args.date or detect_latest_market_date(args.account)

    health = build_health_summary(account=args.account, date=date, session=args.session)
    report = build_report(account=args.account, date=date, session=args.session, last_n_cycles=0)
    core_bucket = run_core_bucket_analysis(account=args.account, date=date, session=args.session)

    outcome_path = _signal_outcomes_path(args.account, date)
    outcome_rows = _load_outcome_rows(outcome_path)
    if args.session:
        outcome_rows = [
            row
            for row in outcome_rows
            if str(row.get("session") or "").upper() == args.session.upper()
        ]
    outcomes = _build_outcome_summary(outcome_rows)
    hints = _build_attention_hints(
        report=report,
        health=health,
        core_bucket=core_bucket,
        outcomes=outcomes,
    )

    if args.output_json:
        payload = build_json_output(
            account=args.account,
            date=date,
            session=args.session,
            health=health,
            report=report,
            core_bucket=core_bucket,
            outcomes=outcomes,
            hints=hints,
        )
        serialised = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.output_file:
            out_path = Path(args.output_file)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(serialised, encoding="utf-8")
        else:
            sys.stdout.write(serialised + "\n")
        return

    print_report(
        account=args.account,
        date=date,
        session=args.session,
        health=health,
        report=report,
        core_bucket=core_bucket,
        outcomes=outcomes,
        outcome_path=outcome_path,
        hints=hints,
    )


if __name__ == "__main__":
    main()
