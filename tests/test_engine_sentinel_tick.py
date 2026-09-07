"""S2 — run_engine_sentinel_tick adapter (the I/O boundary).

Wires the pure EngineSentinel to the worker-heartbeat mtime and env-scoped Slack
notifier. Time and the mtime reader are injected for determinism; the real
runtime path resolves KST now + os.path.getmtime. Sends must be fail-safe so a
broken alert path can never kill the bot's health loop.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from app.notifications.engine_sentinel import (
    EngineSentinel,
    load_sentinel_config,
    run_engine_sentinel_tick,
)
from app.notifications.slack import ENGINE_HEALTH_EVENT_TYPE

KST = ZoneInfo("Asia/Seoul")


class _FakeNotifier:
    def __init__(self) -> None:
        self.sends: list[tuple[str, str, object]] = []

    def send(self, event_type: str, message: str, *, details: object = None) -> object:
        self.sends.append((event_type, message, details))
        return {"ok": True}


def test_stale_worker_heartbeat_sends_one_engine_health_alert() -> None:
    now = datetime(2026, 7, 17, 10, 0, tzinfo=KST)  # Friday, in window
    stale_epoch = (now.timestamp()) - 420.0  # 7 min old, today
    notifier = _FakeNotifier()

    run_engine_sentinel_tick(
        env={},
        sentinel=EngineSentinel(),
        notifier=notifier,
        now=now,
        mtime_reader=lambda _path: stale_epoch,
    )

    assert len(notifier.sends) == 1
    event_type, message, _details = notifier.sends[0]
    assert event_type == ENGINE_HEALTH_EVENT_TYPE
    assert "다운" in message


def test_missing_worker_heartbeat_file_is_silent() -> None:
    # os.path.getmtime raises OSError when the file is absent; the adapter must
    # treat it as "mtime None" -> no alert, no exception.
    def _raise(_path: str) -> float:
        raise FileNotFoundError("no heartbeat yet")

    now = datetime(2026, 7, 17, 10, 0, tzinfo=KST)
    notifier = _FakeNotifier()

    run_engine_sentinel_tick(
        env={},
        sentinel=EngineSentinel(),
        notifier=notifier,
        now=now,
        mtime_reader=_raise,
    )

    assert notifier.sends == []


def test_runtime_uses_worker_heartbeat_and_tolerates_normal_defer_gap() -> None:
    # Production refresh ticks take slightly longer than their nominal 180s
    # sleep, while pressure deferrals intentionally leave live_snapshot.json
    # untouched. The heartbeat is the liveness signal and 205s is healthy.
    now = datetime(2026, 7, 17, 10, 0, tzinfo=KST)
    log_dir = "/var/tmp/account-a/logs"
    env = {
        "KIS_LIVE_SNAPSHOT_LOG_DIR": log_dir,
        "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS": "180",
    }
    expected_path = os.path.join(
        log_dir,
        "kis_trader_live_snapshot.heartbeat",
    )
    observed_paths: list[str] = []

    def _read(path: str) -> float:
        observed_paths.append(path)
        return now.timestamp() - 205.0

    notifier = _FakeNotifier()
    run_engine_sentinel_tick(
        env=env,
        sentinel=EngineSentinel(config=load_sentinel_config(env)),
        notifier=notifier,
        now=now,
        mtime_reader=_read,
    )

    assert observed_paths == [expected_path]
    assert notifier.sends == []


def test_kill_switch_disables_send() -> None:
    # ENGINE_SENTINEL_ENABLED=0 short-circuits the tick: no send even for a
    # would-be-DOWN (in-window, today, stale) scenario.
    now = datetime(2026, 7, 17, 10, 0, tzinfo=KST)
    stale_epoch = now.timestamp() - 420.0
    notifier = _FakeNotifier()

    run_engine_sentinel_tick(
        env={"ENGINE_SENTINEL_ENABLED": "0"},
        sentinel=EngineSentinel(),
        notifier=notifier,
        now=now,
        mtime_reader=lambda _p: stale_epoch,
    )

    assert notifier.sends == []


def test_notifier_exception_is_swallowed_with_console_breadcrumb(capsys) -> None:
    # A broken Slack path must never propagate out of the tick (it runs inside
    # the bot's health loop); RC3 discipline leaves a console breadcrumb.
    class _RaisingNotifier:
        def send(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("slack unreachable")

    now = datetime(2026, 7, 17, 10, 0, tzinfo=KST)
    stale_epoch = now.timestamp() - 420.0

    run_engine_sentinel_tick(
        env={},
        sentinel=EngineSentinel(),
        notifier=_RaisingNotifier(),
        now=now,
        mtime_reader=lambda _p: stale_epoch,
    )  # must not raise

    out = capsys.readouterr().out
    assert "engine sentinel alert send failed" in out
