from __future__ import annotations

import argparse
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from app.core.file_read_limits import LocalReadLimitError, read_text_bounded
from app.notifications.backtest_control import (
    DEFAULT_BACKTEST_MAX_RUNTIME_SEC,
    DEFAULT_BACKTEST_PIPELINE_SCRIPT,
    DEFAULT_BACKTEST_REPORTS_DIR,
    PROJECT_ROOT,
    SLACK_BACKTEST_MAX_RUNTIME_SEC_ENV,
    SLACK_BACKTEST_PER_DAY_BUDGET_SEC_ENV,
    SLACK_BACKTEST_REPORTS_DIR_ENV,
    SLACK_BACKTEST_STOP_AFTER_ENV,
    BacktestCommandOptions,
    BacktestLaunchResult,
    _build_backtest_subprocess_env,
    _format_backtest_log_tail,
    _parse_backtest_command_options,
    _process_start_marker,
    _remove_pid_file_if_matches,
    _resolve_backtest_reports_dir,
    _running_pid_from_file,
    _start_backtest_completion_monitor,
    _terminate_backtest_process_group,
    _truthy_env,
    launch_backtest_pipeline,
    render_backtest_command_reply,
    render_backtest_kill_reply,
    render_backtest_orphan_monitor_notice,
    render_backtest_status_reply,
    resolve_backtest_max_runtime,
)
from app.notifications.dashboard_control import render_dashboard_command_reply
from app.notifications.runtime_status_snapshot import (
    DEFAULT_SNAPSHOT_PATH,
    DEFAULT_STALE_AFTER_SEC,
    LIMITED_BOTTLENECKS_MISSING_TEXT,
    LIMITED_STATUS_MISSING_TEXT,
    render_bottlenecks_snapshot_reply,
    render_health_snapshot_reply,
    render_orders_today_reply,
    render_positions_snapshot_reply,
    render_status_snapshot_reply,
)
from app.notifications.slack import (
    BACK_TESTER_CHANNEL_ENV,
    SLACK_BOT_TOKEN_ENV,
    sanitize_text,
)
from app.notifications.engine_sentinel import (
    EngineSentinel,
    load_sentinel_config,
    run_engine_sentinel_tick,
)
from app.notifications.disclosure_sentinel import (
    DisclosureSentinel,
    load_disclosure_sentinel_config,
    run_disclosure_sentinel_tick,
)


SLACK_APP_TOKEN_ENV = "SLACK_APP_TOKEN"
SLACK_BOT_HEALTH_CHECK_INTERVAL_ENV = "SLACK_BOT_HEALTH_CHECK_INTERVAL_SEC"
SLACK_BOT_RECONNECT_GRACE_ENV = "SLACK_BOT_RECONNECT_GRACE_SEC"
SLACK_BOT_MAX_FORCED_RECONNECTS_ENV = "SLACK_BOT_MAX_FORCED_RECONNECTS"
DEFAULT_HEALTH_CHECK_INTERVAL_SEC = 30
DEFAULT_RECONNECT_GRACE_SEC = 90
DEFAULT_MAX_FORCED_RECONNECTS = 2
SLACK_BOT_ALLOWED_CHANNEL_IDS_ENV = "SLACK_BOT_ALLOWED_CHANNEL_IDS"
SLACK_BOT_ALLOWED_USER_IDS_ENV = "SLACK_BOT_ALLOWED_USER_IDS"
SLACK_BOT_ALLOWED_TEAM_IDS_ENV = "SLACK_BOT_ALLOWED_TEAM_IDS"
# Backtest control surface: canonical definitions live in
# app.notifications.backtest_control and are bound by the module-top import
# (SLACK_BACKTEST_*_ENV, DEFAULT_BACKTEST_*, PROJECT_ROOT).
DEFAULT_DOTENV_PATH = PROJECT_ROOT / ".env"
DOTENV_DEFAULT_KEYS = frozenset(
    {
        SLACK_APP_TOKEN_ENV,
        SLACK_BOT_TOKEN_ENV,
        BACK_TESTER_CHANNEL_ENV,
        SLACK_BOT_ALLOWED_CHANNEL_IDS_ENV,
        SLACK_BOT_ALLOWED_USER_IDS_ENV,
        SLACK_BOT_ALLOWED_TEAM_IDS_ENV,
        SLACK_BACKTEST_REPORTS_DIR_ENV,
        SLACK_BACKTEST_STOP_AFTER_ENV,
        SLACK_BACKTEST_MAX_RUNTIME_SEC_ENV,
        SLACK_BACKTEST_PER_DAY_BUDGET_SEC_ENV,
    }
)


