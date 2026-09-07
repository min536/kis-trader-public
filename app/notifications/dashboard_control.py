"""Slack dashboard server subprocess control.

Mirrors app.notifications.backtest_control's subprocess-lifecycle pattern
(pid-file bookkeeping, process-group termination, log-tail rendering) for the
ops-console dashboard server (workspace/claude-design/kis-trader-v2/server.py).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from app.notifications.backtest_control import (
    PROJECT_ROOT,
    _format_backtest_log_tail,
    _process_start_marker,
    _remove_pid_file_if_matches,
    _running_pid_from_file,
    _terminate_backtest_process_group,
)

if TYPE_CHECKING:
    from app.notifications.slack_bot import SlackBotCommand

SLACK_DASHBOARD_REPORTS_DIR_ENV = "SLACK_DASHBOARD_REPORTS_DIR"
DEFAULT_DASHBOARD_REPORTS_DIR = PROJECT_ROOT / "reports" / "slack_dashboard"
# Read as a module global at call time (see backtest_control's
# DEFAULT_BACKTEST_PIPELINE_SCRIPT) so tests can monkeypatch it.
DEFAULT_DASHBOARD_SERVER_SCRIPT = (
    PROJECT_ROOT / "workspace" / "claude-design" / "kis-trader-v2" / "server.py"
)
SLACK_DASHBOARD_HOST_ENV = "SLACK_DASHBOARD_HOST"
DEFAULT_DASHBOARD_HOST = "127.0.0.1"
SLACK_DASHBOARD_PORT_ENV = "SLACK_DASHBOARD_PORT"
DEFAULT_DASHBOARD_PORT = 4173
# Self-declared identity in workspace/claude-design/kis-trader-v2/server.py's
# /api/health payload (build_health_payload). Used to tell "our dashboard is
# already up" apart from "something else is squatting on this port".
KT_DASHBOARD_SERVICE = "kis-trader-v2-site"


@dataclass(frozen=True)
class DashboardLaunchResult:
    status: str
    pid: int | None = None
    log_path: Path | None = None
    url: str = ""
    message: str = ""


def _resolve_dashboard_reports_dir(source: Mapping[str, str]) -> Path:
    return Path(
        str(source.get(SLACK_DASHBOARD_REPORTS_DIR_ENV) or DEFAULT_DASHBOARD_REPORTS_DIR)
    )


def _resolve_dashboard_host(source: Mapping[str, str]) -> str:
    return str(source.get(SLACK_DASHBOARD_HOST_ENV) or "").strip() or DEFAULT_DASHBOARD_HOST


def _resolve_dashboard_port(source: Mapping[str, str]) -> int:
    try:
        return int(
            str(source.get(SLACK_DASHBOARD_PORT_ENV) or "").strip() or DEFAULT_DASHBOARD_PORT
        )
    except (TypeError, ValueError):
        return DEFAULT_DASHBOARD_PORT


def _dashboard_pid_path(reports_dir: Path) -> Path:
    return reports_dir / "slack_dashboard.pid"


def _probe_dashboard_port(host: str, port: int, *, timeout: float = 1.0) -> str:
    """Classify what (if anything) is answering on host:port's /api/health.

    Returns one of:
      "empty"        - connection refused/timeout/other transport error.
      "kis_dashboard" - 200 with a body whose "service" matches our own.
      "foreign"      - something answered but it isn't our dashboard (HTTP
                       error status, or a 200 with an unparsable/mismatched
                       body).
    """
    url = f"http://{host}:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError:
        # Subclass of URLError - something responded, must be checked first.
        return "foreign"
    except urllib.error.URLError:
        return "empty"
    except Exception:
        return "empty"

    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return "foreign"
    if isinstance(payload, dict) and payload.get("service") == KT_DASHBOARD_SERVICE:
        return "kis_dashboard"
    return "foreign"


def launch_dashboard_server(
    *,
    env: Mapping[str, str] | None = None,
    popen_factory=subprocess.Popen,
    now: datetime | None = None,
    probe_fn=_probe_dashboard_port,
) -> DashboardLaunchResult:
    source = os.environ if env is None else env
    reports_dir = _resolve_dashboard_reports_dir(source)
    host = _resolve_dashboard_host(source)
    port = _resolve_dashboard_port(source)
    url = f"http://{host}:{port}"

    pid_path = _dashboard_pid_path(reports_dir)
    running_pid = _running_pid_from_file(pid_path)
    if running_pid is not None:
        return DashboardLaunchResult(
            status="already_running",
            pid=running_pid,
            log_path=reports_dir,
            url=url,
            message=f"dashboard server already running pid={running_pid}",
        )

    probe = probe_fn(host, port)
    if probe == "kis_dashboard":
        return DashboardLaunchResult(
            status="already_serving_external",
            url=url,
            message="dashboard already serving on this port (started outside Slack)",
        )
    if probe == "foreign":
        return DashboardLaunchResult(
            status="port_occupied_foreign",
            url=url,
            message=f"port {port} occupied by another process",
        )

    script = Path(str(DEFAULT_DASHBOARD_SERVER_SCRIPT))
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    reports_dir.mkdir(parents=True, exist_ok=True)
    log_path = reports_dir / f"slack_dashboard_{timestamp}.log"
    argv = [sys.executable, str(script)]

    subprocess_env = dict(source)
    subprocess_env["HOST"] = host
    subprocess_env["PORT"] = str(port)

    try:
        with log_path.open("a", encoding="utf-8") as handle:
            process = popen_factory(
                argv,
                cwd=PROJECT_ROOT,
                env=subprocess_env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        pid = int(getattr(process, "pid", 0) or 0)
        marker = _process_start_marker(pid)
        pid_payload = f"{pid}\n{marker}\n" if marker else f"{pid}\n"
        pid_path.write_text(pid_payload, encoding="utf-8")
        return DashboardLaunchResult(status="started", pid=pid, log_path=log_path, url=url)
    except OSError as exc:
        return DashboardLaunchResult(
            status="start_failed", log_path=log_path, url=url, message=str(exc)
        )


def stop_dashboard_server(*, env: Mapping[str, str] | None = None) -> DashboardLaunchResult:
    source = os.environ if env is None else env
    reports_dir = _resolve_dashboard_reports_dir(source)
    pid_path = _dashboard_pid_path(reports_dir)
    pid = _running_pid_from_file(pid_path)
    if pid is None:
        return DashboardLaunchResult(status="not_running")
    _terminate_backtest_process_group(pid)
    _remove_pid_file_if_matches(pid_path, pid)
    return DashboardLaunchResult(status="stopped", pid=pid)


def render_dashboard_status_reply(*, env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    reports_dir = _resolve_dashboard_reports_dir(source)
    pid = _running_pid_from_file(_dashboard_pid_path(reports_dir))
    if pid is None:
        return "ℹ️ *Dashboard server is not running.*"
    host = _resolve_dashboard_host(source)
    port = _resolve_dashboard_port(source)
    return (
        "🖥️ *Dashboard server is running*\n"
        f"• pid: `{pid}`\n"
        f"• url: http://{host}:{port}"
    )


def _parse_dashboard_command(raw_text: str) -> tuple[str, int | None, str | None]:
    """Parse a `dashboard` command line into (action, port_override, error).

    Strips a leading "dashboard"/"dash" keyword, pulls out a `port=N` token
    (validated to 1024..65535), and treats the first remaining token as the
    action (default "start"). ``error`` is a human-readable string when the
    port token is malformed/out-of-range, else ``None``.
    """
    tokens = str(raw_text or "").split()
    first = tokens[0].lower() if tokens else ""
    rest = tokens[1:] if first in {"dashboard", "dash"} else tokens

    port_override: int | None = None
    action_tokens: list[str] = []
    for token in rest:
        if token.lower().startswith("port="):
            raw_value = token.split("=", 1)[1]
            try:
                parsed = int(raw_value)
            except (TypeError, ValueError):
                return "start", None, f"port=`{raw_value}` 는 정수가 아닙니다 (1024..65535)"
            if not 1024 <= parsed <= 65535:
                return "start", None, f"port={parsed} 는 범위를 벗어났습니다 (1024..65535)"
            port_override = parsed
            continue
        action_tokens.append(token)

    action = action_tokens[0].lower() if action_tokens else "start"
    return action, port_override, None


def render_dashboard_command_reply(
    command: SlackBotCommand,
    *,
    env: Mapping[str, str] | None = None,
    liveness_delay_sec: float = 0.4,
    sleep_fn=time.sleep,
) -> str:
    action, port_override, parse_error = _parse_dashboard_command(command.raw_text)

    if parse_error:
        return (
            "⚠️ *dashboard 옵션 오류입니다.*\n"
            f"{parse_error}\n"
            "사용법: `dashboard [port=N]` (시작) | `dashboard status` | `dashboard stop`"
        )

    if port_override is not None:
        base = os.environ if env is None else env
        effective_env: Mapping[str, str] | None = {
            **base,
            SLACK_DASHBOARD_PORT_ENV: str(port_override),
        }
    else:
        effective_env = env

    if action == "status":
        return render_dashboard_status_reply(env=effective_env)
    if action in {"stop", "kill"}:
        result = stop_dashboard_server(env=effective_env)
        if result.status == "not_running":
            return "ℹ️ *Dashboard server is not running.* (nothing to stop)"
        return (
            "🛑 *Dashboard server stopped.*\n"
            f"• pid: `{result.pid}` (SIGTERM to process group)"
        )
    if action != "start":
        return (
            "⚠️ *지원하지 않는 dashboard 옵션입니다.*\n"
            "사용법: `dashboard [port=N]` (시작) | `dashboard status` | `dashboard stop`"
        )

    result = launch_dashboard_server(env=effective_env)
    if result.status == "already_running":
        return (
            "⏳ *Dashboard server already running*\n"
            f"• pid: `{result.pid}`\n"
            f"• url: {result.url}"
        )
    if result.status == "already_serving_external":
        return (
            "✅ *Dashboard already serving on this port.*\n"
            f"• url: {result.url}\n"
            "• 슬랙 밖에서 기동된 인스턴스라 `dashboard stop`으로는 중지되지 않습니다."
        )
    if result.status == "port_occupied_foreign":
        return (
            "⚠️ *포트를 다른 프로세스가 점유 중입니다.*\n"
            f"{result.message}\n"
            "• 다른 포트로 열려면 `dashboard port=<번호>` (1024..65535)"
        )
    if result.status == "start_failed":
        return (
            "❌ *Dashboard server failed to start.*\n"
            f"{result.message or 'unknown start failure'}"
        )

    sleep_fn(liveness_delay_sec)
    source = os.environ if effective_env is None else effective_env
    reports_dir = _resolve_dashboard_reports_dir(source)
    alive_pid = _running_pid_from_file(_dashboard_pid_path(reports_dir))
    if alive_pid is None:
        tail = _format_backtest_log_tail(result.log_path) if result.log_path else ""
        return (
            "❌ *Dashboard server exited right after launch (포트 충돌 등으로 추정).*\n"
            f"• pid: `{result.pid}`"
            f"{tail}"
        )
    return (
        "🚀 *Dashboard server started*\n"
        f"• pid: `{alive_pid}`\n"
        f"• url: {result.url}\n"
        f"• log: `{result.log_path}`"
    )
