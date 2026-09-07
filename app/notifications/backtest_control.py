"""Slack backtest pipeline subprocess control (R3-S5).

Relocated verbatim from app.notifications.slack_bot. slack_bot keeps legacy
bindings so existing import sites and patch targets remain valid.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from app.core.file_read_limits import (
    LocalReadLimitError,
    iter_tail_lines_bounded,
    read_text_bounded,
)

if TYPE_CHECKING:
    from app.notifications.slack_bot import SlackBotCommand

SLACK_BACKTEST_REPORTS_DIR_ENV = "SLACK_BACKTEST_REPORTS_DIR"
SLACK_BACKTEST_STOP_AFTER_ENV = "SLACK_BACKTEST_STOP_AFTER"
SLACK_BACKTEST_MAX_RUNTIME_SEC_ENV = "SLACK_BACKTEST_MAX_RUNTIME_SEC"
DEFAULT_BACKTEST_MAX_RUNTIME_SEC = 3600
SLACK_BACKTEST_PER_DAY_BUDGET_SEC_ENV = "SLACK_BACKTEST_PER_DAY_BUDGET_SEC"
DEFAULT_BACKTEST_PER_DAY_BUDGET_SEC = 210
DEFAULT_BACKTEST_ASSUMED_DAYS = 60
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BACKTEST_REPORTS_DIR = PROJECT_ROOT / "reports" / "open_trading_api"
# The pipeline script is pinned to the in-repo native minute-replay pipeline
# (no :8002 sidecar, no uv/npm). The SLACK_BACKTEST_PIPELINE_SCRIPT rollback
# hatch was removed 2026-07-10 after the deployed launchd plist kept silently
# re-pinning the retired sidecar wrapper (docs/todo_20260710.md §D).
DEFAULT_BACKTEST_PIPELINE_SCRIPT = PROJECT_ROOT / "scripts" / "native_backtest_pipeline.sh"
BACKTEST_HEARTBEAT_FILENAME = "slack_backtest.heartbeat.json"


def build_backtest_heartbeat_record(
    *, pid, started_at_epoch, max_runtime_sec, channel, thread_ts
) -> dict[str, Any]:
    """Persisted state of a running completion monitor (survives a bot restart)."""
    return {
        "pid": int(pid),
        "started_at_epoch": float(started_at_epoch),
        "max_runtime_sec": int(max_runtime_sec) if max_runtime_sec else None,
        "channel": str(channel),
        "thread_ts": thread_ts,
    }


def _backtest_heartbeat_path(reports_dir: Path | str) -> Path:
    return Path(reports_dir) / BACKTEST_HEARTBEAT_FILENAME


def write_backtest_heartbeat(reports_dir, record) -> bool:
    path = _backtest_heartbeat_path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def read_backtest_heartbeat(reports_dir):
    """Bounded read of the persisted monitor heartbeat (None if missing)."""
    path = _backtest_heartbeat_path(reports_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(read_text_bounded(path, encoding="utf-8"))
    except (LocalReadLimitError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def clear_backtest_heartbeat(reports_dir: Path | str) -> None:
    """Remove the persisted heartbeat (fail-safe)."""
    try:
        _backtest_heartbeat_path(reports_dir).unlink(missing_ok=True)
    except OSError:
        pass


@dataclass(frozen=True)
class BacktestCommandOptions:
    date: str | None = None
    dry_run: bool = False
    skip_run: bool = False
    action: str = "run"
    days: int | None = None


@dataclass(frozen=True)
class BacktestLaunchResult:
    status: str
    pid: int | None = None
    log_path: Path | None = None
    message: str = ""
    process: Any | None = field(default=None, repr=False, compare=False)


def _parse_days_token(value: str) -> tuple[int | None, str]:
    """Validate a `days` token: integer in 1..365, else a user-facing error."""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None, f"days는 1..365 정수여야 합니다: {value}"
    if not 1 <= parsed <= 365:
        return None, f"days는 1..365 정수여야 합니다: {value}"
    return parsed, ""


def _parse_backtest_command_options(raw_text: str) -> tuple[BacktestCommandOptions | None, str]:
    try:
        tokens = shlex.split(str(raw_text or ""))
    except ValueError as exc:
        return None, f"backtest 옵션을 해석할 수 없습니다: {exc}"
    rest = tokens[1:] if tokens and tokens[0].lower() == "backtest" else tokens
    if rest and rest[0].lower() in {"status", "kill"}:
        action = rest[0].lower()
        if len(rest) > 1:
            return None, f"`backtest {action}`는 추가 옵션을 받지 않습니다."
        return BacktestCommandOptions(action=action), ""
    date: str | None = None
    dry_run = False
    skip_run = False
    days: int | None = None
    idx = 0
    while idx < len(rest):
        token = rest[idx]
        lowered = token.lower()
        if lowered in {"--dry-run", "dry-run"}:
            dry_run = True
            idx += 1
            continue
        if lowered in {"--skip-run", "skip-run"}:
            skip_run = True
            idx += 1
            continue
        if lowered == "--date":
            if idx + 1 >= len(rest):
                return None, "--date 다음에 YYYYMMDD가 필요합니다."
            date = rest[idx + 1]
            idx += 2
            continue
        if lowered.startswith("date="):
            date = token.split("=", 1)[1]
            idx += 1
            continue
        if lowered == "--days":
            if idx + 1 >= len(rest):
                return None, "--days 다음에 일수(1..365)가 필요합니다."
            days, days_error = _parse_days_token(rest[idx + 1])
            if days is None:
                return None, days_error
            idx += 2
            continue
        if lowered.startswith("days="):
            days, days_error = _parse_days_token(token.split("=", 1)[1])
            if days is None:
                return None, days_error
            idx += 1
            continue
        if re.fullmatch(r"\d{8}", token):
            date = token
            idx += 1
            continue
        return None, f"지원하지 않는 backtest 옵션입니다: `{token}`"
    if date is not None and not re.fullmatch(r"\d{8}", date):
        return None, f"date는 YYYYMMDD 형식이어야 합니다: `{date}`"
    return BacktestCommandOptions(date=date, dry_run=dry_run, skip_run=skip_run, days=days), ""


def _coerce_positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def resolve_backtest_max_runtime(
    options: BacktestCommandOptions, source: Mapping[str, str]
) -> int:
    """Adaptive monitor timeout: max(floor, int(days * per_day * 1.5) + 120).

    SLACK_BACKTEST_MAX_RUNTIME_SEC acts as a *floor* here (cap→floor semantic
    change approved in docs/slack_backtest_speed_timeout_plan_20260711.md
    §3.2/§3.3); per-day budget defaults to 210s (measured under contention),
    and a bare `backtest` (no days option) assumes a 60-day window.
    """
    floor = _coerce_positive_int(
        source.get(SLACK_BACKTEST_MAX_RUNTIME_SEC_ENV),
        default=DEFAULT_BACKTEST_MAX_RUNTIME_SEC,
    )
    per_day = _coerce_positive_int(
        source.get(SLACK_BACKTEST_PER_DAY_BUDGET_SEC_ENV),
        default=DEFAULT_BACKTEST_PER_DAY_BUDGET_SEC,
    )
    days = options.days or DEFAULT_BACKTEST_ASSUMED_DAYS
    return max(floor, int(days * per_day * 1.5) + 120)


def _truthy_env(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _process_start_marker(pid: int) -> str | None:
    """Stable identity for a live pid (its start time): an OS-recycled pid
    gets a different marker, so a stale pid file cannot impersonate a run."""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    marker = result.stdout.strip()
    return marker or None


def _running_pid_from_file(path: Path) -> int | None:
    try:
        raw = read_text_bounded(path, encoding="utf-8", max_bytes=128).strip()
        lines = raw.splitlines()
        pid = int(lines[0].strip())
    except (LocalReadLimitError, OSError, TypeError, ValueError, IndexError):
        return None
    recorded_marker = lines[1].strip() if len(lines) > 1 else ""
    if recorded_marker:
        current_marker = _process_start_marker(pid)
        if current_marker is not None and current_marker != recorded_marker:
            return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    try:
        ps_result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return pid
    if ps_result.returncode != 0:
        return None
    if "Z" in ps_result.stdout.strip():
        return None
    return pid


def _remove_pid_file_if_matches(path: Path, pid: int | None) -> None:
    if pid is None:
        return
    try:
        raw = read_text_bounded(path, encoding="utf-8", max_bytes=128).strip()
        first_line = raw.splitlines()[0].strip()
    except (LocalReadLimitError, OSError, IndexError):
        return
    if first_line != str(pid):
        return
    try:
        path.unlink()
    except OSError:
        pass


def _format_backtest_log_tail(path: Path, *, max_lines: int = 8) -> str:
    if not path.exists():
        return ""
    try:
        raw_lines = [
            line.strip()
            for _lineno, line in iter_tail_lines_bounded(
                path,
                max_lines=max_lines,
                encoding="utf-8",
            )
            if line.strip()
        ]
    except (LocalReadLimitError, OSError, UnicodeDecodeError):
        return ""
    if not raw_lines:
        return ""
    tail = "\n".join(raw_lines)
    if len(tail) > 1200:
        tail = tail[-1200:]
    return f"\n• last log:\n```{tail}```"


def _build_backtest_subprocess_env(source: Mapping[str, str]) -> dict[str, str]:
    allowed_exact = {
        "PATH",
        "HOME",
        "LANG",
        "TMPDIR",
        "KIS_ENV",
        "PYTHONPATH",
    }
    env: dict[str, str] = {}
    for key, value in source.items():
        if key in allowed_exact or key.startswith("LC_"):
            env[key] = str(value)
    env.setdefault("PATH", os.defpath)
    return env


def launch_backtest_pipeline(
    options: BacktestCommandOptions,
    *,
    env: Mapping[str, str] | None = None,
    popen_factory=subprocess.Popen,
    now: datetime | None = None,
) -> BacktestLaunchResult:
    source = os.environ if env is None else env
    reports_dir = Path(
        str(source.get(SLACK_BACKTEST_REPORTS_DIR_ENV) or DEFAULT_BACKTEST_REPORTS_DIR)
    )
    # Always the native pipeline — env cannot re-pin the retired sidecar wrapper
    # (module global read at call time; tests patch DEFAULT_BACKTEST_PIPELINE_SCRIPT).
    script = Path(str(DEFAULT_BACKTEST_PIPELINE_SCRIPT))
    pid_path = reports_dir / "slack_backtest.pid"
    running_pid = _running_pid_from_file(pid_path)
    if running_pid is not None:
        return BacktestLaunchResult(
            status="already_running",
            pid=running_pid,
            log_path=reports_dir,
            message=f"backtest pipeline already running pid={running_pid}",
        )

    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    reports_dir.mkdir(parents=True, exist_ok=True)
    log_path = reports_dir / f"slack_backtest_{timestamp}.log"
    argv = [str(script)]
    if options.date:
        argv.extend(["--date", options.date])
    if options.days:
        argv.extend(["--days", str(options.days)])
    if options.skip_run:
        argv.append("--skip-run")
    if options.dry_run:
        argv.append("--dry-run")
    if _truthy_env(source.get(SLACK_BACKTEST_STOP_AFTER_ENV)):
        argv.append("--stop-after")

    try:
        with log_path.open("a", encoding="utf-8") as handle:
            process = popen_factory(
                argv,
                cwd=PROJECT_ROOT,
                env=_build_backtest_subprocess_env(source),
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        pid = int(getattr(process, "pid", 0) or 0)
        marker = _process_start_marker(pid)
        pid_payload = f"{pid}\n{marker}\n" if marker else f"{pid}\n"
        pid_path.write_text(pid_payload, encoding="utf-8")
        return BacktestLaunchResult(
            status="started",
            pid=pid,
            log_path=log_path,
            process=process,
        )
    except OSError as exc:
        return BacktestLaunchResult(status="start_failed", log_path=log_path, message=str(exc))


def _resolve_backtest_reports_dir(source: Mapping[str, str]) -> Path:
    return Path(
        str(source.get(SLACK_BACKTEST_REPORTS_DIR_ENV) or DEFAULT_BACKTEST_REPORTS_DIR)
    )


def render_backtest_status_reply(*, env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    reports_dir = _resolve_backtest_reports_dir(source)
    pid = _running_pid_from_file(reports_dir / "slack_backtest.pid")
    if pid is None:
        return "ℹ️ *Backtest pipeline is not running.*"
    logs = sorted(reports_dir.glob("slack_backtest_*.log"))
    tail = _format_backtest_log_tail(logs[-1]) if logs else ""
    return (
        "⏳ *Backtest pipeline is running*\n"
        f"• pid: `{pid}`"
        f"{tail}"
    )


def render_backtest_orphan_monitor_notice(
    *, env: Mapping[str, str] | None = None, now_epoch: float | None = None
) -> str | None:
    """Bot restart kills the in-process completion monitor thread; if a pipeline
    is still running, the channel must learn it lost auto-completion reporting."""
    source = os.environ if env is None else env
    reports_dir = _resolve_backtest_reports_dir(source)
    pid = _running_pid_from_file(reports_dir / "slack_backtest.pid")
    if pid is None:
        return None
    lines = [
        "⚠️ *Backtest pipeline is still running, but the bot restarted.*",
        f"• pid: `{pid}`",
    ]
    heartbeat = read_backtest_heartbeat(reports_dir)
    started_at = heartbeat.get("started_at_epoch") if isinstance(heartbeat, dict) else None
    if isinstance(started_at, (int, float)):
        now = time.time() if now_epoch is None else float(now_epoch)
        elapsed = max(0, int(now - started_at))
        lines.append(f"• running for: `{elapsed}s`")
        max_runtime = heartbeat.get("max_runtime_sec")
        if isinstance(max_runtime, int) and max_runtime > 0:
            lines.append(
                f"• max_runtime: `{max_runtime}s` (timeout in ~`{max_runtime - elapsed}s`)"
            )
    lines.append(
        "• completion monitoring was lost — no automatic completion/timeout message "
        "will be posted for this run."
    )
    lines.append("• use `backtest status` to check progress, `backtest kill` to stop it.")
    return "\n".join(lines)


def _terminate_backtest_process_group(pid: int) -> bool:
    """SIGTERM the pipeline's process group (launch uses start_new_session=True,
    so pgid == pid); fall back to the single pid if the group is gone."""
    try:
        os.killpg(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def render_backtest_kill_reply(*, env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    reports_dir = _resolve_backtest_reports_dir(source)
    pid_path = reports_dir / "slack_backtest.pid"
    pid = _running_pid_from_file(pid_path)
    if pid is None:
        return "ℹ️ *Backtest pipeline is not running.* (nothing to kill)"
    signalled = _terminate_backtest_process_group(pid)
    if not signalled:
        return (
            "⚠️ *Backtest pipeline kill failed.*\n"
            f"• pid: `{pid}` — could not deliver SIGTERM"
        )
    for _ in range(30):
        if _running_pid_from_file(pid_path) is None:
            _remove_pid_file_if_matches(pid_path, pid)
            return (
                "🛑 *Backtest pipeline terminated.*\n"
                f"• pid: `{pid}` (SIGTERM to process group)"
            )
        time.sleep(0.1)
    return (
        "🛑 *Backtest pipeline termination signalled.*\n"
        f"• pid: `{pid}` — SIGTERM sent; process is still shutting down"
    )


def render_backtest_command_reply(
    command: SlackBotCommand,
    *,
    env: Mapping[str, str] | None = None,
    on_started: Any | None = None,
) -> str:
    options, error = _parse_backtest_command_options(command.raw_text)
    if options is None:
        return f"⚠️ *backtest 명령을 실행하지 않았습니다.*\n{error}"
    if options.action == "status":
        return render_backtest_status_reply(env=env)
    if options.action == "kill":
        return render_backtest_kill_reply(env=env)
    result = launch_backtest_pipeline(options, env=env)
    if result.status == "started":
        if on_started is not None:
            on_started(result, options)
        mode = "dry-run" if options.dry_run else "run"
        date_text = options.date or "latest"
        return (
            "🚀 *Backtest pipeline started*\n"
            f"• mode: `{mode}`\n"
            f"• date: `{date_text}`\n"
            f"• pid: `{result.pid}`\n"
            f"• log: `{result.log_path}`\n"
            "flow: native minute-replay (manifest 로드 → 분봉 replay → 지표 집계 → JSON 리포트)"
        )
    if result.status == "already_running":
        return (
            "⏳ *Backtest pipeline already running*\n"
            f"• pid: `{result.pid}`\n"
            "기존 실행이 끝난 뒤 다시 시도하세요."
        )
    if result.status == "config_error":
        return (
            "⚠️ *Backtest command is not configured.*\n"
            f"{result.message}"
        )
    return (
        "❌ *Backtest pipeline failed to start.*\n"
        f"{result.message or 'unknown start failure'}"
    )


def _start_backtest_completion_monitor(
    result: BacktestLaunchResult,
    *,
    web_client: Any,
    channel: str,
    thread_ts: str | None,
    started_at: float | None = None,
    max_runtime_sec: int | None = None,
) -> None:
    process = result.process
    pid = result.pid
    log_path = result.log_path
    if process is None or pid is None or log_path is None:
        return

    pid_path = log_path.parent / "slack_backtest.pid"
    reports_dir = log_path.parent
    start_monotonic = time.monotonic() if started_at is None else started_at
    try:
        write_backtest_heartbeat(
            reports_dir,
            build_backtest_heartbeat_record(
                pid=pid,
                started_at_epoch=time.time(),
                max_runtime_sec=max_runtime_sec,
                channel=channel,
                thread_ts=thread_ts,
            ),
        )
    except Exception:
        pass

    def monitor() -> None:
        try:
            timed_out = False
            try:
                exit_code = int(process.wait(timeout=max_runtime_sec))
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_backtest_process_group(pid)
                try:
                    exit_code = int(process.wait(timeout=30))
                except subprocess.TimeoutExpired:
                    exit_code = -1
            duration_sec = max(0, int(time.monotonic() - start_monotonic))
            if timed_out:
                status = f"⏱️ *Backtest pipeline timed out after {max_runtime_sec}s — terminated*"
            elif exit_code == 0:
                status = "✅ *Backtest pipeline completed*"
            else:
                status = "❌ *Backtest pipeline failed*"
            failure_tail = (
                _format_backtest_log_tail(log_path) if (timed_out or exit_code != 0) else ""
            )
            text = (
                f"{status}\n"
                f"• exit_code: `{exit_code}`\n"
                f"• duration: `{duration_sec}s`\n"
                f"• log: `{log_path}`"
                f"{failure_tail}"
            )
            web_client.chat_postMessage(
                channel=channel,
                text=text,
                thread_ts=thread_ts,
                unfurl_links=False,
                unfurl_media=False,
            )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "backtest completion monitor failed reason=%s",
                exc.__class__.__name__,
            )
        finally:
            _remove_pid_file_if_matches(pid_path, pid)
            clear_backtest_heartbeat(reports_dir)

    thread = threading.Thread(
        target=monitor,
        name=f"slack-backtest-monitor-{pid}",
        daemon=True,
    )
    thread.start()
