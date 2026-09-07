"""CLI: preopen_readiness_check

Pre-open readiness summary for the next market session.

Default mode is offline-only so it is safe to run repeatedly before the market
opens. Optional online probes can verify token issuance and balance access.

Usage:
    python3 -m app.tools.preopen_readiness_check
    python3 -m app.tools.preopen_readiness_check --stale-minutes 30
    python3 -m app.tools.preopen_readiness_check --online
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.auth.account_scope import (
    get_account_scope_context,
    get_cycle_snapshots_path,
    get_order_log_path,
    get_performance_snapshots_path,
    get_runtime_state_path,
)
from app.auth.settings import get_settings
from app.auth.token import issue_access_token
from app.core.market_session import get_korean_market_session
from app.core.time_utils import KOREA_TZ, get_korean_now
from app.domestic_stock.balance import inquire_balance
from app.reporting.cycle_snapshots import load_recent_cycle_snapshots
from app.runtime_state import load_runtime_state
from app.tools.eod_health_check import detect_latest_market_date
from app.tools.live_health_check import build_health_summary

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


def _safe_iso_to_kst(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KOREA_TZ)
    return parsed.astimezone(KOREA_TZ)


def _file_readiness(path: Path, *, stale_minutes: int) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "status": "FAIL",
            "age_minutes": None,
            "updated_at": None,
            "reason": "파일이 없습니다.",
        }
    updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=KOREA_TZ)
    age_minutes = max(0.0, (get_korean_now() - updated_at).total_seconds() / 60.0)
    status = "OK" if age_minutes <= float(stale_minutes) else "WARN"
    return {
        "path": str(path),
        "exists": True,
        "status": status,
        "age_minutes": round(age_minutes, 1),
        "updated_at": updated_at.isoformat(),
        "reason": (
            "fresh"
            if status == "OK"
            else f"{stale_minutes}분 기준으로는 오래됐습니다."
        ),
    }


def _latest_cycle_summary(limit: int = 1) -> dict[str, Any]:
    records = load_recent_cycle_snapshots(limit=limit)
    if not records:
        return {}
    latest = records[-1]
    return {
        "timestamp": latest.get("timestamp"),
        "final_action": latest.get("final_action"),
        "final_reason": latest.get("final_reason"),
        "market_session": ((latest.get("market_session") or {}).get("session")),
        "rate_limit_source": latest.get("rate_limit_source"),
        "sell_watch_partial": bool(latest.get("sell_watch_partial")),
        "buy_scan_skipped_reason": latest.get("buy_scan_skipped_reason"),
    }


def _latest_health(account: str) -> dict[str, Any]:
    try:
        latest_date = detect_latest_market_date(account)
    except FileNotFoundError:
        return {"available": False}
    try:
        summary = build_health_summary(account=account, date=latest_date, session="REGULAR")
    except SystemExit:
        return {"available": False, "date": latest_date}
    return {
        "available": True,
        "date": latest_date,
        "summary": summary,
    }


def build_report(*, stale_minutes: int, online: bool) -> dict[str, Any]:
    settings = get_settings()
    context = get_account_scope_context(settings)
    runtime_state = load_runtime_state()
    session_status = get_korean_market_session()
    latest_cycle = _latest_cycle_summary()
    health = _latest_health(context["account_signature"])

    files = {
        "runtime_state": _file_readiness(get_runtime_state_path(settings), stale_minutes=stale_minutes),
        "cycle_snapshots": _file_readiness(get_cycle_snapshots_path(settings), stale_minutes=stale_minutes),
        "performance_snapshots": _file_readiness(get_performance_snapshots_path(settings), stale_minutes=stale_minutes),
        "order_log": _file_readiness(get_order_log_path(settings), stale_minutes=max(stale_minutes * 4, stale_minutes)),
    }

    checks: list[dict[str, Any]] = []
    last_cycle_started_at = _safe_iso_to_kst(runtime_state.get("last_cycle_started_at"))
    checks.append(
        {
            "name": "session_gate",
            "status": "OK" if session_status.session in {"PREMARKET", "CLOSED", "AFTER_MARKET"} else "WARN",
            "detail": f"current_session={session_status.session} | order_allowed={'YES' if session_status.order_allowed else 'NO'}",
        }
    )
    checks.append(
        {
            "name": "runtime_state",
            "status": "OK" if files["runtime_state"]["status"] == "OK" else files["runtime_state"]["status"],
            "detail": (
                f"last_action={runtime_state.get('last_action') or '-'} | "
                f"last_cycle_started_at={(last_cycle_started_at.isoformat() if last_cycle_started_at else '-')}"
            ),
        }
    )
    checks.append(
        {
            "name": "snapshot_writes",
            "status": "OK"
            if runtime_state.get("last_snapshot_write_ok") is not False
            and runtime_state.get("last_performance_write_ok") is not False
            else "WARN",
            "detail": (
                f"snapshot={runtime_state.get('last_snapshot_write_ok')} | "
                f"performance={runtime_state.get('last_performance_write_ok')}"
            ),
        }
    )
    checks.append(
        {
            "name": "sell_watch_pressure",
            "status": (
                "WARN"
                if runtime_state.get("last_sell_watch_partial")
                or str(runtime_state.get("rate_limit_source") or "").strip() == "sell_watch"
                else "OK"
            ),
            "detail": (
                f"partial={runtime_state.get('last_sell_watch_partial')} | "
                f"reason={runtime_state.get('last_sell_watch_partial_reason') or '-'} | "
                f"rate_limit_source={runtime_state.get('rate_limit_source') or '-'}"
            ),
        }
    )
    if health.get("available"):
        summary = health["summary"]
        checks.append(
            {
                "name": "latest_market_health",
                "status": (
                    "WARN"
                    if int(summary.get("sell_watch_partial_count") or 0) > 0
                    or int(summary.get("rate_limit_triggered_count") or 0) > 0
                    else "OK"
                ),
                "detail": (
                    f"date={health['date']} | final={summary.get('final_candidate_count')} | "
                    f"executed={summary.get('executed_count')} | "
                    f"rate_limit={summary.get('rate_limit_triggered_count')} | "
                    f"sell_partial={summary.get('sell_watch_partial_count')}"
                ),
            }
        )

    online_checks: list[dict[str, Any]] = []
    if online:
        try:
            token = issue_access_token()
            online_checks.append(
                {"name": "token_issue", "status": "OK", "detail": "access token 발급 성공"}
            )
        except Exception as exc:
            token = None
            online_checks.append(
                {"name": "token_issue", "status": "FAIL", "detail": f"token 발급 실패: {exc}"}
            )

        if token:
            try:
                balance = inquire_balance(token=token)
                status = "OK" if str(balance.get("rt_cd")) == "0" else "FAIL"
                online_checks.append(
                    {
                        "name": "balance_probe",
                        "status": status,
                        "detail": (
                            "잔고 조회 성공"
                            if status == "OK"
                            else f"잔고 조회 실패: {json.dumps(balance, ensure_ascii=False)[:180]}"
                        ),
                    }
                )
            except Exception as exc:
                online_checks.append(
                    {"name": "balance_probe", "status": "FAIL", "detail": f"잔고 조회 예외: {exc}"}
                )

    return {
        "generated_at": get_korean_now().isoformat(),
        "account": context,
        "session": {
            "session": session_status.session,
            "order_allowed": session_status.order_allowed,
            "reason": session_status.reason,
        },
        "files": files,
        "latest_cycle": latest_cycle,
        "health": health,
        "checks": checks,
        "online_checks": online_checks,
    }


def print_terminal(report: dict[str, Any]) -> None:
    print()
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(_h("  preopen_readiness_check"))
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(f"  generated_at : {report['generated_at']}")
    print(f"  account      : {report['account']['account_signature']}")
    print(f"  masked       : {report['account']['masked_account_display']}")
    print(
        f"  session      : {report['session']['session']} | "
        f"order_allowed={'YES' if report['session']['order_allowed'] else 'NO'}"
    )
    print(f"  reason       : {report['session']['reason']}")
    print()

    print(_h("  1. File Freshness"))
    for label, payload in report["files"].items():
        status = payload["status"]
        badge = _ok(status) if status == "OK" else (_warn(status) if status == "WARN" else _bad(status))
        age = "-" if payload["age_minutes"] is None else f"{payload['age_minutes']}m"
        print(f"  {label:<22} {badge:<12} age={age:<8} {payload['reason']}")
    print()

    print(_h("  2. Readiness Checks"))
    for check in report["checks"]:
        status = check["status"]
        badge = _ok(status) if status == "OK" else (_warn(status) if status == "WARN" else _bad(status))
        print(f"  {check['name']:<22} {badge:<12} {check['detail']}")
    print()

    latest_cycle = report.get("latest_cycle") or {}
    if latest_cycle:
        print(_h("  3. Latest Cycle"))
        print(
            f"  ts={latest_cycle.get('timestamp') or '-'} | "
            f"action={latest_cycle.get('final_action') or '-'} | "
            f"session={latest_cycle.get('market_session') or '-'}"
        )
        print(
            f"  rate_limit_source={latest_cycle.get('rate_limit_source') or '-'} | "
            f"sell_partial={latest_cycle.get('sell_watch_partial')} | "
            f"buy_skip={latest_cycle.get('buy_scan_skipped_reason') or '-'}"
        )
        print(f"  reason={latest_cycle.get('final_reason') or '-'}")
        print()

    health = report.get("health") or {}
    if health.get("available"):
        summary = health["summary"]
        print(_h("  4. Latest Market Health"))
        print(
            f"  date={health['date']} | final={summary.get('final_candidate_count')} | "
            f"executed={summary.get('executed_count')} | "
            f"rate_limit={summary.get('rate_limit_triggered_count')} | "
            f"sell_partial={summary.get('sell_watch_partial_count')}"
        )
        print()

    online_checks = report.get("online_checks") or []
    if online_checks:
        print(_h("  5. Online Probes"))
        for check in online_checks:
            status = check["status"]
            badge = _ok(status) if status == "OK" else (_bad(status) if status == "FAIL" else _warn(status))
            print(f"  {check['name']:<22} {badge:<12} {check['detail']}")
        print()
    elif not online_checks:
        print(_dim("  online probes: skipped (run with --online)"))
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="다음 장 시작 전 엔진/로그/API readiness 를 한 번에 점검합니다."
    )
    parser.add_argument(
        "--stale-minutes",
        type=int,
        default=30,
        help="파일 freshness 경고 기준 분 (기본값: 30)",
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="token 발급과 잔고 조회까지 online probe 를 수행",
    )
    args = parser.parse_args()

    report = build_report(
        stale_minutes=max(1, args.stale_minutes),
        online=args.online,
    )
    print_terminal(report)


if __name__ == "__main__":
    main()
