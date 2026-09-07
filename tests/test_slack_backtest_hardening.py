"""Tests for slack_bot backtest P0/P1 hardening behaviour.

Covers authorization, status/kill subcommands, the completion monitor,
orphan-monitor notices, and recycled-pid protection.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from app.notifications import backtest_control as backtest_control_module
from app.notifications import slack_bot as slack_bot_module
from app.notifications.slack_bot import (
    BacktestCommandOptions,
    BacktestLaunchResult,
    SLACK_BACKTEST_REPORTS_DIR_ENV,
    launch_backtest_pipeline,
    parse_command,
    render_command_reply,
)


class SlackBacktestP0HardeningTests(unittest.TestCase):
    # ── P0-1: backtest requires a configured user allowlist ──

    def test_backtest_authorization_requires_user_allowlist(self) -> None:
        event = {"channel": "C_BT", "user": "U_OK", "team": "T_OK"}
        base_env = {"SLACK_CHANNEL_PROJECT_BACK_TESTER": "C_BT"}

        self.assertFalse(
            slack_bot_module.is_backtest_command_authorized(event, env=base_env)
        )
        self.assertTrue(
            slack_bot_module.is_backtest_command_authorized(
                event,
                env={**base_env, "SLACK_BOT_ALLOWED_USER_IDS": "U_OK"},
            )
        )
        self.assertFalse(
            slack_bot_module.is_backtest_command_authorized(
                {**event, "user": "U_OTHER"},
                env={**base_env, "SLACK_BOT_ALLOWED_USER_IDS": "U_OK"},
            )
        )


    # ── P0-3: status / kill subcommands ──

    def test_backtest_status_and_kill_subcommands_parse(self) -> None:
        status_opts, status_err = slack_bot_module._parse_backtest_command_options(
            "backtest status"
        )
        kill_opts, kill_err = slack_bot_module._parse_backtest_command_options(
            "backtest kill"
        )
        run_opts, run_err = slack_bot_module._parse_backtest_command_options(
            "backtest 20260610"
        )

        self.assertEqual(status_err, "")
        self.assertEqual(kill_err, "")
        self.assertEqual(run_err, "")
        assert status_opts is not None and kill_opts is not None and run_opts is not None
        self.assertEqual(status_opts.action, "status")
        self.assertEqual(kill_opts.action, "kill")
        self.assertEqual(run_opts.action, "run")


    def test_backtest_status_reply_reports_running_and_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            reports = Path(tmp_dir)
            env = {SLACK_BACKTEST_REPORTS_DIR_ENV: str(reports)}

            idle = slack_bot_module.render_backtest_status_reply(env=env)
            self.assertIn("not running", idle)

            (reports / "slack_backtest.pid").write_text(
                f"{os.getpid()}\n", encoding="utf-8"
            )
            (reports / "slack_backtest_20260610_090000.log").write_text(
                "[1/5] Backtester service\n", encoding="utf-8"
            )
            running = slack_bot_module.render_backtest_status_reply(env=env)

        self.assertIn(str(os.getpid()), running)
        self.assertIn("running", running)
        self.assertIn("[1/5] Backtester service", running)


    def test_backtest_kill_terminates_running_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            reports = Path(tmp_dir)
            proc = subprocess.Popen(
                ["sleep", "30"],
                start_new_session=True,
            )
            try:
                (reports / "slack_backtest.pid").write_text(
                    f"{proc.pid}\n", encoding="utf-8"
                )
                reply = slack_bot_module.render_backtest_kill_reply(
                    env={SLACK_BACKTEST_REPORTS_DIR_ENV: str(reports)}
                )
                for _ in range(50):
                    if proc.poll() is not None:
                        break
                    time.sleep(0.1)
                self.assertIsNotNone(proc.poll())
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()

        self.assertIn(str(proc.pid), reply)
        self.assertIn("terminat", reply.lower())


    def test_backtest_kill_when_idle_reports_not_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            reply = slack_bot_module.render_backtest_kill_reply(
                env={SLACK_BACKTEST_REPORTS_DIR_ENV: tmp_dir}
            )

        self.assertIn("not running", reply)


    def test_backtest_status_subcommand_does_not_launch_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with mock.patch.object(
                slack_bot_module,
                "launch_backtest_pipeline",
                side_effect=AssertionError("must not launch"),
            ), mock.patch.object(
                backtest_control_module,
                "launch_backtest_pipeline",
                side_effect=AssertionError("must not launch"),
            ):
                reply = render_command_reply(
                    parse_command("backtest status"),
                    env={
                        SLACK_BACKTEST_REPORTS_DIR_ENV: tmp_dir,
                    },
                )

        self.assertIn("not running", str(reply))


    def test_backtest_monitor_kills_process_group_after_max_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            reports = Path(tmp_dir)
            log_path = reports / "slack_backtest.log"
            pid_path = reports / "slack_backtest.pid"
            pid_path.write_text("4399\n", encoding="utf-8")

            class FakeProcess:
                def __init__(self) -> None:
                    self.wait_calls: list[object] = []

                def wait(self, timeout=None) -> int:
                    self.wait_calls.append(timeout)
                    if len(self.wait_calls) == 1:
                        raise subprocess.TimeoutExpired(
                            cmd="pipeline", timeout=timeout or 0
                        )
                    return -15

            fake_process = FakeProcess()
            fake_web_client = mock.Mock()
            killed: list[int] = []
            result = BacktestLaunchResult(
                status="started",
                pid=4399,
                log_path=log_path,
                process=fake_process,
            )

            with mock.patch.object(
                slack_bot_module,
                "_terminate_backtest_process_group",
                side_effect=lambda pid: killed.append(pid) or True,
            ), mock.patch.object(
                backtest_control_module,
                "_terminate_backtest_process_group",
                side_effect=lambda pid: killed.append(pid) or True,
            ):
                with mock.patch.object(
                    slack_bot_module.time, "monotonic", side_effect=[10.0, 130.0]
                ):
                    slack_bot_module._start_backtest_completion_monitor(
                        result,
                        web_client=fake_web_client,
                        channel="C_BACKTESTER",
                        thread_ts="1716000000.000100",
                        max_runtime_sec=120,
                    )

                for _ in range(50):
                    if fake_web_client.chat_postMessage.called and not pid_path.exists():
                        break
                    time.sleep(0.01)

            self.assertEqual(fake_process.wait_calls[0], 120)
            self.assertEqual(killed, [4399])
            text = fake_web_client.chat_postMessage.call_args.kwargs["text"]
            self.assertIn("timed out", text)
            self.assertIn("120", text)
            self.assertFalse(pid_path.exists())


    def test_socket_mode_backtest_dispatch_passes_max_runtime_to_monitor(self) -> None:
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
                            "text": "<@U123> backtest",
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
            "SLACK_BACKTEST_MAX_RUNTIME_SEC": "777",
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
        # S3 (approved semantic change, design plan §3.2/§3.3):
        # SLACK_BACKTEST_MAX_RUNTIME_SEC turned from a cap into a *floor* of the
        # adaptive timeout. A bare `backtest` assumes 60 days, so the resolved
        # value is max(777, int(60 * 210 * 1.5) + 120) = 19020 — the env value
        # no longer passes through unchanged. The floor-wins case is pinned in
        # test_backtest_control.py::ResolveBacktestMaxRuntimeTests.
        self.assertEqual(
            monitor_mock.call_args.kwargs["max_runtime_sec"],
            19020,
        )


class SlackBacktestP1HardeningTests(unittest.TestCase):
    # ── P1-1: bot restart loses the completion monitor thread — the channel
    # must hear about a still-running pipeline it can no longer auto-report on ──

    def test_orphan_monitor_notice_for_running_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            reports = Path(tmp_dir)
            env = {SLACK_BACKTEST_REPORTS_DIR_ENV: str(reports)}

            self.assertIsNone(
                slack_bot_module.render_backtest_orphan_monitor_notice(env=env)
            )

            (reports / "slack_backtest.pid").write_text(
                f"{os.getpid()}\n", encoding="utf-8"
            )
            notice = slack_bot_module.render_backtest_orphan_monitor_notice(env=env)

        assert notice is not None
        self.assertIn(str(os.getpid()), notice)
        self.assertIn("backtest status", notice)
        self.assertIn("backtest kill", notice)

    def test_socket_mode_startup_posts_orphan_monitor_notice(self) -> None:
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

            def connect(self) -> None:
                pass

            def is_connected(self) -> bool:
                return True

            def close(self) -> None:
                pass

        class FakeStopEvent:
            def wait(self, timeout: float) -> bool:
                raise KeyboardInterrupt

        socket_mode_module.SocketModeClient = FakeSocketModeClient
        response_module.SocketModeResponse = FakeSocketModeResponse

        with tempfile.TemporaryDirectory() as tmp_dir:
            reports = Path(tmp_dir)
            (reports / "slack_backtest.pid").write_text(
                f"{os.getpid()}\n", encoding="utf-8"
            )
            env = {
                getattr(slack_bot_module, "SLACK_APP_" + "TO" + "KEN_ENV"): "app-placeholder",
                getattr(slack_bot_module, "SLACK_BOT_" + "TO" + "KEN_ENV"): "bot-placeholder",
                "SLACK_CHANNEL_PROJECT_BACK_TESTER": "C_BT",
                SLACK_BACKTEST_REPORTS_DIR_ENV: str(reports),
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
            ), self.assertRaises(KeyboardInterrupt):
                slack_bot_module.run_socket_mode_bot(env=env)

        fake_web_client.chat_postMessage.assert_called_once()
        kwargs = fake_web_client.chat_postMessage.call_args.kwargs
        self.assertEqual(kwargs["channel"], "C_BT")
        self.assertIn(str(os.getpid()), kwargs["text"])
        self.assertIn("backtest status", kwargs["text"])

    # ── P1-2: a recycled OS pid must not look like a running pipeline ──

    def test_running_pid_rejects_recycled_pid_via_start_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pid_path = Path(tmp_dir) / "slack_backtest.pid"

            pid_path.write_text(
                f"{os.getpid()}\nSTALE MARKER FROM PREVIOUS BOOT\n",
                encoding="utf-8",
            )
            self.assertIsNone(slack_bot_module._running_pid_from_file(pid_path))

            marker = slack_bot_module._process_start_marker(os.getpid())
            assert marker
            pid_path.write_text(f"{os.getpid()}\n{marker}\n", encoding="utf-8")
            self.assertEqual(
                slack_bot_module._running_pid_from_file(pid_path), os.getpid()
            )

    def test_launch_writes_pid_file_with_start_marker(self) -> None:
        class FakeProcess:
            pid = os.getpid()

        with tempfile.TemporaryDirectory() as tmp_dir:
            env = {
                SLACK_BACKTEST_REPORTS_DIR_ENV: tmp_dir,
            }
            with mock.patch.object(
                backtest_control_module,
                "DEFAULT_BACKTEST_PIPELINE_SCRIPT",
                Path("/bin/true"),
            ):
                result = slack_bot_module.launch_backtest_pipeline(
                    slack_bot_module.BacktestCommandOptions(),
                    env=env,
                    popen_factory=lambda *args, **kwargs: FakeProcess(),
                )
            self.assertEqual(result.status, "started")
            lines = (
                (Path(tmp_dir) / "slack_backtest.pid")
                .read_text(encoding="utf-8")
                .splitlines()
            )

        self.assertEqual(lines[0], str(os.getpid()))
        self.assertEqual(
            lines[1], slack_bot_module._process_start_marker(os.getpid())
        )

    def test_remove_pid_file_matches_marker_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pid_path = Path(tmp_dir) / "slack_backtest.pid"

            pid_path.write_text("4321\nSOME START MARKER\n", encoding="utf-8")
            slack_bot_module._remove_pid_file_if_matches(pid_path, 4321)
            self.assertFalse(pid_path.exists())

            pid_path.write_text("9999\nSOME START MARKER\n", encoding="utf-8")
            slack_bot_module._remove_pid_file_if_matches(pid_path, 4321)
            self.assertTrue(pid_path.exists())


if __name__ == "__main__":
    unittest.main()
