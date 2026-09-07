"""Runtime health watchdog — pure state machine (no I/O, clock injected).

Detects "silent failure": the engine process stays alive but cycles fail
consecutively with no escalation. Feeds each cycle outcome (error text or
success) and returns zero or more ``HealthAlert`` values for the caller to
deliver. All wall-clock time is injected via the ``now`` argument so the
machine is fully deterministic and unit-testable.

The latch is process-local by design (like ``account_alerts``): a restart
re-arms the watchdog. There is no persistence. Classification is deliberately
ignored — every cycle exception counts toward the streak, because a transient
error that never stops IS an outage (the 2026-07-17 lesson).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

_SAMPLE_ERROR_MAX_LEN = 160

_DEFAULT_WARN_STREAK = 5
_DEFAULT_CRITICAL_STREAK = 20
_DEFAULT_REPEAT_COOLDOWN_SECONDS = 1800.0

_WARN_STREAK_ENV = "HEALTH_WATCHDOG_WARN_STREAK"
_CRITICAL_STREAK_ENV = "HEALTH_WATCHDOG_CRITICAL_STREAK"
_REPEAT_COOLDOWN_SECONDS_ENV = "HEALTH_WATCHDOG_REPEAT_COOLDOWN_SECONDS"


@dataclass(frozen=True)
class HealthAlert:
    level: str
    consecutive_errors: int
    duration_seconds: float
    sample_error: str | None
    message: str


@dataclass(frozen=True)
class HealthWatchdogConfig:
    warn_streak: int = _DEFAULT_WARN_STREAK
    critical_streak: int = _DEFAULT_CRITICAL_STREAK
    repeat_cooldown_seconds: float = _DEFAULT_REPEAT_COOLDOWN_SECONDS


def _parse_int(value: object, *, default: int, minimum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    return parsed


def _parse_float(value: object, *, default: float, minimum: float) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    return parsed


def load_health_watchdog_config(env: Mapping[str, str]) -> HealthWatchdogConfig:
    return HealthWatchdogConfig(
        warn_streak=_parse_int(
            env.get(_WARN_STREAK_ENV),
            default=_DEFAULT_WARN_STREAK,
            minimum=1,
        ),
        critical_streak=_parse_int(
            env.get(_CRITICAL_STREAK_ENV),
            default=_DEFAULT_CRITICAL_STREAK,
            minimum=1,
        ),
        repeat_cooldown_seconds=_parse_float(
            env.get(_REPEAT_COOLDOWN_SECONDS_ENV),
            default=_DEFAULT_REPEAT_COOLDOWN_SECONDS,
            minimum=1.0,
        ),
    )


class HealthWatchdog:
    def __init__(self, *, config: HealthWatchdogConfig | None = None) -> None:
        self._config = config or HealthWatchdogConfig()
        self._consecutive_error_cycles = 0
        self._streak_started_at: datetime | None = None
        self._last_alert_level: str | None = None
        self._last_alert_at: datetime | None = None
        self._last_sample_error: str | None = None

    def record_cycle_outcome(
        self,
        *,
        error_text: str | None,
        now: datetime,
    ) -> list[HealthAlert]:
        if error_text is None:
            return self._handle_success(now)
        if self._consecutive_error_cycles == 0:
            self._streak_started_at = now
        self._consecutive_error_cycles += 1
        self._last_sample_error = error_text[:_SAMPLE_ERROR_MAX_LEN]
        target_level = self._target_level()
        if target_level is None:
            return []
        if self._should_fire(target_level, now):
            return [self._build_alert(target_level, now)]
        return []

    def _handle_success(self, now: datetime) -> list[HealthAlert]:
        alerts: list[HealthAlert] = []
        if self._last_alert_level is not None:
            duration_seconds = self._streak_duration_seconds(now)
            alerts.append(
                HealthAlert(
                    level="RECOVERED",
                    consecutive_errors=self._consecutive_error_cycles,
                    duration_seconds=duration_seconds,
                    sample_error=self._last_sample_error,
                    message=_format_alert_message(
                        level="RECOVERED",
                        consecutive_errors=self._consecutive_error_cycles,
                        duration_seconds=duration_seconds,
                        sample_error=self._last_sample_error,
                    ),
                )
            )
        self._reset()
        return alerts

    def _reset(self) -> None:
        self._consecutive_error_cycles = 0
        self._streak_started_at = None
        self._last_alert_level = None
        self._last_alert_at = None
        self._last_sample_error = None

    def _target_level(self) -> str | None:
        if self._consecutive_error_cycles >= self._config.critical_streak:
            return "CRITICAL"
        if self._consecutive_error_cycles >= self._config.warn_streak:
            return "WARN"
        return None

    def _should_fire(self, target_level: str, now: datetime) -> bool:
        if target_level != self._last_alert_level:
            return True
        if self._last_alert_at is None:
            return True
        elapsed = (now - self._last_alert_at).total_seconds()
        return elapsed >= self._config.repeat_cooldown_seconds

    def _build_alert(self, level: str, now: datetime) -> HealthAlert:
        duration_seconds = self._streak_duration_seconds(now)
        alert = HealthAlert(
            level=level,
            consecutive_errors=self._consecutive_error_cycles,
            duration_seconds=duration_seconds,
            sample_error=self._last_sample_error,
            message=_format_alert_message(
                level=level,
                consecutive_errors=self._consecutive_error_cycles,
                duration_seconds=duration_seconds,
                sample_error=self._last_sample_error,
            ),
        )
        self._last_alert_level = level
        self._last_alert_at = now
        return alert

    def _streak_duration_seconds(self, now: datetime) -> float:
        if self._streak_started_at is None:
            return 0.0
        return (now - self._streak_started_at).total_seconds()


def _format_alert_message(
    *,
    level: str,
    consecutive_errors: int,
    duration_seconds: float,
    sample_error: str | None,
) -> str:
    minutes = int(duration_seconds // 60)
    if level == "RECOVERED":
        return (
            f"[회복] 엔진 사이클이 정상 복귀했습니다"
            f" (직전 연속 실패 {consecutive_errors}회, 약 {minutes}분)."
        )
    sample = sample_error or "(없음)"
    prefix = "[심각]" if level == "CRITICAL" else "[경고]"
    return (
        f"{prefix} 엔진 사이클이 연속 {consecutive_errors}회 실패했습니다"
        f" (약 {minutes}분 지속). 최근 오류: {sample}"
    )
