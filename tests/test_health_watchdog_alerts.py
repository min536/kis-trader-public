from __future__ import annotations

import pytest

from app.notifications import runtime_alerts
from app.notifications.runtime_alerts import (
    record_cycle_health,
    reset_health_watchdog,
)
from app.notifications.slack import (
    ENGINE_HEALTH_EVENT_TYPE,
    OPERATOR_CHANNEL_ENV,
    resolve_channel_env_var,
)

_CONFIG_ENV_KEYS = (
    "HEALTH_WATCHDOG_ENABLED",
    "HEALTH_WATCHDOG_WARN_STREAK",
    "HEALTH_WATCHDOG_CRITICAL_STREAK",
    "HEALTH_WATCHDOG_REPEAT_COOLDOWN_SECONDS",
)


class _FakeNotifier:
    def __init__(self, *, raises: bool = False) -> None:
        self.sent: list[tuple[str, str, dict[str, object]]] = []
        self._raises = raises

    def send(self, event_type, message, *, symbol=None, details=None, allow_smoke_test=False):
        if self._raises:
            raise RuntimeError("slack down")
        self.sent.append((event_type, message, dict(details or {})))
        return "ok"


@pytest.fixture(autouse=True)
def _reset_watchdog(monkeypatch):
    for key in _CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    reset_health_watchdog()
    yield
    reset_health_watchdog()


def test_five_errors_send_single_engine_health_event(monkeypatch) -> None:
    fake = _FakeNotifier()
    monkeypatch.setattr(runtime_alerts, "get_slack_notifier", lambda: fake)

    for _ in range(5):
        record_cycle_health(RuntimeError("OPSQ0008 잔고 조회 실패"))

    engine_events = [e for e in fake.sent if e[0] == ENGINE_HEALTH_EVENT_TYPE]
    assert len(engine_events) == 1
    assert engine_events[0][2]["level"] == "WARN"


def test_notifier_exception_is_not_propagated_and_is_logged(monkeypatch, capsys) -> None:
    fake = _FakeNotifier(raises=True)
    monkeypatch.setattr(runtime_alerts, "get_slack_notifier", lambda: fake)

    # Drive to the WARN threshold; the 5th cycle triggers a send that raises.
    for _ in range(5):
        record_cycle_health(RuntimeError("OPSQ0008 잔고 조회 실패"))

    out = capsys.readouterr().out
    assert "engine health" in out.lower()


def test_kill_switch_disables_sending(monkeypatch) -> None:
    monkeypatch.setenv("HEALTH_WATCHDOG_ENABLED", "0")
    fake = _FakeNotifier()
    monkeypatch.setattr(runtime_alerts, "get_slack_notifier", lambda: fake)

    for _ in range(6):
        record_cycle_health(RuntimeError("OPSQ0008 잔고 조회 실패"))

    assert fake.sent == []


def test_engine_health_resolves_to_operator_channel() -> None:
    assert resolve_channel_env_var(ENGINE_HEALTH_EVENT_TYPE) == OPERATOR_CHANNEL_ENV
