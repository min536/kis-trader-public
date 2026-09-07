"""Engine sentinel — pure dead-man switch for the live trading engine.

The health watchdog (``health_watchdog.py``) catches a *living* engine whose
cycles fail consecutively, but it is an in-process observer: if the engine
process itself dies, the watchdog dies with it and nothing alerts. The sentinel
closes that blind spot from an independent process (the always-resident Slack
bot) by watching the snapshot worker heartbeat. The worker is owned by the
session wrapper and stops when the engine process exits.

This module is pure and deterministic: all wall-clock time and heartbeat mtime
are injected. The rule structurally avoids market-holiday / never-started false
positives by requiring the heartbeat mtime to be *today* — it only fires for
"ran today, then stopped mid-session". Latch / cooldown / re-arm mirror
``health_watchdog.py`` (process-local; a restart re-arms).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from app.core.time_utils import get_korean_now
from app.notifications.slack import ENGINE_HEALTH_EVENT_TYPE, SlackNotifier

_DEFAULT_WINDOW = "09:00-15:30"
_DEFAULT_REFRESH_INTERVAL_SECONDS = 180.0
_MIN_STALE_THRESHOLD_SECONDS = 360.0
_DEFAULT_STALE_THRESHOLD_SECONDS = _MIN_STALE_THRESHOLD_SECONDS
_DEFAULT_REPEAT_COOLDOWN_SECONDS = 1800.0

ENGINE_SENTINEL_STALE_THRESHOLD_ENV = "ENGINE_SENTINEL_STALE_THRESHOLD_SECONDS"
ENGINE_SENTINEL_REPEAT_COOLDOWN_ENV = "ENGINE_SENTINEL_REPEAT_COOLDOWN_SECONDS"
ENGINE_SENTINEL_WINDOW_ENV = "ENGINE_SENTINEL_WINDOW"
ENGINE_SENTINEL_ENABLED_ENV = "ENGINE_SENTINEL_ENABLED"
_REFRESH_INTERVAL_ENV = "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS"
_SNAPSHOT_LOG_DIR_ENV = "KIS_LIVE_SNAPSHOT_LOG_DIR"
_HEARTBEAT_FILENAME = "kis_trader_live_snapshot.heartbeat"


@dataclass(frozen=True)
class SentinelAlert:
    level: str
    stale_seconds: float
    message: str


@dataclass(frozen=True)
class SentinelConfig:
    stale_threshold_seconds: float = _DEFAULT_STALE_THRESHOLD_SECONDS
    repeat_cooldown_seconds: float = _DEFAULT_REPEAT_COOLDOWN_SECONDS
    window_text: str = _DEFAULT_WINDOW


def _parse_float(value: object, *, default: float, minimum: float) -> float:
    """Health_watchdog-isomorphic guard: bad or below-minimum -> default."""
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    return parsed


def _parse_window(window_text: str) -> tuple[int, int] | None:
    """Local, runtime-decoupled twin of session_loop.parse_hhmm_window."""
    text = str(window_text or "").strip()
    if "-" not in text:
        return None
    start_text, end_text = (part.strip() for part in text.split("-", 1))
    try:
        start_hour, start_minute = (int(part) for part in start_text.split(":", 1))
        end_hour, end_minute = (int(part) for part in end_text.split(":", 1))
    except (TypeError, ValueError):
        return None
    return (start_hour * 60 + start_minute, end_hour * 60 + end_minute)


def _parse_window_text(value: object) -> str:
    if value is None:
        return _DEFAULT_WINDOW
    text = str(value).strip()
    if _parse_window(text) is None:
        return _DEFAULT_WINDOW
    return text


def load_sentinel_config(env: Mapping[str, str]) -> SentinelConfig:
    refresh_interval_seconds = _parse_float(
        env.get(_REFRESH_INTERVAL_ENV),
        default=_DEFAULT_REFRESH_INTERVAL_SECONDS,
        minimum=1.0,
    )
    default_stale_threshold_seconds = max(
        refresh_interval_seconds * 2.0,
        _MIN_STALE_THRESHOLD_SECONDS,
    )
    return SentinelConfig(
        stale_threshold_seconds=_parse_float(
            env.get(ENGINE_SENTINEL_STALE_THRESHOLD_ENV),
            default=default_stale_threshold_seconds,
            minimum=1.0,
        ),
        repeat_cooldown_seconds=_parse_float(
            env.get(ENGINE_SENTINEL_REPEAT_COOLDOWN_ENV),
            default=_DEFAULT_REPEAT_COOLDOWN_SECONDS,
            minimum=1.0,
        ),
        window_text=_parse_window_text(env.get(ENGINE_SENTINEL_WINDOW_ENV)),
    )


class EngineSentinel:
    def __init__(self, *, config: SentinelConfig | None = None) -> None:
        self._config = config or SentinelConfig()
        self._latched_down = False
        self._last_down_alert_at: datetime | None = None

    def evaluate(
        self,
        *,
        snapshot_mtime_epoch: float | None,
        now: datetime,
    ) -> list[SentinelAlert]:
        if snapshot_mtime_epoch is None:
            return []
        mtime_dt = datetime.fromtimestamp(snapshot_mtime_epoch, tz=now.tzinfo)
        stale_seconds = (now - mtime_dt).total_seconds()
        if self._is_down(now=now, mtime_dt=mtime_dt, stale_seconds=stale_seconds):
            return self._handle_down(stale_seconds, now)
        if self._latched_down and stale_seconds <= self._config.stale_threshold_seconds:
            return self._handle_recovered(stale_seconds, now)
        return []

    def _is_down(
        self, *, now: datetime, mtime_dt: datetime, stale_seconds: float
    ) -> bool:
        return (
            self._in_window(now)
            and mtime_dt.date() == now.date()
            and stale_seconds > self._config.stale_threshold_seconds
        )

    def _in_window(self, now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        parsed = _parse_window(self._config.window_text)
        if parsed is None:
            return False
        start_minutes, end_minutes = parsed
        current_minutes = now.hour * 60 + now.minute
        return start_minutes <= current_minutes <= end_minutes

    def _handle_down(self, stale_seconds: float, now: datetime) -> list[SentinelAlert]:
        if self._latched_down and self._last_down_alert_at is not None:
            elapsed = (now - self._last_down_alert_at).total_seconds()
            if elapsed < self._config.repeat_cooldown_seconds:
                return []
        self._latched_down = True
        self._last_down_alert_at = now
        minutes = int(stale_seconds // 60)
        message = (
            f"[다운] 엔진 생존 신호가 약 {minutes}분째 갱신되지 않았습니다"
            " (거래시간 중 엔진 프로세스 중단 의심)."
        )
        return [SentinelAlert(level="DOWN", stale_seconds=stale_seconds, message=message)]

    def _handle_recovered(
        self, stale_seconds: float, now: datetime
    ) -> list[SentinelAlert]:
        self._latched_down = False
        self._last_down_alert_at = None
        message = (
            "[회복] 엔진 생존 신호 갱신이 정상 복귀했습니다"
            f" (최근 신호 {int(stale_seconds)}초 전)."
        )
        return [
            SentinelAlert(
                level="RECOVERED", stale_seconds=stale_seconds, message=message
            )
        ]


def run_engine_sentinel_tick(
    *,
    env: Mapping[str, str],
    sentinel: EngineSentinel,
    notifier: object | None = None,
    now: datetime | None = None,
    mtime_reader=None,
) -> None:
    enabled = str(env.get(ENGINE_SENTINEL_ENABLED_ENV, "1")).strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return
    if now is None:
        now = get_korean_now()
    if notifier is None:
        notifier = SlackNotifier(env=env)
    reader = mtime_reader or os.path.getmtime
    snapshot_log_dir = env.get(_SNAPSHOT_LOG_DIR_ENV) or "logs"
    path = os.path.join(snapshot_log_dir, _HEARTBEAT_FILENAME)
    try:
        mtime_epoch = reader(path)
    except OSError:
        mtime_epoch = None
    alerts = sentinel.evaluate(snapshot_mtime_epoch=mtime_epoch, now=now)
    for alert in alerts:
        try:
            result = notifier.send(
                ENGINE_HEALTH_EVENT_TYPE,
                alert.message,
                details={
                    "level": alert.level,
                    "stale_seconds": round(alert.stale_seconds, 1),
                    "signal": "snapshot_worker_heartbeat",
                },
            )
            print(
                f"[info] engine sentinel alert sent | level={alert.level}"
                f" | result={result}"
            )
        except Exception as exc:
            # RC3: never let a broken alert path kill the bot loop, but leave a
            # console breadcrumb so the failure is itself observable.
            print(
                f"[warn] engine sentinel alert send failed | level={alert.level}"
                f" | error={exc}"
            )
