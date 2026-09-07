from __future__ import annotations

import os
import time
from typing import Any, Mapping

from app.market_data.quality_sentinel import (
    QUALITY_ARTIFACT_PATH,
    build_quality_alert_lines,
    evaluate_market_data_quality,
    write_market_data_quality_artifact,
)
from app.notifications.order_events import slack_event_type_for_order_action

# Opt-in: the sentinel always writes the observability artifact, but only emits
# an operator alert when this flag is truthy (default off → no surprise alerts).
MARKET_DATA_QUALITY_ALERTS_ENV = "MARKET_DATA_QUALITY_ALERTS_ENABLED"


def _quality_alerts_enabled(env) -> bool:
    source = os.environ if env is None else env
    return str(source.get(MARKET_DATA_QUALITY_ALERTS_ENV, "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def run_market_data_quality_sentinel(
    snapshots,
    *,
    now_epoch,
    refresh_interval_seconds,
    snapshot_updated_at=None,
    checked_at=None,
    env=None,
    artifact_path=None,
    alert_sender=None,
):
    # Fail-safe: this runs inside the trade cycle — it must never raise, so a
    # sentinel/artifact/alert failure degrades to None instead of disturbing it.
    try:
        report = evaluate_market_data_quality(
            snapshots,
            now_epoch=now_epoch,
            refresh_interval_seconds=refresh_interval_seconds,
            snapshot_updated_at=snapshot_updated_at,
            checked_at=checked_at,
        )
        write_market_data_quality_artifact(report, path=artifact_path or QUALITY_ARTIFACT_PATH)
        if alert_sender is not None and _quality_alerts_enabled(env):
            text = build_market_data_quality_alert_text(report)
            if text:
                alert_sender(text)
        return report
    except Exception:
        return None


def build_market_data_quality_alert_text(report: Any) -> str | None:
    """Slack text for a market-data quality report, or None when it is ok.

    Alert-only: returns text only when the sentinel flagged a problem, so the
    caller posts it (or not) without inspecting the report itself.
    """
    lines = build_quality_alert_lines(report)
    if not lines:
        return None
    return "⚠️ *Market-data quality warning*\n" + "\n".join(lines)


def slack_session_status_text(session_status: object) -> str | None:
    text = str(getattr(session_status, "session", session_status) or "").strip()
    return text or None


def market_session_status_payload(status: Any) -> dict[str, object]:
    return {
        "session": status.session,
        "order_allowed": bool(status.order_allowed),
        "reason": status.reason,
        "buy_block_action": status.buy_block_action,
        "sell_block_action": status.sell_block_action,
    }


def run_cycle_market_data_quality_sentinel(
    snapshots,
    *,
    settings,
    checked_at=None,
    alert_sender=None,
    env=None,
    artifact_path=None,
    now_epoch=None,
):
    """Cycle-facing adapter for the market-data quality sentinel (fail-safe)."""
    snaps = list(snapshots or ())
    updated_at = None
    for snap in snaps:
        try:
            value = (
                snap.get("live_snapshot_updated_at")
                if isinstance(snap, Mapping)
                else getattr(snap, "live_snapshot_updated_at", None)
            )
        except Exception:
            value = None
        if value:
            updated_at = value
    refresh_interval = int(getattr(settings, "live_snapshot_refresh_interval_seconds", 0) or 0)
    return run_market_data_quality_sentinel(
        snaps,
        now_epoch=now_epoch if now_epoch is not None else time.time(),
        refresh_interval_seconds=refresh_interval,
        snapshot_updated_at=updated_at,
        checked_at=checked_at,
        env=env,
        artifact_path=artifact_path,
        alert_sender=alert_sender,
    )
