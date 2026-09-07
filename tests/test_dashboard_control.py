"""Tests for app.notifications.dashboard_control (Slack `dashboard` command).

Mirrors app.notifications.backtest_control's subprocess-lifecycle pattern for
the ops-console dashboard server (workspace/claude-design/kis-trader-v2/server.py).
All subprocess launches go through a fake popen_factory here — no real server
spawn, no port binding, no network calls.
"""

from __future__ import annotations

import json
import sys

from app.notifications import dashboard_control
from app.notifications import slack_bot as slack_bot_module
from app.notifications.slack_bot import SlackBotCommand


class _FakeProcess:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid


def _fake_popen_factory(captured: dict):
    def _factory(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakeProcess()

    return _factory


def test_launch_dashboard_server_defaults(tmp_path):
    captured: dict = {}
    env = {dashboard_control.SLACK_DASHBOARD_REPORTS_DIR_ENV: str(tmp_path)}

    result = dashboard_control.launch_dashboard_server(
        env=env,
        popen_factory=_fake_popen_factory(captured),
        probe_fn=lambda host, port: "empty",
    )

    assert captured["argv"] == [
        sys.executable, str(dashboard_control.DEFAULT_DASHBOARD_SERVER_SCRIPT)
    ]
    assert captured["kwargs"]["env"]["HOST"] == "127.0.0.1"
    assert captured["kwargs"]["env"]["PORT"] == "4173"
    assert (tmp_path / "slack_dashboard.pid").exists()
    assert result.status == "started"
    assert result.url == "http://127.0.0.1:4173"


def test_launch_dashboard_server_already_running(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_control, "_running_pid_from_file", lambda path: 999)

    def _factory(argv, **kwargs):
        raise AssertionError("popen_factory must not be called when already running")

    env = {dashboard_control.SLACK_DASHBOARD_REPORTS_DIR_ENV: str(tmp_path)}
    result = dashboard_control.launch_dashboard_server(env=env, popen_factory=_factory)

    assert result.status == "already_running"
    assert result.pid == 999
    assert result.url == "http://127.0.0.1:4173"


def test_launch_dashboard_server_env_overrides(tmp_path):
    captured: dict = {}
    env = {
        dashboard_control.SLACK_DASHBOARD_REPORTS_DIR_ENV: str(tmp_path),
        dashboard_control.SLACK_DASHBOARD_HOST_ENV: "0.0.0.0",
        dashboard_control.SLACK_DASHBOARD_PORT_ENV: "9000",
    }

    result = dashboard_control.launch_dashboard_server(
        env=env,
        popen_factory=_fake_popen_factory(captured),
        probe_fn=lambda host, port: "empty",
    )

    assert captured["kwargs"]["env"]["HOST"] == "0.0.0.0"
    assert captured["kwargs"]["env"]["PORT"] == "9000"
    assert result.url == "http://0.0.0.0:9000"


def test_stop_dashboard_server(tmp_path, monkeypatch):
    env = {dashboard_control.SLACK_DASHBOARD_REPORTS_DIR_ENV: str(tmp_path)}

    not_running = dashboard_control.stop_dashboard_server(env=env)
    assert not_running.status == "not_running"

    pid_path = tmp_path / "slack_dashboard.pid"
    pid_path.write_text("777\n", encoding="utf-8")
    terminate_calls: list[int] = []
    monkeypatch.setattr(dashboard_control, "_running_pid_from_file", lambda path: 777)
    monkeypatch.setattr(
        dashboard_control,
        "_terminate_backtest_process_group",
        lambda pid: terminate_calls.append(pid) or True,
    )

    stopped = dashboard_control.stop_dashboard_server(env=env)

    assert terminate_calls == [777]
    assert stopped.status == "stopped"
    assert stopped.pid == 777
    assert not pid_path.exists()


def test_render_dashboard_command_reply_start_success(tmp_path, monkeypatch):
    fake_log = tmp_path / "slack_dashboard_x.log"
    monkeypatch.setattr(
        dashboard_control,
        "launch_dashboard_server",
        lambda **kwargs: dashboard_control.DashboardLaunchResult(
            status="started",
            pid=555,
            log_path=fake_log,
            url="http://127.0.0.1:4173",
        ),
    )
    monkeypatch.setattr(dashboard_control, "_running_pid_from_file", lambda path: 555)
    env = {dashboard_control.SLACK_DASHBOARD_REPORTS_DIR_ENV: str(tmp_path)}
    command = SlackBotCommand(name="dashboard", raw_text="dashboard")

    reply = dashboard_control.render_dashboard_command_reply(
        command, env=env, sleep_fn=lambda _seconds: None
    )

    assert "http://127.0.0.1:4173" in reply
    assert "555" in reply


def test_render_dashboard_command_reply_start_liveness_failure(tmp_path, monkeypatch):
    fake_log = tmp_path / "slack_dashboard_x.log"
    fake_log.write_text("Traceback: port already in use\n", encoding="utf-8")
    monkeypatch.setattr(
        dashboard_control,
        "launch_dashboard_server",
        lambda **kwargs: dashboard_control.DashboardLaunchResult(
            status="started",
            pid=556,
            log_path=fake_log,
            url="http://127.0.0.1:4173",
        ),
    )
    monkeypatch.setattr(dashboard_control, "_running_pid_from_file", lambda path: None)
    env = {dashboard_control.SLACK_DASHBOARD_REPORTS_DIR_ENV: str(tmp_path)}
    command = SlackBotCommand(name="dashboard", raw_text="dashboard start")

    reply = dashboard_control.render_dashboard_command_reply(
        command, env=env, sleep_fn=lambda _seconds: None
    )

    assert "port already in use" in reply
    assert "556" in reply


def test_slack_bot_dashboard_command_wiring():
    assert slack_bot_module.parse_command("dashboard").name == "dashboard"
    assert slack_bot_module.parse_command("dash").name == "dashboard"
    assert "`dashboard [port=N]`" in slack_bot_module.HELP_TEXT


def test_dashboard_authorization_via_app_mention():
    command = slack_bot_module.parse_command("dashboard")
    event = {"channel": "C1", "user": "U_OK", "team": "T1"}

    # No user allowlist configured -> mandatory allowlist means False.
    assert slack_bot_module.is_app_mention_authorized(event, command, env={}) is False

    # User allowlist configured and the caller matches -> True.
    assert (
        slack_bot_module.is_app_mention_authorized(
            event, command, env={"SLACK_BOT_ALLOWED_USER_IDS": "U_OK"}
        )
        is True
    )

    # Channel allowlist configured but the caller's channel doesn't match -> False.
    assert (
        slack_bot_module.is_app_mention_authorized(
            event,
            command,
            env={
                "SLACK_BOT_ALLOWED_USER_IDS": "U_OK",
                "SLACK_BOT_ALLOWED_CHANNEL_IDS": "C_EXPECTED",
            },
        )
        is False
    )

    # dashboard starts a host process: the user allowlist is mandatory, not
    # merely one-of-three-optional. A matching channel allowlist alone (no
    # user allowlist configured at all) must still deny.
    assert (
        slack_bot_module.is_app_mention_authorized(
            event,
            command,
            env={"SLACK_BOT_ALLOWED_CHANNEL_IDS": "C1"},
        )
        is False
    )


def test_probe_dashboard_port_classifies_responses(monkeypatch):
    """_probe_dashboard_port must classify without ever touching a real socket."""

    class _FakeResponse:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self._body

    # (a) 200 + matching kis-trader signature -> "kis_dashboard"
    matching_body = json.dumps(
        {"ok": True, "service": dashboard_control.KT_DASHBOARD_SERVICE}
    ).encode("utf-8")
    monkeypatch.setattr(
        dashboard_control.urllib.request,
        "urlopen",
        lambda url, timeout=None: _FakeResponse(matching_body),
    )
    assert dashboard_control._probe_dashboard_port("127.0.0.1", 4173) == "kis_dashboard"

    # (b) HTTPError (something answered, but with an error status) -> "foreign"
    def _raise_http_error(url, timeout=None):
        raise dashboard_control.urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(dashboard_control.urllib.request, "urlopen", _raise_http_error)
    assert dashboard_control._probe_dashboard_port("127.0.0.1", 4173) == "foreign"

    # (c) URLError, non-HTTP (connection refused / timeout) -> "empty"
    def _raise_url_error(url, timeout=None):
        raise dashboard_control.urllib.error.URLError("Connection refused")

    monkeypatch.setattr(dashboard_control.urllib.request, "urlopen", _raise_url_error)
    assert dashboard_control._probe_dashboard_port("127.0.0.1", 4173) == "empty"

    # (d) 200 but signature mismatch -> "foreign"
    mismatched_body = json.dumps({"ok": True, "service": "some-other-service"}).encode(
        "utf-8"
    )
    monkeypatch.setattr(
        dashboard_control.urllib.request,
        "urlopen",
        lambda url, timeout=None: _FakeResponse(mismatched_body),
    )
    assert dashboard_control._probe_dashboard_port("127.0.0.1", 4173) == "foreign"


def test_launch_dashboard_server_probe_kis_dashboard_already_serving(tmp_path):
    def _must_not_call(argv, **kwargs):
        raise AssertionError("popen_factory must not be called when already serving")

    env = {dashboard_control.SLACK_DASHBOARD_REPORTS_DIR_ENV: str(tmp_path)}
    result = dashboard_control.launch_dashboard_server(
        env=env,
        popen_factory=_must_not_call,
        probe_fn=lambda host, port: "kis_dashboard",
    )

    assert result.status == "already_serving_external"
    assert result.url == "http://127.0.0.1:4173"


def test_launch_dashboard_server_probe_foreign_port_occupied(tmp_path):
    def _must_not_call(argv, **kwargs):
        raise AssertionError("popen_factory must not be called when port occupied")

    env = {dashboard_control.SLACK_DASHBOARD_REPORTS_DIR_ENV: str(tmp_path)}
    result = dashboard_control.launch_dashboard_server(
        env=env,
        popen_factory=_must_not_call,
        probe_fn=lambda host, port: "foreign",
    )

    assert result.status == "port_occupied_foreign"
    assert "4173" in result.message


def test_launch_dashboard_server_probe_empty_spawns(tmp_path):
    captured: dict = {}
    env = {dashboard_control.SLACK_DASHBOARD_REPORTS_DIR_ENV: str(tmp_path)}

    result = dashboard_control.launch_dashboard_server(
        env=env,
        popen_factory=_fake_popen_factory(captured),
        probe_fn=lambda host, port: "empty",
    )

    assert "argv" in captured
    assert result.status == "started"


def test_parse_dashboard_command():
    assert dashboard_control._parse_dashboard_command("dashboard port=4180") == (
        "start",
        4180,
        None,
    )
    assert dashboard_control._parse_dashboard_command("dashboard status") == (
        "status",
        None,
        None,
    )
    assert dashboard_control._parse_dashboard_command("dashboard") == (
        "start",
        None,
        None,
    )

    # Out-of-range port -> non-empty error, no crash.
    action, port, error = dashboard_control._parse_dashboard_command("dashboard port=80")
    assert port is None
    assert error

    # Non-integer port -> non-empty error.
    action, port, error = dashboard_control._parse_dashboard_command("dashboard port=abc")
    assert port is None
    assert error


def test_render_dashboard_command_reply_port_override(tmp_path, monkeypatch):
    captured: dict = {}

    def _fake_launch(**kwargs):
        captured["env"] = kwargs.get("env")
        return dashboard_control.DashboardLaunchResult(
            status="started",
            pid=558,
            log_path=tmp_path / "slack_dashboard_x.log",
            url="http://127.0.0.1:4180",
        )

    monkeypatch.setattr(dashboard_control, "launch_dashboard_server", _fake_launch)
    monkeypatch.setattr(dashboard_control, "_running_pid_from_file", lambda path: 558)
    env = {dashboard_control.SLACK_DASHBOARD_REPORTS_DIR_ENV: str(tmp_path)}
    command = SlackBotCommand(name="dashboard", raw_text="dashboard port=4180")

    reply = dashboard_control.render_dashboard_command_reply(
        command, env=env, sleep_fn=lambda _seconds: None
    )

    assert captured["env"][dashboard_control.SLACK_DASHBOARD_PORT_ENV] == "4180"
    assert "558" in reply


def test_render_dashboard_command_reply_already_serving_external(tmp_path, monkeypatch):
    monkeypatch.setattr(
        dashboard_control,
        "launch_dashboard_server",
        lambda **kwargs: dashboard_control.DashboardLaunchResult(
            status="already_serving_external",
            url="http://127.0.0.1:4173",
            message="dashboard already serving on this port (started outside Slack)",
        ),
    )
    env = {dashboard_control.SLACK_DASHBOARD_REPORTS_DIR_ENV: str(tmp_path)}
    command = SlackBotCommand(name="dashboard", raw_text="dashboard")

    reply = dashboard_control.render_dashboard_command_reply(
        command, env=env, sleep_fn=lambda _seconds: None
    )

    assert "stop`으로는 중지되지 않" in reply
    assert "http://127.0.0.1:4173" in reply


def test_render_dashboard_command_reply_port_occupied_foreign(tmp_path, monkeypatch):
    monkeypatch.setattr(
        dashboard_control,
        "launch_dashboard_server",
        lambda **kwargs: dashboard_control.DashboardLaunchResult(
            status="port_occupied_foreign",
            url="http://127.0.0.1:4173",
            message="port 4173 occupied by another process",
        ),
    )
    env = {dashboard_control.SLACK_DASHBOARD_REPORTS_DIR_ENV: str(tmp_path)}
    command = SlackBotCommand(name="dashboard", raw_text="dashboard")

    reply = dashboard_control.render_dashboard_command_reply(
        command, env=env, sleep_fn=lambda _seconds: None
    )

    assert "port=" in reply
    assert "port 4173 occupied by another process" in reply