def _parse_positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default

SUPPORTED_COMMANDS = (
    "ping", "help", "status", "bottlenecks",
    "health", "positions", "position", "orders", "order", "backtest", "dashboard",
    "briefing",
)
HELP_TEXT = (
    "📌 *지원 명령*\n"
    "• `ping` — 봇 연결 확인\n"
    "• `help` — 명령 목록\n"
    "• `status` — 현재 runtime 상태\n"
    "• `health` — 시스템 건강 상태\n"
    "• `positions` — 보유 종목\n"
    "• `orders today` — 오늘 주문 내역\n"
    "• `bottlenecks` — 오늘 병목 현황\n"
    "• `briefing` — 개장 전 모닝 브리핑(계좌/T+2/브레이크/공시/레짐) 조립\n"
    "• `backtest [YYYYMMDD] [days=N]` — project-backtester 전용 research backtest pipeline 실행\n"
    "  · `days=N` — 리플레이 일수 지정 (1..365, 기본 60) — 실행 타임아웃도 일수에 맞춰 조정\n"
    "• `backtest status` — 실행 중인 backtest pipeline 상태/로그 확인\n"
    "• `backtest kill` — 실행 중인 backtest pipeline 중지 (SIGTERM)\n"
    "• `dashboard [port=N]` — ops console 대시보드 서버 시작 (기본 http://127.0.0.1:4173, port=N으로 포트 지정)\n"
    "• `dashboard status` — 대시보드 서버 상태 확인\n"
    "• `dashboard stop` — 대시보드 서버 중지 (SIGTERM)\n\n"
    "매수/매도/중지 같은 trading control 명령은 지원하지 않습니다."
)
LIMITED_STATUS_TEXT = LIMITED_STATUS_MISSING_TEXT
LIMITED_BOTTLENECKS_TEXT = LIMITED_BOTTLENECKS_MISSING_TEXT
PUBLIC_COMMANDS = frozenset({"ping", "help", "unknown"})
SENSITIVE_COMMANDS = frozenset(
    {"status", "bottlenecks", "health", "positions", "orders", "backtest", "dashboard", "briefing"}
)
UNAUTHORIZED_COMMAND_TEXT = "This Slack command is limited to authorized channels, users, or teams."

_MENTION_RE = re.compile(r"<@[A-Z0-9]+(?:\|[^>]+)?>")
_PLAIN_BOT_NAME_RE = re.compile(r"@?kis-trader\b", re.IGNORECASE)


@dataclass(frozen=True)
class SlackBotCommand:
    name: str
    raw_text: str


# BacktestCommandOptions / BacktestLaunchResult: canonical dataclasses live in
# app.notifications.backtest_control and are bound by the module-top import.


def strip_bot_mention(text: str) -> str:
    cleaned = _MENTION_RE.sub(" ", str(text or ""))
    cleaned = _PLAIN_BOT_NAME_RE.sub(" ", cleaned)
    return " ".join(cleaned.split())


_COMMAND_ALIASES = {
    "position": "positions",
    "order": "orders",
    "dash": "dashboard",
}


def parse_command(text: str) -> SlackBotCommand:
    raw_text = strip_bot_mention(text)
    first_token = raw_text.split(maxsplit=1)[0].lower() if raw_text else "help"
    resolved = _COMMAND_ALIASES.get(first_token, first_token)
    if resolved in SUPPORTED_COMMANDS:
        return SlackBotCommand(name=resolved, raw_text=raw_text)
    return SlackBotCommand(name="unknown", raw_text=raw_text)


# Backtest subprocess control cluster: canonical implementations live in
# app.notifications.backtest_control and are bound by the module-top import.


