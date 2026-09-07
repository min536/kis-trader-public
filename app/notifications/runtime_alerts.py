"""Runtime alert hooks shared by the main loop.

Lazy singletons for the Slack notifier / bottleneck aggregator plus the
record-bottleneck alert path called from the session loop and API budget
hooks. Bodies moved verbatim from app/main.py (R1 core slimming).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from app.core.error_classification import looks_like_transient_api_error
from app.notifications.bottleneck import BottleneckAggregator, MAIN_LOOP_EXCEPTION
from app.notifications.health_watchdog import (
    HealthWatchdog,
    load_health_watchdog_config,
)
from app.notifications.slack import ENGINE_HEALTH_EVENT_TYPE, SlackNotifier

_SLACK_NOTIFIER: SlackNotifier | None = None


def get_slack_notifier() -> SlackNotifier:
    global _SLACK_NOTIFIER
    if _SLACK_NOTIFIER is None:
        _SLACK_NOTIFIER = SlackNotifier()
    return _SLACK_NOTIFIER


_BOTTLENECK_AGGREGATOR: BottleneckAggregator | None = None


def get_bottleneck_aggregator() -> BottleneckAggregator:
    global _BOTTLENECK_AGGREGATOR
    if _BOTTLENECK_AGGREGATOR is None:
        _BOTTLENECK_AGGREGATOR = BottleneckAggregator()
    return _BOTTLENECK_AGGREGATOR


def record_bottleneck(kind: str, *, context: dict[str, object] | None = None) -> None:
    try:
        decision = get_bottleneck_aggregator().record(kind)
        if not decision.should_alert:
            return
        details: dict[str, object] = {
            "type": decision.bottleneck_type,
            "count": decision.count_in_window,
            "threshold": decision.threshold,
        }
        if context:
            for key, val in context.items():
                text = str(val).strip()
                if text:
                    details[key] = text[:160]
        result = get_slack_notifier().send(
            "repeated_bottleneck",
            f"{decision.bottleneck_type} repeated "
            f"{decision.count_in_window}x in window",
            details=details,
        )
        print(
            f"[info] bottleneck alert sent | type={decision.bottleneck_type}"
            f" | result={result}"
        )
    except Exception as exc:
        # RC3: the send failure was previously swallowed with a bare return.
        # Keep swallowing (never break the caller), but leave a console
        # breadcrumb so a broken alert path is observable. The should_alert-False
        # early return above stays silent (no per-cycle noise).
        print(f"[warn] bottleneck alert send failed | error={exc}")
        return


def record_main_loop_exception_if_needed(exc: Exception) -> None:
    if looks_like_transient_api_error(exc):
        return
    record_bottleneck(MAIN_LOOP_EXCEPTION, context={"error": str(exc)[:160]})


_HEALTH_WATCHDOG: HealthWatchdog | None = None


def get_health_watchdog() -> HealthWatchdog:
    global _HEALTH_WATCHDOG
    if _HEALTH_WATCHDOG is None:
        _HEALTH_WATCHDOG = HealthWatchdog(
            config=load_health_watchdog_config(os.environ)
        )
    return _HEALTH_WATCHDOG


def reset_health_watchdog() -> None:
    """Clear the process-local watchdog (test isolation / restart re-arm)."""

    global _HEALTH_WATCHDOG
    _HEALTH_WATCHDOG = None


def _health_watchdog_enabled(env: dict[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    raw = str(source.get("HEALTH_WATCHDOG_ENABLED", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def record_cycle_health(error: Exception | None) -> None:
    error_text = None if error is None else str(error)
    # Always tally the cycle so the streak state stays coherent; the kill-switch
    # only gates the outbound Slack send.
    alerts = get_health_watchdog().record_cycle_outcome(
        error_text=error_text,
        now=datetime.now(timezone.utc),
    )
    if not _health_watchdog_enabled():
        return
    notifier = get_slack_notifier()
    for alert in alerts:
        try:
            result = notifier.send(
                ENGINE_HEALTH_EVENT_TYPE,
                alert.message,
                details={
                    "level": alert.level,
                    "consecutive_errors": alert.consecutive_errors,
                    "duration_seconds": round(alert.duration_seconds, 1),
                    "sample_error": alert.sample_error or "",
                },
            )
            print(
                f"[info] engine health alert sent | level={alert.level}"
                f" | result={result}"
            )
        except Exception as exc:
            # RC3 lesson: do NOT swallow the send failure silently — leave a
            # console breadcrumb so a broken alert path is itself observable.
            print(
                f"[warn] engine health alert send failed | level={alert.level}"
                f" | error={exc}"
            )
