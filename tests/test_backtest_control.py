"""Tests for app.notifications.backtest_control (R3-S5).

slack_bot의 백테스트 서브프로세스 제어 클러스터(파이프라인 기동/상태/중지/
완료 모니터)를 verbatim 이동. slack_bot은 facade로 동일 객체를 재수출해야
한다 (patch/import seam 보존).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.notifications import backtest_control, slack_bot

_CONTROL_NAMES = (
    "SLACK_BACKTEST_REPORTS_DIR_ENV",
    "SLACK_BACKTEST_STOP_AFTER_ENV",
    "SLACK_BACKTEST_MAX_RUNTIME_SEC_ENV",
    "DEFAULT_BACKTEST_MAX_RUNTIME_SEC",
    "PROJECT_ROOT",
    "DEFAULT_BACKTEST_REPORTS_DIR",
    "DEFAULT_BACKTEST_PIPELINE_SCRIPT",
    "BacktestCommandOptions",
    "BacktestLaunchResult",
    "_parse_backtest_command_options",
    "_truthy_env",
    "_process_start_marker",
    "_running_pid_from_file",
    "_remove_pid_file_if_matches",
    "_format_backtest_log_tail",
    "_build_backtest_subprocess_env",
    "launch_backtest_pipeline",
    "_resolve_backtest_reports_dir",
    "render_backtest_status_reply",
    "render_backtest_orphan_monitor_notice",
    "_terminate_backtest_process_group",
    "render_backtest_kill_reply",
    "render_backtest_command_reply",
    "_start_backtest_completion_monitor",
)


class CompletionMonitorFailureTests(unittest.TestCase):
    def test_post_failure_is_logged_and_pid_file_still_cleaned(self) -> None:
        import tempfile
        import time as time_module
        from pathlib import Path
        from unittest import mock

        class FakeProcess:
            def wait(self, timeout=None) -> int:
                return 0

        with tempfile.TemporaryDirectory() as tmp_dir:
            reports = Path(tmp_dir)
            log_path = reports / "slack_backtest.log"
            pid_path = reports / "slack_backtest.pid"
            pid_path.write_text("777\n", encoding="utf-8")
            web_client = mock.Mock()
            web_client.chat_postMessage.side_effect = RuntimeError("post failed")
            result = backtest_control.BacktestLaunchResult(
                status="started",
                pid=777,
                log_path=log_path,
                process=FakeProcess(),
            )

            with self.assertLogs(
                "app.notifications.backtest_control", level="WARNING"
            ) as captured:
                backtest_control._start_backtest_completion_monitor(
                    result,
                    web_client=web_client,
                    channel="C_BACKTESTER",
                    thread_ts=None,
                )
                for _ in range(50):
                    if not pid_path.exists():
                        break
                    time_module.sleep(0.01)

        self.assertFalse(pid_path.exists())
        self.assertTrue(
            any("RuntimeError" in message for message in captured.output),
            captured.output,
        )


class BacktestHeartbeatTests(unittest.TestCase):
    def test_build_record_carries_monitor_state(self) -> None:
        record = backtest_control.build_backtest_heartbeat_record(
            pid=777,
            started_at_epoch=1_000.0,
            max_runtime_sec=3_600,
            channel="C_BACKTESTER",
            thread_ts="123.45",
        )
        self.assertEqual(record["pid"], 777)
        self.assertEqual(record["started_at_epoch"], 1_000.0)
        self.assertEqual(record["max_runtime_sec"], 3_600)
        self.assertEqual(record["channel"], "C_BACKTESTER")
        self.assertEqual(record["thread_ts"], "123.45")

    def test_write_then_read_round_trips(self) -> None:
        record = backtest_control.build_backtest_heartbeat_record(
            pid=777, started_at_epoch=1_000.0, max_runtime_sec=3_600,
            channel="C_BACKTESTER", thread_ts="123.45",
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            reports = Path(tmp_dir)
            self.assertTrue(backtest_control.write_backtest_heartbeat(reports, record))
            self.assertEqual(backtest_control.read_backtest_heartbeat(reports), record)

    def test_read_corrupt_heartbeat_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            reports = Path(tmp_dir)
            (reports / backtest_control.BACKTEST_HEARTBEAT_FILENAME).write_text(
                "{not json", encoding="utf-8"
            )
            self.assertIsNone(backtest_control.read_backtest_heartbeat(reports))

    def test_orphan_notice_includes_heartbeat_elapsed_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            reports = Path(tmp_dir)
            (reports / "slack_backtest.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
            backtest_control.write_backtest_heartbeat(
                reports,
                backtest_control.build_backtest_heartbeat_record(
                    pid=os.getpid(), started_at_epoch=1_000.0,
                    max_runtime_sec=3_600, channel="C_BACKTESTER", thread_ts=None,
                ),
            )
            notice = backtest_control.render_backtest_orphan_monitor_notice(
                env={backtest_control.SLACK_BACKTEST_REPORTS_DIR_ENV: str(reports)},
                now_epoch=1_100.0,
            )
            self.assertIsNotNone(notice)
            self.assertIn("100s", notice)
            self.assertIn("3600s", notice)

    def test_monitor_writes_heartbeat_at_start_and_clears_on_finish(self) -> None:
        import threading
        import time as time_module
        from unittest import mock

        release = threading.Event()

        class BlockingProcess:
            def wait(self, timeout=None) -> int:
                release.wait(timeout=5)
                return 0

        with tempfile.TemporaryDirectory() as tmp_dir:
            reports = Path(tmp_dir)
            (reports / "slack_backtest.pid").write_text("777\n", encoding="utf-8")
            result = backtest_control.BacktestLaunchResult(
                status="started", pid=777,
                log_path=reports / "slack_backtest.log", process=BlockingProcess(),
            )
            backtest_control._start_backtest_completion_monitor(
                result, web_client=mock.Mock(), channel="C_BACKTESTER", thread_ts=None,
            )
            # Written synchronously before the (blocked) thread can clear it.
            self.assertIsNotNone(backtest_control.read_backtest_heartbeat(reports))
            release.set()
            for _ in range(100):
                if backtest_control.read_backtest_heartbeat(reports) is None:
                    break
                time_module.sleep(0.01)
            self.assertIsNone(backtest_control.read_backtest_heartbeat(reports))


class BacktestControlFacadeTests(unittest.TestCase):
    def test_slack_bot_binds_canonical_objects(self) -> None:
        for name in _CONTROL_NAMES:
            with self.subTest(name):
                self.assertIs(
                    getattr(slack_bot, name),
                    getattr(backtest_control, name),
                )


class DefaultPipelineScriptTests(unittest.TestCase):
    def test_default_script_is_native_pipeline(self) -> None:
        # S5a-3: the Slack backtest launcher defaults to the in-repo native
        # pipeline, not the retired open-trading-api sidecar wrapper.
        self.assertEqual(
            backtest_control.DEFAULT_BACKTEST_PIPELINE_SCRIPT.name,
            "native_backtest_pipeline.sh",
        )

    def test_env_pin_cannot_override_native_script(self) -> None:
        # 2026-07-10 regression (docs/todo_20260710.md §D): the deployed launchd
        # plist pinned SLACK_BACKTEST_PIPELINE_SCRIPT to the retired :8002 sidecar
        # wrapper and silently overrode the native default. The rollback hatch is
        # removed — the launcher must ignore the env key entirely.
        env = {
            "SLACK_BACKTEST_PIPELINE_SCRIPT": "scripts/open_trading_api_pipeline.sh",
        }
        captured = {}

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv

            class _P:
                pid = 4321

            return _P()

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env["SLACK_BACKTEST_REPORTS_DIR"] = tmp
            backtest_control.launch_backtest_pipeline(
                backtest_control.BacktestCommandOptions(),
                env=env,
                popen_factory=fake_popen,
            )
        self.assertEqual(
            captured["argv"][0],
            str(backtest_control.DEFAULT_BACKTEST_PIPELINE_SCRIPT),
        )


class BacktestDaysOptionTests(unittest.TestCase):
    def test_days_tokens_parse_and_validate(self) -> None:
        # S3: `days=N` / `--days N` select the replay window length (1..365).
        for raw in ("backtest days=10", "backtest --days 10"):
            with self.subTest(raw=raw):
                options, error = backtest_control._parse_backtest_command_options(raw)
                self.assertEqual(error, "")
                self.assertIsNotNone(options)
                self.assertEqual(options.days, 10)
        for token in ("days=0", "days=abc", "days=400"):
            with self.subTest(token=token):
                options, error = backtest_control._parse_backtest_command_options(
                    f"backtest {token}"
                )
                self.assertIsNone(options)
                self.assertIn("days는 1..365 정수여야 합니다", error)

    def test_launch_appends_days_argv(self) -> None:
        # S3: options.days maps to `--days N` on the pipeline script argv.
        captured: dict = {}

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv

            class _FakeProcess:
                pid = 4321

            return _FakeProcess()

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = backtest_control.launch_backtest_pipeline(
                backtest_control.BacktestCommandOptions(days=10),
                env={backtest_control.SLACK_BACKTEST_REPORTS_DIR_ENV: tmp_dir},
                popen_factory=fake_popen,
            )

        self.assertEqual(result.status, "started")
        argv = captured["argv"]
        self.assertIn("--days", argv)
        self.assertEqual(argv[argv.index("--days") + 1], "10")


class ResolveBacktestMaxRuntimeTests(unittest.TestCase):
    def test_adaptive_max_runtime_cases(self) -> None:
        # S3: max(floor, int(days * per_day * 1.5) + 120) — the env
        # SLACK_BACKTEST_MAX_RUNTIME_SEC is a floor (cap→floor, design
        # §3.2/§3.3 approved); per_day env default 210; assumed days 60.
        cases = (
            (None, {}, 19020),
            (10, {}, 3600),
            (10, {"SLACK_BACKTEST_MAX_RUNTIME_SEC": "7200"}, 7200),
            (10, {"SLACK_BACKTEST_PER_DAY_BUDGET_SEC": "400"}, 6120),
        )
        for days, env, expected in cases:
            with self.subTest(days=days, env=env):
                self.assertEqual(
                    backtest_control.resolve_backtest_max_runtime(
                        backtest_control.BacktestCommandOptions(days=days), env
                    ),
                    expected,
                )


class LaunchBacktestPipelineWithoutAccountTests(unittest.TestCase):
    def test_launch_starts_without_account_env(self) -> None:
        captured: dict = {}

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv

            class _FakeProcess:
                pid = 4321

            return _FakeProcess()

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = backtest_control.launch_backtest_pipeline(
                backtest_control.BacktestCommandOptions(),
                env={backtest_control.SLACK_BACKTEST_REPORTS_DIR_ENV: tmp_dir},
                popen_factory=fake_popen,
            )

        self.assertEqual(result.status, "started")
        self.assertNotIn("--account", captured["argv"])


if __name__ == "__main__":
    unittest.main()