def render_command_reply(
    command: SlackBotCommand,
    *,
    env: Mapping[str, str] | None = None,
    snapshot_path: Path = DEFAULT_SNAPSHOT_PATH,
    now: datetime | None = None,
    stale_after_sec: int = DEFAULT_STALE_AFTER_SEC,
) -> str | dict[str, Any]:
    if command.name == "ping":
        return "pong. kis-trader Slack bot is alive."
    if command.name == "help":
        return HELP_TEXT
    if command.name == "status":
        return render_status_snapshot_reply(
            path=snapshot_path,
            now=now,
            stale_after_sec=stale_after_sec,
        )
    if command.name == "health":
        return render_health_snapshot_reply(
            path=snapshot_path,
            now=now,
            stale_after_sec=stale_after_sec,
        )
    if command.name == "positions":
        return render_positions_snapshot_reply(
            path=snapshot_path,
            now=now,
            stale_after_sec=stale_after_sec,
        )
    if command.name == "orders":
        return render_orders_today_reply(
            path=snapshot_path,
            now=now,
            stale_after_sec=stale_after_sec,
        )
    if command.name == "bottlenecks":
        return render_bottlenecks_snapshot_reply(
            path=snapshot_path,
            now=now,
            stale_after_sec=stale_after_sec,
        )
    if command.name == "briefing":
        # On-demand twin of the scheduled --slack run: same offline assembly
        # path, returns the reply text (read-only; never sends).
        from app.tools.morning_briefing_report import render_briefing_command_reply

        return render_briefing_command_reply(env=env)
    if command.name == "backtest":
        return render_backtest_command_reply(command, env=env)
    if command.name == "dashboard":
        return render_dashboard_command_reply(command, env=env)
    return f"⚠️ *지원하지 않는 명령입니다.*\n\n{HELP_TEXT}"


def _split_allowed_ids(value: object) -> frozenset[str]:
    return frozenset(
        item.strip()
        for item in str(value or "").replace("\n", ",").split(",")
        if item.strip()
    )


def _optional_allowed_id_matches(
    env_name: str,
    actual_value: str,
    *,
    source: Mapping[str, str],
) -> bool:
    allowed_values = _split_allowed_ids(source.get(env_name))
    return not allowed_values or actual_value in allowed_values


