from __future__ import annotations

import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Mapping


KIS_RATE_LIMIT_BACKOFF = "KIS_RATE_LIMIT_BACKOFF"
LIVE_SNAPSHOT_STALE = "LIVE_SNAPSHOT_STALE"
SNAPSHOT_WORKER_RESTART = "SNAPSHOT_WORKER_RESTART"
ORDER_RECONCILIATION_DRIFT = "ORDER_RECONCILIATION_DRIFT"
MAIN_LOOP_EXCEPTION = "MAIN_LOOP_EXCEPTION"

BOTTLENECK_TYPES: tuple[str, ...] = (
    KIS_RATE_LIMIT_BACKOFF,
    LIVE_SNAPSHOT_STALE,
    SNAPSHOT_WORKER_RESTART,
    ORDER_RECONCILIATION_DRIFT,
    MAIN_LOOP_EXCEPTION,
)

WINDOW_SECONDS_ENV = "SLACK_BOTTLENECK_WINDOW_SECONDS"
COOLDOWN_SECONDS_ENV = "SLACK_BOTTLENECK_COOLDOWN_SECONDS"

DEFAULT_WINDOW_SECONDS = 600.0
DEFAULT_COOLDOWN_SECONDS = 1800.0

THRESHOLD_ENV_BY_TYPE: dict[str, str] = {
    KIS_RATE_LIMIT_BACKOFF: "SLACK_RATE_LIMIT_THRESHOLD",
    LIVE_SNAPSHOT_STALE: "SLACK_SNAPSHOT_STALE_THRESHOLD",
    SNAPSHOT_WORKER_RESTART: "SLACK_DRIFT_THRESHOLD",
    ORDER_RECONCILIATION_DRIFT: "SLACK_DRIFT_THRESHOLD",
    MAIN_LOOP_EXCEPTION: "SLACK_MAIN_LOOP_EXCEPTION_THRESHOLD",
}

DEFAULT_THRESHOLD_BY_TYPE: dict[str, int] = {
    KIS_RATE_LIMIT_BACKOFF: 5,
    LIVE_SNAPSHOT_STALE: 3,
    SNAPSHOT_WORKER_RESTART: 2,
    ORDER_RECONCILIATION_DRIFT: 2,
    MAIN_LOOP_EXCEPTION: 1,
}


@dataclass(frozen=True)
class AlertDecision:
    should_alert: bool
    bottleneck_type: str
    count_in_window: int
    threshold: int
    reason: str | None = None


Clock = Callable[[], float]


def _parse_float(value: object, *, default: float, minimum: float) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    return parsed


def _parse_int(value: object, *, default: int, minimum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    return parsed


class BottleneckAggregator:
    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        clock: Clock | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._env = env if env is not None else os.environ
        self._clock = clock or time.monotonic
        self._logger = logger or logging.getLogger(__name__)
        self._timestamps: dict[str, deque[float]] = {
            t: deque() for t in BOTTLENECK_TYPES
        }
        self._last_alert_at: dict[str, float] = {}

    @property
    def window_seconds(self) -> float:
        return _parse_float(
            self._env.get(WINDOW_SECONDS_ENV),
            default=DEFAULT_WINDOW_SECONDS,
            minimum=1.0,
        )

    @property
    def cooldown_seconds(self) -> float:
        return _parse_float(
            self._env.get(COOLDOWN_SECONDS_ENV),
            default=DEFAULT_COOLDOWN_SECONDS,
            minimum=0.0,
        )

    def threshold(self, bottleneck_type: str) -> int:
        env_var = THRESHOLD_ENV_BY_TYPE.get(bottleneck_type)
        default = DEFAULT_THRESHOLD_BY_TYPE.get(bottleneck_type, 1)
        if env_var is None:
            return default
        return _parse_int(self._env.get(env_var), default=default, minimum=1)

    def record(self, bottleneck_type: str) -> AlertDecision:
        if bottleneck_type not in self._timestamps:
            return AlertDecision(
                should_alert=False,
                bottleneck_type=bottleneck_type,
                count_in_window=0,
                threshold=0,
                reason="unknown_bottleneck_type",
            )

        now = self._clock()
        window = self.window_seconds
        timestamps = self._timestamps[bottleneck_type]
        timestamps.append(now)
        cutoff = now - window
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

        threshold = self.threshold(bottleneck_type)
        count = len(timestamps)
        if count < threshold:
            return AlertDecision(
                should_alert=False,
                bottleneck_type=bottleneck_type,
                count_in_window=count,
                threshold=threshold,
                reason="below_threshold",
            )

        cooldown = self.cooldown_seconds
        last_alert_at = self._last_alert_at.get(bottleneck_type)
        if last_alert_at is not None and (now - last_alert_at) < cooldown:
            return AlertDecision(
                should_alert=False,
                bottleneck_type=bottleneck_type,
                count_in_window=count,
                threshold=threshold,
                reason="cooldown",
            )

        self._last_alert_at[bottleneck_type] = now
        return AlertDecision(
            should_alert=True,
            bottleneck_type=bottleneck_type,
            count_in_window=count,
            threshold=threshold,
        )
