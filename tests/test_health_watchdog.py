from __future__ import annotations

from datetime import datetime, timedelta

from app.notifications.health_watchdog import (
    HealthWatchdog,
    HealthWatchdogConfig,
    load_health_watchdog_config,
)


_BASE = datetime(2026, 7, 17, 10, 0, 0)


def test_four_consecutive_errors_emit_no_alert() -> None:
    watchdog = HealthWatchdog()
    alerts = []
    for i in range(4):
        alerts.extend(
            watchdog.record_cycle_outcome(
                error_text="OPSQ0008 잔고 조회 실패",
                now=_BASE + timedelta(seconds=i),
            )
        )
    assert alerts == []


def test_fifth_consecutive_error_emits_single_warn() -> None:
    watchdog = HealthWatchdog()
    fired = []
    for i in range(5):
        fired.extend(
            watchdog.record_cycle_outcome(
                error_text="OPSQ0008 잔고 조회 실패",
                now=_BASE + timedelta(seconds=i),
            )
        )
    assert len(fired) == 1
    assert fired[0].level == "WARN"
    assert fired[0].consecutive_errors == 5

    # Sixth consecutive error inside the cooldown window: no additional alert.
    more = watchdog.record_cycle_outcome(
        error_text="OPSQ0008 잔고 조회 실패",
        now=_BASE + timedelta(seconds=5),
    )
    assert more == []


def test_twentieth_consecutive_error_escalates_to_single_critical() -> None:
    watchdog = HealthWatchdog()
    fired = []
    for i in range(20):
        fired.extend(
            watchdog.record_cycle_outcome(
                error_text="OPSQ0008 잔고 조회 실패",
                now=_BASE + timedelta(seconds=i),
            )
        )
    levels = [alert.level for alert in fired]
    assert levels.count("WARN") == 1
    assert levels.count("CRITICAL") == 1
    critical = next(alert for alert in fired if alert.level == "CRITICAL")
    assert critical.consecutive_errors == 20


def test_critical_latch_re_alerts_only_after_cooldown() -> None:
    watchdog = HealthWatchdog()
    for i in range(20):
        watchdog.record_cycle_outcome(
            error_text="OPSQ0008 잔고 조회 실패",
            now=_BASE + timedelta(seconds=i),
        )
    critical_at = _BASE + timedelta(seconds=19)

    within_cooldown = watchdog.record_cycle_outcome(
        error_text="OPSQ0008 잔고 조회 실패",
        now=critical_at + timedelta(seconds=100),
    )
    assert within_cooldown == []

    after_cooldown = watchdog.record_cycle_outcome(
        error_text="OPSQ0008 잔고 조회 실패",
        now=critical_at + timedelta(seconds=1801),
    )
    assert len(after_cooldown) == 1
    assert after_cooldown[0].level == "CRITICAL"


def test_success_after_warn_emits_recovered_then_re_arms() -> None:
    watchdog = HealthWatchdog()
    warn_alerts = []
    for i in range(5):
        warn_alerts.extend(
            watchdog.record_cycle_outcome(
                error_text="OPSQ0008 잔고 조회 실패",
                now=_BASE + timedelta(seconds=i),
            )
        )
    assert [a.level for a in warn_alerts] == ["WARN"]

    recovered = watchdog.record_cycle_outcome(
        error_text=None,
        now=_BASE + timedelta(seconds=5),
    )
    assert len(recovered) == 1
    assert recovered[0].level == "RECOVERED"

    # Re-arm: a fresh 5-error streak fires WARN again.
    re_armed = []
    for i in range(5):
        re_armed.extend(
            watchdog.record_cycle_outcome(
                error_text="OPSQ0008 잔고 조회 실패",
                now=_BASE + timedelta(seconds=10 + i),
            )
        )
    assert [a.level for a in re_armed] == ["WARN"]


def test_recovery_below_warn_threshold_is_silent() -> None:
    watchdog = HealthWatchdog()
    alerts = []
    for i in range(3):
        alerts.extend(
            watchdog.record_cycle_outcome(
                error_text="OPSQ0008 잔고 조회 실패",
                now=_BASE + timedelta(seconds=i),
            )
        )
    alerts.extend(
        watchdog.record_cycle_outcome(
            error_text=None,
            now=_BASE + timedelta(seconds=3),
        )
    )
    assert alerts == []


def test_load_config_defaults_invalid_and_min_guard() -> None:
    # Missing env → defaults.
    defaults = load_health_watchdog_config({})
    assert defaults == HealthWatchdogConfig(
        warn_streak=5,
        critical_streak=20,
        repeat_cooldown_seconds=1800.0,
    )

    # Valid overrides are parsed.
    parsed = load_health_watchdog_config(
        {
            "HEALTH_WATCHDOG_WARN_STREAK": "7",
            "HEALTH_WATCHDOG_CRITICAL_STREAK": "30",
            "HEALTH_WATCHDOG_REPEAT_COOLDOWN_SECONDS": "900",
        }
    )
    assert parsed.warn_streak == 7
    assert parsed.critical_streak == 30
    assert parsed.repeat_cooldown_seconds == 900.0

    # Non-numeric strings fall back to defaults.
    invalid = load_health_watchdog_config(
        {
            "HEALTH_WATCHDOG_WARN_STREAK": "abc",
            "HEALTH_WATCHDOG_REPEAT_COOLDOWN_SECONDS": "n/a",
        }
    )
    assert invalid.warn_streak == 5
    assert invalid.repeat_cooldown_seconds == 1800.0

    # Sub-minimum values are rejected and fall back to defaults (min guard).
    clamped = load_health_watchdog_config(
        {
            "HEALTH_WATCHDOG_WARN_STREAK": "0",
            "HEALTH_WATCHDOG_CRITICAL_STREAK": "-3",
        }
    )
    assert clamped.warn_streak == 5
    assert clamped.critical_streak == 20
