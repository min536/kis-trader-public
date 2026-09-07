"""CLI: core_change_decision_report

Compact decision report for the next core-strategy change.
This is report-only and does not mutate live settings.

Usage:
    python3 -m app.tools.core_change_decision_report --account mock_12345678_01
    python3 -m app.tools.core_change_decision_report --account mock_12345678_01 --date 20260410
    python3 -m app.tools.core_change_decision_report --account mock_12345678_01 --date 20260410 --session REGULAR
    python3 -m app.tools.core_change_decision_report --account mock_12345678_01 --output-json
    python3 -m app.tools.core_change_decision_report --account mock_12345678_01 --output-json --output-file path/to/out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.tools.analyze_core_bucket import run_analysis as run_core_bucket_analysis
from app.tools.eod_health_check import detect_latest_market_date
from app.tools.live_health_check import build_health_summary
from app.tools.overnight_tuning_report import (
    _build_outcome_summary,
    _load_outcome_rows,
    _signal_outcomes_path,
)
from app.tools.postrun_diagnostics import build_report

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_BOLD = "\033[1m"
_CYAN = "\033[96m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _h(text: str) -> str:
    return f"{_BOLD}{_CYAN}{text}{_RESET}"


def _ok(text: str) -> str:
    return f"{_GREEN}{text}{_RESET}"


def _warn(text: str) -> str:
    return f"{_YELLOW}{text}{_RESET}"


def _bad(text: str) -> str:
    return f"{_RED}{text}{_RESET}"


def _dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}"


def _fmt_num(value: float | None, *, decimals: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{decimals}f}"


def _risk_text(level: str) -> str:
    if level == "low":
        return _ok(level)
    if level == "high":
        return _bad(level)
    return _warn(level)


def _timing_text(value: str) -> str:
    if value == "now":
        return _ok(value)
    if value == "not yet":
        return _warn(value)
    return value


def _direction_1(
    *,
    report: dict[str, Any],
    core: dict[str, Any],
    outcomes: dict[str, Any],
) -> dict[str, Any]:
    rc = core.get("rule_contrast_interpretation") or {}
    reasons = ((core.get("core_losers_stats") or {}).get("reasons") or {})
    rescue_eod = (outcomes.get("rescue_medians") or {}).get("ret_eod_bps")
    final_eod = outcomes.get("final_eod")
    support = [
        f"strongest bottleneck is `{str((report.get('conclusion') or {}).get('bottleneck') or 'unknown')}`",
        f"core deep-eval rejections are dominated by `passed_count_insufficient` ({int(reasons.get('passed_count_insufficient', 0) or 0)} rows)",
        (
            f"rule contrast says `{str(rc.get('verdict') or 'unknown')}`"
            f" with gap_to_threshold={rc.get('gap_to_threshold')}"
        ),
    ]
    against = [
        (
            "rescued core is still weak vs finals"
            f" (eod { _fmt_num(rescue_eod) } vs finals { _fmt_num(final_eod) } bps)"
            if rescue_eod is not None and final_eod is not None
            else "rescued core payoff evidence is still limited"
        ),
        "threshold relief could admit more weak core names if applied too broadly",
    ]
    return {
        "title": "Keep current core rule family, but reduce threshold pressure",
        "support": support,
        "against": against,
        "risk": "medium",
        "upside": "medium",
        "timing": "now",
    }


def _direction_2(
    *,
    core: dict[str, Any],
    outcomes: dict[str, Any],
) -> dict[str, Any]:
    shadow = core.get("shadow_analysis") or {}
    rescue_rows = outcomes.get("rescue_rows") or []
    support = [
        "rescued core still does not look strong enough to justify a larger live shift yet",
        (
            f"shadow evidence exists but is still small"
            f" (evaluated={int(shadow.get('evaluated_count', 0) or 0)}, passed={int(shadow.get('shadow_passed_count', 0) or 0)})"
        ),
        f"outcome sample for rescued core is still sparse ({len(rescue_rows)} rows)",
    ]
    against = [
        "execution conversion is already fine, so waiting alone does not remove the current core bottleneck",
        "core already reaches deep_eval, which suggests the next change can now target conversion rather than discovery",
    ]
    return {
        "title": "Keep current live logic, continue shadow evidence gathering",
        "support": support,
        "against": against,
        "risk": "low",
        "upside": "low-medium",
        "timing": "later",
    }


def _direction_3(
    *,
    core: dict[str, Any],
    outcomes: dict[str, Any],
) -> dict[str, Any]:
    rc = core.get("rule_contrast_interpretation") or {}
    shadow = core.get("shadow_analysis") or {}
    rescue_eod = (outcomes.get("rescue_medians") or {}).get("ret_eod_bps")
    reject_eod = outcomes.get("rejected_eod")
    support = [
        "shadow-style continuation family is at least observable in current logs",
        (
            f"shadow passed rows exist"
            f" (trend-gate passed={int(shadow.get('trend_gate_passed_count', 0) or 0)}, shadow-passed={int(shadow.get('shadow_passed_count', 0) or 0)})"
        ),
        "this direction could eventually give core its own continuation-oriented selection family",
    ]
    against = [
        f"current rule contrast is `{str(rc.get('verdict') or 'unknown')}`, which argues the immediate issue still looks more like threshold pressure than rule-family mismatch",
        (
            "rescued core is not yet outperforming rejects"
            if rescue_eod is not None and reject_eod is not None and rescue_eod <= reject_eod
            else "shadow/continuation evidence is still too thin to justify a live-family change"
        ),
    ]
    return {
        "title": "Move toward core-specific continuation family (shadow-style direction)",
        "support": support,
        "against": against,
        "risk": "high",
        "upside": "high",
        "timing": "not yet",
    }


def _print_direction(index: int, payload: dict[str, Any]) -> None:
    print(_h(f"  {index}. {payload['title']}"))
    print("  known support:")
    for line in payload["support"]:
        print(f"  - {line}")
    print("  known against:")
    for line in payload["against"]:
        print(f"  - {line}")
    print(f"  risk level      : {_risk_text(payload['risk'])}")
    print(f"  expected upside : {payload['upside']}")
    print(f"  decision timing : {_timing_text(payload['timing'])}")
    print()


def build_json_output(
    *,
    account: str,
    date: str,
    session: str,
    health: dict[str, Any],
    report: dict[str, Any],
    core: dict[str, Any],
    outcomes: dict[str, Any],
    directions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a structured JSON-serialisable summary for downstream consumers."""
    bfe = (report.get("bucket_final_exec") or {}).get("core") or {}
    conclusion = report.get("conclusion") or {}
    rc = core.get("rule_contrast_interpretation") or {}
    shadow = core.get("shadow_analysis") or {}
    rescue_rows = outcomes.get("rescue_rows") or []

    # Strip ANSI colour codes from any string values in directions
    import re
    _ansi_re = re.compile(r"\x1b\[[0-9;]*m")

    def _strip(v: Any) -> Any:
        if isinstance(v, str):
            return _ansi_re.sub("", v)
        return v

    clean_directions = []
    for d in directions:
        clean_directions.append({
            "title": _strip(d.get("title", "")),
            "support": [_strip(s) for s in (d.get("support") or [])],
            "against": [_strip(s) for s in (d.get("against") or [])],
            "risk": _strip(d.get("risk", "")),
            "upside": _strip(d.get("upside", "")),
            "timing": _strip(d.get("timing", "")),
        })

    return {
        "report_type": "core_change_decision_report",
        "account": account,
        "date": date,
        "session": session or "ALL",
        "known_facts": {
            "execution_conversion": f"{int(health.get('executed_count') or 0)}/{int(health.get('final_candidate_count') or 0)}",
            "trade_budget_limited": int(health.get("trade_budget_limited_count") or 0),
            "core_funnel": {
                "deep_eval": int(bfe.get("deep_eval") or 0),
                "final_candidate": int(bfe.get("final_candidate") or 0),
                "executed": int(bfe.get("executed") or 0),
            },
            "dominant_core_reason": str(conclusion.get("dominant_core_reason") or "unknown"),
            "rule_contrast": {
                "verdict": str(rc.get("verdict") or "unknown"),
                "gap_to_threshold": rc.get("gap_to_threshold"),
            },
            "rescue_payoff": {
                "sample_rows": len(rescue_rows),
                "eod_bps": (outcomes.get("rescue_medians") or {}).get("ret_eod_bps"),
                "finals_eod_bps": outcomes.get("final_eod"),
            },
            "shadow_evidence": {
                "available": bool(shadow.get("available")),
                "evaluated_count": int(shadow.get("evaluated_count") or 0),
                "trend_gate_passed": int(shadow.get("trend_gate_passed_count") or 0),
                "shadow_passed": int(shadow.get("shadow_passed_count") or 0),
            },
        },
        "candidate_directions": clean_directions,
        "decision_read": {
            "recommended": "direction_1",
            "reasoning": "current evidence points to threshold pressure first; rescue/shadow evidence still too weak for a larger family shift",
            "hold_for_later": "direction_2",
            "not_yet": "direction_3",
        },
    }


