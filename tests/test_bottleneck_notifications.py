from __future__ import annotations

import unittest

from app.notifications.bottleneck import (
    BottleneckAggregator,
    KIS_RATE_LIMIT_BACKOFF,
    LIVE_SNAPSHOT_STALE,
    MAIN_LOOP_EXCEPTION,
    ORDER_RECONCILIATION_DRIFT,
    SNAPSHOT_WORKER_RESTART,
)


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "SLACK_BOTTLENECK_WINDOW_SECONDS": "600",
        "SLACK_BOTTLENECK_COOLDOWN_SECONDS": "1800",
        "SLACK_RATE_LIMIT_THRESHOLD": "5",
        "SLACK_SNAPSHOT_STALE_THRESHOLD": "3",
        "SLACK_DRIFT_THRESHOLD": "2",
        "SLACK_MAIN_LOOP_EXCEPTION_THRESHOLD": "1",
    }
    env.update(overrides)
    return env


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._now = float(start)

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += float(seconds)


class BottleneckAggregatorTests(unittest.TestCase):
    def test_below_threshold_does_not_alert(self) -> None:
        clock = _FakeClock()
        agg = BottleneckAggregator(env=_base_env(), clock=clock)

        for _ in range(4):
            decision = agg.record(KIS_RATE_LIMIT_BACKOFF)
            self.assertFalse(decision.should_alert)
            self.assertEqual(decision.reason, "below_threshold")

    def test_threshold_triggers_exactly_one_alert(self) -> None:
        clock = _FakeClock()
        agg = BottleneckAggregator(env=_base_env(), clock=clock)

        decisions = [agg.record(KIS_RATE_LIMIT_BACKOFF) for _ in range(5)]
        self.assertEqual([d.should_alert for d in decisions], [False] * 4 + [True])
        self.assertEqual(decisions[-1].count_in_window, 5)
        self.assertEqual(decisions[-1].threshold, 5)

    def test_cooldown_suppresses_duplicate_alerts(self) -> None:
        clock = _FakeClock()
        agg = BottleneckAggregator(env=_base_env(), clock=clock)

        for _ in range(5):
            agg.record(KIS_RATE_LIMIT_BACKOFF)

        clock.advance(60)
        suppressed = agg.record(KIS_RATE_LIMIT_BACKOFF)
        self.assertFalse(suppressed.should_alert)
        self.assertEqual(suppressed.reason, "cooldown")

    def test_cooldown_expiry_allows_another_alert(self) -> None:
        clock = _FakeClock()
        agg = BottleneckAggregator(
            env=_base_env(SLACK_BOTTLENECK_COOLDOWN_SECONDS="120"),
            clock=clock,
        )

        for _ in range(5):
            agg.record(KIS_RATE_LIMIT_BACKOFF)

        clock.advance(121)
        decision = agg.record(KIS_RATE_LIMIT_BACKOFF)
        self.assertTrue(decision.should_alert)

    def test_independent_types_have_independent_cooldown(self) -> None:
        clock = _FakeClock()
        agg = BottleneckAggregator(env=_base_env(), clock=clock)

        for _ in range(5):
            agg.record(KIS_RATE_LIMIT_BACKOFF)

        decision = agg.record(MAIN_LOOP_EXCEPTION)
        self.assertTrue(decision.should_alert)
        self.assertEqual(decision.bottleneck_type, MAIN_LOOP_EXCEPTION)

        for _ in range(2):
            self.assertFalse(agg.record(LIVE_SNAPSHOT_STALE).should_alert)
        self.assertTrue(agg.record(LIVE_SNAPSHOT_STALE).should_alert)

    def test_window_eviction_resets_count(self) -> None:
        clock = _FakeClock()
        agg = BottleneckAggregator(
            env=_base_env(SLACK_BOTTLENECK_WINDOW_SECONDS="60"),
            clock=clock,
        )

        for _ in range(4):
            agg.record(KIS_RATE_LIMIT_BACKOFF)
        clock.advance(61)
        for _ in range(4):
            self.assertFalse(agg.record(KIS_RATE_LIMIT_BACKOFF).should_alert)
        decision = agg.record(KIS_RATE_LIMIT_BACKOFF)
        self.assertTrue(decision.should_alert)

    def test_unknown_bottleneck_is_silent(self) -> None:
        agg = BottleneckAggregator(env=_base_env(), clock=_FakeClock())
        decision = agg.record("NOT_A_REAL_TYPE")
        self.assertFalse(decision.should_alert)
        self.assertEqual(decision.reason, "unknown_bottleneck_type")

    def test_thresholds_use_configured_env_values(self) -> None:
        clock = _FakeClock()
        agg = BottleneckAggregator(
            env=_base_env(
                SLACK_RATE_LIMIT_THRESHOLD="2",
                SLACK_SNAPSHOT_STALE_THRESHOLD="4",
            ),
            clock=clock,
        )

        self.assertFalse(agg.record(KIS_RATE_LIMIT_BACKOFF).should_alert)
        self.assertTrue(agg.record(KIS_RATE_LIMIT_BACKOFF).should_alert)

        for _ in range(3):
            self.assertFalse(agg.record(LIVE_SNAPSHOT_STALE).should_alert)
        self.assertTrue(agg.record(LIVE_SNAPSHOT_STALE).should_alert)

    def test_drift_threshold_shared_by_two_types_independently(self) -> None:
        clock = _FakeClock()
        agg = BottleneckAggregator(env=_base_env(), clock=clock)

        self.assertFalse(agg.record(ORDER_RECONCILIATION_DRIFT).should_alert)
        self.assertTrue(agg.record(ORDER_RECONCILIATION_DRIFT).should_alert)
        self.assertFalse(agg.record(SNAPSHOT_WORKER_RESTART).should_alert)
        self.assertTrue(agg.record(SNAPSHOT_WORKER_RESTART).should_alert)


def test_record_bottleneck_prints_breadcrumb_on_send_failure(monkeypatch, capsys):
    # RC3 residual: the send failure used to be swallowed silently. It must now
    # leave a console breadcrumb (still without raising to the caller).
    from types import SimpleNamespace

    from app.notifications import runtime_alerts
    from app.notifications.bottleneck import AlertDecision, MAIN_LOOP_EXCEPTION

    decision = AlertDecision(
        should_alert=True,
        bottleneck_type=MAIN_LOOP_EXCEPTION,
        count_in_window=1,
        threshold=1,
    )

    class _Exploding:
        def send(self, *_args, **_kwargs):
            raise RuntimeError("slack down")

    monkeypatch.setattr(
        runtime_alerts,
        "_BOTTLENECK_AGGREGATOR",
        SimpleNamespace(record=lambda _kind: decision),
    )
    monkeypatch.setattr(runtime_alerts, "_SLACK_NOTIFIER", _Exploding())

    runtime_alerts.record_bottleneck(MAIN_LOOP_EXCEPTION)  # must not raise

    assert "bottleneck alert send failed" in capsys.readouterr().out


if __name__ == "__main__":
    unittest.main()
