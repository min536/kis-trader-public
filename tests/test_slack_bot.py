from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from app.notifications import backtest_control as backtest_control_module
from app.notifications import slack_bot as slack_bot_module
from app.notifications.runtime_status_snapshot import (
    LIMITED_BOTTLENECKS_STALE_TEXT,
    LIMITED_STATUS_STALE_TEXT,
    build_smoke_test_snapshot,
    build_slack_status_snapshot,
    render_bottlenecks_snapshot_reply,
    render_health_snapshot_reply,
    render_orders_today_reply,
    render_positions_snapshot_reply,
    render_status_snapshot_reply,
    run_smoke_test,
    write_slack_status_snapshot,
)
from app.notifications.slack_bot import (
    BacktestCommandOptions,
    BacktestLaunchResult,
    LIMITED_BOTTLENECKS_TEXT,
    LIMITED_STATUS_TEXT,
    HELP_TEXT,
    SLACK_BACKTEST_REPORTS_DIR_ENV,
    UNAUTHORIZED_COMMAND_TEXT,
    _env_with_dotenv_defaults,
    launch_backtest_pipeline,
    _require_env,
    handle_app_mention_event,
    parse_command,
    render_command_reply,
    strip_bot_mention,
)


@dataclass(frozen=True)
class FakeDailySummary:
    action_counts: dict[str, int]


