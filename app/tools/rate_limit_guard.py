"""Rate-limit guard for the mock KIS trading session.

The guard is intentionally outside ``app.main``: it observes the runtime state
and session lock, then starts or restarts the existing session wrapper only when
the mock account is in regular order-allowed market hours.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.auth.account_scope import get_account_scope_context, get_runtime_state_path
from app.auth.settings import PROJECT_ROOT
from app.core.market_session import MarketSessionStatus, get_korean_market_session
from app.core.session_lock import inspect_lock, process_alive
from app.core.time_utils import KOREA_TZ, get_korean_now


DEFAULT_STALE_CYCLE_SECONDS = 300
DEFAULT_RATE_LIMIT_HITS_THRESHOLD = 3
DEFAULT_BACKOFF_REMAINING_THRESHOLD_SECONDS = 180


@dataclass(frozen=True)
class GuardPaths:
    project_dir: Path
    log_dir: Path
    pid_file: Path
    lock_file: Path
    run_script: Path
    restart_script: Path
    guard_log_file: Path
    guard_state_file: Path


@dataclass(frozen=True)
class GuardDecision:
    action: str
    reason: str
    should_execute: bool
    command: tuple[str, ...] = ()


def default_paths() -> GuardPaths:
    log_dir = PROJECT_ROOT / "logs"
    return GuardPaths(
        project_dir=PROJECT_ROOT,
        log_dir=log_dir,
        pid_file=log_dir / "kis_trader.pid",
        lock_file=log_dir / "kis_trader.session.lock",
        run_script=PROJECT_ROOT / "scripts" / "run_session.sh",
        restart_script=PROJECT_ROOT / "scripts" / "restart_session.sh",
        guard_log_file=log_dir / "rate_limit_guard.log",
        guard_state_file=log_dir / "rate_limit_guard_state.json",
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        dir=str(path.parent),
        prefix=f".{path.name}.tmp.",
        delete=False,
        encoding="utf-8",
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        tmp_name = handle.name
    os.replace(tmp_name, path)


def _append_guard_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = get_korean_now().strftime("%Y-%m-%d %H:%M:%S %Z")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def _parse_datetime(value: object) -> datetime | None:
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


def _read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _is_active_session(lock_status: dict[str, Any], pid_file: Path) -> bool:
    app_pid = lock_status.get("app_pid")
    if (
        lock_status.get("lock_held")
        and lock_status.get("same_program")
        and process_alive(app_pid if isinstance(app_pid, int) else None)
    ):
        return True
    pid = _read_pid(pid_file)
    return process_alive(pid)


def _market_can_launch(status: MarketSessionStatus) -> bool:
    return status.session == "REGULAR" and status.order_allowed


def _cycle_age_seconds(runtime_state: dict[str, Any], *, now: datetime) -> float | None:
    started_at = _parse_datetime(runtime_state.get("last_cycle_started_at"))
    if started_at is None:
        return None
    return max(0.0, (now - started_at).total_seconds())


def _has_recent_rate_limit_pressure(
    runtime_state: dict[str, Any],
    *,
    rate_limit_hits_threshold: int,
    backoff_remaining_threshold_seconds: int,
) -> bool:
    budget = runtime_state.get("last_budget_status")
    budget_status = budget if isinstance(budget, dict) else {}
    recent_hits = int(budget_status.get("recent_rate_limit_hits_10m", 0) or 0)
    rate_limit_hits = int(budget_status.get("rate_limit_hits", 0) or 0)
    backoff_remaining = int(budget_status.get("backoff_remaining_seconds", 0) or 0)
    triggered = bool(runtime_state.get("rate_limit_triggered"))
    source = str(
        runtime_state.get("rate_limit_source")
        or budget_status.get("last_rate_limit_source")
        or ""
    ).strip()

    return (
        triggered
        or bool(source)
        or recent_hits >= rate_limit_hits_threshold
        or rate_limit_hits > 0
        or backoff_remaining >= backoff_remaining_threshold_seconds
    )


def decide_guard_action(
    *,
    paths: GuardPaths,
    account_context: dict[str, str],
    market_status: MarketSessionStatus,
    runtime_state: dict[str, Any],
    lock_status: dict[str, Any],
    now: datetime,
    stale_cycle_seconds: int = DEFAULT_STALE_CYCLE_SECONDS,
    rate_limit_hits_threshold: int = DEFAULT_RATE_LIMIT_HITS_THRESHOLD,
    backoff_remaining_threshold_seconds: int = DEFAULT_BACKOFF_REMAINING_THRESHOLD_SECONDS,
) -> GuardDecision:
    if account_context.get("account_environment") != "mock":
        return GuardDecision(
            action="blocked_non_mock_account",
            reason="모의투자 계정이 아니므로 rate-limit guard 치유 동작을 막았습니다.",
            should_execute=False,
        )

    if not _market_can_launch(market_status):
        return GuardDecision(
            action="observe_market_closed",
            reason=f"{market_status.session}: {market_status.reason}",
            should_execute=False,
        )

    if lock_status.get("lock_held") and not lock_status.get("same_program"):
        return GuardDecision(
            action="blocked_unrelated_lock",
            reason="세션 lock을 다른 프로세스가 잡고 있어 자동 치유를 중단했습니다.",
            should_execute=False,
        )

    active_session = _is_active_session(lock_status, paths.pid_file)
    if not active_session:
        return GuardDecision(
            action="start_session",
            reason="정규장 모의투자 세션이 실행 중이 아니어서 새 세션을 시작합니다.",
            should_execute=True,
            command=(str(paths.run_script),),
        )

    budget = runtime_state.get("last_budget_status")
    budget_status = budget if isinstance(budget, dict) else {}
    backoff_remaining = int(budget_status.get("backoff_remaining_seconds", 0) or 0)
    recent_hits = int(budget_status.get("recent_rate_limit_hits_10m", 0) or 0)
    cycle_age = _cycle_age_seconds(runtime_state, now=now)
    stale_cycle = cycle_age is None or cycle_age >= stale_cycle_seconds
    rate_limit_pressure = _has_recent_rate_limit_pressure(
        runtime_state,
        rate_limit_hits_threshold=rate_limit_hits_threshold,
        backoff_remaining_threshold_seconds=backoff_remaining_threshold_seconds,
    )

    if rate_limit_pressure and stale_cycle:
        age_text = "unknown" if cycle_age is None else f"{int(cycle_age)}s"
        return GuardDecision(
            action="restart_session",
            reason=(
                "rate limit 압력 이후 세션 cycle이 갱신되지 않았습니다 "
                f"(cycle_age={age_text}, hits_10m={recent_hits}, "
                f"backoff_remaining={backoff_remaining}s)."
            ),
            should_execute=True,
            command=(str(paths.restart_script),),
        )

    if backoff_remaining > 0 or recent_hits > 0:
        return GuardDecision(
            action="observe_rate_limit_recovery",
            reason=(
                "엔진 자체 backoff/degraded mode가 회복 중입니다 "
                f"(hits_10m={recent_hits}, backoff_remaining={backoff_remaining}s)."
            ),
            should_execute=False,
        )

    return GuardDecision(
        action="healthy",
        reason="실행 중인 모의투자 세션에서 rate limit backoff가 감지되지 않았습니다.",
        should_execute=False,
    )


def _launch_background(command: tuple[str, ...], *, cwd: Path, log_file: Path) -> int:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_file.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_handle.close()
    return int(process.pid)


def run_once(
    *,
    paths: GuardPaths | None = None,
    execute: bool = False,
    stale_cycle_seconds: int = DEFAULT_STALE_CYCLE_SECONDS,
    rate_limit_hits_threshold: int = DEFAULT_RATE_LIMIT_HITS_THRESHOLD,
    backoff_remaining_threshold_seconds: int = DEFAULT_BACKOFF_REMAINING_THRESHOLD_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved_paths = paths or default_paths()
    current_time = now or get_korean_now()
    account_context = get_account_scope_context()
    market_status = get_korean_market_session(current_time)
    runtime_state = _read_json(get_runtime_state_path())
    lock_status = inspect_lock(
        resolved_paths.lock_file,
        expected_command="run_session.sh -> app.main",
    )
    decision = decide_guard_action(
        paths=resolved_paths,
        account_context=account_context,
        market_status=market_status,
        runtime_state=runtime_state,
        lock_status=lock_status,
        now=current_time,
        stale_cycle_seconds=stale_cycle_seconds,
        rate_limit_hits_threshold=rate_limit_hits_threshold,
        backoff_remaining_threshold_seconds=backoff_remaining_threshold_seconds,
    )

    launched_pid: int | None = None
    if execute and decision.should_execute and decision.command:
        launched_pid = _launch_background(
            decision.command,
            cwd=resolved_paths.project_dir,
            log_file=resolved_paths.guard_log_file,
        )
        _append_guard_log(
            resolved_paths.guard_log_file,
            f"executed action={decision.action} pid={launched_pid} reason={decision.reason}",
        )
    else:
        mode = "check_only" if not execute else "observe"
        _append_guard_log(
            resolved_paths.guard_log_file,
            f"{mode} action={decision.action} reason={decision.reason}",
        )

    result: dict[str, Any] = {
        "checked_at": current_time.isoformat(),
        "account_environment": account_context.get("account_environment"),
        "account_signature": account_context.get("account_signature"),
        "market_session": market_status.session,
        "market_order_allowed": market_status.order_allowed,
        "decision": asdict(decision),
        "executed": bool(execute and decision.should_execute and decision.command),
        "launched_pid": launched_pid,
        "runtime_state_path": str(get_runtime_state_path()),
        "lock_status": {
            "lock_held": lock_status.get("lock_held"),
            "app_pid": lock_status.get("app_pid"),
            "status": lock_status.get("status"),
            "same_program": lock_status.get("same_program"),
            "stale": lock_status.get("stale"),
            "stale_reason": lock_status.get("stale_reason"),
        },
        "last_cycle_started_at": runtime_state.get("last_cycle_started_at"),
        "last_budget_status": runtime_state.get("last_budget_status"),
    }
    _write_json_atomic(resolved_paths.guard_state_file, result)
    return result


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check and heal KIS mock rate-limit state")
    parser.add_argument("--execute", action="store_true", help="Run the selected heal command")
    parser.add_argument(
        "--stale-cycle-seconds",
        type=int,
        default=DEFAULT_STALE_CYCLE_SECONDS,
        help="Restart only when a rate-limit pressured cycle is stale for this many seconds",
    )
    parser.add_argument(
        "--rate-limit-hits-threshold",
        type=int,
        default=DEFAULT_RATE_LIMIT_HITS_THRESHOLD,
        help="recent_rate_limit_hits_10m threshold treated as pressure",
    )
    parser.add_argument(
        "--backoff-remaining-threshold-seconds",
        type=int,
        default=DEFAULT_BACKOFF_REMAINING_THRESHOLD_SECONDS,
        help="Long remaining backoff treated as pressure",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON result instead of a one-line summary",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    result = run_once(
        execute=args.execute,
        stale_cycle_seconds=max(1, int(args.stale_cycle_seconds)),
        rate_limit_hits_threshold=max(1, int(args.rate_limit_hits_threshold)),
        backoff_remaining_threshold_seconds=max(
            1,
            int(args.backoff_remaining_threshold_seconds),
        ),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        decision = result["decision"]
        executed = "executed" if result["executed"] else "observed"
        print(
            f"rate_limit_guard {executed}: {decision['action']} | "
            f"{decision['reason']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