def is_backtest_command_authorized(
    event: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    source = os.environ if env is None else env
    expected_channel = str(source.get(BACK_TESTER_CHANNEL_ENV) or "").strip()
    actual_channel = str(event.get("channel") or "").strip()
    if not expected_channel or actual_channel != expected_channel:
        return False
    user = str(event.get("user") or "").strip()
    team = str(event.get("team") or event.get("team_id") or "").strip()
    # backtest starts a host process: the user allowlist is mandatory, not optional.
    allowed_users = _split_allowed_ids(source.get(SLACK_BOT_ALLOWED_USER_IDS_ENV))
    if not allowed_users or user not in allowed_users:
        return False
    return _optional_allowed_id_matches(
        SLACK_BOT_ALLOWED_TEAM_IDS_ENV,
        team,
        source=source,
    )


def is_dashboard_command_authorized(
    event: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    source = os.environ if env is None else env
    user = str(event.get("user") or "").strip()
    channel = str(event.get("channel") or "").strip()
    team = str(event.get("team") or event.get("team_id") or "").strip()
    # dashboard starts a host process: the user allowlist is mandatory, not optional.
    allowed_users = _split_allowed_ids(source.get(SLACK_BOT_ALLOWED_USER_IDS_ENV))
    if not allowed_users or user not in allowed_users:
        return False
    if not _optional_allowed_id_matches(
        SLACK_BOT_ALLOWED_CHANNEL_IDS_ENV, channel, source=source
    ):
        return False
    return _optional_allowed_id_matches(SLACK_BOT_ALLOWED_TEAM_IDS_ENV, team, source=source)


def is_app_mention_authorized(
    event: Mapping[str, Any],
    command: SlackBotCommand,
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    if command.name in PUBLIC_COMMANDS:
        return True
    if command.name == "backtest":
        return is_backtest_command_authorized(event, env=env)
    if command.name == "dashboard":
        return is_dashboard_command_authorized(event, env=env)
    if command.name not in SENSITIVE_COMMANDS:
        return False

    source = os.environ if env is None else env
    checks = (
        (SLACK_BOT_ALLOWED_CHANNEL_IDS_ENV, str(event.get("channel") or "").strip()),
        (SLACK_BOT_ALLOWED_USER_IDS_ENV, str(event.get("user") or "").strip()),
        (
            SLACK_BOT_ALLOWED_TEAM_IDS_ENV,
            str(event.get("team") or event.get("team_id") or "").strip(),
        ),
    )
    configured = 0
    for env_name, actual_value in checks:
        allowed_values = _split_allowed_ids(source.get(env_name))
        if not allowed_values:
            continue
        configured += 1
        if actual_value not in allowed_values:
            return False
    return configured > 0


def handle_app_mention_event(
    event: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
) -> str | dict[str, Any] | None:
    try:
        if str(event.get("subtype") or "").strip():
            return None
        text = sanitize_text(event.get("text") or "")
        command = parse_command(text)
        if not is_app_mention_authorized(event, command, env=env):
            return UNAUTHORIZED_COMMAND_TEXT
        return render_command_reply(command, env=env)
    except Exception:
        return "Sorry, I could not process that read-only command safely."


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if key not in DOTENV_DEFAULT_KEYS:
        return None
    value = value.strip()
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in {"'", '"'}
    ):
        value = value[1:-1]
    return key, value


def _env_with_dotenv_defaults(
    env: Mapping[str, str],
    *,
    dotenv_path: Path = DEFAULT_DOTENV_PATH,
) -> dict[str, str]:
    resolved = dict(env)
    try:
        lines = read_text_bounded(dotenv_path, encoding="utf-8").splitlines()
    except (LocalReadLimitError, OSError):
        return resolved

    for line in lines:
        parsed = _parse_dotenv_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if key not in resolved or not str(resolved.get(key) or "").strip():
            resolved[key] = value
    return resolved


def _require_env(env: Mapping[str, str]) -> tuple[str, str]:
    app_token = str(env.get(SLACK_APP_TOKEN_ENV) or "").strip()
    bot_token = str(env.get(SLACK_BOT_TOKEN_ENV) or "").strip()
    missing = []
    if not app_token:
        missing.append(SLACK_APP_TOKEN_ENV)
    if not bot_token:
        missing.append(SLACK_BOT_TOKEN_ENV)
    if missing:
        raise RuntimeError(f"Missing required Slack env: {', '.join(missing)}")
    return app_token, bot_token


def run_socket_mode_bot(*, env: Mapping[str, str] | None = None) -> None:
    resolved_env = (
        dict(env)
        if env is not None
        else _env_with_dotenv_defaults(os.environ)
    )
    app_token, bot_token = _require_env(resolved_env)

    try:
        from slack_sdk import WebClient
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.response import SocketModeResponse
    except ImportError as exc:
        raise RuntimeError(
            "slack_sdk is required for Socket Mode. Install project requirements first."
        ) from exc

    web_client = WebClient(token=bot_token)
    socket_client = SocketModeClient(app_token=app_token, web_client=web_client)
    logger = logging.getLogger(__name__)

    def process(client: SocketModeClient, req: Any) -> None:
        try:
            if getattr(req, "type", "") != "events_api":
                return
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )

            payload = req.payload if isinstance(req.payload, dict) else {}
            event = payload.get("event")
            if not isinstance(event, dict):
                logger.info("slack bot ignored empty event payload")
                return
            event_type = str(event.get("type") or "").strip()
            if event_type != "app_mention":
                logger.info("slack bot ignored event type=%s", event_type or "-")
                return

            channel = str(event.get("channel") or "").strip()
            if not channel:
                logger.info("slack bot ignored app_mention without channel")
                return
            thread_ts = str(event.get("thread_ts") or event.get("ts") or "").strip() or None
            event_for_reply = dict(event)
            event_for_reply.setdefault("team", payload.get("team_id") or payload.get("team"))
            text = sanitize_text(event.get("text") or "")
            command = parse_command(text)
            logger.info("slack bot received app_mention command=%s", command.name)
            if not is_app_mention_authorized(event_for_reply, command, env=resolved_env):
                reply = UNAUTHORIZED_COMMAND_TEXT
            elif command.name == "backtest":
                reply = render_backtest_command_reply(
                    command,
                    env=resolved_env,
                    on_started=lambda result, options: _start_backtest_completion_monitor(
                        result,
                        web_client=web_client,
                        channel=channel,
                        thread_ts=thread_ts,
                        max_runtime_sec=resolve_backtest_max_runtime(
                            options, resolved_env
                        ),
                    ),
                )
            else:
                reply = render_command_reply(command, env=resolved_env)
            if not reply:
                return
            try:
                if isinstance(reply, dict):
                    web_client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        unfurl_links=False,
                        unfurl_media=False,
                        **reply
                    )
                else:
                    web_client.chat_postMessage(
                        channel=channel,
                        text=reply,
                        thread_ts=thread_ts,
                        unfurl_links=False,
                        unfurl_media=False,
                    )
                logger.info("slack bot reply sent command=%s", command.name)
            except Exception as exc:
                logger.warning(
                    "slack bot reply failed reason=%s",
                    exc.__class__.__name__,
                )
        except Exception as exc:
            logger.warning(
                "slack bot event handling failed reason=%s",
                exc.__class__.__name__,
            )

    socket_client.socket_mode_request_listeners.append(process)
    socket_client.connect()
    logger.info("Slack Socket Mode bot connected.")

    orphan_notice = render_backtest_orphan_monitor_notice(env=resolved_env)
    orphan_channel = str(resolved_env.get(BACK_TESTER_CHANNEL_ENV) or "").strip()
    if orphan_notice and orphan_channel:
        try:
            web_client.chat_postMessage(
                channel=orphan_channel,
                text=orphan_notice,
                unfurl_links=False,
                unfurl_media=False,
            )
        except Exception as exc:
            logger.warning(
                "backtest orphan monitor notice failed reason=%s",
                exc.__class__.__name__,
            )

    health_check_interval_sec = _parse_positive_int(
        resolved_env.get(SLACK_BOT_HEALTH_CHECK_INTERVAL_ENV),
        default=DEFAULT_HEALTH_CHECK_INTERVAL_SEC,
    )
    reconnect_grace_sec = _parse_positive_int(
        resolved_env.get(SLACK_BOT_RECONNECT_GRACE_ENV),
        default=DEFAULT_RECONNECT_GRACE_SEC,
    )
    max_forced_reconnects = _parse_positive_int(
        resolved_env.get(SLACK_BOT_MAX_FORCED_RECONNECTS_ENV),
        default=DEFAULT_MAX_FORCED_RECONNECTS,
    )

    sentinel = EngineSentinel(config=load_sentinel_config(resolved_env))
    disclosure_sentinel = DisclosureSentinel(config=load_disclosure_sentinel_config(resolved_env))
    stop_event = threading.Event()
    last_connected_at = time.monotonic()
    forced_reconnects = 0
    try:
        while not stop_event.wait(health_check_interval_sec):
            run_engine_sentinel_tick(env=resolved_env, sentinel=sentinel)
            run_disclosure_sentinel_tick(env=resolved_env, sentinel=disclosure_sentinel)
            try:
                connected = bool(socket_client.is_connected())
            except Exception as exc:
                logger.warning(
                    "slack bot health check failed reason=%s",
                    exc.__class__.__name__,
                )
                connected = False
            if connected:
                last_connected_at = time.monotonic()
                forced_reconnects = 0
                continue
            disconnected_for = time.monotonic() - last_connected_at
            if disconnected_for < reconnect_grace_sec:
                logger.info(
                    "slack bot disconnected for %.0fs; waiting for SDK auto-reconnect",
                    disconnected_for,
                )
                continue
            forced_reconnects += 1
            if forced_reconnects > max_forced_reconnects:
                logger.warning(
                    "slack bot stayed disconnected after %d forced reconnects; "
                    "exiting for supervisor restart",
                    max_forced_reconnects,
                )
                raise RuntimeError("slack bot remained disconnected after forced reconnects")
            logger.warning(
                "slack bot still disconnected after %.0fs; forcing reconnect %d/%d",
                disconnected_for,
                forced_reconnects,
                max_forced_reconnects,
            )
            try:
                socket_client.connect_to_new_endpoint()
            except Exception as exc:
                logger.warning(
                    "slack bot reconnect failed reason=%s; exiting for supervisor restart",
                    exc.__class__.__name__,
                )
                raise RuntimeError("slack bot reconnect failed") from exc
    finally:
        try:
            socket_client.close()
        except Exception:
            pass


def _build_arg_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Run the read-only Slack Socket Mode bot")


def main(argv: list[str] | None = None) -> int:
    _build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    try:
        run_socket_mode_bot()
    except KeyboardInterrupt:
        print("Slack Socket Mode bot stopped.")
        return 0
    except RuntimeError as exc:
        print(str(exc))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