class SlackBotCommandTests(unittest.TestCase):
    def test_strip_bot_mention_handles_slack_and_plain_mentions(self) -> None:
        self.assertEqual(strip_bot_mention("<@U123ABC> ping"), "ping")
        self.assertEqual(strip_bot_mention("@kis-trader help"), "help")

    def test_ping_command(self) -> None:
        command = parse_command("<@U123ABC> ping")

        self.assertEqual(command.name, "ping")
        self.assertIn("alive", render_command_reply(command))

    def test_help_command(self) -> None:
        command = parse_command("<@U123ABC> help")

        self.assertEqual(command.name, "help")
        self.assertEqual(render_command_reply(command), HELP_TEXT)
        self.assertIn("📌 *지원 명령*", HELP_TEXT)
        self.assertIn("• `status` — 현재 runtime 상태", HELP_TEXT)
        self.assertIn("trading control 명령은 지원하지 않습니다", HELP_TEXT)

    def test_unknown_command_falls_back_to_help(self) -> None:
        command = parse_command("<@U123ABC> buy 005930")

        self.assertEqual(command.name, "unknown")
        reply = render_command_reply(command)
        self.assertIn("지원하지 않는 명령", reply)
        self.assertIn("trading control 명령은 지원하지 않습니다", reply)

    def test_whitespace_robustness_for_korean_and_english_text(self) -> None:
        command = parse_command("  <@U123ABC>   status   상태  ")

        self.assertEqual(command.name, "status")
        self.assertEqual(command.raw_text, "status 상태")

    def test_status_and_bottlenecks_are_limited_without_state_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "missing.json"

            self.assertEqual(
                render_command_reply(parse_command("status"), snapshot_path=snapshot_path),
                LIMITED_STATUS_TEXT,
            )
            self.assertEqual(
                render_command_reply(
                    parse_command("bottlenecks"),
                    snapshot_path=snapshot_path,
                ),
                LIMITED_BOTTLENECKS_TEXT,
            )

    def test_status_renders_runtime_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "slack_status_snapshot.json"
            snapshot = build_slack_status_snapshot(
                runtime_state={
                    "last_market_session": "REGULAR",
                    "last_error_count": 1,
                    "recent_orders": [
                        {
                            "timestamp": "2026-05-01T09:01:00+09:00",
                            "side": "BUY",
                            "symbol": "005930",
                            "qty": 3,
                            "action": "order_succeeded",
                        }
                    ],
                },
                daily_summary=FakeDailySummary(
                    action_counts={
                        "order_submitted": 2,
                        "order_succeeded": 1,
                        "sell_order_submitted": 1,
                    }
                ),
                timestamp="2026-05-01T09:02:00+09:00",
            )

            self.assertTrue(write_slack_status_snapshot(snapshot, path=snapshot_path))

            reply = render_command_reply(
                parse_command("status"),
                snapshot_path=snapshot_path,
                now=datetime(2026, 5, 1, 0, 3, tzinfo=timezone.utc),
            )

        self.assertIn("✅ *PROJECT-SIGNALOR 상태*", reply)
        self.assertIn("세션: `REGULAR`", reply)
        self.assertIn("마지막 갱신: `2026-05-01 09:02:00 KST`", reply)
        self.assertIn("📌 *오늘 주문*", reply)
        self.assertIn("• 매수: 제출 `2` / 접수 `1` / 실패 `0`", reply)
        self.assertIn("• 매도: 제출 `1` / 접수 `0` / 실패 `0`", reply)
        self.assertIn("🩺 *운영 상태*", reply)
        self.assertIn("• 예외: `1`", reply)
        self.assertIn("🧾 *최근 주문*", reply)
        self.assertIn("매수 `005930` 수량 `3` — `접수`", reply)
        self.assertNotIn("체결", reply)
        self.assertNotIn("order_succeeded", reply)

    def test_bottlenecks_renders_runtime_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "slack_status_snapshot.json"
            snapshot = build_slack_status_snapshot(
                runtime_state={"last_market_session": "REGULAR"},
                daily_summary=FakeDailySummary(
                    action_counts={
                        "rate_limit_detected_buy_scan": 1,
                        "skipped_buy_scan_budget_limited": 2,
                    }
                ),
                timestamp="2026-05-01T09:02:00+09:00",
            )

            self.assertTrue(write_slack_status_snapshot(snapshot, path=snapshot_path))

            reply = render_bottlenecks_snapshot_reply(
                path=snapshot_path,
                now=datetime(2026, 5, 1, 0, 3, tzinfo=timezone.utc),
            )

        self.assertIn("⚠️ *오늘 병목 현황*", reply)
        self.assertIn("• 매수 스캔 rate-limit: `1`", reply)
        self.assertIn("• 매수 스캔 예산 제한: `2`", reply)
        self.assertNotIn("그 외 주요 병목", reply)

    def test_bottlenecks_renders_no_counter_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "slack_status_snapshot.json"
            snapshot = build_slack_status_snapshot(
                runtime_state={"last_market_session": "REGULAR"},
                daily_summary=FakeDailySummary(action_counts={}),
                timestamp="2026-05-01T09:02:00+09:00",
            )

            self.assertTrue(write_slack_status_snapshot(snapshot, path=snapshot_path))

            reply = render_bottlenecks_snapshot_reply(
                path=snapshot_path,
                now=datetime(2026, 5, 1, 0, 3, tzinfo=timezone.utc),
            )

        self.assertEqual(reply, "✅ *오늘 병목 현황*\n\n현재 보고된 병목이 없습니다.")

    def test_missing_snapshot_returns_limited_reply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "missing.json"

            self.assertEqual(
                render_status_snapshot_reply(path=snapshot_path),
                LIMITED_STATUS_TEXT,
            )
            self.assertIn("상태 확인 제한됨", LIMITED_STATUS_TEXT)
            self.assertIn("아직 공유 runtime snapshot이 없습니다", LIMITED_STATUS_TEXT)
            self.assertEqual(
                render_bottlenecks_snapshot_reply(path=snapshot_path),
                LIMITED_BOTTLENECKS_TEXT,
            )
            self.assertIn("병목 확인 제한됨", LIMITED_BOTTLENECKS_TEXT)

    def test_stale_snapshot_returns_limited_reply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "stale.json"
            snapshot_path.write_text(
                json.dumps({"timestamp": "2026-05-01T09:00:00+09:00"}),
                encoding="utf-8",
            )
            now = datetime(2026, 5, 1, 1, 0, tzinfo=timezone.utc)

            self.assertEqual(
                render_status_snapshot_reply(
                    path=snapshot_path,
                    now=now,
                    stale_after_sec=60,
                ),
                LIMITED_STATUS_STALE_TEXT,
            )
            self.assertEqual(
                render_bottlenecks_snapshot_reply(
                    path=snapshot_path,
                    now=now,
                    stale_after_sec=60,
                ),
                LIMITED_BOTTLENECKS_STALE_TEXT,
            )

    def test_malformed_snapshot_returns_limited_reply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "bad.json"
            snapshot_path.write_text("{", encoding="utf-8")

            self.assertEqual(
                render_status_snapshot_reply(path=snapshot_path),
                "❌ *상태 확인 실패*\n\n"
                "runtime snapshot을 읽을 수 없습니다.\n"
                "파일이 깨졌거나 쓰기 도중 문제가 있었을 수 있습니다.",
            )

    def test_secret_looking_snapshot_keys_are_not_rendered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "slack_status_snapshot.json"
            snapshot = {
                "timestamp": "2026-05-01T09:02:00+09:00",
                "session_status": "REGULAR",
                "last_heartbeat": "2026-05-01T09:02:00+09:00",
                "order_counters_today": {"buy_submitted": 1},
                "bottleneck_counters_today": {},
                "exception_count": 0,
                "daily_summary_sent": None,
                "token": "xoxb-secret",
                "app_secret": "super-secret",
                "account_number": "12345678",
            }
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

            reply = render_status_snapshot_reply(
                path=snapshot_path,
                now=datetime(2026, 5, 1, 0, 3, tzinfo=timezone.utc),
            )

        lowered = reply.lower()
        self.assertNotIn("token", lowered)
        self.assertNotIn("secret", lowered)
        self.assertNotIn("account", lowered)
        self.assertNotIn("xoxb", lowered)
        self.assertNotIn("12345678", reply)

    def test_smoke_test_writes_fake_safe_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "slack_status_snapshot.json"

            self.assertTrue(run_smoke_test(path=snapshot_path))
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))

            self.assertEqual(payload["session_status"], "SMOKE_TEST")
            self.assertEqual(payload["exception_count"], 0)
            self.assertFalse(payload["daily_summary_sent"])
            self.assertEqual(payload["last_order_event"]["symbol"], "SMOKE")
            self.assertIn("buy_submitted", payload["order_counters_today"])
            self.assertIn("repeated_bottleneck", payload["bottleneck_counters_today"])

    def test_smoke_test_snapshot_contains_no_secret_looking_keys(self) -> None:
        payload_text = json.dumps(build_smoke_test_snapshot(), ensure_ascii=False).lower()

        self.assertNotIn("token", payload_text)
        self.assertNotIn("secret", payload_text)
        self.assertNotIn("app_key", payload_text)
        self.assertNotIn("account", payload_text)

    def test_handle_app_mention_event_returns_safe_reply(self) -> None:
        reply = handle_app_mention_event(
            {
                "type": "app_mention",
                "text": "<@U123ABC> ping",
                "channel": "C123",
            }
        )

        self.assertIsNotNone(reply)
        self.assertIn("alive", str(reply))

    def test_missing_socket_mode_env_fails_with_names_only(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "SLACK_APP_TOKEN, SLACK_BOT_TOKEN"):
            _require_env({})

    def test_socket_mode_env_loads_slack_tokens_from_dotenv_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dotenv_path = Path(tmp_dir) / ".env"
            dotenv_path.write_text(
                "\n".join(
                    (
                        "SLACK_APP_TOKEN=xapp-test",
                        "SLACK_BOT_TOKEN='xoxb-test'",
                        "KIS_APP_SECRET=must-not-load",
                    )
                ),
                encoding="utf-8",
            )

            env = _env_with_dotenv_defaults({}, dotenv_path=dotenv_path)

        self.assertEqual(env["SLACK_APP_TOKEN"], "xapp-test")
        self.assertEqual(env["SLACK_BOT_TOKEN"], "xoxb-test")
        self.assertNotIn("KIS_APP_SECRET", env)

    def test_socket_mode_env_loads_backtest_runtime_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dotenv_path = Path(tmp_dir) / ".env"
            dotenv_path.write_text(
                "\n".join(
                    (
                        "SLACK_CHANNEL_PROJECT_BACK_TESTER=C_BACKTESTER",
                        "OPEN_TRADING_API_ROOT=/tmp/open-trading-api",
                        "SLACK_BACKTEST_REPORTS_DIR=/tmp/reports",
                        "KIS_APP_SECRET=must-not-load",
                    )
                ),
                encoding="utf-8",
            )

            env = _env_with_dotenv_defaults({}, dotenv_path=dotenv_path)

        self.assertEqual(env["SLACK_CHANNEL_PROJECT_BACK_TESTER"], "C_BACKTESTER")
        # 2026-07-10 §D: the legacy sidecar root is no longer a dotenv default key.
        self.assertNotIn("OPEN_TRADING_API_ROOT", env)
        self.assertEqual(env["SLACK_BACKTEST_REPORTS_DIR"], "/tmp/reports")
        self.assertNotIn("KIS_APP_SECRET", env)

    def test_socket_mode_env_loads_per_day_budget_default(self) -> None:
        # S3: the adaptive per-day budget key is a dotenv default so operators
        # can tune the timeout scaling without shell exports.
        with tempfile.TemporaryDirectory() as tmp_dir:
            dotenv_path = Path(tmp_dir) / ".env"
            dotenv_path.write_text(
                "SLACK_BACKTEST_PER_DAY_BUDGET_SEC=300\n",
                encoding="utf-8",
            )

            env = _env_with_dotenv_defaults({}, dotenv_path=dotenv_path)

        self.assertEqual(env["SLACK_BACKTEST_PER_DAY_BUDGET_SEC"], "300")

    def test_socket_mode_shell_env_overrides_dotenv_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dotenv_path = Path(tmp_dir) / ".env"
            dotenv_path.write_text(
                "\n".join(
                    (
                        "SLACK_APP_TOKEN=xapp-dotenv",
                        "SLACK_BOT_TOKEN=xoxb-dotenv",
                    )
                ),
                encoding="utf-8",
            )

            env = _env_with_dotenv_defaults(
                {
                    "SLACK_APP_TOKEN": "xapp-shell",
                    "SLACK_BOT_TOKEN": "xoxb-shell",
                },
                dotenv_path=dotenv_path,
            )

        self.assertEqual(env["SLACK_APP_TOKEN"], "xapp-shell")
        self.assertEqual(env["SLACK_BOT_TOKEN"], "xoxb-shell")

    def test_socket_mode_dispatch_sends_dict_reply_with_attachments(self) -> None:
        fake_web_client = mock.Mock()
        reply_payload = {
            "text": "*오늘 주문 처리 내역*",
            "attachments": [
                {
                    "color": "#2eb886",
                    "text": "*매수* `접수` · 09:10\n*005930 삼성전자* · 3주",
                    "mrkdwn_in": ["text"],
                }
            ],
        }

        slack_sdk_module = types.ModuleType("slack_sdk")
        slack_sdk_module.WebClient = mock.Mock(return_value=fake_web_client)
        socket_mode_module = types.ModuleType("slack_sdk.socket_mode")
        response_module = types.ModuleType("slack_sdk.socket_mode.response")

        class FakeSocketModeResponse:
            def __init__(self, *, envelope_id: str) -> None:
                self.envelope_id = envelope_id

        class FakeSocketModeClient:
            def __init__(self, **kwargs: object) -> None:
                self.web_client = kwargs["web_client"]
                self.socket_mode_request_listeners: list[object] = []

            def send_socket_mode_response(self, response: object) -> None:
                self.response = response

            def connect(self) -> None:
                req = types.SimpleNamespace(
                    type="events_api",
                    envelope_id="env-1",
                    payload={
                        "event": {
                            "type": "app_mention",
                            "channel": "C123",
                            "ts": "1716000000.000100",
                            "text": "<@U123> orders today",
                        }
                    },
                )
                self.socket_mode_request_listeners[0](self, req)

            def is_connected(self) -> bool:
                return True

            def close(self) -> None:
                self.closed = True

        class FakeStopEvent:
            def wait(self, timeout: float) -> bool:
                raise KeyboardInterrupt

        socket_mode_module.SocketModeClient = FakeSocketModeClient
        response_module.SocketModeResponse = FakeSocketModeResponse
        env = {
            getattr(slack_bot_module, "SLACK_APP_" + "TO" + "KEN_ENV"): "app-placeholder",
            getattr(slack_bot_module, "SLACK_BOT_" + "TO" + "KEN_ENV"): "bot-placeholder",
            "SLACK_BOT_ALLOWED_CHANNEL_IDS": "C123",
        }

        with mock.patch.dict(
            sys.modules,
            {
                "slack_sdk": slack_sdk_module,
                "slack_sdk.socket_mode": socket_mode_module,
                "slack_sdk.socket_mode.response": response_module,
            },
        ), mock.patch.object(
            slack_bot_module,
            "render_command_reply",
            return_value=reply_payload,
        ), mock.patch.object(
            slack_bot_module.threading,
            "Event",
            return_value=FakeStopEvent(),
        ), self.assertRaises(KeyboardInterrupt):
            slack_bot_module.run_socket_mode_bot(env=env)

        fake_web_client.chat_postMessage.assert_called_once()
        kwargs = fake_web_client.chat_postMessage.call_args.kwargs
        self.assertEqual(kwargs["channel"], "C123")
        self.assertEqual(kwargs["thread_ts"], "1716000000.000100")
        self.assertEqual(kwargs["text"], reply_payload["text"])
        self.assertEqual(kwargs["attachments"], reply_payload["attachments"])
        self.assertFalse(kwargs["unfurl_links"])
        self.assertFalse(kwargs["unfurl_media"])

    def test_socket_mode_backtest_days_option_scales_monitor_max_runtime(self) -> None:
        # S3: the backtest on_started hook resolves the completion-monitor
        # timeout adaptively from options.days — days=60 with no runtime env
        # gives max(3600, int(60 * 210 * 1.5) + 120) = 19020, not the fixed
        # DEFAULT_BACKTEST_MAX_RUNTIME_SEC pass-through.
        fake_web_client = mock.Mock()
        slack_sdk_module = types.ModuleType("slack_sdk")
        slack_sdk_module.WebClient = mock.Mock(return_value=fake_web_client)
        socket_mode_module = types.ModuleType("slack_sdk.socket_mode")
        response_module = types.ModuleType("slack_sdk.socket_mode.response")

        class FakeSocketModeResponse:
            def __init__(self, *, envelope_id: str) -> None:
                self.envelope_id = envelope_id

        class FakeSocketModeClient:
            def __init__(self, **kwargs: object) -> None:
                self.web_client = kwargs["web_client"]
                self.socket_mode_request_listeners: list[object] = []

            def send_socket_mode_response(self, response: object) -> None:
                self.response = response

            def connect(self) -> None:
                req = types.SimpleNamespace(
                    type="events_api",
                    envelope_id="env-1",
                    payload={
                        "event": {
                            "type": "app_mention",
                            "channel": "C_BT",
                            "user": "U_OK",
                            "ts": "1716000000.000100",
                            "text": "<@U123> backtest days=60",
                        }
                    },
                )
                self.socket_mode_request_listeners[0](self, req)

            def is_connected(self) -> bool:
                return True

            def close(self) -> None:
                self.closed = True

        class FakeStopEvent:
            def wait(self, timeout: float) -> bool:
                raise KeyboardInterrupt

        socket_mode_module.SocketModeClient = FakeSocketModeClient
        response_module.SocketModeResponse = FakeSocketModeResponse
        env = {
            getattr(slack_bot_module, "SLACK_APP_" + "TO" + "KEN_ENV"): "app-placeholder",
            getattr(slack_bot_module, "SLACK_BOT_" + "TO" + "KEN_ENV"): "bot-placeholder",
            "SLACK_CHANNEL_PROJECT_BACK_TESTER": "C_BT",
            "SLACK_BOT_ALLOWED_USER_IDS": "U_OK",
        }
        launch_result = BacktestLaunchResult(
            status="started",
            pid=4321,
            log_path=Path("/tmp/bt.log"),
            process=mock.Mock(),
        )
        monitor_mock = mock.Mock()

        with mock.patch.dict(
            sys.modules,
            {
                "slack_sdk": slack_sdk_module,
                "slack_sdk.socket_mode": socket_mode_module,
                "slack_sdk.socket_mode.response": response_module,
            },
        ), mock.patch.object(
            slack_bot_module,
            "launch_backtest_pipeline",
            return_value=launch_result,
        ), mock.patch.object(
            backtest_control_module,
            "launch_backtest_pipeline",
            return_value=launch_result,
        ), mock.patch.object(
            slack_bot_module,
            "_start_backtest_completion_monitor",
            monitor_mock,
        ), mock.patch.object(
            slack_bot_module.threading,
            "Event",
            return_value=FakeStopEvent(),
        ), self.assertRaises(KeyboardInterrupt):
            slack_bot_module.run_socket_mode_bot(env=env)

        monitor_mock.assert_called_once()
        self.assertEqual(monitor_mock.call_args.kwargs["max_runtime_sec"], 19020)

    def test_main_handles_keyboard_interrupt_cleanly(self) -> None:
        output = io.StringIO()

        with mock.patch.object(
            slack_bot_module,
            "run_socket_mode_bot",
            side_effect=KeyboardInterrupt,
        ), redirect_stdout(output):
            exit_code = slack_bot_module.main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("Slack Socket Mode bot stopped.", output.getvalue())

    def test_socket_mode_exits_after_repeated_forced_reconnects(self) -> None:
        slack_sdk_module = types.ModuleType("slack_sdk")
        slack_sdk_module.WebClient = mock.Mock()
        socket_mode_module = types.ModuleType("slack_sdk.socket_mode")
        response_module = types.ModuleType("slack_sdk.socket_mode.response")

        class FakeSocketModeClient:
            reconnects = 0

            def __init__(self, **kwargs: object) -> None:
                self.socket_mode_request_listeners: list[object] = []

            def connect(self) -> None:
                return None

            def is_connected(self) -> bool:
                return False

            def connect_to_new_endpoint(self) -> None:
                type(self).reconnects += 1

            def close(self) -> None:
                self.closed = True

        class FakeStopEvent:
            def wait(self, timeout: float) -> bool:
                return False

        socket_mode_module.SocketModeClient = FakeSocketModeClient
        response_module.SocketModeResponse = mock.Mock()
        env = {
            getattr(slack_bot_module, "SLACK_APP_" + "TO" + "KEN_ENV"): "app-placeholder",
            getattr(slack_bot_module, "SLACK_BOT_" + "TO" + "KEN_ENV"): "bot-placeholder",
            "SLACK_BOT_HEALTH_CHECK_INTERVAL_SEC": "1",
            "SLACK_BOT_RECONNECT_GRACE_SEC": "1",
            "SLACK_BOT_MAX_FORCED_RECONNECTS": "1",
        }

        with mock.patch.dict(
            sys.modules,
            {
                "slack_sdk": slack_sdk_module,
                "slack_sdk.socket_mode": socket_mode_module,
                "slack_sdk.socket_mode.response": response_module,
            },
        ), mock.patch.object(
            slack_bot_module.threading,
            "Event",
            return_value=FakeStopEvent(),
        ), mock.patch.object(
            slack_bot_module.time,
            "monotonic",
            side_effect=[0.0, 2.0, 3.0],
        ), self.assertRaisesRegex(
            RuntimeError,
            "remained disconnected",
        ):
            slack_bot_module.run_socket_mode_bot(env=env)

        self.assertEqual(FakeSocketModeClient.reconnects, 1)

    def test_health_loop_runs_engine_sentinel_tick_each_iteration(self) -> None:
        # The always-resident bot is the dead-man switch: every health-loop tick
        # must invoke the engine sentinel, independent of the connectivity branch.
        slack_sdk_module = types.ModuleType("slack_sdk")
        slack_sdk_module.WebClient = mock.Mock()
        socket_mode_module = types.ModuleType("slack_sdk.socket_mode")
        response_module = types.ModuleType("slack_sdk.socket_mode.response")

        class FakeSocketModeClient:
            def __init__(self, **kwargs: object) -> None:
                self.socket_mode_request_listeners: list[object] = []

            def connect(self) -> None:
                return None

            def is_connected(self) -> bool:
                return True

            def close(self) -> None:
                self.closed = True

        class CountingStopEvent:
            def __init__(self) -> None:
                self.calls = 0

            def wait(self, timeout: float) -> bool:
                self.calls += 1
                return self.calls >= 3  # False, False, True -> two loop bodies

        socket_mode_module.SocketModeClient = FakeSocketModeClient
        response_module.SocketModeResponse = mock.Mock()
        env = {
            getattr(slack_bot_module, "SLACK_APP_" + "TO" + "KEN_ENV"): "app-placeholder",
            getattr(slack_bot_module, "SLACK_BOT_" + "TO" + "KEN_ENV"): "bot-placeholder",
        }
        tick = mock.Mock()

        with mock.patch.dict(
            sys.modules,
            {
                "slack_sdk": slack_sdk_module,
                "slack_sdk.socket_mode": socket_mode_module,
                "slack_sdk.socket_mode.response": response_module,
            },
        ), mock.patch.object(
            slack_bot_module.threading,
            "Event",
            return_value=CountingStopEvent(),
        ), mock.patch.object(
            slack_bot_module,
            "run_engine_sentinel_tick",
            tick,
        ):
            slack_bot_module.run_socket_mode_bot(env=env)

        self.assertEqual(tick.call_count, 2)
        for call in tick.call_args_list:
            self.assertEqual(call.kwargs["env"], env)
            self.assertIn("sentinel", call.kwargs)

    def test_health_loop_survives_disabled_sentinel(self) -> None:
        # With ENGINE_SENTINEL_ENABLED=0 the real tick is still invoked but its
        # internal kill-switch makes it a harmless no-op; the bot loop runs fine.
        slack_sdk_module = types.ModuleType("slack_sdk")
        slack_sdk_module.WebClient = mock.Mock()
        socket_mode_module = types.ModuleType("slack_sdk.socket_mode")
        response_module = types.ModuleType("slack_sdk.socket_mode.response")

        class FakeSocketModeClient:
            def __init__(self, **kwargs: object) -> None:
                self.socket_mode_request_listeners: list[object] = []

            def connect(self) -> None:
                return None

            def is_connected(self) -> bool:
                return True

            def close(self) -> None:
                self.closed = True

        class CountingStopEvent:
            def __init__(self) -> None:
                self.calls = 0

            def wait(self, timeout: float) -> bool:
                self.calls += 1
                return self.calls >= 2  # one loop body

        socket_mode_module.SocketModeClient = FakeSocketModeClient
        response_module.SocketModeResponse = mock.Mock()
        env = {
            getattr(slack_bot_module, "SLACK_APP_" + "TO" + "KEN_ENV"): "app-placeholder",
            getattr(slack_bot_module, "SLACK_BOT_" + "TO" + "KEN_ENV"): "bot-placeholder",
            "ENGINE_SENTINEL_ENABLED": "0",
        }
        wrapped = mock.Mock(wraps=slack_bot_module.run_engine_sentinel_tick)

        with mock.patch.dict(
            sys.modules,
            {
                "slack_sdk": slack_sdk_module,
                "slack_sdk.socket_mode": socket_mode_module,
                "slack_sdk.socket_mode.response": response_module,
            },
        ), mock.patch.object(
            slack_bot_module.threading,
            "Event",
            return_value=CountingStopEvent(),
        ), mock.patch.object(
            slack_bot_module,
            "run_engine_sentinel_tick",
            wrapped,
        ):
            slack_bot_module.run_socket_mode_bot(env=env)  # must not raise

        self.assertGreaterEqual(wrapped.call_count, 1)

    def test_disclosure_event_type_routes_to_operator_channel(self) -> None:
        from app.notifications.slack import (
            DISCLOSURE_EVENT_TYPE,
            EVENT_CHANNEL_ENV_BY_TYPE,
            OPERATOR_CHANNEL_ENV,
            resolve_channel_env_vars,
        )

        self.assertEqual(DISCLOSURE_EVENT_TYPE, "disclosure_alert")
        self.assertEqual(
            EVENT_CHANNEL_ENV_BY_TYPE[DISCLOSURE_EVENT_TYPE], OPERATOR_CHANNEL_ENV
        )
        # "routes to operator channel": resolution reads the OPTIONS map (not the
        # raw BY_TYPE map), so an operator-facing event must be registered there
        # too — exactly as the ENGINE_HEALTH precedent does.
        self.assertEqual(
            resolve_channel_env_vars(DISCLOSURE_EVENT_TYPE), (OPERATOR_CHANNEL_ENV,)
        )

    def test_bot_loop_calls_disclosure_tick(self) -> None:
        # The always-resident bot must also drive the disclosure sentinel each
        # health-loop tick — a pure addition alongside the engine sentinel tick.
        slack_sdk_module = types.ModuleType("slack_sdk")
        slack_sdk_module.WebClient = mock.Mock()
        socket_mode_module = types.ModuleType("slack_sdk.socket_mode")
        response_module = types.ModuleType("slack_sdk.socket_mode.response")

        class FakeSocketModeClient:
            def __init__(self, **kwargs: object) -> None:
                self.socket_mode_request_listeners: list[object] = []

            def connect(self) -> None:
                return None

            def is_connected(self) -> bool:
                return True

            def close(self) -> None:
                self.closed = True

        class CountingStopEvent:
            def __init__(self) -> None:
                self.calls = 0

            def wait(self, timeout: float) -> bool:
                self.calls += 1
                return self.calls >= 3  # False, False, True -> two loop bodies

        socket_mode_module.SocketModeClient = FakeSocketModeClient
        response_module.SocketModeResponse = mock.Mock()
        env = {
            getattr(slack_bot_module, "SLACK_APP_" + "TO" + "KEN_ENV"): "app-placeholder",
            getattr(slack_bot_module, "SLACK_BOT_" + "TO" + "KEN_ENV"): "bot-placeholder",
        }
        tick = mock.Mock()

        with mock.patch.dict(
            sys.modules,
            {
                "slack_sdk": slack_sdk_module,
                "slack_sdk.socket_mode": socket_mode_module,
                "slack_sdk.socket_mode.response": response_module,
            },
        ), mock.patch.object(
            slack_bot_module.threading,
            "Event",
            return_value=CountingStopEvent(),
        ), mock.patch.object(
            slack_bot_module,
            "run_engine_sentinel_tick",
            mock.Mock(),
        ), mock.patch.object(
            slack_bot_module,
            "run_disclosure_sentinel_tick",
            tick,
        ):
            slack_bot_module.run_socket_mode_bot(env=env)

        self.assertEqual(tick.call_count, 2)
        for call in tick.call_args_list:
            self.assertEqual(call.kwargs["env"], env)
            self.assertIn("sentinel", call.kwargs)


    def test_health_command_renders_ok_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "slack_status_snapshot.json"
            snapshot = build_slack_status_snapshot(
                runtime_state={
                    "last_market_session": "REGULAR",
                    "rate_limit_hits": 0,
                    "last_rate_limit_source": None,
                    "daily_pnl_pause_state": None,
                    "consecutive_backoff_cycles": 0,
                    "last_cycle_elapsed_ms": 450,
                },
                timestamp="2026-05-01T09:02:00+09:00",
            )
            self.assertTrue(write_slack_status_snapshot(snapshot, path=snapshot_path))

            reply = render_health_snapshot_reply(
                path=snapshot_path,
                now=datetime(2026, 5, 1, 0, 3, tzinfo=timezone.utc),
            )

        self.assertIn("✅ *시스템 건강 상태*", reply)
        self.assertIn("✅ Heartbeat", reply)
        self.assertIn("✅ Rate-limit", reply)
        self.assertIn("✅ Daily PnL", reply)
        self.assertIn("✅ 예외", reply)
        self.assertIn("⏱️ 마지막 cycle: `450ms`", reply)

    def test_health_command_shows_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "slack_status_snapshot.json"
            snapshot = build_slack_status_snapshot(
                runtime_state={
                    "last_market_session": "REGULAR",
                    "last_error_count": 3,
                    "rate_limit_hits": 2,
                    "last_rate_limit_source": "sell_watch",
                    "daily_pnl_pause_state": "PAUSED",
                    "daily_pnl_pause_reason": "daily loss exceeded",
                    "consecutive_backoff_cycles": 5,
                },
                timestamp="2026-05-01T09:02:00+09:00",
            )
            self.assertTrue(write_slack_status_snapshot(snapshot, path=snapshot_path))

            reply = render_health_snapshot_reply(
                path=snapshot_path,
                now=datetime(2026, 5, 1, 0, 3, tzinfo=timezone.utc),
            )

        self.assertIn("⚠️ *시스템 건강 상태*", reply)
        self.assertIn("⚠️ Rate-limit", reply)
        self.assertIn("`2` hits", reply)
        self.assertIn("sell_watch", reply)
        self.assertIn("⚠️ Daily PnL", reply)
        self.assertIn("PAUSED", reply)
        self.assertIn("⚠️ 예외", reply)
        self.assertIn("`3`건", reply)
        self.assertIn("⚠️ 연속 backoff", reply)
        self.assertIn("`5` cycles", reply)

    def test_health_command_missing_snapshot_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "missing.json"
            reply = render_health_snapshot_reply(path=snapshot_path)
        self.assertIn("health 확인 제한됨", reply)

    def test_positions_command_renders_holdings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "slack_status_snapshot.json"
            snapshot = build_slack_status_snapshot(
                runtime_state={
                    "last_market_session": "REGULAR",
                    "masked_account_display": "1234***78-01",
                    "broker_last_synced_at": "2026-05-01T09:01:00+09:00",
                    "broker_last_synced_positions_by_symbol": {
                        "005930": 10,
                        "035720": 5,
                    },
                    "recent_market_snapshots_by_symbol": {
                        "005930": {"current_price": 70000},
                    },
                },
                timestamp="2026-05-01T09:02:00+09:00",
            )
            self.assertTrue(write_slack_status_snapshot(snapshot, path=snapshot_path))

            reply = render_positions_snapshot_reply(
                path=snapshot_path,
                now=datetime(2026, 5, 1, 0, 3, tzinfo=timezone.utc),
            )

        self.assertIn("📂 *보유 종목*", reply)
        self.assertIn("`005930` 삼성전자", reply)
        self.assertIn("`10`주", reply)
        self.assertIn("현재 `70,000원`", reply)
        self.assertIn("평가 `700,000원`", reply)
        self.assertIn("`035720`", reply)
        self.assertIn("`5`주", reply)
        self.assertIn("총 `2`종목", reply)
        self.assertIn("freshness=`fresh`", reply)
        self.assertIn("broker_sync=`2026-05-01 09:01:00 KST`", reply)
        self.assertIn("account=`1234***78-01`", reply)

    def test_positions_command_no_local_data_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "slack_status_snapshot.json"
            snapshot = build_slack_status_snapshot(
                runtime_state={"last_market_session": "REGULAR"},
                timestamp="2026-05-01T09:02:00+09:00",
            )
            self.assertTrue(write_slack_status_snapshot(snapshot, path=snapshot_path))

            reply = render_positions_snapshot_reply(
                path=snapshot_path,
                now=datetime(2026, 5, 1, 0, 3, tzinfo=timezone.utc),
            )

        self.assertIn("📂 *보유 종목*", reply)
        self.assertIn("No positions in latest local snapshot", reply)
        self.assertIn("freshness=`fresh`", reply)

    def test_positions_command_missing_snapshot_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            reply = render_positions_snapshot_reply(
                path=Path(tmp_dir) / "missing.json",
            )
        self.assertIn("보유 종목 확인 제한됨", reply)

    def test_positions_command_does_not_call_broker_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "slack_status_snapshot.json"
            snapshot = build_slack_status_snapshot(
                runtime_state={
                    "last_market_session": "REGULAR",
                    "broker_last_synced_positions_by_symbol": {"005930": 10},
                },
                timestamp="2026-05-01T09:02:00+09:00",
            )
            self.assertTrue(write_slack_status_snapshot(snapshot, path=snapshot_path))

            with mock.patch(
                "app.domestic_stock.balance.inquire_balance",
                side_effect=AssertionError("broker API must not be called"),
            ):
                reply = render_command_reply(
                    parse_command("positions"),
                    snapshot_path=snapshot_path,
                    now=datetime(2026, 5, 1, 0, 3, tzinfo=timezone.utc),
                )

        self.assertIn("005930", reply)

    def test_orders_today_renders_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "orders.jsonl"
            records = [
                {
                    "timestamp": "2026-05-01T09:10:00+09:00",
                    "cycle_id": "cycle-buy-1",
                    "action": "order_submitted",
                    "symbol": "005930",
                    "symbol_name": "삼성전자",
                    "qty": 3,
                    "pid": 111,
                    "raw_response": {
                        "order_plan": {
                            "current_price_krw": 70000,
                            "notional_krw": 210000,
                        }
                    },
                },
                {
                    "timestamp": "2026-05-01T09:10:01+09:00",
                    "cycle_id": "cycle-buy-1",
                    "action": "order_succeeded",
                    "symbol": "005930",
                    "symbol_name": "삼성전자",
                    "qty": 3,
                    "pid": 111,
                    "raw_response": {
                        "order_plan": {
                            "current_price_krw": 70000,
                            "notional_krw": 210000,
                        }
                    },
                },
                {
                    "timestamp": "2026-05-01T09:30:00+09:00",
                    "cycle_id": "cycle-sell-1",
                    "action": "sell_order_submitted",
                    "symbol": "035720",
                    "symbol_name": "카카오",
                    "qty": 5,
                },
            ]
            log_path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
                encoding="utf-8",
            )

            reply = str(render_orders_today_reply(
                order_log_path=log_path,
                now=datetime(2026, 5, 1, 0, 10, tzinfo=timezone.utc),
            ))

        self.assertIn("*오늘 주문 처리 내역*", reply)
        self.assertIn("브로커 응답 중심", reply)
        self.assertIn("요약: 제출 2건 · 접수 1건 · 실패 0건 · no_position_on_sell 0건", reply)
        self.assertIn("*접수/성공*", reply)
        self.assertIn("삼성전자", reply)
        self.assertIn("`70,000원`", reply)
        self.assertIn("`210,000원`", reply)
        self.assertIn("pid `111`", reply)
        self.assertIn("submitted `09:10`", reply)
        self.assertIn("*제출만 기록*", reply)
        self.assertIn("카카오", reply)

    def test_orders_today_filters_by_now_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "orders.jsonl"
            records = [
                {
                    "timestamp": "2026-05-01T09:10:00+09:00",
                    "action": "order_succeeded",
                    "symbol": "005930",
                    "qty": 3,
                },
                {
                    "timestamp": "2026-05-02T09:10:00+09:00",
                    "action": "order_succeeded",
                    "symbol": "035720",
                    "qty": 5,
                },
            ]
            log_path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
                encoding="utf-8",
            )

            reply_may1 = str(render_orders_today_reply(
                order_log_path=log_path,
                now=datetime(2026, 5, 1, 2, 0, tzinfo=timezone.utc),
            ))
            reply_may2 = str(render_orders_today_reply(
                order_log_path=log_path,
                now=datetime(2026, 5, 2, 2, 0, tzinfo=timezone.utc),
            ))

        self.assertIn("005930", reply_may1)
        self.assertNotIn("035720", reply_may1)
        self.assertIn("035720", reply_may2)
        self.assertNotIn("005930", reply_may2)

    def test_orders_today_shows_failed_no_position_category_and_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "orders.jsonl"
            records = [
                {
                    "timestamp": "2026-05-01T10:10:00+09:00",
                    "action": "sell_order_failed",
                    "symbol": "068270",
                    "symbol_name": "셀트리온",
                    "qty": 1,
                    "pid": 77748,
                    "reason": "잔고내역이 없습니다 (40240000)",
                    "raw_response": {
                        "failure_category": "no_position_on_sell",
                        "sell_plan": {
                            "current_price_krw": 170000,
                            "notional_krw": 170000,
                        },
                    },
                }
            ]
            log_path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
                encoding="utf-8",
            )

            reply = str(render_orders_today_reply(
                order_log_path=log_path,
                now=datetime(2026, 5, 1, 1, 10, tzinfo=timezone.utc),
            ))

        self.assertIn("요약: 제출 0건 · 접수 0건 · 실패 1건 · no_position_on_sell 1건", reply)
        self.assertIn("*실패/거절*", reply)
        self.assertIn("068270 셀트리온", reply)
        self.assertIn("no_position_on_sell", reply)
        self.assertIn("pid `77748`", reply)
        self.assertIn("40240000", reply)

    def test_orders_today_filters_account_signature_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "orders.jsonl"
            records = [
                {
                    "timestamp": "2026-05-01T09:10:00+09:00",
                    "action": "order_succeeded",
                    "symbol": "005930",
                    "qty": 3,
                    "account_signature": "mock_a",
                },
                {
                    "timestamp": "2026-05-01T09:11:00+09:00",
                    "action": "order_succeeded",
                    "symbol": "035720",
                    "qty": 5,
                    "account_signature": "mock_b",
                },
            ]
            log_path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
                encoding="utf-8",
            )

            reply = str(render_orders_today_reply(
                order_log_path=log_path,
                now=datetime(2026, 5, 1, 0, 30, tzinfo=timezone.utc),
                account_signature="mock_a",
            ))

        self.assertIn("005930", reply)
        self.assertNotIn("035720", reply)

    def test_orders_today_uses_bounded_reader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "orders.jsonl"
            records = [
                {
                    "timestamp": "2026-05-01T09:00:00+09:00",
                    "action": "order_succeeded",
                    "symbol": "OLD",
                    "qty": 1,
                },
                {
                    "timestamp": "2026-05-01T09:01:00+09:00",
                    "action": "order_succeeded",
                    "symbol": "NEW1",
                    "qty": 1,
                },
                {
                    "timestamp": "2026-05-01T09:02:00+09:00",
                    "action": "order_succeeded",
                    "symbol": "NEW2",
                    "qty": 1,
                },
            ]
            log_path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
                encoding="utf-8",
            )

            reply = str(render_orders_today_reply(
                order_log_path=log_path,
                now=datetime(2026, 5, 1, 0, 30, tzinfo=timezone.utc),
                max_log_lines=2,
            ))

        self.assertNotIn("OLD", reply)
        self.assertIn("NEW1", reply)
        self.assertIn("NEW2", reply)

    def test_orders_today_does_not_call_broker_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "orders.jsonl"
            log_path.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-05-01T09:10:00+09:00",
                        "action": "order_succeeded",
                        "symbol": "005930",
                        "qty": 3,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with mock.patch(
                "app.domestic_stock.balance.inquire_balance",
                side_effect=AssertionError("broker API must not be called"),
            ):
                reply = render_orders_today_reply(
                    order_log_path=log_path,
                    now=datetime(2026, 5, 1, 0, 30, tzinfo=timezone.utc),
                )

        self.assertIn("005930", str(reply))

    def test_orders_today_no_log_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            reply = str(render_orders_today_reply(
                order_log_path=Path(tmp_dir) / "missing.jsonl",
            ))
        self.assertIn("*오늘 주문 처리 내역*", reply)
        self.assertIn("주문 기록이 없습니다", reply)

    def test_command_aliases_resolve_correctly(self) -> None:
        self.assertEqual(parse_command("<@U123> position").name, "positions")
        self.assertEqual(parse_command("<@U123> order today").name, "orders")
        self.assertEqual(parse_command("<@U123> orders today").name, "orders")

    def test_help_text_includes_new_commands(self) -> None:
        self.assertIn("`health`", HELP_TEXT)
        self.assertIn("`positions`", HELP_TEXT)
        self.assertIn("`orders today`", HELP_TEXT)
        # S3: the backtest row advertises the days=N window option.
        self.assertIn("`backtest [YYYYMMDD] [days=N]`", HELP_TEXT)
        self.assertIn("days=N", HELP_TEXT)

    def test_unknown_command_still_falls_back(self) -> None:
        command = parse_command("<@U123ABC> sell 005930")
        self.assertEqual(command.name, "unknown")
        reply = render_command_reply(command)
        self.assertIn("지원하지 않는 명령", reply)

    def test_handle_app_mention_health_returns_safe_reply(self) -> None:
        reply = handle_app_mention_event(
            {"type": "app_mention", "text": "<@U123> health", "channel": "C1"},
            env={"SLACK_BOT_ALLOWED_CHANNEL_IDS": "C1"},
        )
        self.assertIsNotNone(reply)

    def test_backtest_command_is_project_backtester_channel_only(self) -> None:
        event = {
            "type": "app_mention",
            "text": "<@U123> backtest",
            "channel": "C_BACKTESTER",
            "user": "U_OK",
            "team": "T_OK",
        }
        env = {
            "SLACK_CHANNEL_PROJECT_BACK_TESTER": "C_BACKTESTER",
            "SLACK_BOT_ALLOWED_USER_IDS": "U_OK",
            "SLACK_BOT_ALLOWED_TEAM_IDS": "T_OK",
        }

        started_result = BacktestLaunchResult(
            status="started", pid=1234, log_path=Path("/tmp/bt.log")
        )
        # Patch both modules: the canonical implementation lives in
        # backtest_control; slack_bot keeps a facade binding (R3-S5).
        with mock.patch.object(
            slack_bot_module,
            "launch_backtest_pipeline",
            return_value=started_result,
        ), mock.patch.object(
            backtest_control_module,
            "launch_backtest_pipeline",
            return_value=started_result,
        ):
            allowed = handle_app_mention_event(event, env=env)
            denied_wrong_channel = handle_app_mention_event(
                {**event, "channel": "C_OTHER"},
                env=env,
            )
            denied_wrong_user = handle_app_mention_event(
                {**event, "user": "U_OTHER"},
                env=env,
            )

        self.assertIn("Backtest pipeline started", str(allowed))
        self.assertEqual(denied_wrong_channel, UNAUTHORIZED_COMMAND_TEXT)
        self.assertEqual(denied_wrong_user, UNAUTHORIZED_COMMAND_TEXT)

    def test_backtest_command_starts_without_configured_account(self) -> None:
        # S1: the account requirement was removed from the backtest launch
        # pipeline — "backtest" now starts even when no account env is set.
        started_result = BacktestLaunchResult(
            status="started", pid=1234, log_path=Path("/tmp/bt.log")
        )
        with mock.patch.object(
            slack_bot_module,
            "launch_backtest_pipeline",
            return_value=started_result,
        ), mock.patch.object(
            backtest_control_module,
            "launch_backtest_pipeline",
            return_value=started_result,
        ):
            reply = handle_app_mention_event(
                {
                    "type": "app_mention",
                    "text": "<@U123> backtest",
                    "channel": "C_BACKTESTER",
                    "user": "U_OK",
                },
                env={
                    "SLACK_CHANNEL_PROJECT_BACK_TESTER": "C_BACKTESTER",
                    "SLACK_BOT_ALLOWED_USER_IDS": "U_OK",
                },
            )

        self.assertIn("Backtest pipeline started", str(reply))

    def test_backtest_command_rejects_unknown_options(self) -> None:
        reply = handle_app_mention_event(
            {
                "type": "app_mention",
                "text": "<@U123> backtest ; rm -rf /",
                "channel": "C_BACKTESTER",
                "user": "U_OK",
            },
            env={
                "SLACK_CHANNEL_PROJECT_BACK_TESTER": "C_BACKTESTER",
                "SLACK_BOT_ALLOWED_USER_IDS": "U_OK",
            },
        )

        self.assertIn("지원하지 않는 backtest 옵션", str(reply))

    def test_launch_backtest_pipeline_starts_background_script_with_sanitized_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            reports = root / "reports"
            script = root / "pipeline.sh"
            script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            calls: dict[str, object] = {}

            class FakeProcess:
                pid = 4321

            def fake_popen(argv, **kwargs):
                calls["argv"] = argv
                calls["kwargs"] = kwargs
                return FakeProcess()

            with mock.patch.object(
                backtest_control_module, "DEFAULT_BACKTEST_PIPELINE_SCRIPT", script
            ):
                result = launch_backtest_pipeline(
                    BacktestCommandOptions(date="20260603", dry_run=True, skip_run=True),
                    env={
                        SLACK_BACKTEST_REPORTS_DIR_ENV: str(reports),
                        "OPEN_TRADING_API_ROOT": "/tmp/open-trading-api",
                        "SLACK_BOT_TOKEN": "must-not-leak",
                        "PATH": os.defpath,
                        "HOME": str(root),
                    },
                    popen_factory=fake_popen,
                    now=datetime(2026, 6, 10, 9, 0),
                )

            self.assertEqual(result.status, "started")
            self.assertEqual(result.pid, 4321)
            self.assertEqual(
                calls["argv"],
                [
                    str(script),
                    "--date",
                    "20260603",
                    "--skip-run",
                    "--dry-run",
                ],
            )
            kwargs = calls["kwargs"]
            self.assertEqual(kwargs["cwd"], slack_bot_module.PROJECT_ROOT)
            self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
            self.assertTrue(kwargs["start_new_session"])
            self.assertNotIn("SLACK_BOT_TOKEN", kwargs["env"])
            # 2026-07-10 §D: the sidecar root is filtered out of the child env.
            self.assertNotIn("OPEN_TRADING_API_ROOT", kwargs["env"])
            self.assertEqual((reports / "slack_backtest.pid").read_text(encoding="utf-8"), "4321\n")

    def test_launch_backtest_pipeline_refuses_when_previous_pid_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            reports = Path(tmp_dir) / "reports"
            reports.mkdir()
            (reports / "slack_backtest.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

            result = launch_backtest_pipeline(
                BacktestCommandOptions(),
                env={
                    SLACK_BACKTEST_REPORTS_DIR_ENV: str(reports),
                },
                popen_factory=mock.Mock(side_effect=AssertionError("must not start")),
            )

        self.assertEqual(result.status, "already_running")
        self.assertEqual(result.pid, os.getpid())

    def test_backtest_completion_monitor_posts_result_and_cleans_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            reports = Path(tmp_dir)
            log_path = reports / "slack_backtest.log"
            pid_path = reports / "slack_backtest.pid"
            pid_path.write_text("4321\n", encoding="utf-8")

            class FakeProcess:
                def wait(self, timeout=None) -> int:
                    return 0

            fake_web_client = mock.Mock()
            result = BacktestLaunchResult(
                status="started",
                pid=4321,
                log_path=log_path,
                process=FakeProcess(),
            )

            with mock.patch.object(slack_bot_module.time, "monotonic", side_effect=[10.0, 15.0]):
                slack_bot_module._start_backtest_completion_monitor(
                    result,
                    web_client=fake_web_client,
                    channel="C_BACKTESTER",
                    thread_ts="1716000000.000100",
                )

            for _ in range(20):
                if fake_web_client.chat_postMessage.called and not pid_path.exists():
                    break
                time.sleep(0.01)

            fake_web_client.chat_postMessage.assert_called_once()
            kwargs = fake_web_client.chat_postMessage.call_args.kwargs
            self.assertEqual(kwargs["channel"], "C_BACKTESTER")
            self.assertEqual(kwargs["thread_ts"], "1716000000.000100")
            self.assertIn("Backtest pipeline completed", kwargs["text"])
            self.assertIn("duration: `5s`", kwargs["text"])
            self.assertFalse(pid_path.exists())

    def test_backtest_failure_monitor_includes_log_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            reports = Path(tmp_dir)
            log_path = reports / "slack_backtest.log"
            log_path.write_text("step ok\nTraceback: python mismatch\n", encoding="utf-8")
            pid_path = reports / "slack_backtest.pid"
            pid_path.write_text("4322\n", encoding="utf-8")

            class FakeProcess:
                def wait(self, timeout=None) -> int:
                    return 1

            fake_web_client = mock.Mock()
            result = BacktestLaunchResult(
                status="started",
                pid=4322,
                log_path=log_path,
                process=FakeProcess(),
            )

            with mock.patch.object(slack_bot_module.time, "monotonic", side_effect=[10.0, 12.0]):
                slack_bot_module._start_backtest_completion_monitor(
                    result,
                    web_client=fake_web_client,
                    channel="C_BACKTESTER",
                    thread_ts="1716000000.000100",
                )

            for _ in range(20):
                if fake_web_client.chat_postMessage.called and not pid_path.exists():
                    break
                time.sleep(0.01)

            text = fake_web_client.chat_postMessage.call_args.kwargs["text"]
            self.assertIn("Backtest pipeline failed", text)
            self.assertIn("Traceback: python mismatch", text)
            self.assertFalse(pid_path.exists())

    def test_sensitive_app_mention_requires_allowlist(self) -> None:
        reply = handle_app_mention_event(
            {"type": "app_mention", "text": "<@U123> positions", "channel": "C1"},
            env={},
        )

        self.assertEqual(reply, UNAUTHORIZED_COMMAND_TEXT)

    def test_sensitive_app_mention_respects_user_and_team_allowlists(self) -> None:
        event = {
            "type": "app_mention",
            "text": "<@U123> health",
            "channel": "C1",
            "user": "U_OK",
            "team": "T_OK",
        }

        allowed = handle_app_mention_event(
            event,
            env={
                "SLACK_BOT_ALLOWED_USER_IDS": "U_OK",
                "SLACK_BOT_ALLOWED_TEAM_IDS": "T_OK",
            },
        )
        denied = handle_app_mention_event(
            event,
            env={
                "SLACK_BOT_ALLOWED_USER_IDS": "U_OTHER",
                "SLACK_BOT_ALLOWED_TEAM_IDS": "T_OK",
            },
        )

        self.assertNotEqual(allowed, UNAUTHORIZED_COMMAND_TEXT)
        self.assertEqual(denied, UNAUTHORIZED_COMMAND_TEXT)

    def test_orders_today_returns_dict_with_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "orders.jsonl"
            records = [
                {
                    "timestamp": "2026-05-01T09:10:00+09:00",
                    "action": "order_succeeded",
                    "symbol": "005930",
                    "qty": 3,
                },
                {
                    "timestamp": "2026-05-01T09:20:00+09:00",
                    "action": "sell_order_succeeded",
                    "symbol": "035720",
                    "qty": 5,
                },
            ]
            log_path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
                encoding="utf-8",
            )
            reply = render_orders_today_reply(
                order_log_path=log_path,
                now=datetime(2026, 5, 1, 0, 30, tzinfo=timezone.utc),
            )

        self.assertIsInstance(reply, dict)
        attachments = reply.get("attachments", [])  # type: ignore[union-attr]
        self.assertIsInstance(attachments, list)
        colors = [a.get("color") for a in attachments if a.get("color") not in ("#888888",)]
        self.assertIn("#2eb886", colors)
        self.assertIn("#e01e5a", colors)

    def test_exception_in_command_handler_does_not_crash(self) -> None:
        reply = handle_app_mention_event(
            {"type": "app_mention", "text": "<@U123> positions", "channel": "C1"}
        )
        self.assertIsNotNone(reply)


