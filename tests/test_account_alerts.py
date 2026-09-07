from __future__ import annotations

import pytest

from app.notifications.account_alerts import (
    maybe_emit_fatal_account_alert,
    reset_fatal_account_latch,
)


@pytest.fixture(autouse=True)
def _reset_latch():
    reset_fatal_account_latch()
    yield
    reset_fatal_account_latch()


def test_first_fatal_account_alert_emits_account_blocked_event() -> None:
    events: list[tuple[str, str, dict[str, object]]] = []

    def notify(event_type, message, *, symbol=None, details=None):
        events.append((event_type, message, dict(details or {})))

    fired = maybe_emit_fatal_account_alert(
        reason="모의투자 주문이 불가한 계좌입니다. (40910000)",
        side="SELL",
        symbol="082740",
        account_signature="mock_acct_0123456789abcdef",
        notify=notify,
    )

    assert fired is True
    assert len(events) == 1
    event_type, message, _details = events[0]
    assert event_type == "account.blocked"
    assert "모의투자" in message or "mock" in message.lower()


def test_latch_fires_once_then_suppresses_second_alert_same_account() -> None:
    events: list[str] = []

    def notify(event_type, message, *, symbol=None, details=None):
        events.append(event_type)

    first = maybe_emit_fatal_account_alert(
        reason="x (40910000)",
        side="SELL",
        symbol="082740",
        account_signature="acct-A",
        notify=notify,
    )
    second = maybe_emit_fatal_account_alert(
        reason="x (40910000)",
        side="BUY",
        symbol="005930",
        account_signature="acct-A",
        notify=notify,
    )

    assert first is True
    assert second is False
    assert events == ["account.blocked"]


def test_latch_is_per_account_signature() -> None:
    events: list[str] = []

    def notify(event_type, message, *, symbol=None, details=None):
        events.append(str(details["account_signature"]))

    maybe_emit_fatal_account_alert(
        reason="x (40910000)", side="SELL", symbol="a",
        account_signature="acct-A", notify=notify,
    )
    maybe_emit_fatal_account_alert(
        reason="x (40910000)", side="SELL", symbol="b",
        account_signature="acct-B", notify=notify,
    )

    assert events == ["acct-A", "acct-B"]


def test_latch_rearms_after_reset() -> None:
    events: list[str] = []

    def notify(event_type, message, *, symbol=None, details=None):
        events.append(event_type)

    maybe_emit_fatal_account_alert(
        reason="x (40910000)", side="SELL", symbol="a",
        account_signature="acct-A", notify=notify,
    )
    reset_fatal_account_latch()
    fired_again = maybe_emit_fatal_account_alert(
        reason="x (40910000)", side="SELL", symbol="a",
        account_signature="acct-A", notify=notify,
    )

    assert fired_again is True
    assert events == ["account.blocked", "account.blocked"]


def test_emitter_swallows_notifier_errors_and_does_not_latch() -> None:
    def failing_notify(event_type, message, *, symbol=None, details=None):
        raise RuntimeError("slack down")

    fired = maybe_emit_fatal_account_alert(
        reason="x (40910000)", side="SELL", symbol="a",
        account_signature="acct-A", notify=failing_notify,
    )
    assert fired is False

    # Latch was NOT set (emit failed), so a later working notify still fires.
    events: list[str] = []

    def good_notify(event_type, message, *, symbol=None, details=None):
        events.append(event_type)

    retried = maybe_emit_fatal_account_alert(
        reason="x (40910000)", side="SELL", symbol="a",
        account_signature="acct-A", notify=good_notify,
    )
    assert retried is True
    assert events == ["account.blocked"]
