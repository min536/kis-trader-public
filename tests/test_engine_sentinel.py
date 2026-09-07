"""S1 — EngineSentinel pure evaluator (time/mtime injected, no I/O).

Proves the worker-heartbeat dead-man switch fires ONLY for "ran today then
stopped mid-session" and structurally avoids holiday / never-started false
positives. Latch + cooldown + re-arm mirror health_watchdog.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.notifications.engine_sentinel import (
    EngineSentinel,
    SentinelConfig,
    load_sentinel_config,
)

KST = ZoneInfo("Asia/Seoul")


def _sentinel(**overrides: object) -> EngineSentinel:
    config = SentinelConfig(**overrides)  # type: ignore[arg-type]
    return EngineSentinel(config=config)


def test_outside_window_never_alerts() -> None:
    # Saturday noon — weekend is outside every trading window; a two-hour-stale
    # heartbeat must still yield nothing.
    sentinel = _sentinel()
    now = datetime(2026, 7, 18, 12, 0, tzinfo=KST)  # Saturday
    stale_mtime = (now - timedelta(hours=2)).timestamp()
    assert sentinel.evaluate(snapshot_mtime_epoch=stale_mtime, now=now) == []


def test_after_scheduled_session_stop_never_alerts() -> None:
    # The regular session ends at 15:30. A heartbeat that naturally stops with
    # the session must not become a false DOWN during a later grace window.
    sentinel = _sentinel()
    now = datetime(2026, 7, 17, 15, 36, 1, tzinfo=KST)
    final_heartbeat = datetime(2026, 7, 17, 15, 30, 0, tzinfo=KST).timestamp()
    assert sentinel.evaluate(snapshot_mtime_epoch=final_heartbeat, now=now) == []


def test_in_window_today_stale_fires_down_once_then_latches() -> None:
    # Friday 10:00 KST is inside 09:00-15:30; heartbeat last touched 7 min ago
    # (> 360s) while still "today" -> ran today then stalled.
    sentinel = _sentinel()
    now = datetime(2026, 7, 17, 10, 0, tzinfo=KST)
    stale_mtime = (now - timedelta(seconds=420)).timestamp()

    first = sentinel.evaluate(snapshot_mtime_epoch=stale_mtime, now=now)
    assert [a.level for a in first] == ["DOWN"]
    assert first[0].stale_seconds >= 300

    # Latched: an immediate re-evaluation at the same instant stays silent.
    second = sentinel.evaluate(snapshot_mtime_epoch=stale_mtime, now=now)
    assert second == []


def test_mtime_from_yesterday_never_alerts() -> None:
    # Weekday, in-window, but the last snapshot write was *yesterday* — the
    # engine never ran today (market holiday / not started). The mtime-is-today
    # gate must suppress this: DOWN means "ran today then stopped", not "absent".
    sentinel = _sentinel()
    now = datetime(2026, 7, 17, 10, 0, tzinfo=KST)  # Friday, in window
    yesterday_mtime = (now - timedelta(days=1)).timestamp()
    assert sentinel.evaluate(snapshot_mtime_epoch=yesterday_mtime, now=now) == []


def test_down_re_alerts_after_repeat_cooldown() -> None:
    sentinel = _sentinel()  # default 1800s repeat cooldown
    now = datetime(2026, 7, 17, 10, 0, tzinfo=KST)
    stale_mtime = (now - timedelta(seconds=420)).timestamp()

    first = sentinel.evaluate(snapshot_mtime_epoch=stale_mtime, now=now)
    assert [a.level for a in first] == ["DOWN"]

    # Still stale but inside the cooldown window -> suppressed.
    within = sentinel.evaluate(
        snapshot_mtime_epoch=stale_mtime, now=now + timedelta(seconds=1799)
    )
    assert within == []

    # Cooldown elapsed while still down -> a fresh reminder fires.
    after = sentinel.evaluate(
        snapshot_mtime_epoch=stale_mtime, now=now + timedelta(seconds=1801)
    )
    assert [a.level for a in after] == ["DOWN"]


def test_recovered_then_re_arms_for_next_outage() -> None:
    sentinel = _sentinel()
    now = datetime(2026, 7, 17, 10, 0, tzinfo=KST)
    stale_mtime = (now - timedelta(seconds=420)).timestamp()

    down = sentinel.evaluate(snapshot_mtime_epoch=stale_mtime, now=now)
    assert [a.level for a in down] == ["DOWN"]

    # Engine resumed writing -> snapshot is fresh again: one RECOVERED, latch cleared.
    now2 = now + timedelta(seconds=60)
    fresh_mtime = (now2 - timedelta(seconds=10)).timestamp()
    recovered = sentinel.evaluate(snapshot_mtime_epoch=fresh_mtime, now=now2)
    assert [a.level for a in recovered] == ["RECOVERED"]

    # Re-armed: a later stall fires DOWN afresh (the old latch does not swallow it).
    now3 = now2 + timedelta(seconds=60)
    stale_again = (now3 - timedelta(seconds=420)).timestamp()
    down_again = sentinel.evaluate(snapshot_mtime_epoch=stale_again, now=now3)
    assert [a.level for a in down_again] == ["DOWN"]


def test_missing_heartbeat_is_harmless() -> None:
    # No heartbeat file yet (engine never ran here): mtime is None. Must not
    # raise and must not alert.
    sentinel = _sentinel()
    now = datetime(2026, 7, 17, 10, 0, tzinfo=KST)  # weekday, in window
    assert sentinel.evaluate(snapshot_mtime_epoch=None, now=now) == []


def test_load_sentinel_config_parses_env_with_guards() -> None:
    # Missing keys -> defaults.
    default = load_sentinel_config({})
    assert default.stale_threshold_seconds == 360.0
    assert default.repeat_cooldown_seconds == 1800.0
    assert default.window_text == "09:00-15:30"

    # Valid overrides parsed through.
    ok = load_sentinel_config(
        {
            "ENGINE_SENTINEL_STALE_THRESHOLD_SECONDS": "240",
            "ENGINE_SENTINEL_REPEAT_COOLDOWN_SECONDS": "900",
            "ENGINE_SENTINEL_WINDOW": "10:00-14:00",
        }
    )
    assert ok.stale_threshold_seconds == 240.0
    assert ok.repeat_cooldown_seconds == 900.0
    assert ok.window_text == "10:00-14:00"

    # Garbage number -> default; below-minimum -> default (guard, not clamp-to-min);
    # malformed window string -> default window.
    guarded = load_sentinel_config(
        {
            "ENGINE_SENTINEL_STALE_THRESHOLD_SECONDS": "not-a-number",
            "ENGINE_SENTINEL_REPEAT_COOLDOWN_SECONDS": "0",
            "ENGINE_SENTINEL_WINDOW": "not-a-window",
        }
    )
    assert guarded.stale_threshold_seconds == 360.0
    assert guarded.repeat_cooldown_seconds == 1800.0
    assert guarded.window_text == "09:00-15:30"


def test_default_stale_threshold_tracks_worker_refresh_interval() -> None:
    # The worker sleeps for one refresh interval after each collection attempt,
    # so the health threshold needs two intervals of headroom. An explicit
    # sentinel threshold remains an operator override.
    derived = load_sentinel_config(
        {"LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS": "240"}
    )
    assert derived.stale_threshold_seconds == 480.0

    explicit = load_sentinel_config(
        {
            "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS": "240",
            "ENGINE_SENTINEL_STALE_THRESHOLD_SECONDS": "300",
        }
    )
    assert explicit.stale_threshold_seconds == 300.0