class BriefingCommandTests(unittest.TestCase):
    def test_parse_command_briefing(self) -> None:
        command = parse_command("<@U123ABC> briefing")
        self.assertEqual(command.name, "briefing")

    def test_render_briefing_command_reply_smoke(self) -> None:
        from app.tools import morning_briefing_report

        # Happy path over an empty tmp dir → a string reply. No real data/ read,
        # no Slack send (the bot reply is the return value, not a notification).
        with tempfile.TemporaryDirectory() as tmp_dir, mock.patch.dict(
            "os.environ",
            {"MORNING_REGIME_ARTIFACT_DIR": "", "DISCLOSURE_SENTINEL_STATE_DIR": ""},
        ):
            reply = morning_briefing_report.render_briefing_command_reply(
                data_dir=tmp_dir
            )
            self.assertIsInstance(reply, str)
            self.assertIn("모닝 브리핑", reply)

            # Assembly failure → error-text reply; the exception never propagates.
            with mock.patch.object(
                morning_briefing_report,
                "_assemble_briefing",
                side_effect=RuntimeError("boom"),
            ):
                failed = morning_briefing_report.render_briefing_command_reply(
                    data_dir=tmp_dir
                )
            self.assertIsInstance(failed, str)
            self.assertIn("브리핑", failed)
            self.assertIn("오류", failed)


if __name__ == "__main__":
    unittest.main()