def print_report(
    *,
    account: str,
    date: str,
    session: str,
    health: dict[str, Any],
    report: dict[str, Any],
    core: dict[str, Any],
    outcomes: dict[str, Any],
    outcome_path: Path,
    directions: list[dict[str, Any]] | None = None,
) -> None:
    bfe = (report.get("bucket_final_exec") or {}).get("core") or {}
    shadow = core.get("shadow_analysis") or {}
    rc = core.get("rule_contrast_interpretation") or {}
    rescue_rows = outcomes.get("rescue_rows") or []
    rescue_eod = (outcomes.get("rescue_medians") or {}).get("ret_eod_bps")
    final_eod = outcomes.get("final_eod")

    print()
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(_h("  core_change_decision_report"))
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(f"  account : {account}")
    print(f"  date    : {date}")
    print(f"  session : {session or 'ALL'}")
    print(f"  outcomes: {outcome_path.name if outcome_path.exists() else 'missing'}")
    print()

    print(_h("  1. Known From Logs"))
    print(
        "  execution conversion"
        f" : {int(health.get('executed_count') or 0)}/{int(health.get('final_candidate_count') or 0)}"
    )
    print(f"  trade_budget_limited : {int(health.get('trade_budget_limited_count') or 0)}")
    print(
        "  core funnel          : "
        f"deep={int(bfe.get('deep_eval', 0) or 0)}"
        f" | final={int(bfe.get('final_candidate', 0) or 0)}"
        f" | executed={int(bfe.get('executed', 0) or 0)}"
    )
    print(
        "  dominant core reason : "
        f"{str((report.get('conclusion') or {}).get('dominant_core_reason') or 'unknown')}"
    )
    print(
        "  rule-contrast read   : "
        f"{str(rc.get('verdict') or 'unknown')}"
        f" | gap_to_threshold={rc.get('gap_to_threshold')}"
    )
    if rescue_rows:
        print(
            "  rescued core payoff  : "
            f"rows={len(rescue_rows)}"
            f" | eod={_fmt_num(rescue_eod)}"
            f" | finals={_fmt_num(final_eod)}"
        )
    if shadow.get("available"):
        print(
            "  shadow evidence      : "
            f"evaluated={int(shadow.get('evaluated_count', 0) or 0)}"
            f" | trend_pass={int(shadow.get('trend_gate_passed_count', 0) or 0)}"
            f" | shadow_pass={int(shadow.get('shadow_passed_count', 0) or 0)}"
        )
    print()

    print(_h("  2. Still Uncertain"))
    uncertain_lines = [
        f"rescued core outcome sample is still small ({len(rescue_rows)} rows)",
        (
            f"shadow pass evidence is still thin ({int(shadow.get('shadow_passed_count', 0) or 0)} row)"
            if shadow.get("available")
            else "shadow evidence is not available yet"
        ),
        "current logs show where core fails, but not yet whether a continuation family would beat a lighter threshold path",
    ]
    for line in uncertain_lines:
        print(f"  - {line}")
    print()

    if directions is None:
        directions = [
            _direction_1(report=report, core=core, outcomes=outcomes),
            _direction_2(core=core, outcomes=outcomes),
            _direction_3(core=core, outcomes=outcomes),
        ]
    print(_h("  3. Candidate Directions"))
    print()
    for idx, payload in enumerate(directions, start=1):
        _print_direction(idx, payload)

    print(_h("  4. Decision Read"))
    print("  next change candidate : " + _ok("Direction 1 looks best supported now"))
    print("  why                   : current evidence points to threshold pressure first, while rescue/shadow evidence is still too weak for a larger family shift")
    print("  hold for later        : Direction 2")
    print("  not yet               : Direction 3")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compact decision report for the next core-strategy change"
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
    core = run_core_bucket_analysis(account=args.account, date=date, session=args.session)
    outcome_path = _signal_outcomes_path(args.account, date)
    outcome_rows = _load_outcome_rows(outcome_path)
    if args.session:
        outcome_rows = [
            row
            for row in outcome_rows
            if str(row.get("session") or "").upper() == args.session.upper()
        ]
    outcomes = _build_outcome_summary(outcome_rows)

    directions = [
        _direction_1(report=report, core=core, outcomes=outcomes),
        _direction_2(core=core, outcomes=outcomes),
        _direction_3(core=core, outcomes=outcomes),
    ]

    if args.output_json:
        payload = build_json_output(
            account=args.account,
            date=date,
            session=args.session,
            health=health,
            report=report,
            core=core,
            outcomes=outcomes,
            directions=directions,
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
        core=core,
        outcomes=outcomes,
        outcome_path=outcome_path,
        directions=directions,
    )


if __name__ == "__main__":
    main()
